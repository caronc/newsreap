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
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import ProgrammingError

from os.path import abspath
from os.path import dirname

try:
    from newsreap.objects.nntp.Group import Group

except ImportError:
    # Path
    sys.path.insert(0, dirname(dirname(dirname(dirname(abspath(__file__))))))
    from newsreap.objects.nntp.Group import Group

from newsreap.objects.nntp.GroupAlias import GroupAlias
from newsreap.objects.nntp.Common import get_groups
from newsreap.objects.nntp.Common import missing_groups

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

    # 'nr update <alias>' would run that step of update instead
    root = click.get_current_context().find_root()
    update = root.command.get_command(root, 'update')
    if update is not None and _alias in update.list_commands(root):
        logger.error("'%s' can't be an alias; 'nr update %s' is a command of "
                     "its own." % (_alias, _alias))
        exit(1)

    # A name that matches nothing is most likely a typo; nothing is added
    missing = missing_groups(session, groups)
    if missing:
        logger.error('No group or alias is called %s; nothing was added.' %
                     ', '.join("'%s'" % name for name in missing))
        exit(1)

    groups = get_groups(session, groups)
    if not groups:
        logger.error(
            "There were no alias/groups found matching your criteria.",
        )
        exit(1)

    # Track our database updates
    pending_commits = 0

    for name, _id in groups.items():
        if session.merge(GroupAlias(group_id=_id, name=_alias)):
            logger.debug(
                "Adding alias '%s' to group '%s'." % (_alias, name),
            )
            pending_commits += 1

    if pending_commits > 0:
        # commit our results
        session.commit()


@click.command(name='rm')
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

    try:
        # The groups the alias stands for ({name: id})
        a_groups = dict(session.query(Group.name, Group.id).join(GroupAlias)
                        .filter(GroupAlias.group_id == Group.id)
                        .filter(GroupAlias.name == _alias).all())

    except (InvalidRequestError, OperationalError, ProgrammingError):
        session.rollback()
        logger.error("The database isn't set up yet; run 'nr db init' first.")
        exit(1)

    if not a_groups:
        logger.error("There is no alias '%s'." % _alias)
        exit(1)

    if len(groups) == 0:
        # No groups were specified; the alias goes in its entirety
        session.query(GroupAlias)\
            .filter(GroupAlias.name == _alias)\
            .delete(synchronize_session=False)
        session.commit()
        return

    # The groups (or the groups of the aliases) named, that this alias has
    remove_groups = set()
    failed = False
    for name in groups:
        found = get_groups(session, name) or {}
        if not found:
            failed = True
            continue

        for group in found:
            if group in a_groups:
                remove_groups.add(a_groups[group])

            else:
                logger.error("'%s' isn't part of the alias '%s'." % (
                    group, _alias))
                failed = True

    if remove_groups:
        # Only this alias lets go of them; any other alias keeps them
        session.query(GroupAlias)\
            .filter(GroupAlias.name == _alias)\
            .filter(GroupAlias.group_id.in_(remove_groups))\
            .delete(synchronize_session=False)
        session.commit()

    if failed:
        exit(1)


@click.command(name='ls')
@click.pass_obj
def alias_list(ctx):
    """
    Lists alias and their associated groups that have been mapped to.

    """
    session = ctx['NNTPSettings'].session()
    if not session:
        logger.error("The database is not correctly configured.")
        exit(1)

    try:
        entries = session.query(Group.name, GroupAlias.name)\
            .join(GroupAlias)\
            .filter(Group.id == GroupAlias.group_id)\
            .order_by(GroupAlias.name)\
            .order_by(Group.name).all()

    except (InvalidRequestError, OperationalError, ProgrammingError):
        session.rollback()
        logger.error("The database isn't set up yet; run 'nr db init' first.")
        exit(1)

    last = None
    for entry in entries:

        if last != entry[1]:
            if last is not None:
                # New line
                print()

            # Store new group
            last = entry[1]
            print('%s:' % last)

        print('  - %s' % entry[0])
