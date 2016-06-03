# -*- coding: utf-8 -*-
#
# NewsReap NNTP Search CLI Plugin
#
# Copyright (C) 2015 Chris Caron <lead2gold@gmail.com>
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

import click
import re
from os.path import join
from os.path import isfile

from lib.objects.group.Article import Article
#from lib.objects.nntp.Group import Group
#from lib.objects.nntp.GroupAlias import GroupAlias
#from lib.objects.nntp.GroupTrack import GroupTrack
#from lib.objects.nntp.Server import Server
from lib.objects.nntp.Common import get_groups

#from lib.objects.search.Article import Article
#from sqlalchemy.exc import OperationalError
from sqlalchemy import not_

#from lib.NNTPConnection import NNTPConnection
#from lib.NNTPConnectionRequest import NNTPConnectionRequest
from lib.NNTPGroupDatabase import NNTPGroupDatabase

# Logging
import logging
from lib.Logging import NEWSREAP_CLI
logger = logging.getLogger(NEWSREAP_CLI)


NEWSREAP_CLI_PLUGINS = {
    # format:
    # cli short hand group: function prefix
    'search': 'search',
}

class SearchOperation(object):
    """
    Search Operation Type
    """
    INCLUDE = '+' # AND
    EXCLUDE = '-' # AND NOT

class SearchCategory(object):
    """
    Search Category
    """
    SUBJECT = 's' # SUBJECT
    POSTER = 'p' # POSTER


def parse_search_keyword(keywords):
    """
    A simple function that parses a keyword and returns it's search code
    and cleaned up string.

    Returns a list of tuples in the order of:
        [
            (Operation, Category, SearchKey),
            (Operation, Category, SearchKey),
            (Operation, Category, SearchKey),
            ...
        ]

    If there is a problem then (None, None, None) is returned
    """

    _parse = re.compile(r'(?P<cat>%[sp])?^(?P<op>\+|\-)?(?P<key>.+)$')

    response = []
    for keyword in keywords:
        result = _parse.search(keyword)

        if not result:
            continue

        if not result.group('key'):
            continue

        # Operation
        if result.group('op') != '-':
            _op = SearchOperation.INCLUDE
        else:
            _op = SearchOperation.EXCLUDE

        # Category
        if result.group('cat') == 'p':
            _cat = SearchCategory.POSTER
        else:
            _cat = SearchCategory.SUBJECT

        response.append((_op, _cat, result.group('key')))

    return response


# If we make the function name the same as the prefix identified above.
# Instead we make it an option/action of it's own.
@click.command(name='search')
@click.argument('group', nargs=1)
@click.argument('keywords', nargs=-1)
@click.option('--minscore', '-A', default=0, type=int)
@click.option('--maxscore', '-B', default=9999, type=int)
@click.option('--case-insensitive', '-i', is_flag=True)
@click.pass_obj
def search(ctx, group, keywords, minscore, maxscore, case_insensitive):
    """
    Searches cached groups for articles.

    Specified keywords stack on one another.  Each keyword specified must
    match somewhere in the subject line or else the result is filtered.

    Keywords can also be prefixed with special characters too to help
    identify what is being scanned.

        1. Example 1: A search that should ignore any text with 'Test' in it
                    but include text with 'Jack' in it. Unless you include
                    the case-insensitive switch (inspired from grep), the
                    search will be case sensitive:

                    -Test +Jack

        The + (plus) is always implied. It's primary use it to eliminate
        abiguity (and allow for the minus to exist).  It is also nessisary if
        you intend to search for something with a plus in it, hence the
        following would search for the string '+++AWESOME+++':

                    +++++AWESOME+++

        The extra plus symbol is stripped off and the search works as intended.

        2.  Example 2: Search by Poster.  Since all keywords imply that you're
                 searching for a subject keyword, the next token that changes
                 this is '%p' where as the subject is always implied
                 identified as '%s'.  Hence the following would look for me:

                    %pChris %pl2g

            This can also be written like this:

                    %p+Chris %p+l2g

            You should not be confused here, the tokens at the front will be
            stripped off and the search will run as normal. These tokens are
            very important because it allows you to mix and match search with
            both the subject and poster:

                    %p+Chris %p+l2g AWESOME

            The above implies that AWESOME will have a +%s infront of it.
            Make sense?

        The final thing worth noting is doing a search for text that contains
        dash/minus (-) signs.  Click (the awesome cli wrapper this script
        uses can pick the - up as an actual switch thinking you're trying to
        pass it into this function. So you can easily disable this with by adding
        a double dash/minus sign (--) like so:

            nr search -- -keyword +keyword2

    """

    session = ctx['NNTPSettings'].session()
    if not session:
        logger.error("The database is not correctly configured.")
        exit(1)

    if not group:
        logger.error("You must specify a group/alias.")
        exit(1)

    # Simplify Alias
    groups = get_groups(session, group)
    if not groups:
        logger.error("You must specify a group/alias.")
        exit(1)

    for name, _id in groups.iteritems():
        db_path = join(ctx['NNTPSettings'].cfg_path, 'cache', 'search')
        db_file = '%s.db' % join(db_path, name)
        if not isfile(db_file):
            logger.warning(
                "There is no cached content for '%s'." % db_file
            )
            continue

        engine = 'sqlite:///%s' % db_file
        db = NNTPGroupDatabase(engine=engine, reset=None)
        group_session = db.session()

        gt = group_session.query(Article)

        # Parse our keywords
        parsed_keywords = parse_search_keyword(keywords)
        for _op, _cat, keyword in parsed_keywords:

            if _cat == SearchCategory.SUBJECT:
                if _op == SearchOperation.INCLUDE:
                    if case_insensitive:
                        logger.debug('Scanning -and- (case-insensitive) subject: "%s"' % (keyword))
                        gt = gt.filter(Article.subject.ilike('%%%s%%' % keyword))
                    else:
                        logger.debug('Scanning -and- (case-sensitive) subject: "%s"' % (keyword))
                        gt = gt.filter(Article.subject.like('%%%s%%' % keyword))

                else: # _op == SearchCategory.EXCLUDE
                    if case_insensitive:
                        logger.debug('Scanning -not- (case-insensitive) subject: "%s"' % (keyword))
                        gt = gt.filter(not_(Article.subject.ilike('%%%s%%' % keyword)))
                    else:
                        logger.debug('Scanning -and not- (case-sensitive) subject: "%s"' % (keyword))
                        gt = gt.filter(not_(Article.subject.like('%%%s%%' % keyword)))

            elif _cat == SearchCategory.POSTER:
                if _op == SearchOperation.INCLUDE:
                    if case_insensitive:
                        logger.debug('Scanning -and- (case-insensitive) poster: "%s"' % (keyword))
                        gt = gt.filter(Article.poster.ilike('%%%s%%' % keyword))
                    else:
                        logger.debug('Scanning -and- (case-sensitive) poster: "%s"' % (keyword))
                        gt = gt.filter(Article.poster.like('%%%s%%' % keyword))

                else: # _op == SearchCategory.EXCLUDE
                    if case_insensitive:
                        logger.debug('Scanning -and not- (case-insensitive) poster: "%s"' % (keyword))
                        gt = gt.filter(not_(Article.poster.ilike('%%%s%%' % keyword)))
                    else:
                        logger.debug('Scanning -and not- (case-sensitive) poster: "%s"' % (keyword))
                        gt = gt.filter(not_(Article.poster.like('%%%s%%' % keyword)))

        # Handle Scores
        if maxscore == minscore:
            logger.debug('Scanning -score == %d-' % (maxscore))
            gt = gt.filter(Article.score == maxscore)
        else:
            logger.debug('Scanning -score >= %d and score <= %d-' % (minscore, maxscore))
            gt = gt.filter(Article.score <= maxscore)\
                    .filter(Article.score >= minscore)

        gt = gt.order_by(Article.score.desc())

        # Iterate through our list
        print("%s:" % (name))
        for entry in gt:
            print("  [%.5d] %.4d %s" % (entry.article_no, entry.score, entry.subject))

        group_session.close()
        db.close()

    return
