# -*- coding: utf-8 -*-
#
# NewsReap NNTP Group CLI Plugin
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

import click
import sys

from sqlalchemy.exc import OperationalError

from os.path import abspath
from os.path import dirname

try:
    from newsreap.objects.nntp.Group import Group
except ImportError:
    # Path
    sys.path.insert(0, dirname(dirname(dirname(dirname(abspath(__file__))))))

from newsreap.objects.nntp.Common import get_groups

# Logging
import logging
from newsreap.Logging import NEWSREAP_CLI
logger = logging.getLogger(NEWSREAP_CLI)

NEWSREAP_CLI_PLUGINS = {
    # format:
    # cli short hand group: function prefix
    'group': {
        'prefix': 'group',
        'desc': 'Group management',
    },
}


# Define our functions below
# all functions are prefixed with what is identified
# above or they are simply ignored.
@click.command(name='watch')
@click.argument('groups', nargs=-1)
@click.pass_obj
def group_watch(ctx, groups):
    """
    Adds a group to a watch list.

    """
    session = ctx['NNTPSettings'].session()
    if not session:
        logger.error("The database is not correctly configured.")
        exit(1)

    # Track our database updates
    pending_commits = 0

    groups = get_groups(session, groups)
    if not groups:
        logger.error(
            "There were no alias/groups found matching your criteria.",
        )
        exit(1)

    # PEP8 E712 does not allow us to make a comparison to a boolean value
    # using the == instead of the keyword 'in'.  However SQLAlchemy
    # requires us to do just because that's how the amazing tool works.
    # so to get around the pep8 error, we'll just define a variable equal
    # to True and then we can compare to it
    pep8_e712 = False

    for name, _id in groups.items():
        # Remove the entry if we can otherwise we just gracefully move on
        if session.query(Group).filter(Group.id == _id)\
                .filter(Group.watch == pep8_e712)\
                .update({Group.watch: True}):
            logger.info("Added the group '%s' to the watchlist." % name)
            pending_commits += 1

    if pending_commits > 0:
        # commit our results
        session.commit()

    return


@click.command(name='unwatch')
@click.argument('groups', nargs=-1)
@click.pass_obj
def group_unwatch(ctx, groups):
    """
    Remove specified group(s) from a watchlist.
    """
    session = ctx['NNTPSettings'].session()
    if not session:
        logger.error("The database is not correctly configured.")
        exit(1)

    # Track our database updates
    pending_commits = 0

    groups = get_groups(session, groups)
    if not groups:
        logger.error(
            "There were no alias/groups found matching your criteria.",
        )
        exit(1)

    # PEP8 E712 does not allow us to make a comparison to a boolean value
    # using the == instead of the keyword 'in'.  However SQLAlchemy
    # requires us to do just because that's how the amazing tool works.
    # so to get around the pep8 error, we'll just define a variable equal
    # to True and then we can compare to it
    pep8_e712 = True

    for name, _id in groups.items():
        # Remove the entry if we can otherwise we just gracefully move on
        if session.query(Group).filter(Group.id == _id)\
                .filter(Group.watch == pep8_e712)\
                .update({Group.watch: False}):
            logger.info(
                "Removed the group '%s' from the watchlist." % name,
            )
            pending_commits += 1

    if pending_commits > 0:
        # commit our results
        session.commit()

    return


@click.command(name='list')
@click.option('--all', '-a', is_flag=True, help='Include all.')
@click.pass_obj
def group_list(ctx, all):
    """
    List all groups.

    If more then one name is specified, then content is filtered based on what
    is typed.

    If content is already cached in a database; that is used. Otherwise the
    first available news server is polled for the information.
    """
    # PEP8 E712 does not allow us to make a comparison to a boolean value
    # using the == instead of the keyword 'in'.  However SQLAlchemy
    # requires us to do just because that's how the amazing tool works.
    # so to get around the pep8 error, we'll just define a variable equal
    # to True and then we can compare to it
    pep8_e712 = True

    results = None

    # Use our Database first if it exists
    session = ctx['NNTPSettings'].session()
    if session:

        # Used cached copy if we can
        try:
            # Check our database for groups to display
            if all:
                results = session.query(Group)\
                    .order_by(Group.name.asc())\
                    .all()
            else:
                results = session.query(Group)\
                    .filter(Group.watch == pep8_e712)\
                    .order_by(Group.name.asc())\
                    .all()

        except OperationalError:
            # database isn't initialized; no problem; just continue
            pass

        if results is not None:
            # Display result fetched from the database
            for r in results:
                print('%-65s %10s %s' % (
                    r.name,
                    r.count,
                    r.flags,
                ))
            return

    if all:
        # Our fallback is to just query our server if we don't have anything in
        # the databasse.
        if not results:
            results = ctx['NNTPManager'].groups()

        if results:
            results = sorted(results, key=lambda k: k['group'])
            for r in results:
                print('%-65s %10s %s' % (
                    r['group'],
                    r['count'],
                    r['flags'],
                ))
    return
