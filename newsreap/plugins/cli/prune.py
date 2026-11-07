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
# always say what to clear, and are asked before anything goes:
#   nr prune a.b.test                # what is older than its retention
#   nr prune --watched -n            # see what would go from every group
#   nr prune downloads --days 14     # downloads that didn't finish
#   nr prune work                    # leftover work files
#   nr prune headers --days 90 -n    # see which indexed headers would go
#   nr prune statistics              # transfer statistics older than 31 days
#   nr -y prune all                  # every kind, as the configuration says

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
        # 'nr prune a.b.test' is 'nr prune run a.b.test'
        'default': 'run',
    },
}

# What each kind is called, and the setting its days come from
KINDS = {
    'headers': ('Indexed headers', 'raw_retention'),
    'downloads': ("Downloads that didn't finish", 'prune_downloads'),
    'work': ('Leftover work files', 'prune_work'),
    'statistics': ('Transfer statistics', 'statistics'),
}

days_option = click.option(
    '--days', type=click.IntRange(min=1), default=None,
    help='How many days to keep them (the configuration when not given).')

dry_run_option = click.option(
    '--dry-run', '-n', is_flag=True, help='Only show what would be removed.')


def clear_out(ctx, pruner, kinds, dry_run, hint):
    """
    Shows what the pruner finds, asks, and clears it out

    - kinds: the kinds asked for; one left alone (0 days) is explained
    - hint: how to clear a kind that is left alone
    - Returns False when none of them could be looked at
    """
    for kind in kinds:
        if not getattr(pruner, kind):
            name, setting = KINDS[kind]
            click.echo('%s are kept (%s is 0); %s.' % (
                name, setting, hint.format(setting=setting)))

    if not any(getattr(pruner, kind) for kind in kinds):
        return False

    found = pruner.find()
    if not (found.headers or found.downloads or found.work or
            found.statistics):
        click.echo('Nothing to clear out.')
        return True

    for name, _, count in found.headers:
        click.echo('%s: %d header(s) older than %d day(s)' % (
            name, count, pruner.headers))
    for path, size in found.downloads:
        click.echo("Download that didn't finish (%s): %s" % (
            bytes_to_strsize(size), path))
    if found.work:
        click.echo('%d leftover work file(s) (%s) in %s' % (
            len(found.work), bytes_to_strsize(sum(s for _, s in found.work)),
            ctx['NNTPSettings'].work_dir))
    if found.statistics:
        click.echo('%d day(s) of server statistics older than %d day(s)' % (
            found.statistics, pruner.statistics))

    if dry_run:
        click.echo('Dry run: nothing was changed.')
        return True

    if not ctx.get('noprompt') and not click.confirm(
            'Remove all of this?', default=False):
        click.echo('Nothing was removed.')
        return True

    pruner.clear(found)
    click.echo('Cleared out.')
    return True


def one_kind(ctx, kind, days, dry_run):
    """
    Clears out one kind only (the others are left alone)
    """
    wanted = {name: 0 for name in KINDS}
    wanted[kind] = days
    if not clear_out(ctx, NNTPPruner(ctx['NNTPSettings'], **wanted), (kind, ),
                     dry_run, 'give --days to clear them'):
        exit(1)


@click.command(name='downloads')
@days_option
@dry_run_option
@click.pass_obj
def prune_downloads(ctx, days, dry_run):
    """
    Remove downloads that didn't finish.

    They are kept so a download can carry on where it stopped; these go
    once nothing has touched them for --days (or prune_downloads in the
    configuration, which keeps them by default).  A download being run, and
    finished downloads, are never touched.
    """
    one_kind(ctx, 'downloads', days, dry_run)


@click.command(name='work')
@days_option
@dry_run_option
@click.pass_obj
def prune_work(ctx, days, dry_run):
    """
    Remove files left in the work directory.

    These go once they are --days old (or prune_work in the configuration,
    a day by default).  Only a work directory inside our cache directory is
    looked at.
    """
    one_kind(ctx, 'work', days, dry_run)


@click.command(name='headers')
@days_option
@dry_run_option
@click.pass_obj
def prune_headers(ctx, days, dry_run):
    """
    Remove old indexed article headers.

    Headers posted more than --days ago (or raw_retention in the
    configuration, which keeps them by default) go from the header caches.
    The collections made from them stay, so searching and saving NZB files
    still work; only 'nr search --articles' no longer finds them.
    """
    one_kind(ctx, 'headers', days, dry_run)


@click.command(name='statistics')
@days_option
@dry_run_option
@click.pass_obj
def prune_statistics(ctx, days, dry_run):
    """
    Remove old transfer statistics.

    Each server's statistics are kept per day ('nr db status' shows
    them); the days older than --days (or the statistics setting in the
    configuration, 31 days by default) go.
    """
    one_kind(ctx, 'statistics', days, dry_run)


@click.command(name='run', hidden=True)
@click.argument('groups', nargs=-1)
@click.option('--watched', '-w', is_flag=True, help='All watched groups.')
@click.option('--days', type=click.IntRange(min=1), default=None,
              help="How many days to keep (each group's retention when "
                   "not given).")
@dry_run_option
@click.pass_obj
def prune_run(ctx, groups, watched, days, dry_run):
    """
    Remove what is older than each group's retention.

    A group keeps the days 'nr group set --retention' gave it, otherwise
    the retention setting in the configuration (0 keeps everything).  Its
    cached headers, the files and collections indexed from it, and the
    posts kept from it go once they are older; what other groups have too
    stays.  Nothing goes unless this is run (or 'nr update --prune'), and
    fetching older articles on purpose (ie: nr update --days 90) still
    works afterwards.

    \b
        nr prune a.b.test
        nr prune --watched --dry-run
        nr -y prune a.b.test --days 30
    """
    settings = ctx['NNTPSettings']
    try:
        groups = settings.groups(groups, watched=watched)

    except DatabaseUnavailable:
        logger.error("The database isn't set up yet; run 'nr db init' "
                     "first.")
        exit(1)

    except UnknownGroups as e:
        logger.error(str(e))
        exit(1)

    if not groups:
        logger.error(
            "No groups were given; name them (see 'nr group ls'), or use "
            "--watched for the watched ones.")
        exit(1)

    # What would go from each one
    pruner = NNTPPruner(settings)
    found = {name: pruner.prune_group(name, days=days, dry_run=True)
             for name in sorted(groups)}

    wanted = []
    for name, plan in found.items():
        if not plan.days:
            click.echo('%s: keeps everything (no retention set).' % name)

        elif not (plan.headers or plan.files or plan.posts):
            click.echo('%s: nothing older than %d day(s).' % (
                name, plan.days))

        else:
            click.echo(
                '%s: %d header(s), %d file(s) and %d post(s) older than %d '
                'day(s)' % (name, plan.headers, plan.files, plan.posts,
                            plan.days))
            wanted.append(name)

    if not wanted:
        return

    if dry_run:
        click.echo('Dry run: nothing was changed.')
        return

    if not ctx.get('noprompt') and not click.confirm(
            'Remove all of this?', default=False):
        click.echo('Nothing was removed.')
        return

    for name in wanted:
        done = pruner.prune_group(name, days=found[name].days)
        click.echo(
            '%s: %d header(s), %d file(s), %d collection(s) and %d post(s) '
            'removed' % (name, done.headers, done.files, done.collections,
                         done.posts))


@click.command(name='all')
@dry_run_option
@click.pass_obj
def prune_all(ctx, dry_run):
    """
    Remove everything the configuration says to.

    Each kind (headers, downloads, work, statistics) keeps its days from
    the configuration, and a kind set to 0 is left alone.
    """
    clear_out(ctx, NNTPPruner(ctx['NNTPSettings']), tuple(KINDS), dry_run,
              'set {setting} in the configuration to clear them')
