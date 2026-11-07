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

from os.path import abspath
from os.path import dirname

try:
    from newsreap.NNTPDatabase import UnknownGroups

except ImportError:
    # Path
    sys.path.insert(0, dirname(dirname(dirname(dirname(abspath(__file__))))))
    from newsreap.NNTPDatabase import UnknownGroups

from newsreap.Database import DatabaseUnavailable
from newsreap.NNTPDatabase import UnknownAlias
# (not alias_...: every name starting with alias_ becomes a command)
from newsreap.NNTPDatabase import alias_name as clean_alias

# Logging
import logging
from newsreap.Logging import NEWSREAP_CLI
logger = logging.getLogger(NEWSREAP_CLI)

# What is said when the database can't be used
NO_DATABASE = "The database isn't set up yet; run 'nr db init' first."

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
    try:
        _alias = clean_alias(alias)

    except ValueError:
        logger.error("An invalid alias identifier was specified.")
        exit(1)

    if len(groups) == 0:
        logger.error("You must specify at least one group after the alias.")
        exit(1)

    # 'nr update <alias>' would run that step of update instead
    root = click.get_current_context().find_root()
    update = root.command.get_command(root, 'update')
    if update is not None and _alias in update.list_commands(root):
        logger.error("'%s' can't be an alias; 'nr update %s' is a command of "
                     "its own." % (_alias, _alias))
        exit(1)

    try:
        added = ctx['NNTPSettings'].alias_add(_alias, groups)

    except DatabaseUnavailable:
        logger.error(NO_DATABASE)
        exit(1)

    except UnknownGroups as e:
        # A name that matches nothing is most likely a typo
        logger.error('%s Nothing was added.' % e)
        exit(1)

    except ValueError as e:
        logger.error(str(e))
        exit(1)

    for name in added:
        logger.debug("Added alias '%s' to group '%s'." % (_alias, name))


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
    try:
        result = ctx['NNTPSettings'].alias_remove(alias, groups)

    except DatabaseUnavailable:
        logger.error(NO_DATABASE)
        exit(1)

    except UnknownAlias as e:
        logger.error(str(e))
        exit(1)

    except ValueError:
        logger.error("An invalid alias identifier was specified.")
        exit(1)

    # What was named is removed; anything that couldn't be is said here
    for name in result.unknown:
        logger.error("There is no group (or alias) named '%s'." % name)

    for name in result.outside:
        logger.error("'%s' isn't part of the alias '%s'." % (
            name, clean_alias(alias)))

    if result.unknown or result.outside:
        exit(1)


@click.command(name='ls')
@click.pass_obj
def alias_list(ctx):
    """
    Lists alias and their associated groups that have been mapped to.

    """
    try:
        aliases = ctx['NNTPSettings'].aliases()

    except DatabaseUnavailable:
        logger.error(NO_DATABASE)
        exit(1)

    for index, (alias, groups) in enumerate(aliases.items()):
        if index:
            # A blank line between aliases
            click.echo()

        click.echo('%s:' % alias)
        for group in groups:
            click.echo('  - %s' % group)
