# NewsReap NNTP Search CLI Plugin
#
# Copyright (C) 2015-2017 Chris Caron <lead2gold@gmail.com>
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.

import logging
import click
import os
import shlex
import subprocess
import sys

from os import listdir
from os import makedirs
from os.path import basename
from os.path import expanduser
from os.path import join
from shutil import which
from os.path import isdir
from os.path import isfile
from os.path import dirname
from os.path import abspath

try:
    from newsreap.Logging import NEWSREAP_CLI
except ImportError:
    # Path
    sys.path.insert(0, dirname(dirname(dirname(dirname(abspath(__file__))))))
    from newsreap.Logging import NEWSREAP_CLI

from newsreap.objects.group.Article import Article
from newsreap.objects.index.Collection import Collection
from newsreap.objects.nntp.Common import get_groups

import json
from collections import namedtuple
from itertools import chain
from itertools import islice
from types import SimpleNamespace

from sqlalchemy import and_
from sqlalchemy import or_
from sqlalchemy import func
from sqlalchemy import literal

from newsreap.NNTPArchiver import DEFAULT_ARCHIVE_MAX_SIZE
from newsreap.NNTPArchiver import NNTPArchiver
from newsreap.NNTPDownloader import tidy_name
from newsreap.NNTPFetcher import NNTPFetcher
from newsreap.NNTPFetcher import replace_if_changed
from newsreap.Utils import start_program
from newsreap.Utils import bytes_to_strsize
from newsreap.NNTPGroupDatabase import NNTPGroupDatabase
from newsreap.NNTPIndexer import NNTPIndexer
from newsreap.NNTPArticleRanks import NNTPArticleRanks
from newsreap.NNTPFilterRule import tags_to_text
from newsreap.NNTPIndexDatabase import NNTPIndexDatabase
from newsreap.NNTPKeyring import NNTPKeyring
from newsreap.NNTPIndexer import COLLECTION_FIELDS
from newsreap.NNTPIndexer import keyword_choices
from newsreap.NNTPSearchTerm import SEARCH_ANY
from newsreap.NNTPSearchTerm import SEARCH_FIELDS
from newsreap.NNTPIndexer import rank_query
from newsreap.NNTPIndexer import rank_text
from newsreap.CliOptions import matching
from newsreap.CliOptions import ranked

# initialize our logger
logger = logging.getLogger(NEWSREAP_CLI)

# How many cached headers are looked at a time (with --articles)
RAW_PAGE_SIZE = 1000

# What each kind of search can look at (see NNTPSearchTerm)
POST_FIELDS = (SEARCH_ANY, *SEARCH_FIELDS)
RAW_FIELDS = (SEARCH_ANY, 'subject', 'poster', 'message_id')

# One thing a search found: its key (a collection's id, or an article's
# Message-ID; None for a heading), the lines that show it, and what it is
# (for --json and --delete)
Found = namedtuple('Found', ('key', 'lines', 'data'))

NEWSREAP_CLI_PLUGINS = {
    # format:
    # cli short hand group: function prefix
    'search': 'search',
}


def page(lines, pager=True):
    """
    Shows lines of results a screen at a time (as 'less' does) when they
    go to a terminal, or all of them when they don't (ie: into a file) or
    `pager` is off; returns False if there were none
    """
    lines = iter(lines)
    first = next(lines, None)
    if first is None:
        return False

    command = pager_command() if pager else None
    if command is None:
        for line in chain([first], lines):
            click.echo(line)
        return True

    run_pager(command, chain([first], lines))
    return True


def pager_command():
    """
    Returns the pager to show results with (the PAGER setting, or less);
    None when there is no terminal to page on (ie: written to a file)
    """
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None

    command = shlex.split(os.environ.get('PAGER', ''))
    if not command:
        if os.environ.get('TERM') in ('dumb', 'emacs'):
            # A terminal that can't page
            return None
        command = ['less']

    path = which(command[0])
    return [path, *command[1:]] if path else None


def run_pager(command, lines):
    """
    Hands lines to a pager (see pager_command()) and waits for it to be
    closed.

    less reads your keys from the terminal its error messages go to.  Run
    through tox, that is a terminal tox keeps to itself (it only shows
    what is written to it), so the pager gets its errors sent to the
    terminal you type into instead.
    """
    env = dict(os.environ)
    if basename(command[0]) == 'less' and len(command) == 1 and \
            not env.get('LESS'):
        # Keep colours (as click does)
        env['LESS'] = '-R'

    keyboard = None
    try:
        keyboard = os.open(os.ttyname(sys.stdin.fileno()), os.O_RDWR)

    except OSError:
        # Leave its errors where ours go
        pass

    try:
        process = start_program(
            command, stdin=subprocess.PIPE, stderr=keyboard, env=env,
            text=True, errors='replace')

    finally:
        if keyboard is not None:
            os.close(keyboard)

    try:
        for line in lines:
            process.stdin.write('%s\n' % line)

    except BrokenPipeError:
        # Closed before everything was shown
        pass

    except BaseException:
        # Don't leave it running (and the terminal in a mess)
        process.terminate()
        raise

    finally:
        try:
            process.stdin.close()

        except BrokenPipeError:
            pass

        # less catches Ctrl-C for itself (it stops a search); wait until it
        # is closed
        while True:
            try:
                process.wait()
                break

            except KeyboardInterrupt:
                pass


def lookup_groups(ctx, groups):
    """
    Returns the names of the groups the aliases or group names given refer
    to (exits if any of them don't exist)
    """
    session = ctx['NNTPSettings'].session()
    if not session:
        logger.error("The database is not correctly configured.")
        exit(1)

    names = {}
    for group in groups:
        found = get_groups(session=session, lookup=group)
        if not found:
            logger.error("The group/alias '%s' does not exist." % group)
            exit(1)

        names.update(found)

    return names


def raw_lines(ctx, groups, keywords, rank, terms, case_sensitive,
              limit=None):
    """
    Searches the article headers 'nr update' cached (the ones not indexed
    yet, or not let go of yet); yields what is found (see Found; at most
    `limit` articles, across every group)
    """
    # Where the articles' scores and tags are (if anything was ever indexed)
    index = None
    index_session = None
    index_db = ctx['NNTPSettings'].index_database()
    if NNTPIndexDatabase.exists(index_db):
        index = NNTPIndexDatabase.from_engine(index_db)
        index_session = index.session()

    try:
        yield from raw_group_lines(
            ctx, groups, keywords, rank, terms, case_sensitive,
            index_session, limit)

    finally:
        if index is not None:
            index.close()


def raw_group_lines(ctx, groups, keywords, rank, terms, case_sensitive,
                    index_session, limit=None):
    """
    Yields the cached headers of each group that match (see raw_lines())
    """
    # How many more may be shown
    left = limit or None
    for name in groups:
        if left is not None and left <= 0:
            break

        db_file = ctx['NNTPSettings'].group_database(name)
        if not isfile(db_file):
            logger.warning("There is no cached content for '%s'." % name)
            continue

        db = NNTPGroupDatabase.from_file(db_file)
        session = db.session()
        try:
            for found in raw_group_query(
                    session, name, keywords, rank, terms, case_sensitive,
                    index_session):
                if found.key is None:
                    # The group's heading
                    yield found
                    continue

                if left is not None:
                    if left <= 0:
                        break
                    left -= 1

                yield found

        finally:
            session.close()
            db.close()


def raw_group_query(session, name, keywords, rank, terms, case_sensitive,
                    index_session):
    """
    Yields what one group's cached headers that match are (see Found): its
    heading (the group's name) before the first, then each article
    """
    query = session.query(Article)

    # Each keyword finds what has any one of its choices
    for exclude, choices in keyword_choices(keywords, case_sensitive):
        found = []
        for column, value, term in choices:
            column = getattr(Article, column)
            if term is not None:
                found.append(term.condition('sqlite', column))
            elif case_sensitive:
                found.append(func.instr(column, value) > 0)
            else:
                found.append(column.icontains(value, autoescape=True))

        found = or_(*found)
        query = query.filter(~found if exclude else found)

    # Every condition narrows the results down further
    for term in terms:
        if term.field == 'message_id':
            # Searched without its < and > (whichever way it was cached)
            column = func.trim(Article.message_id, '<>')

        else:
            column = Article.poster if term.field == 'poster' \
                else Article.subject

        found = term.condition('sqlite', column)
        if term.field == 'message_id' and term.exact() is not None:
            # Looked up directly
            found = and_(Article.message_id.in_(
                [term.exact(), '<%s>' % term.exact()]), found)

        query = query.filter(~found if term.negate else found)

    # When they were posted is narrowed down here; their scores and
    # tags are kept with the index (see NNTPArticleRanks)
    query = rank_query(
        query, literal(0), literal(''),
        (Article.posted_date, Article.posted_date),
        after=rank.get('after'), before=rank.get('before'))\
        .order_by(Article.article_no)

    # The group's name before the first one found
    heading = '%s:' % name
    entries = iter(query.yield_per(RAW_PAGE_SIZE))
    while True:
        page = list(islice(entries, RAW_PAGE_SIZE))
        if not page:
            break

        ranks = NNTPArticleRanks.load(
            index_session, [entry.message_id for entry in page]) \
            if index_session is not None else {}

        for entry in page:
            score, tags = ranks.get(entry.message_id, (0, frozenset()))
            if not NNTPArticleRanks.matches(
                    score, tags, tags_wanted=rank.get('tags'),
                    without_tags=rank.get('without_tags'),
                    min_score=rank.get('min_score'),
                    max_score=rank.get('max_score'),
                    scores=rank.get('scores')):
                continue

            if heading:
                yield Found(None, [heading], None)
                heading = None

            yield Found(entry.message_id.strip().strip('<>'), [
                '  [%.5d] %s%s' % (
                    entry.article_no, entry.subject, rank_text(
                        SimpleNamespace(
                            score=score, tags=tags_to_text(tags))))], {
                'kind': 'article', 'group': name,
                'article_no': entry.article_no,
                'message_id': entry.message_id.strip().strip('<>'),
                'subject': entry.subject, 'poster': entry.poster,
                'posted': entry.posted_date.isoformat()
                if entry.posted_date else None,
                'size': entry.size, 'score': score, 'tags': sorted(tags)})


def post_lines(ctx, keywords, groups, limit, rank, terms, case_sensitive,
               oldest=False, notes=None, keys=()):
    """
    Searches the articles 'nr update --content' kept; yields what is found
    (see Found; what to say if there is nothing to search is added to
    notes)
    - keys: only the posts tied to one of these PGP keys (key IDs,
      fingerprints or names)
    """
    archiver = NNTPArchiver(
        ctx['NNTPSettings'].index_database(), default_hooks=False)
    try:
        if archiver.session() is None:
            if notes is not None:
                notes.append(
                    "Nothing archived yet; run 'nr update <group> "
                    "--content' first.")
            return

        key_ids = None
        if keys:
            key_ids = set()
            for key in keys:
                found = NNTPKeyring.resolve(archiver.session(), key)
                if not found and notes is not None:
                    notes.append("No PGP key matches '%s' (see 'nr pgp "
                                 "ls')." % key)
                key_ids.update(found)

        found = archiver.find(
            keywords, groups=groups, limit=limit,
            case_sensitive=case_sensitive, terms=terms, oldest=oldest,
            key_ids=key_ids, **rank)

        # Their scores and tags (kept with every article's)
        ranks = NNTPArticleRanks.load(
            archiver.session(), [post.message_id for post in found])

        for post in found:
            score, tags = ranks.get(post.message_id, (0, frozenset()))
            lines = ['%s  %-24.24s  %s%s' % (
                post.posted.strftime('%Y-%m-%d %H:%M') if post.posted
                else '????-??-?? ??:??', post.poster, post.subject,
                rank_text(SimpleNamespace(
                    score=score, tags=tags_to_text(tags)))),
                '    <%s>' % post.message_id]

            snippet = archiver.snippet(post, keywords, terms)
            if snippet:
                lines.append('    %s' % snippet)

            yield Found(post.message_id, lines, {
                'kind': 'post', 'message_id': post.message_id,
                'subject': post.subject, 'poster': post.poster,
                'posted': post.posted.isoformat() if post.posted else None,
                'size': post.size, 'score': score, 'tags': sorted(tags)})

    finally:
        archiver.close()


@click.command(name='search')
@click.argument('keywords', nargs=-1)
@click.option('--group', '-g', 'groups', multiple=True,
              help='Only look in this group or alias (can be given more '
                   'than once).')
@click.option('--ignore-case/--case-sensitive', '-i/-c', default=True,
              help='Whether the case of the keywords matters (it doesn\'t '
                   'by default).')
@click.option('--complete', 'complete', flag_value=True, default=None,
              help='Only collections with every file and segment found.')
@click.option('--incomplete', 'complete', flag_value=False,
              help='Only collections missing a file or segment.')
@click.option('--forming', is_flag=True,
              help='Include collections whose articles are still arriving.')
@click.option('--limit', '-l', type=click.IntRange(min=1),
              help='Show at most this many.')
@click.option('--oldest-first', '-O', 'oldest', is_flag=True,
              help='Oldest first (instead of newest first).')
@click.option('--quiet', '-q', 'quiet', is_flag=True,
              help="Only show what each one is called (a collection's id, "
                   "or an article's Message-ID), one per line, to hand to "
                   "another command (ie: nr search holiday -q | xargs nr "
                   "get).")
@click.option('--json', 'as_json', is_flag=True,
              help='Show what each one is as JSON (one object per line).')
@click.option('--delete', is_flag=True,
              help='Remove what is found (it is shown first, and you are '
                   'asked; -y answers yes).  With a --download option, '
                   'only what was all saved is removed.')
@click.option('--download', '-d', 'get_files', is_flag=True,
              help='Download the files what is found carries (rar, zip, '
                   '...) as they were posted; nothing is repaired or '
                   'extracted, and articles that are only text are left '
                   'out.')
@click.option('--download-text', '-x', 'get_text', is_flag=True,
              help='Save each article that is only text as a .txt file '
                   '(articles carrying a file are left out).')
@click.option('--download-articles', '-a', 'get_articles', is_flag=True,
              help='Save each article exactly as the server sent it (an '
                   '.eml file).')
@click.option('--download-nzb', '-z', 'get_nzb', is_flag=True,
              help='Save an NZB file of what is found.')
@click.option('--download-dir', '-o', 'download_dir', default=None,
              metavar='DIR',
              help='Where downloads go (the download_dir setting when not '
                   'given).')
@click.option('--retry-missing', is_flag=True,
              help='Ask again for articles no server had, even ones that '
                   "can't have arrived since (they are otherwise only asked "
                   'for again while the post is new, or once you add a '
                   'server).')
@click.option('--pager/--no-pager', default=True,
              help='Show the results a screen at a time on a terminal (the '
                   'default; PAGER picks the pager, less otherwise), or '
                   'write them all out.')
@click.option('--collections', is_flag=True,
              help='Search the collections: what was posted, put together '
                   '(the default).')
@click.option('--articles', '--raw', 'raw', is_flag=True,
              help='Search every article header cached for the groups given '
                   '(one line each, as a newsreader lists them).')
@click.option('--posts', is_flag=True,
              help="Search what the small articles 'nr update --content' "
                   "kept say (their text and file names too).")
@click.option('--key', '-k', 'keys', multiple=True,
              help='Only posts signed by, encrypted for or carrying this PGP '
                   'key (a key ID, fingerprint or part of its name; implies '
                   '--posts; see nr pgp).')
@ranked
@matching
@click.pass_obj
def search(ctx, keywords, groups, ignore_case, complete, forming, limit,
           oldest, quiet, as_json, delete, get_files, get_text,
           get_articles, get_nzb, download_dir, retry_missing, pager,
           collections, posts, raw, keys, rank, terms):
    """
    Search everything indexed (across every group).

    Each keyword must be in a collection's name or one of its file subjects
    (newest first, or oldest first with --oldest-first); with none,
    everything is listed.  'alert|danger' finds either word.  On a terminal they are shown a screen at a time
    (as 'less' does: up and down move a row, space and b a page, / finds,
    q quits); piped or saved to a file (or with --no-pager), all of them
    are written out.  Collections whose articles are still arriving are only shown
    with --forming.  Case and accents don't matter unless --case-sensitive
    is given; Usenet subjects are rarely written the same way twice.

    Keywords can start with:

    \b
        -     leave out what has it:     nr search -- holiday -2019
        +     needed to search for text that starts with + or -:
                                         nr search ++++AWESOME
        %p    look in the poster instead: nr search %pChris holiday
              (%p-Chris leaves out what Chris posted)

    A | between words finds either one ('alert|danger'; quote it, and put
    - in front to leave out both: '-sample|proof'); \\| is a | itself.

    Put -- before the keywords if one of them starts with a dash.  A
    keyword can hold * and ? wildcards too ('holi*ay').

    For more control, the options work like the tests of 'find': the whole
    subject must match the pattern given (* matches anything, ? one
    character; a leading ! leaves out what matches).  The i versions ignore
    case and accents; the regex versions take a regular expression that
    must match the whole subject.  Every one given narrows the results down
    further:

    \b
        nr search --isubject '*holiday*' --isubject '!*sample*'
        nr search --ifilename '*.nfo' --iposter 'chris*'
        nr search --posts --ibody '*coffee*' --iregex-subject 're: .*'

    Three kinds of things can be searched: collections (what was posted,
    put together; the default), --articles (every cached article header,
    one line each) and --posts (what the small articles 'nr update
    --content' kept say, searched by their words: 'holid' finds
    'holiday').  Give more than one to search them all; each is shown
    under its own heading.

    Collections have a subject (their name or a file subject), file names,
    posters and Message-IDs (of their articles); posts have a body too;
    articles only a subject, poster and Message-ID.  Without a kind given,
    the one your conditions need is searched (ie: --ibody searches posts).
    Give Message-IDs without their < and >; read a post or article with
    'nr show <message-id>'.

    What is found can be handed on or removed:

    \b
        nr search holiday -q | xargs nr get     # download what is found
        nr search holiday -q | xargs nr nzb save
        nr search --articles -g test --iposter '*bot*' --delete
        nr search holiday --json                # one JSON object each

    --delete shows what it found and asks before removing it (-y answers
    yes); to keep removing the same things as they arrive, save the
    conditions as a rule with 'nr filter purge ... --save'.

    What is found can be downloaded too, in a directory named after it
    in your download directory (-o picks another); give as many of these
    as you like:

    \b
        nr search holiday -d        # the files, as posted (rar, zip, ...)
        nr search holiday -x        # the articles that are only text
        nr search holiday -a        # every article as the server sent it
        nr search holiday -z        # an NZB file of each
        nr search holiday -d -z --delete   # then remove what was saved

    Each article is fetched once, whatever is asked for.  Running the same
    download again skips what is already there and right (its crc32 is
    checked), and fetches only what is missing.  If anything can't be
    saved, the command says what and exits with 1; with --delete, only
    what was all saved is removed.
    """
    # What each kind of search can look at
    fields = {'collections': COLLECTION_FIELDS, 'posts': POST_FIELDS,
              'raw': RAW_FIELDS}

    # What is searched: the kinds asked for, or else the first kind (of
    # collections and posts) that has everything the conditions look at
    kinds = [kind for kind, wanted in (
        ('collections', collections), ('posts', posts or keys), ('raw', raw))
        if wanted] or [next((
            kind for kind in ('collections', 'posts')
            if all(term.field in fields[kind] for term in terms)),
            'collections')]

    for kind in kinds:
        for term in terms:
            if term.field not in fields[kind]:
                logger.error("--%s can't be used to search %s%s." % (
                    term.option, 'articles' if kind == 'raw' else kind,
                    ' (only --posts)' if term.field == 'body' else ''))
                exit(1)

    if 'raw' in kinds and not groups:
        logger.error('Searching --articles needs a --group.')
        exit(1)

    # What to download (if anything)
    wanted = {'files': get_files, 'text': get_text,
              'articles': get_articles, 'nzb': get_nzb}
    if (download_dir or retry_missing) and not any(wanted.values()):
        logger.error('%s needs one of --download, --download-text, '
                     '--download-articles or --download-nzb.' % (
                         '--download-dir' if download_dir
                         else '--retry-missing'))
        exit(1)

    if any(wanted.values()) and as_json:
        logger.error("--json can't be used to download.")
        exit(1)

    # The groups we're narrowing it down to (if any)
    names = lookup_groups(ctx, groups) if groups else None

    # What to say if nothing is found
    notes = []
    sections = []
    indexer = None
    options = None

    if 'collections' in kinds:
        indexer = NNTPIndexer(ctx['NNTPSettings'].index_database())
        if indexer.session() is None:
            notes.append(nothing_indexed(ctx))

        else:
            options = dict(
                keywords=keywords, groups=set(names) if names else None,
                complete=complete, limit=limit,
                case_sensitive=not ignore_case, terms=terms, **rank)
            sections.append(('Collections', (
                collection_found(entry) for entry in indexer.find(
                    forming=forming, oldest=oldest, stream=True,
                    **options))))

    if 'posts' in kinds:
        sections.append(('Posts', post_lines(
            ctx, keywords, set(names) if names else None, limit, rank,
            terms, not ignore_case, oldest=oldest, notes=notes,
            keys=keys)))

    if 'raw' in kinds:
        sections.append(('Articles', raw_lines(
            ctx, names, keywords, rank, terms, not ignore_case,
            limit=limit)))

    def everything():
        # What each kind found (a heading before each, when there are
        # several)
        for title, found in sections:
            if limit and title != 'Articles':
                # At most this many of each kind (reading stops there; the
                # articles count their own, leaving out headings)
                found = islice(found, limit)

            first = next(found, None)
            if first is None:
                continue

            if len(kinds) > 1:
                yield Found(None, ['%s:' % title], None)

            yield first
            yield from found

    def lines(found):
        for entry in found:
            yield from entry.lines

    try:
        if any(wanted.values()):
            exit(1 if download(
                ctx, everything(), wanted, download_dir, delete, quiet,
                ' '.join(keywords), retry_missing=retry_missing) else 0)

        if delete:
            remove(ctx, everything(), names, pager)
            return

        if quiet or as_json:
            # One per line: what each is (for scripts), or its key (to
            # hand to another command, ie: nr get)
            shown = False
            for entry in everything():
                if entry.key is not None:
                    click.echo(json.dumps(entry.data) if as_json
                               else entry.key)
                    shown = True

            if not shown and not quiet:
                click.echo('Nothing found.', err=True)
            return

        if not page(lines(everything()), pager=pager):
            click.echo('Nothing found.')
            for note in notes:
                click.echo(note)

            if options is not None:
                for hint in search_hints(
                        ctx, indexer, keywords, forming, options):
                    click.echo(hint)

    finally:
        if indexer is not None:
            indexer.close()


def collection_found(entry):
    """
    Returns what a collection search found (see Found)
    """
    return Found(entry.id, [NNTPIndexer.summary(entry)], {
        'kind': 'collection', 'id': entry.id, 'name': entry.name,
        'size': entry.size, 'files': entry.files,
        'files_expected': entry.files_expected,
        'segments': entry.segments,
        'segments_expected': entry.segments_expected,
        'score': getattr(entry, 'score', 0) or 0,
        'tags': sorted(filter(None, (getattr(entry, 'tags', '') or '')
                              .split()))})


def shown(found, show=True, fields=None):
    """
    Prints what a search found and returns its results (not its headings)
    with only what is needed to act on them, so a big search isn't held in
    memory with all of its printed lines

    - show: print the lines (a quiet run only gathers)
    - fields: the details kept of each result (all of them if None)
    """
    kept = []
    for entry in found:
        if show:
            for line in entry.lines:
                click.echo(line)

        if entry.key is None:
            # A heading
            continue

        kept.append(Found(entry.key, (), entry.data if fields is None else {
            key: entry.data[key] for key in fields if key in entry.data}))

    return kept


def counted(entries):
    """
    Returns what search results are, by kind ({kind: [entry]}), and how
    that is said (ie: '2 collection(s), 4 article(s)')
    """
    kinds = {}
    for entry in entries:
        kinds.setdefault(entry.data['kind'], []).append(entry)

    return kinds, ', '.join('%d %s(s)' % (len(found), kind)
                            for kind, found in kinds.items())


def download(ctx, found, wanted, download_dir, delete, quiet, title,
             retry_missing=False):
    """
    Downloads what a search found (see the --download options): it is
    shown first, and the user is asked (unless -y was given).  With
    delete, what was all saved is removed afterwards.  Returns how many
    couldn't all be saved.

    - retry_missing: ask again for articles no server had (see NNTPRetry)
    """
    entries = shown(found, show=not quiet)
    if not entries:
        click.echo('Nothing found.')
        return 0

    kinds, what = counted(entries)
    size = sum(entry.data.get('size') or 0 for entry in entries)
    if not ctx.get('noprompt') and not click.confirm(
            'Download %s (%s)%s?' % (
                what, bytes_to_strsize(size),
                ' and then remove them' if delete else ''), default=False):
        click.echo('Nothing was downloaded.')
        return 0

    settings = ctx['NNTPSettings']
    folder = abspath(expanduser(download_dir)) if download_dir \
        else settings.download_dir()

    fetching = wanted['files'] or wanted['text'] or wanted['articles']
    indexer = NNTPIndexer(settings.index_database())

    # What is fetched is kept in the archive as 'nr update --content'
    # would keep it (the same size limit and filters), so it is never
    # fetched again for its text
    archiving = True
    try:
        filters = settings.filters()

    except ValueError as e:
        logger.warning('%s; what is downloaded is not kept in the archive.' %
                       e)
        filters, archiving = None, False

    try:
        archiver = NNTPArchiver(
            settings.index_database(),
            max_size=settings.nntp_processing.get('archive_max_size') or
            DEFAULT_ARCHIVE_MAX_SIZE,
            filters=filters if filters is not None and len(filters) else None,
            pgp=settings.pgp(), gpg=settings.gpg())

    except ValueError as e:
        logger.warning('%s; what is downloaded is not kept in the archive.' %
                       e)
        archiver, archiving = None, False

    fetcher = NNTPFetcher(
        ctx['NNTPManager'], settings.staging_dir(), folder,
        files=wanted['files'], text=wanted['text'],
        articles=wanted['articles'], archiver=archiver,
        keep=archiving, retry_window=settings.retry_window(),
        retry_missing=retry_missing)

    # What was all saved (and so may be removed), and what wasn't
    saved = []
    failed = 0
    try:
        for entry in kinds.get('collection', []):
            name = entry.data['name'] or 'collection-%s' % entry.key
            done = True
            if fetching:
                files, known = indexer.fetch_files(entry.key)
                done = keep(ctx, fetcher, indexer, name, files, known)

            if wanted['nzb']:
                makedirs(folder, exist_ok=True)
                path = join(folder, '%s.nzb' % tidy_name(name))
                if indexer.nzb(entry.key, path + '.new',
                               work_dir=settings.work_dir):
                    replace_if_changed(path + '.new', path)
                    click.echo('%s: saved %s' % (name, path))

                else:
                    logger.error('%s: the NZB file could not be written.' %
                                 name)
                    done = False

            if done:
                saved.append(entry)
            else:
                failed += 1

        for kind in ('article', 'post'):
            if kind not in kinds:
                continue

            # Named after the search (and the kind, when both kinds were
            # searched, so their NZB files don't take each other's place)
            name = ('articles' if kind == 'article' else 'posts') \
                if not title else title if len(
                    {'article', 'post'} & set(kinds)) == 1 \
                else '%s %ss' % (title, kind)
            ids = [entry.key for entry in kinds[kind]]
            files, known = indexer.fetch_files(message_ids=ids) \
                if indexer.session() is not None else ([], {})

            # The ones that aren't indexed are put together by their
            # subjects
            indexed = {m for f in files for _, m in f['segments']}
            files.extend(NNTPFetcher.files_of(
                {**entry.data, 'message_id': entry.key}
                for entry in kinds[kind] if entry.key not in indexed))

            # Each article is saved (or not) on its own
            unfinished = set()
            if fetching and not keep(
                    ctx, fetcher, indexer, name, files, known,
                    texts=post_texts(ctx, ids) if kind == 'post' and
                    wanted['text'] else None):
                unfinished = fetcher.unfinished

            if wanted['nzb']:
                makedirs(folder, exist_ok=True)
                path = join(folder, '%s.nzb' % tidy_name(name))
                if NNTPFetcher.write_nzb(path, files, title=name,
                                         work_dir=settings.work_dir):
                    click.echo('%s: saved %s' % (name, path))

                else:
                    logger.error('%s: the NZB file could not be written.' %
                                 name)
                    unfinished = set(ids)

            for entry in kinds[kind]:
                if entry.key in unfinished:
                    failed += 1
                else:
                    saved.append(entry)

    except KeyboardInterrupt:
        # What came is kept; nothing is removed
        click.echo('Stopped; run it again to carry on.', err=True)
        sys.exit(130)

    finally:
        indexer.close()
        if archiver is not None:
            archiver.close()

    if delete and saved:
        unremoved = forget(ctx, counted(saved)[0])
        failed += unremoved
        if not unremoved:
            click.echo('Removed %s.' % counted(saved)[1])

    if failed:
        logger.error('%d of them could not all be saved; running this again '
                     'asks for what may have arrived since (--retry-missing '
                     'asks for all of it).' % failed)

    return failed


def keep(ctx, fetcher, indexer, name, files, known, texts=None):
    """
    Fetches what is wanted of the files given and keeps what was learned
    in the index; returns True when it was all saved
    """
    try:
        done = fetcher.fetch(name, files, known, texts=texts)

    finally:
        # Kept even when it was stopped
        if fetcher.learned_segments and not indexer.note_fetched(
                fetcher.learned_segments) and not ctx.get('unnoted'):
            # Said once; the downloads themselves are fine
            ctx['unnoted'] = True
            click.echo("Note: what was downloaded couldn't be noted in the "
                       'index, so running this again may fetch some '
                       'articles to find out what they hold.', err=True)

    click.echo('%s: %s' % (name, fetcher))
    if not done:
        logger.error('%s: %s.' % (name, fetcher.problem))
        if fetcher.staging and isdir(fetcher.staging):
            click.echo('What was downloaded is kept in %s (running this '
                       'again carries on from there)' % fetcher.staging)

    elif isdir(fetcher.folder):
        click.echo('Saved in %s' % fetcher.folder)

    return done


def post_texts(ctx, message_ids):
    """
    Returns the text 'nr update --content' kept of the posts given (see
    NNTPFetcher.fetch())
    """
    archiver = NNTPArchiver(
        ctx['NNTPSettings'].index_database(), default_hooks=False)
    try:
        return {
            post.message_id: {
                'text': post.text, 'subject': post.subject,
                'poster': post.poster, 'posted': post.posted}
            for post in archiver.posts(message_ids) if post.text}

    finally:
        archiver.close()


def remove(ctx, found, groups, pager):
    """
    Removes what a search found (--delete): it is shown first, and the user
    is asked (unless -y was given)
    """
    # Removing only needs what each is and where it is kept
    wanted = shown(found, fields=('kind', 'group'))
    if not wanted:
        click.echo('Nothing found.')
        return

    kinds, what = counted(wanted)
    if not ctx.get('noprompt') and not click.confirm(
            'Remove %s?' % what, default=False):
        click.echo('Nothing was removed.')
        return

    if forget(ctx, kinds):
        exit(1)
    click.echo('Removed %s.' % what)


def forget(ctx, kinds):
    """
    Removes search results ({kind: [entry]}; see counted()) everywhere
    they are kept; returns how many couldn't be
    """
    failed = 0
    settings = ctx['NNTPSettings']
    if 'collection' in kinds:
        indexer = NNTPIndexer(settings.index_database())
        try:
            for entry in kinds['collection']:
                if not indexer.delete(entry.key):
                    logger.error(
                        'Could not remove collection %s.' % entry.key)
                    failed += 1

        finally:
            indexer.close()

    if 'post' in kinds:
        archiver = NNTPArchiver(settings.index_database(), default_hooks=False)
        try:
            archiver.forget([entry.key for entry in kinds['post']])

        finally:
            archiver.close()

    if 'article' in kinds:
        # From the cache of each group they were found in, with their scores
        # and tags
        by_group = {}
        for entry in kinds['article']:
            by_group.setdefault(entry.data['group'], []).append(entry.key)

        for name, ids in by_group.items():
            cache = NNTPGroupDatabase.from_file(settings.group_database(name))
            try:
                cache.forget(ids)

            finally:
                cache.close()

        indexer = NNTPIndexer(settings.index_database())
        try:
            indexer.forget_ranks([entry.key for entry in kinds['article']])

        finally:
            indexer.close()

    return failed


def nothing_indexed(ctx):
    """
    Returns what to say when nothing is indexed yet
    """
    cache_dir = dirname(ctx['NNTPSettings'].group_database('_'))
    if isdir(cache_dir) and listdir(cache_dir):
        return "Nothing indexed yet; run 'nr update index' first."

    return "Nothing fetched yet; run 'nr update <group> --newest 100000' " \
        "first (see 'nr group ls' for the groups)."


def search_hints(ctx, indexer, keywords, forming, options):
    """
    Returns why a search may have found nothing (and what to try instead)
    """
    if not indexer.session().query(Collection.id).first():
        yield nothing_indexed(ctx)
        return

    session = ctx['NNTPSettings'].session()
    for keyword in keywords:
        # A group given where the keywords go
        if '.' in keyword and session and \
                get_groups(session=session, lookup=keyword):
            yield "'%s' is a group; to look in it: nr search -g %s" % (
                keyword, keyword)

    if not forming:
        # Collections whose articles are still arriving are left out
        count = len(indexer.find(forming=True, **options))
        if count:
            yield '%d collection(s) still forming match; add --forming ' \
                'to see them.' % count
