# NewsReap Status CLI Plugin
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

# An overview of our setup, with a few lines on what is kept and how the
# servers are doing ('nr db status' has the details):
#   nr status

import logging
import click
import sys

from os.path import abspath
from os.path import dirname

try:
    from newsreap.Logging import NEWSREAP_CLI
except ImportError:
    # Path
    sys.path.insert(0, dirname(dirname(dirname(dirname(abspath(__file__))))))
    from newsreap.Logging import NEWSREAP_CLI


from newsreap import __title__
from newsreap import __version__
from newsreap.Database import DatabaseUnavailable
from newsreap.NNTPDatabase import failure_share
from newsreap.NNTPIndexDatabase import NNTPIndexDatabase
from newsreap.NNTPKeyring import NNTPKeyring
from newsreap.Utils import bytes_to_strsize
from newsreap.objects.index.ArchivedPost import ArchiveKind
from newsreap.objects.index.Collection import CollectionState

# initialize our logger
logger = logging.getLogger(NEWSREAP_CLI)

NEWSREAP_CLI_PLUGINS = 'status'

# How each line of the group table is laid out
GROUP_LINE = '  %-38s %10s %9s  %-15s %8s  %-16s  %s'


def heading(text):
    """
    Shows the title of a section
    """
    click.echo('')
    click.secho(text, bold=True)


def detail(name, value):
    """
    Shows one detail of a section
    """
    click.echo('  %-18s %s' % (name + ':', value))


def count(value):
    """
    Returns a number with its thousands separated
    """
    return '{:,}'.format(value or 0)


def when(value):
    """
    Returns when something happened (the epoch is what 'never' is kept as)
    """
    if not value or value.year < 1971:
        return 'never'
    return value.strftime('%Y-%m-%d %H:%M')


def index_database(settings):
    """
    Returns our NNTPIndexDatabase; None if nothing was ever indexed
    """
    if not NNTPIndexDatabase.exists(settings.index_database()):
        return None

    return NNTPIndexDatabase.from_engine(settings.index_database())


def show_groups(rows):
    """
    Shows the group table (see NNTPSettings.group_report()); returns the
    rows
    """
    if not rows:
        click.echo(
            "  Nothing fetched yet; run 'nr update <group> --newest 100000' "
            "(see 'nr group ls' for the groups).")
        return rows

    click.echo(GROUP_LINE % (
        'Group (W: watched)', 'Headers', 'Cache', 'Collections', 'Archived',
        'Scanned', 'Indexed'))

    for row in rows:
        click.echo(GROUP_LINE % (
            row['name'] + (' W' if row['watched'] else ''),
            count(row['headers']),
            bytes_to_strsize(row['size']) if row['size'] else '-',
            '%d (%d forming)' % (row['collections'], row['forming'])
            if row['forming'] else '%d' % row['collections'],
            count(row['archived']), when(row['scanned']),
            when(row['indexed'])))

    return rows


def show_setup(ctx):
    """
    Shows where everything is, our servers and our filters
    """
    settings = ctx['NNTPSettings']
    heading('Setup')
    detail('Version', '%s v%s' % (__title__, __version__))
    detail('Configuration', settings.cfg_file or '(defaults)')
    detail('Config directory', settings.config_dir)
    detail('Data directory', settings.data_dir)
    detail('Cache directory', settings.cache_dir)
    detail('Work directory', settings.work_dir)

    try:
        filters = settings.filters()
        text = '%d rule(s)' % len(filters)
        if filters.problems:
            text += ', %d left out (they have problems)' % len(
                filters.problems)
        detail('Filters', '%s (%s)' % (settings.filters_file(), text))

    except ValueError as e:
        detail('Filters', 'unusable: %s' % e)

    detail('Always (nr update)', '%s (groups can say otherwise; see nr '
           'group set)' % ', '.join(
               name if value else 'no %s' % name for name, value in (
                   ('content', settings.content()), ('nzb', settings.nzb()))))

    detail('PGP keys', 'kept from archived posts (see nr pgp)'
           if settings.pgp() else "not kept (set 'pgp: yes' under "
           "processing to keep them)")

    gpg = settings.gpg()
    if gpg is not None:
        detail('Decrypting', 'with %d secret key(s) in %s' % (
            len(set(gpg.secret_keys().values())), gpg.home)
            if gpg.available() else
            "not done (gpg isn't installed)")

    heading('Servers')
    for no, server in enumerate(settings.nntp_servers):
        secure = 'TLS, certificate checked' if server.get('secure') and \
            server.get('verify_cert', True) else 'TLS, certificate not ' \
            'checked' if server.get('secure') else 'not encrypted'
        if server.get('ca_certs'):
            secure += ' (against %s)' % server['ca_certs']

        click.echo('  %s:%s  %s, %s' % (
            server['host'], server.get('port'), secure,
            'primary' if no == 0 else 'backup'))


def show_storage(settings):
    """
    Shows how big each of our databases is
    """
    heading('Storage')
    found = settings.storage()

    for name, key in (('Settings', 'settings'), ('Index', 'index')):
        size, where = found[key]
        if where is not None:
            detail(name, '%s (%s)' % (
                bytes_to_strsize(size) if size is not None else
                'on a server', where))

    size, folder = found['caches']
    detail('Header caches', '%s (%d group(s), %s)' % (
        bytes_to_strsize(size), len(settings.cached_groups()), folder))

    detail('Total', bytes_to_strsize(sum(
        size for size, _ in found.values() if size)))


def show_index(index, gpg=None, retries=0):
    """
    Shows what the index holds
    - index: the NNTPIndexDatabase (None if nothing was ever indexed)
    - gpg: the NNTPGnuPG posts are decrypted with (if any)
    - retries: how many times 'nr update' tries them again on its own
    """
    heading('Collections')
    if index is None:
        click.echo("  Nothing indexed yet.")
        return

    totals = index.totals()
    states = totals['states']
    detail('Total', count(sum(
        found for state, found in states.items()
        if state != CollectionState.Deleted)))
    detail('Complete', count(states.get(CollectionState.Complete)))
    detail('Settled', count(states.get(CollectionState.Settled)) +
           ' (missing parts, as complete as they will get)')
    detail('Forming', count(states.get(CollectionState.Forming)) +
           ' (articles still arriving)')
    detail('Deleted', count(states.get(CollectionState.Deleted)) +
           ' (with nr delete; they stay away)')
    if totals['unfinished']:
        # A delete that stopped part way; running it again finishes it
        detail('Not fully deleted', '%s (to finish: nr delete %s%s)' % (
            count(totals['unfinished']),
            ' '.join(str(i) for i in totals['unfinished_ids']),
            ' ...' if totals['unfinished'] > len(totals['unfinished_ids'])
            else ''))

    detail('Obfuscated', count(totals['obfuscated']) +
           ' (put together from how they were posted)')
    detail('From NZB files', count(totals['from_nzb']))
    detail('Files', count(totals['files']))
    detail('Size of it all', bytes_to_strsize(totals['size']))

    heading('Articles')
    kinds = totals['kinds']
    detail('Kept (--content)', '%s (%s text, %s binary, %s missing, %s '
           'refused)' % (
               count(sum(kinds.values())), count(kinds.get(ArchiveKind.Text)),
               count(kinds.get(ArchiveKind.Binary)),
               count(kinds.get(ArchiveKind.Missing)),
               count(kinds.get(ArchiveKind.Refused))))
    detail('Scored or tagged', count(totals['ranked']))

    session = index.session()
    keys, posts = NNTPKeyring.counts(session)
    if keys or posts:
        detail('PGP keys', '%s (%s post(s) tied to a key)' % (
            count(keys), count(posts)))

    if gpg is not None and gpg.available():
        secret = gpg.secret_keys()
        waiting = len(NNTPKeyring.locked(session, secret, final=False))
        if waiting:
            # The ones 'nr update' has run out of tries for
            done = waiting - len(NNTPKeyring.locked(
                session, secret, final=False, retries=retries))
            detail('Not decrypted yet', "%s (%s; see 'nr pgp decrypt')" % (
                count(waiting),
                '%s no longer tried by nr update' % count(done) if done
                else 'nr update tries them again'))

    detail('NZB files read', '%s (%s unusable)' % (
        count(totals['nzb_read']), count(totals['nzb_unusable'])))


def size(value):
    """
    Returns a size kept short (ie: 400KB, 3.4MB, 3.5TB)
    """
    return bytes_to_strsize(value or 0, short=True)


def speed(value):
    """
    Returns a speed (bytes per second) kept short
    """
    return '%s/s' % size(value)


def moved(stats):
    """
    Returns how much was received and sent (ie: 1.2GB down, 4MB up)
    """
    return '%s down, %s up' % (size(stats.rx_bytes), size(stats.tx_bytes))


def transfer_line(stats):
    """
    Returns one period of a server's transfers on a line
    """
    if not (stats.commands or stats.total_bytes):
        return 'nothing'

    text = '%s at %s' % (moved(stats), speed(stats.rate))
    articles = stats.fetched + stats.posted
    if articles:
        text += ', %s article(s)' % count(round(articles))

    failures = failure_share(stats)
    if failures:
        text += ', %.1f%% failed' % (failures * 100)

    return text


def warning_lines(transfers):
    """
    Returns what looks wrong today next to the usual day, one line each
    """
    lines = []
    for what, now, usual in transfers.warnings:
        if what == 'slow':
            lines.append('slower than usual: %s today, %s on a usual day' % (
                speed(now), speed(usual)))

        else:
            lines.append(
                'more failures than usual: %.1f%% of commands today, %.1f%% '
                'on a usual day' % (now * 100, usual * 100))
    return lines


def show_transfers(settings, brief=False):
    """
    Shows how much went to and from each server, and how fast (brief: a
    line or two each)
    """
    heading('Transfers')
    found = settings.transfers()
    if not found:
        click.echo('  Nothing yet; every command that talks to a server adds '
                   'to it.')
        return

    for transfers in found:
        if brief:
            click.echo('  %-18s today %s; %d day(s) %s' % (
                transfers.host, moved(transfers.today), transfers.days,
                moved(transfers.kept)))

        else:
            click.echo('  %s' % transfers.host)
            for name, stats in (
                    ('Today', transfers.today),
                    ('Yesterday', transfers.yesterday),
                    ('Last 7 days', transfers.week),
                    ('%d day(s) kept' % transfers.days, transfers.kept)):
                click.echo('    %-16s %s' % (name, transfer_line(stats)))

            if transfers.usual is not None:
                click.echo('    %-16s %s' % (
                    'A usual day', transfer_line(transfers.usual)))
            click.echo('    %-16s %s' % (
                'Fastest', speed(transfers.kept.peak_rate)))

        for line in warning_lines(transfers):
            click.secho('  %s! %s' % ('' if brief else '  ', line),
                        fg='yellow')


def show_aliases(settings):
    """
    Shows the aliases defined (and the groups they stand for)
    """
    heading('Aliases')
    found = settings.aliases()
    if not found:
        click.echo("  None (see 'nr alias add').")
        return

    for alias, groups in found.items():
        click.echo('  %-18s %s' % (alias, ', '.join(groups)))


@click.command(name='status')
@click.pass_obj
def status(ctx):
    """
    Show an overview of our setup.

    Where everything is, the servers and filters used, the aliases
    defined, and a few lines on what is kept and how much went to and from
    each server (with a warning when today looks wrong).

    'nr db status' has the details (every number, and every group with
    something kept); 'nr group ls' lists the groups and their settings.

    """
    settings = ctx['NNTPSettings']
    try:
        groups_known, watched = settings.group_counts()

    except DatabaseUnavailable:
        click.echo(
            "The database isn't set up yet; run 'nr db init' first.")
        exit(1)

    show_setup(ctx)
    show_aliases(settings)

    heading('Kept')
    detail('Groups', '%s known, %s watched%s' % (
        count(groups_known), count(watched),
        '' if groups_known else "; run 'nr group refresh' for them"))
    detail('On disk', size(sum(
        found for found, _ in settings.storage().values() if found)))

    show_transfers(settings, brief=True)

    click.echo('')
    click.echo("See 'nr db status' for the details and 'nr group ls' for "
               "the groups.")


def show_details(settings):
    """
    Shows every number we keep: storage, transfers, what the index holds
    and every group with something kept ('nr db status')
    """
    groups_known, watched = settings.group_counts()
    show_storage(settings)
    show_transfers(settings)

    index = index_database(settings)
    try:
        show_index(index, settings.gpg(), settings.pgp_retries())

    finally:
        if index is not None:
            index.close()

    heading('Groups')
    detail('Known', '%s (%s watched)%s' % (
        count(groups_known), count(watched),
        '' if groups_known else "; run 'nr group refresh' for them"))
    click.echo('')
    show_groups(settings.group_report())
