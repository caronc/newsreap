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

from sqlalchemy import func

from newsreap.NNTPArchiver import NNTPArchiver
from newsreap.NNTPGroupDatabase import NNTPGroupDatabase
from newsreap.NNTPIndexer import NNTPIndexer
from newsreap.NNTPIndexer import parse_keywords

# initialize our logger
logger = logging.getLogger(NEWSREAP_CLI)

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


def search_raw(ctx, groups, keywords, minscore, maxscore, case_sensitive):
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
        for exclude, column, value in parse_keywords(keywords):
            column = getattr(Article, column)
            found = func.instr(column, value) > 0 if case_sensitive \
                else column.icontains(value, autoescape=True)
            query = query.filter(~found if exclude else found)

        query = query.filter(Article.score <= maxscore)\
            .filter(Article.score >= minscore)\
            .order_by(Article.score.desc(), Article.article_no)

        click.echo('%s:' % name)
        for entry in query:
            click.echo('  [%.5d] %.4d %s' % (
                entry.article_no, entry.score, entry.subject))

        session.close()
        db.close()


def search_posts(ctx, keywords, groups, limit, case_sensitive):
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
                case_sensitive=case_sensitive):
            click.echo('%s  %-24.24s  %s' % (
                post.posted.strftime('%Y-%m-%d %H:%M') if post.posted
                else '????-??-?? ??:??', post.poster, post.subject))
            click.echo('    <%s>' % post.message_id)

            snippet = archiver.snippet(post, keywords)
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
@click.option('--minscore', '-A', default=0, type=int,
              help='The lowest article score shown (with --raw).')
@click.option('--maxscore', '-B', default=9999, type=int,
              help='The highest article score shown (with --raw).')
@click.pass_obj
def search(ctx, keywords, groups, ignore_case, complete, forming, limit,
           posts, raw, minscore, maxscore):
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

    Put -- before the keywords if one of them starts with a dash.

    With --posts, the articles 'nr update archive' kept are searched by
    their words instead ('holid' finds 'holiday'); read one with
    'nr archive show <message-id>'.

    """
    if posts and raw:
        logger.error('Only one of --posts and --raw can be used.')
        exit(1)

    if posts:
        search_posts(
            ctx, keywords, set(lookup_groups(ctx, groups)) if groups
            else None, limit, not ignore_case)
        return

    if raw:
        if not groups:
            logger.error('Searching with --raw needs a --group.')
            exit(1)

        search_raw(
            ctx, lookup_groups(ctx, groups), keywords, minscore, maxscore,
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
                limit=limit, case_sensitive=not ignore_case):
            click.echo(indexer.summary(entry))

    finally:
        indexer.close()
