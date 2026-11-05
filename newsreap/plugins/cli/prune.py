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
#   nr prune downloads --days 14     # downloads that didn't finish
#   nr prune work                    # leftover work files
#   nr prune headers --days 90 -n    # see which indexed headers would go
#   nr -y prune all                  # all three, as the configuration says

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

from newsreap.NNTPPruner import NNTPPruner
from newsreap.Utils import bytes_to_strsize

logger = logging.getLogger(NEWSREAP_CLI)

NEWSREAP_CLI_PLUGINS = {
    'prune': {
        'prefix': 'prune',
        'desc': 'Clear out what is no longer needed',
    },
}

# What each kind is called, and the setting its days come from
KINDS = {
    'headers': ('Indexed headers', 'raw_retention'),
    'downloads': ("Downloads that didn't finish", 'prune_downloads'),
    'work': ('Leftover work files', 'prune_work'),
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
    if not (found.headers or found.downloads or found.work):
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


@click.command(name='all')
@dry_run_option
@click.pass_obj
def prune_all(ctx, dry_run):
    """
    Remove everything the configuration says to.

    Each kind (headers, downloads, work) keeps its days from the
    configuration, and a kind set to 0 is left alone.
    """
    clear_out(ctx, NNTPPruner(ctx['NNTPSettings']), tuple(KINDS), dry_run,
              'set {setting} in the configuration to clear them')
