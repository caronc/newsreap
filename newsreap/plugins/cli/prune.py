# NewsReap Prune CLI Plugin
#
# Copyright (C) 2026 Chris Caron <lead2gold@gmail.com>
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

# Clear out what is no longer needed (as 'docker image prune' does); you
# are always asked before anything goes:
#   nr prune                          # everything the configuration says
#   nr prune -n                       # only show what would go
#   nr prune a.b.test                 # what is older than its retention
#   nr prune --only downloads --days 14
#   nr prune --only statistics --only work

import logging
import click
import sys

from os.path import abspath
from os.path import dirname

try:
    from newsreap.Logging import NEWSREAP_CLI

except ImportError:
    sys.path.insert(0, dirname(dirname(dirname(dirname(abspath(__file__))))))
    from newsreap.Logging import NEWSREAP_CLI

from newsreap.Database import DatabaseUnavailable
from newsreap.NNTPDatabase import UnknownGroups
from newsreap.NNTPPruner import NNTPPruner
from newsreap.Utils import bytes_to_strsize

logger = logging.getLogger(NEWSREAP_CLI)

NEWSREAP_CLI_PLUGINS = {
    'prune': {
        'prefix': 'prune',
        'desc': 'Clear out what is no longer needed',
    },
}

# What each kind is called, and the setting its days come from; the
# groups are pruned to their own retention (see 'nr group set')
KINDS = {
    'groups': ('Watched groups', 'retention'),
    'headers': ('Indexed headers', 'raw_retention'),
    'downloads': ("Downloads that didn't finish", 'prune_downloads'),
    'work': ('Leftover work files', 'prune_work'),
    'statistics': ('Transfer statistics', 'statistics'),
}


def chosen_groups(ctx, names, watched):
    """
    Returns the groups to prune (name -> id) and whether a name given
    wasn't a group; the watched ones when none are named.  Raises
    DatabaseUnavailable when there is no database yet
    """
    settings = ctx['NNTPSettings']
    watched = watched or not names
    try:
        return settings.groups(names, watched=watched), False

    except UnknownGroups as e:
        for name in e.names:
            logger.error(
                "There is no group (or alias) named '%s'; see 'nr group "
                "ls --all'." % name)

        # Carry on with the ones we know
        return settings.groups(names, watched=watched, strict=False), True


@click.command(name='prune')
@click.argument('groups', nargs=-1)
@click.option('--watched', '-w', is_flag=True,
              help='Add the watched groups to the ones named (the default '
                   'when none are).')
@click.option('--only', '-o', 'only', multiple=True,
              type=click.Choice(list(KINDS)),
              help='Only clear out this kind (can be given more than once).')
@click.option('--days', type=click.IntRange(min=1), default=None,
              help='How many days to keep (the configuration, or each '
                   "group's retention, when not given); needs one kind.")
@click.option('--dry-run', '-n', is_flag=True,
              help='Only show what would be removed.')
@click.pass_obj
def prune(ctx, groups, watched, only, days, dry_run):
    """
    Clear out what is no longer needed.

    With nothing named, everything the configuration says to clear goes
    (asking first):

    \b
      groups      what the watched groups hold that is older than their
                  retention ('nr group set --retention', or the retention
                  setting): cached headers, the files and collections
                  indexed from them, and the posts kept from them; what
                  other groups have too stays
      headers     indexed headers older than raw_retention (collections
                  stay; only 'nr search --articles' stops finding them)
      downloads   downloads that didn't finish, untouched for
                  prune_downloads days (a running one is never touched)
      work        files left in the work directory for prune_work days
      statistics  transfer statistics older than the statistics setting

    A kind set to 0 is kept.  Name groups (or aliases) to prune only them,
    or use --only to pick kinds.  Nothing goes unless this is run (or 'nr
    update --prune'), and fetching older articles on purpose (ie: nr update
    --days 90) still works afterwards.

    \b
        nr prune --dry-run
        nr prune a.b.test --days 30
        nr prune --only downloads --days 14
        nr -y prune
    """
    settings = ctx['NNTPSettings']
    if (groups or watched) and only:
        # Groups are a kind of their own
        logger.error('Name groups (or --watched) or give --only, not both.')
        exit(1)

    kinds = ('groups', ) if groups or watched else (only or tuple(KINDS))
    if days and len(kinds) > 1:
        logger.error(
            '--days needs one kind; pick it with --only (or name groups).')
        exit(1)

    # Track whether a name given wasn't a group
    unknown = False

    # What would go from each group
    wanted = {}
    pruner = NNTPPruner(settings, **{
        kind: (days if kind in kinds else 0)
        for kind in KINDS if kind != 'groups'})

    # Groups were asked for (rather than just being part of everything)
    asked = bool(groups or watched or only)

    if 'groups' in kinds:
        try:
            found, unknown = chosen_groups(ctx, groups, watched)

        except DatabaseUnavailable:
            if asked:
                logger.error("The database isn't set up yet; run 'nr db "
                             "init' first.")
                exit(1)

            # Nothing is watched yet; the other kinds can still go
            found = {}

        if not found and asked:
            if not unknown:
                logger.error(
                    "No groups are watched; name the ones to prune (see 'nr "
                    "group ls'), or watch them with 'nr group watch'.")
            exit(1)

        # Another worker updating one is left to it
        ours, busy = ctx['NNTPClaims'].groups(sorted(found))
        for name in busy:
            click.echo('%s: another worker is updating it; skipped.' % name)

        for name in ours:
            plan = pruner.prune_group(name, days=days, dry_run=True)
            if not plan.days:
                click.echo('%s: keeps everything (no retention set).' % name)

            elif not (plan.headers or plan.files or plan.posts):
                click.echo('%s: nothing older than %d day(s).' % (
                    name, plan.days))

            else:
                click.echo(
                    '%s: %d header(s), %d file(s) and %d post(s) older than '
                    '%d day(s)' % (name, plan.headers, plan.files,
                                   plan.posts, plan.days))
                wanted[name] = plan.days

    # The other kinds; one set to 0 is kept
    others = [kind for kind in kinds if kind != 'groups']
    for kind in others:
        if not getattr(pruner, kind):
            name, setting = KINDS[kind]
            click.echo('%s are kept (%s is 0); %s.' % (
                name, setting, 'give --days to clear them' if only else
                'set %s in the configuration to clear them' % setting))

    if only and 'groups' not in kinds and \
            not any(getattr(pruner, k) for k in others):
        # Nothing asked for could be looked at
        exit(1)

    found = pruner.find()
    if found.downloads or found.headers or found.work or found.statistics:
        for name, _, count in found.headers:
            click.echo('%s: %d header(s) older than %d day(s)' % (
                name, count, pruner.headers))
        for path, size in found.downloads:
            click.echo("Download that didn't finish (%s): %s" % (
                bytes_to_strsize(size), path))
        if found.work:
            click.echo('%d leftover work file(s) (%s) in %s' % (
                len(found.work),
                bytes_to_strsize(sum(s for _, s in found.work)),
                settings.work_dir))
        if found.statistics:
            click.echo('%d day(s) of server statistics older than %d '
                       'day(s)' % (found.statistics, pruner.statistics))

    elif not wanted:
        click.echo('Nothing to clear out.')
        exit(1 if unknown else 0)

    if dry_run:
        click.echo('Dry run: nothing was changed.')

    elif not ctx.get('noprompt') and not click.confirm(
            'Remove all of this?', default=False):
        click.echo('Nothing was removed.')

    else:
        pruner.clear(found)
        for name, keep in wanted.items():
            done = pruner.prune_group(name, days=keep)
            click.echo(
                '%s: %d header(s), %d file(s), %d collection(s) and %d '
                'post(s) removed' % (name, done.headers, done.files,
                                     done.collections, done.posts))
        click.echo('Cleared out.')

    if unknown:
        exit(1)
