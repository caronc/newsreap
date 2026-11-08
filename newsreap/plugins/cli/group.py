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

from os.path import abspath
from os.path import dirname

try:
    from newsreap.NNTPDatabase import UnknownGroups

except ImportError:
    # Path
    sys.path.insert(0, dirname(dirname(dirname(dirname(abspath(__file__))))))
    from newsreap.NNTPDatabase import UnknownGroups

from newsreap.Database import DatabaseUnavailable
from newsreap.Utils import server_key
from newsreap.NNTPDatabase import UnknownServer
from newsreap.CliOptions import confirmed
from newsreap.NNTPPruner import NNTPPruner
from newsreap.Utils import bytes_to_strsize

# Logging
import logging
from newsreap.Logging import NEWSREAP_CLI
logger = logging.getLogger(NEWSREAP_CLI)

# What is said when the database can't be used
NO_DATABASE = "The database isn't set up yet; run 'nr db init' first."

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
def describe_options(options):
    """
    Returns what a group's own settings say (ie: 'content, no nzb, keeps
    30 days'); empty when it follows the configuration file
    """
    said = [name if options[name] else 'no %s' % name
            for name in ('content', 'nzb')
            if options and options.get(name) is not None]

    days = (options or {}).get('retention')
    if days is not None:
        said.append('keeps %d days' % days if days else 'keeps everything')

    return ', '.join(said)


def choice(value):
    """
    Turns yes, no or default into True, False or None
    """
    return {'yes': True, 'no': False, 'default': None}[value]


def days(value):
    """
    Turns a number of days (or default) into an int (or None); raises
    ValueError for anything else
    """
    if value.strip().lower() == 'default':
        return None

    try:
        found = int(value)

    except ValueError:
        found = -1

    if found < 0:
        raise ValueError(
            "--retention takes a number of days (0 keeps everything) or "
            "'default'.")

    return found


@click.command(name='watch')
@click.argument('groups', nargs=-1)
@click.pass_obj
def group_watch(ctx, groups):
    """
    Adds groups (or aliases) to the watch list.

    'nr update' fetches what is new in every watched group.  What
    else 'nr update' does for a group is set with 'nr group set'.
    """
    try:
        added = ctx['NNTPSettings'].watch(groups)

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
        logger.info("Added the group '%s' to the watchlist." % name)


@click.command(name='set')
@click.argument('groups', nargs=-1)
@click.option('--content', type=click.Choice(['yes', 'no', 'default']),
              help="Keep what its small articles say when 'nr update' "
                   "runs (as --content does).")
@click.option('--nzb', type=click.Choice(['yes', 'no', 'default']),
              help="Read the NZB files posted to it when 'nr update' runs "
                   "(as --nzb does).")
@click.option('--retention', metavar='DAYS|default',
              help="How many days 'nr prune' keeps of it (0 keeps "
                   "everything).")
@click.pass_obj
def group_set(ctx, groups, content, nzb, retention):
    """
    Show or change the settings of groups (or aliases).

    Each group follows the settings under processing: in the
    configuration file until it is given its own; 'default' goes back to
    them.  Watched or not, a group keeps its settings.  The options of
    'nr update' (ie: --no-content) still win for one run.

    \b
        nr group set alt.discuss.coffee --content yes
        nr group set a.b.test --nzb yes --retention 30
        nr group set a.b.test --retention default
        nr group set a.b.test
    """
    options = {}
    if content is not None:
        options['content'] = choice(content)

    if nzb is not None:
        options['nzb'] = choice(nzb)

    try:
        if retention is not None:
            options['retention'] = days(retention)

        found = ctx['NNTPSettings'].configure(groups, **options)

    except DatabaseUnavailable:
        logger.error(NO_DATABASE)
        exit(1)

    except UnknownGroups as e:
        logger.error('%s Nothing was changed.' % e)
        exit(1)

    except ValueError as e:
        logger.error(str(e))
        exit(1)

    for name, settings in found.items():
        click.echo('%s: %s' % (
            name, describe_options(settings) or
            'as the configuration file says'))


@click.command(name='unwatch')
@click.argument('groups', nargs=-1)
@click.pass_obj
def group_unwatch(ctx, groups):
    """
    Remove specified group(s) from a watchlist.
    """
    try:
        result = ctx['NNTPSettings'].unwatch(groups)

    except DatabaseUnavailable:
        logger.error(NO_DATABASE)
        exit(1)

    except ValueError as e:
        logger.error(str(e))
        exit(1)

    for name in result.removed:
        logger.info("Removed the group '%s' from the watchlist." % name)

    # The others are still removed; a name that matches nothing is said
    for name in result.unknown:
        logger.error("There is no group (or alias) named '%s'." % name)

    if result.unknown:
        exit(1)


@click.command(name='purge')
@click.argument('groups', nargs=-1)
@click.pass_obj
def group_purge(ctx, groups):
    """
    Free the space of groups (or aliases).

    Everything kept of them goes: their cached headers, how far they were
    fetched, and the files, collections and posts indexed from them (what
    other groups have too stays).  The groups themselves, their settings
    and whether they are watched stay; a watched one starts over the next
    time 'nr update' runs.  You are asked first (unless -y is given).
    """
    settings = ctx['NNTPSettings']
    try:
        found = settings.groups(groups)

    except DatabaseUnavailable:
        logger.error(NO_DATABASE)
        exit(1)

    except UnknownGroups as e:
        logger.error('%s Nothing was removed.' % e)
        exit(1)

    if not found:
        logger.error('Name the groups (or aliases) to purge.')
        exit(1)

    # One another worker is updating is left alone
    ours, busy = ctx['NNTPClaims'].groups(sorted(found))
    for name in busy:
        logger.error('%s: another worker is updating it; it was left '
                     'alone.' % name)
    if not ours:
        exit(1)
    found = {name: found[name] for name in ours}

    for name in sorted(found):
        size = settings.group_cache_size(name)
        click.echo('%s: %s of cached headers' % (
            name, bytes_to_strsize(size) if size else 'nothing'))

    if not confirmed(ctx, 'Remove everything kept of %s?' % ', '.join(
            sorted(found))):
        click.echo('Nothing was removed.')
        exit(1)

    pruner = NNTPPruner(settings)
    for name in sorted(found):
        done = pruner.forget_group(name)
        click.echo(
            '%s: purged (%s freed; %d file(s), %d collection(s) and %d '
            'post(s) removed)' % (
                name, bytes_to_strsize(done.freed), done.files,
                done.collections, done.posts))

    if busy:
        # Not everything asked for was done
        exit(1)


@click.command(name='refresh')
@click.pass_obj
def group_refresh(ctx):
    """
    Fetch the list of groups every server carries.

    Only the names (and how many articles each holds) are fetched, never
    their headers; 'nr group ls --all' shows them, and 'nr group watch'
    picks the ones 'nr update' fetches.  Run it again now and then to learn
    of new groups.
    """
    settings = ctx['NNTPSettings']

    # Track whether every server answered
    failed = False

    for s in settings.nntp_servers:
        key = server_key(**s)
        try:
            settings.server_id(key)

        except DatabaseUnavailable:
            logger.error(NO_DATABASE)
            exit(1)

        except UnknownServer as e:
            logger.error("%s Run 'nr db init' to add it." % e)
            failed = True
            continue

        # One of the connections the server allows (shared with any other
        # worker); what it did is part of our statistics
        with ctx['NNTPManager'].connection(s) as con:
            results = con.groups()

        if not results:
            logger.error(
                "Could not fetch the group listing from '%s'." % key)
            failed = True
            continue

        try:
            new = settings.save_groups(key, results)

        except UnknownServer as e:
            logger.error("%s Run 'nr db init' to add it." % e)
            exit(1)

        click.echo('%s: %d group(s) listed, %d new.' % (
            key, len(results), new))

    if failed:
        exit(1)


@click.command(name='ls')
@click.option('--all', '-a', is_flag=True,
              help='Every group the servers carry.')
@click.pass_obj
def group_list(ctx, all):
    """
    List the groups that are watched or have something kept.

    A * marks the watched ones.  Each shows how much its cached headers
    take on disk and its own settings (see 'nr group set'); 'nr group
    purge' frees the space of one you no longer want.  --all lists every
    group the servers carry instead.

    The groups come from the database, so run 'nr group refresh' first to
    learn them (and again now and then to keep them current).  With --all
    and nothing set up yet, the first server is asked instead.
    """
    settings = ctx['NNTPSettings']
    try:
        results = settings.group_list(all=all)

    except DatabaseUnavailable:
        if not all:
            logger.error(NO_DATABASE)
            exit(1)

        # Nothing is set up yet; ask the first server instead
        results = None

    if results and not all:
        for r in results:
            size = settings.group_cache_size(r['name'])
            extra = describe_options(r)
            click.echo('%s %-60s %10s %10s%s' % (
                '*' if r['watch'] else ' ', r['name'], r['count'],
                bytes_to_strsize(size) if size else '-',
                '  (%s)' % extra if extra else ''))
        return

    if results:
        for r in results:
            click.echo('%s %-65s %10s %s' % (
                '*' if r['watch'] else ' ', r['name'], r['count'],
                r['flags']))
        return

    if all:
        results = ctx['NNTPManager'].groups()
        for r in sorted(results or (), key=lambda k: k['group']):
            click.echo('  %-65s %10s %s' % (
                r['group'], r['count'], ''.join(r['flags'])))
