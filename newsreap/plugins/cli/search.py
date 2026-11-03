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
from os.path import basename
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

from itertools import chain
from itertools import islice
from types import SimpleNamespace

from sqlalchemy import and_
from sqlalchemy import func
from sqlalchemy import literal

from newsreap.NNTPArchiver import NNTPArchiver
from newsreap.NNTPGroupDatabase import NNTPGroupDatabase
from newsreap.NNTPIndexer import NNTPIndexer
from newsreap.NNTPArticleRanks import NNTPArticleRanks
from newsreap.NNTPFilterRule import tags_to_text
from newsreap.NNTPIndexDatabase import NNTPIndexDatabase
from newsreap.NNTPKeyring import NNTPKeyring
from newsreap.NNTPIndexer import COLLECTION_FIELDS
from newsreap.NNTPIndexer import keyword_terms
from newsreap.NNTPSearchTerm import SEARCH_ANY
from newsreap.NNTPSearchTerm import SEARCH_FIELDS
from newsreap.NNTPIndexer import rank_query
from newsreap.NNTPIndexer import rank_text
from newsreap.CliOptions import matching
from newsreap.CliOptions import ranked

# initialize our logger
logger = logging.getLogger(NEWSREAP_CLI)

# How many cached headers are looked at a time (with --raw)
RAW_PAGE_SIZE = 1000

# What each kind of search can look at (see NNTPSearchTerm)
POST_FIELDS = (SEARCH_ANY, *SEARCH_FIELDS)
RAW_FIELDS = (SEARCH_ANY, 'subject', 'poster', 'message_id')

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
        process = subprocess.Popen(
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
    yet, or not let go of yet); yields the lines found (at most `limit`
    articles, across every group)
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
            for line in raw_group_query(
                    session, name, keywords, rank, terms, case_sensitive,
                    index_session):
                if not line.startswith(' '):
                    # The group's heading
                    yield line
                    continue

                if left is not None:
                    if left <= 0:
                        break
                    left -= 1

                yield line

        finally:
            session.close()
            db.close()


def raw_group_query(session, name, keywords, rank, terms, case_sensitive,
                    index_session):
    """
    Yields the lines of one group's cached headers that match: its heading
    (the group's name) before the first, then one line per article
    """
    query = session.query(Article)
    plain, wild = keyword_terms(keywords, case_sensitive)
    for exclude, column, value in plain:
        column = getattr(Article, column)
        found = func.instr(column, value) > 0 if case_sensitive \
            else column.icontains(value, autoescape=True)
        query = query.filter(~found if exclude else found)

    # Every condition narrows the results down further
    for term in [*wild, *terms]:
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
                yield heading
                heading = None

            yield '  [%.5d] %s%s' % (
                entry.article_no, entry.subject, rank_text(
                    SimpleNamespace(
                        score=score, tags=tags_to_text(tags))))


def post_lines(ctx, keywords, groups, limit, rank, terms, case_sensitive,
               oldest=False, notes=None, keys=()):
    """
    Searches the articles 'nr update --content' kept; yields the lines
    found (what to say if there is nothing to search is added to notes)
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
                                 "list')." % key)
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
            yield '%s  %-24.24s  %s%s' % (
                post.posted.strftime('%Y-%m-%d %H:%M') if post.posted
                else '????-??-?? ??:??', post.poster, post.subject,
                rank_text(SimpleNamespace(
                    score=score, tags=tags_to_text(tags))))
            yield '    <%s>' % post.message_id

            snippet = archiver.snippet(post, keywords, terms)
            if snippet:
                yield '    %s' % snippet

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
@click.option('--pager/--no-pager', default=True,
              help='Show the results a screen at a time on a terminal (the '
                   'default; PAGER picks the pager, less otherwise), or '
                   'write them all out.')
@click.option('--collections', is_flag=True,
              help='Search the collections (what is searched unless --posts '
                   'or --raw is given; add it to search them too).')
@click.option('--posts', is_flag=True,
              help="Search the articles 'nr update --content' kept (their "
                   "subjects, text and file names).")
@click.option('--raw', is_flag=True,
              help='Search the cached article headers of the groups given '
                   '(ie: what is not indexed yet).')
@click.option('--key', '-k', 'keys', multiple=True,
              help='Only posts signed by, encrypted for or carrying this PGP '
                   'key (a key ID, fingerprint or part of its name; implies '
                   '--posts; see nr pgp).')
@ranked
@matching
@click.pass_obj
def search(ctx, keywords, groups, ignore_case, complete, forming, limit,
           oldest, pager, collections, posts, raw, keys, rank, terms):
    """
    Search everything indexed (across every group).

    Each keyword must be in a collection's name or one of its file subjects
    (newest first, or oldest first with --oldest-first); with none,
    everything is listed.  On a terminal they are shown a screen at a time
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

    Collections have a subject (their name or a file subject), file names,
    posters and Message-IDs (of their articles); posts (--posts) have a
    body too; cached headers (--raw) only a subject, poster and
    Message-ID.  Give Message-IDs without their < and >.

    With --posts, the articles 'nr update --content' kept are searched by
    their words instead ('holid' finds 'holiday'); read one with
    'nr show <message-id>'.  --collections, --posts and --raw can be
    combined; each one found is shown under its own heading.

    """
    # What is searched (collections unless told otherwise)
    kinds = [kind for kind, wanted in (
        ('collections', collections), ('posts', posts or keys), ('raw', raw))
        if wanted] or ['collections']

    # What each kind of search can look at
    for kind in kinds:
        fields = {'collections': COLLECTION_FIELDS, 'posts': POST_FIELDS,
                  'raw': RAW_FIELDS}[kind]
        for term in terms:
            if term.field not in fields:
                logger.error("--%s can't be used to search %s%s." % (
                    term.option, 'cached headers' if kind == 'raw' else kind,
                    ' (only --posts)' if term.field == 'body' else ''))
                exit(1)

    if 'raw' in kinds and not groups:
        logger.error('Searching with --raw needs a --group.')
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
                indexer.summary(entry) for entry in indexer.find(
                    forming=forming, oldest=oldest, stream=True,
                    **options))))

    if 'posts' in kinds:
        sections.append(('Posts', post_lines(
            ctx, keywords, set(names) if names else None, limit, rank,
            terms, not ignore_case, oldest=oldest, notes=notes,
            keys=keys)))

    if 'raw' in kinds:
        sections.append(('Cached headers', raw_lines(
            ctx, names, keywords, rank, terms, not ignore_case,
            limit=limit)))

    def lines():
        for title, found in sections:
            if limit and title != 'Cached headers':
                # At most this many of each kind (reading stops there; the
                # cached headers count their own, leaving out headings)
                found = islice(found, limit)

            first = next(found, None)
            if first is None:
                continue

            if len(kinds) > 1:
                # Each kind under its own heading
                yield '%s:' % title

            yield first
            yield from found

    try:
        if not page(lines(), pager=pager):
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


def nothing_indexed(ctx):
    """
    Returns what to say when nothing is indexed yet
    """
    cache_dir = dirname(ctx['NNTPSettings'].group_database('_'))
    if isdir(cache_dir) and listdir(cache_dir):
        return "Nothing indexed yet; run 'nr update index' first."

    return "Nothing fetched yet; run 'nr update <group> --newest 100000' " \
        "first (see 'nr group list' for the groups)."


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
