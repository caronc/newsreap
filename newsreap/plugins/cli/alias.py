# -*- coding: utf-8 -*-
#
# NewsReap NNTP Group Alias CLI Plugin
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

# Aliases allow you to map alt.binaries.test to something like a.b.test
# You can add as many aliases as you want to multiple or even the same group
# as you like as long as they don't conflict with one another

import click
import sys

from sqlalchemy.exc import InvalidRequestError

from os.path import abspath
from os.path import dirname

try:
    from newsreap.objects.nntp.Group import Group

except ImportError:
    # Path
    sys.path.insert(0, dirname(dirname(dirname(abspath(__file__)))))
    from newsreap.objects.nntp.Group import Group

from newsreap.objects.nntp.GroupAlias import GroupAlias
from newsreap.objects.nntp.Common import get_groups

# Logging
import logging
from newsreap.Logging import NEWSREAP_CLI
logger = logging.getLogger(NEWSREAP_CLI)

NEWSREAP_CLI_PLUGINS = {
    # format:
    # cli short hand group: function prefix
    'alias': {
        'prefix': 'alias',
        'desc': """
        Group Alias Management

        Aliases make it possible to forget about the silly NNTP Group
        naming convention (such as alt.binaries.test) and instead associate
        these names with something easier to remember and manage.

        You can map as many short-formed names as you want to as many
        groups as you want.

        If you map the same name to multiple groups then they will
        both be treated as 1 when you perform actions with them.

        """,
    },
}


# Define our functions below
# all functions are prefixed with what is identified
# above or they are simply ignored.
@click.command(name='add')
@click.pass_obj
@click.argument('alias', nargs=1)
@click.argument('groups', nargs=-1)
def alias_add(ctx, alias, groups):
    """
    Associate an alias with one or more group names.

    This makes further reference to the group that much easier since you don't
    have to type the entire thing out again.

    For example: one might create an alias of alt.binaries.test to a.b.test

    There are no retrictions as to what the alias has to be, so if you wanted
    to you could just associate them with 1 word (like the above a.b.test
    could have also just been associated with the word 'test' or even 't'.

    """
    session = ctx['NNTPSettings'].session()
    if not session:
        logger.error("The database is not correctly configured.")
        exit(1)

    if not alias:
        logger.error("You must specify an alias.")
        exit(1)

    if len(groups) == 0:
        logger.error("You must specify at least one group after the alias.")
        exit(1)

    # Simplify Alias
    _alias = alias.lower().strip()
    if not _alias:
        logger.error("An invalid alias identifier was specified.")
        exit(1)

    groups = get_groups(session, groups)
    if not groups:
        logger.error(
            "There were no alias/groups found matching your criteria.",
        )
        exit(1)

    # Track our database updates
    pending_commits = 0

    for name, _id in groups.iteritems():
        if session.merge(GroupAlias(group_id=_id, name=_alias)):
            logger.debug(
                "Adding alias '%s' to group '%s'." % (_alias, name),
            )
            pending_commits += 1

    if pending_commits > 0:
        # commit our results
        session.commit()


@click.command(name='del')
@click.pass_obj
@click.argument('alias', nargs=1)
@click.argument('groups', nargs=-1)
def alias_del(ctx, alias, groups):
    """
    Removes specified alias from its associated group.

    If no group is specified, then it is presumed that you want to remove
    the alias in it's entirety (from all group association).

    """

    if not alias:
        logger.error("You must specify an alias.")
        exit(1)

    session = ctx['NNTPSettings'].session()
    if not session:
        logger.error("The database is not correctly configured.")
        exit(1)

    # Simplify Alias
    _alias = alias.lower().strip()
    if not _alias:
        logger.error("An invalid alias identifier was specified.")
        exit(1)

    if len(groups) == 0:
        # No groups were specified; assume the user wants to eliminate
        # the alias and it's association with everything
        try:
            session.query(GroupAlias)\
                    .filter(GroupAlias.name == _alias)\
                    .delete()

        except InvalidRequestError:
            # Dataase isn't set up
            logger.error("The database is not correctly configured.")
            exit(1)

        # Commit our changes
        session.commit()

        # We're done
        return

    # Get the groups impacted
    a_groups = dict(session.query(Group.name, Group.id).join(GroupAlias)
                    .filter(GroupAlias.group_id == Group.id)
                    .filter(GroupAlias.name == _alias).all())

    # Track list of groups to remove
    remove_groups = list()

    if not a_groups:
        # There is nothing to do
        return

    # If we reach here, we have to eliminate the alias from a specific set of
    # groups
    early_exit = False
    for g in groups:

        if early_exit:
            break

        _group = g.lower().strip()
        if not _group:
            continue

        try:
            _groups = session.query(Group.name)\
                .filter(Group.name == _group).all()
            if not _groups:
                # No problem; let us treat it as an alias and try again
                _groups = session.query(Group.name).join(GroupAlias)\
                        .filter(GroupAlias.name == _group).all()
                if not _groups:
                    logger.warning("The group/alias '%s' was not found." % g)
                    continue

        except InvalidRequestError:
            # Dataase isn't set up
            logger.error("The database is not correctly configured.")
            exit(1)

        for group in _groups:
            if group[0] in a_groups and \
               a_groups[group[0]] in remove_groups:
                continue

            # Add our group to our removal list
            remove_groups.append(a_groups[group[0]])

            if remove_groups == a_groups.values():
                # We can make an early exit if this ever equates to True
                early_exit = True
                break

    if remove_groups:
        # Remove the entries
        session.query(GroupAlias)\
                .filter(GroupAlias.group_id.in_(remove_groups))\
                .delete(synchronize_session=False)
        session.commit()

    return


@click.command(name='list')
@click.pass_obj
def alias_list(ctx):
    """
    Lists alias and their associated groups that have been mapped to.

    """
    session = ctx['NNTPSettings'].session()
    if not session:
        logger.error("The database is not correctly configured.")
        exit(1)

    last = None
    for entry in session.query(Group.name, GroupAlias.name)\
            .join(GroupAlias)\
            .filter(Group.id == GroupAlias.group_id)\
            .order_by(GroupAlias.name)\
            .order_by(Group.name).all():

        if last != entry[1]:
            if last is not None:
                # New line
                print

            # Store new group
            last = entry[1]
            print '%s:' % last

        print '  - %s' % entry[0]
