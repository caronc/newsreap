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
import sys

from os import listdir
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


def page(lines):
    """
    Shows lines of results a screen at a time (as 'less' does) when they
    go to a terminal, or all of them when they don't (ie: into a file);
    returns False if there were none
    """
    lines = iter(lines)
    first = next(lines, None)
    if first is None:
        return False

    click.echo_via_pager(
        '%s\n' % line for line in chain([first], lines))
    return True


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


def raw_lines(ctx, groups, keywords, rank, terms, case_sensitive):
    """
    Searches the article headers 'nr update' cached (the ones not indexed
    yet, or not let go of yet); yields the lines found
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
            index_session)

    finally:
        if index is not None:
            index.close()


def raw_group_lines(ctx, groups, keywords, rank, terms, case_sensitive,
                    index_session):
    """
    Yields the cached headers of each group that match (see raw_lines())
    """
    for name in groups:
        db_file = ctx['NNTPSettings'].group_database(name)
        if not isfile(db_file):
            logger.warning("There is no cached content for '%s'." % name)
            continue

        db = NNTPGroupDatabase.from_file(db_file)
        session = db.session()

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

        session.close()
        db.close()


def post_lines(ctx, keywords, groups, limit, rank, terms, case_sensitive,
               oldest=False, notes=None):
    """
    Searches the articles 'nr update --content' kept; yields the lines
    found (what to say if there is nothing to search is added to notes)
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

        found = archiver.find(
            keywords, groups=groups, limit=limit,
            case_sensitive=case_sensitive, terms=terms, oldest=oldest,
            **rank)

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
@click.option('--collections', is_flag=True,
              help='Search the collections (what is searched unless --posts '
                   'or --raw is given; add it to search them too).')
@click.option('--posts', is_flag=True,
              help="Search the articles 'nr update --content' kept (their "
                   "subjects, text and file names).")
@click.option('--raw', is_flag=True,
              help='Search the cached article headers of the groups given '
                   '(ie: what is not indexed yet).')
@ranked
@matching
@click.pass_obj
def search(ctx, keywords, groups, ignore_case, complete, forming, limit,
           oldest, collections, posts, raw, rank, terms):
    """
    Search everything indexed (across every group).

    Each keyword must be in a collection's name or one of its file subjects
    (newest first, or oldest first with --oldest-first); with none,
    everything is listed.  On a terminal they are shown a screen at a time
    (as 'less' does); piped or saved to a file, all of them are written
    out.  Collections whose articles are still arriving are only shown
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
        ('collections', collections), ('posts', posts), ('raw', raw))
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
            terms, not ignore_case, oldest=oldest, notes=notes)))

    if 'raw' in kinds:
        sections.append(('Cached headers', raw_lines(
            ctx, names, keywords, rank, terms, not ignore_case)))

    def lines():
        for title, found in sections:
            first = next(found, None)
            if first is None:
                continue

            if len(kinds) > 1:
                # Each kind under its own heading
                yield '%s:' % title

            yield first
            yield from found

    try:
        if not page(lines()):
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
