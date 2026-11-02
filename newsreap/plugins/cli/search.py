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
from newsreap.objects.nntp.Common import get_groups

from sqlalchemy import and_
from sqlalchemy import func

from newsreap.NNTPArchiver import NNTPArchiver
from newsreap.NNTPGroupDatabase import NNTPGroupDatabase
from newsreap.NNTPIndexer import NNTPIndexer
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

# What each kind of search can look at (see NNTPSearchTerm)
POST_FIELDS = (SEARCH_ANY, *SEARCH_FIELDS)
RAW_FIELDS = (SEARCH_ANY, 'subject', 'poster', 'message_id')

NEWSREAP_CLI_PLUGINS = {
    # format:
    # cli short hand group: function prefix
    'search': 'search',
}


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


def search_raw(ctx, groups, keywords, rank, terms, case_sensitive):
    """
    Searches the article headers 'nr update search' cached (the ones not
    indexed yet, or not let go of yet)
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

        query = rank_query(
            query, Article.score, Article.tags,
            (Article.posted_date, Article.posted_date), **rank)\
            .order_by(Article.score.desc(), Article.article_no)

        click.echo('%s:' % name)
        for entry in query:
            click.echo('  [%.5d] %s%s' % (
                entry.article_no, entry.subject, rank_text(entry)))

        session.close()
        db.close()


def search_posts(ctx, keywords, groups, limit, rank, terms,
                 case_sensitive):
    """
    Searches the articles 'nr update archive' kept
    """
    archiver = NNTPArchiver(
        ctx['NNTPSettings'].index_database(), default_hooks=False)
    try:
        if archiver.session() is None:
            click.echo(
                "Nothing archived yet; run 'nr update archive' first.")
            return

        for post in archiver.find(
                keywords, groups=groups, limit=limit,
                case_sensitive=case_sensitive, terms=terms, **rank):
            click.echo('%s  %-24.24s  %s%s' % (
                post.posted.strftime('%Y-%m-%d %H:%M') if post.posted
                else '????-??-?? ??:??', post.poster, post.subject,
                rank_text(post)))
            click.echo('    <%s>' % post.message_id)

            snippet = archiver.snippet(post, keywords, terms)
            if snippet:
                click.echo('    %s' % snippet)

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
@click.option('--posts', is_flag=True,
              help="Search the articles 'nr update archive' kept (their "
                   "subjects, text and file names) instead.")
@click.option('--raw', is_flag=True,
              help='Search the cached article headers of the groups given '
                   'instead (ie: what is not indexed yet).')
@ranked
@matching
@click.pass_obj
def search(ctx, keywords, groups, ignore_case, complete, forming, limit,
           posts, raw, rank, terms):
    """
    Search everything indexed (across every group).

    Each keyword must be in a collection's name or one of its file subjects
    (newest first).  Case and accents don't matter unless --case-sensitive
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

    With --posts, the articles 'nr update archive' kept are searched by
    their words instead ('holid' finds 'holiday'); read one with
    'nr archive show <message-id>'.

    """
    if posts and raw:
        logger.error('Only one of --posts and --raw can be used.')
        exit(1)

    # What each kind of search can look at
    fields = POST_FIELDS if posts else RAW_FIELDS if raw \
        else COLLECTION_FIELDS
    for term in terms:
        if term.field not in fields:
            logger.error('--%s only works with --posts.' % term.option)
            exit(1)

    if posts:
        search_posts(
            ctx, keywords, set(lookup_groups(ctx, groups)) if groups
            else None, limit, rank, terms, not ignore_case)
        return

    if raw:
        if not groups:
            logger.error('Searching with --raw needs a --group.')
            exit(1)

        search_raw(
            ctx, lookup_groups(ctx, groups), keywords, rank, terms,
            not ignore_case)
        return

    # The groups we're narrowing it down to (if any)
    names = set(lookup_groups(ctx, groups)) if groups else None

    indexer = NNTPIndexer(ctx['NNTPSettings'].index_database())
    try:
        if indexer.session() is None:
            click.echo(
                "Nothing indexed yet; run 'nr update index' first.")
            return

        for entry in indexer.find(
                keywords, groups=names, complete=complete, forming=forming,
                limit=limit, case_sensitive=not ignore_case, terms=terms,
                **rank):
            click.echo(indexer.summary(entry))

    finally:
        indexer.close()
