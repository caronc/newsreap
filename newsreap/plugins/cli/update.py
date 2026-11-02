# NewsReap NNTP Database Caching Functions
#
# Copyright (C) 2015-2026 Chris Caron <lead2gold@gmail.com>
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

# The update commands keep NewsReap's database current:
#   - update groups: caches the groups every server carries
#   - update search: caches the article headers of groups so they can be
#                    searched offline (see NNTPGroupScanner)
#   - update index:  groups the cached articles into files and collections
#                    (see NNTPGroupIndexer)

import logging
import click
import sys

from os import access
from os import W_OK
from os.path import isdir
from os.path import dirname
from os.path import abspath
from datetime import datetime
from datetime import timedelta

from sqlalchemy import insert
from sqlalchemy import update as sql_update
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.exc import OperationalError

try:
    from newsreap.Logging import NEWSREAP_CLI
except ImportError:
    # Path
    sys.path.insert(0, dirname(dirname(dirname(dirname(abspath(__file__))))))
    from newsreap.Logging import NEWSREAP_CLI

from newsreap.objects.nntp.Group import Group
from newsreap.objects.nntp.GroupTrack import GroupTrack
from newsreap.objects.nntp.Server import Server
from newsreap.objects.nntp.Common import get_groups

from newsreap.NNTPConnection import NNTPConnection
from newsreap.NNTPGroupScanner import NNTPGroupScanner
from newsreap.NNTPGroupScanner import DEFAULT_BATCH_SIZE
from newsreap.NNTPGroupDatabase import NNTPGroupDatabase
from newsreap.NNTPArchiver import NNTPArchiver
from newsreap.NNTPFilterRule import parse_time
from newsreap.NNTPIndexer import NNTPIndexer
from newsreap.Utils import utcnow

# initialize our logger
logger = logging.getLogger(NEWSREAP_CLI)

NEWSREAP_CLI_PLUGINS = {
    # format:
    # cli short hand group: function prefix
    'update': {
        'prefix': 'update',
        'desc': 'Cache/Index to Database management',
    },
}


def load_filters(settings):
    """
    Returns the filters to apply (None if there are no rules); exits if
    the filters file has a problem, before anything is fetched
    """
    try:
        filters = settings.filters()

    except ValueError as e:
        logger.error(str(e))
        exit(1)

    return filters if len(filters) else None


def lookup_server(session, host):
    """
    Returns the Server entry for a host (None if it isn't in the database)
    """
    try:
        return session.query(Server).filter(Server.host == host).first()

    except (InvalidRequestError, OperationalError):
        # The database hasn't been set up yet
        logger.error(
            "The database is not set up yet; run 'nr db init' first.")
        exit(1)


def parse_date(value, name):
    """
    Turns a date the user gave us into a (UTC) datetime, the way every
    other command reads them (exits, saying why, if it can't)

    - value: the text given (ie: 2024, 2024.02, 2024-02-01 10:00, or how
      long ago: 90d)
    - name: the option it came from (for the error message)
    """
    try:
        found = parse_time(value, '--date-%s' % name)

    except ValueError as e:
        logger.error(str(e))
        exit(1)

    if found is None:
        logger.error('--date-%s needs a date.' % name)
        exit(1)

    return found


@click.command(name='groups')
@click.pass_obj
def update_groups(ctx):
    """
    Cache all Usenet groups.

    The following function queries all servers defined and caches their
    statistics to the database for caching (speed).  This function can be
    called as often as you wish to re-caches the data in the database.

    """
    # Use our Database first if it exists
    session = ctx['NNTPSettings'].session()
    if not session:
        logger.error('Could not acquire a database connection.')
        exit(1)

    # Track whether every server answered
    failed = False

    # Get Group Listings for all of our servers
    for s in ctx['NNTPSettings'].nntp_servers:
        # get our server (if it's kept in the database)
        server = lookup_server(session, s['host'])
        if not server:
            logger.warning(
                "The NNTP Server '%s' isn't in the database yet; run "
                "'nr db init' to add it." % s['host'])
            failed = True
            continue

        # Fetch the server's group listing
        con = NNTPConnection(**s)
        try:
            results = con.groups()

        finally:
            # Our connection's statistics are part of the total
            con.close()
            ctx['NNTPManager'].add_stats(con.stats)

        if not results:
            logger.warning(
                "Could not fetch the group listing from '%s'." % s['host'])
            failed = True
            continue

        # The groups we already know about (name -> id), read once rather
        # than once per group
        known = dict(session.query(Group.name, Group.id))

        # Sort our listing into new groups and ones to update
        new_groups = []
        updates = []
        watermarks = {}
        for r in results:
            name = r['group']

            # Convert flag list back into a string
            flags = ''.join(r['flags'])

            group_id = known.get(name)
            if name not in known:
                # A group we haven't seen before
                new_groups.append(
                    {'name': name, 'count': r['count'], 'flags': flags})

                # Servers sometimes list a group twice; only add it once
                known[name] = None

            elif group_id is not None:
                # A group we know; refresh its details
                updates.append(
                    {'id': group_id, 'count': r['count'], 'flags': flags})
                watermarks[group_id] = (r['low'], r['high'])

        if new_groups:
            # Add our new groups all at once
            session.execute(insert(Group), new_groups)

        if updates:
            # Update the ones we know all at once
            session.execute(sql_update(Group), updates)

        # Using our groups; update any tracking settings (if present)
        for track in session.query(GroupTrack)\
                .filter(GroupTrack.server_id == server.id):
            if track.group_id in watermarks:
                track.low, track.high = watermarks[track.group_id]

        session.commit()

        click.echo('%s: %d group(s) listed, %d new.' % (
            s['host'], len(results), len(new_groups)))

    if failed:
        exit(1)


@click.command(name='search')
@click.argument('groups', nargs=-1)
@click.option('--date-from', '-f',
              help='Only articles posted on or after this date (ie: '
                   '2024, 2024-02-01, or how long ago: 90d).')
@click.option('--date-to', '-t',
              help='Only articles posted on or before this date (the same '
                   'as --date-from).')
@click.option('--days', '-d', type=click.IntRange(min=1),
              help='Only articles posted in the last X days.')
@click.option('--newest', '-n', type=click.IntRange(min=1),
              help='Only the newest X articles.')
@click.option('--batch-size', '-b', type=click.IntRange(min=1),
              help='Article headers fetched per request '
                   '(default: header_batch_size from the configuration).')
@click.option('--batches', '-c', type=click.IntRange(min=1),
              help='Only process this many batches (per group).')
@click.option('--force', is_flag=True,
              help='Fetch articles again even if they were fetched before.')
@click.option('--watched', '-w', is_flag=True, help='All watched groups.')
@click.pass_obj
def update_search(ctx, groups, date_from, date_to, days, newest, batch_size,
                  batches, force, watched):
    """
    Cache the article headers of groups for searching.

    Articles are cached into their own database (one per group) due to the
    sheer size of the content within each group.

    Only what hasn't been fetched before is fetched, so running this again
    picks up new articles (and anything a previous run missed or didn't get
    to).  Narrow down what is fetched with the date and count options.

    """
    # Use our Database first if it exists
    session = ctx['NNTPSettings'].session()
    if not session:
        logger.error('Could not acquire a database connection.')
        exit(1)

    if not ctx['NNTPSettings'].nntp_servers:
        logger.error("There are no servers defined.")
        exit(1)

    # What we drop (and how we score and tag the rest)
    filters = load_filters(ctx['NNTPSettings'])

    # Prepare our dates
    if date_from is not None:
        date_from = parse_date(date_from, 'from')

    if days:
        # The last X days
        # (cached post dates are kept in UTC)
        since = utcnow() - timedelta(days=days)
        date_from = max(date_from, since) if date_from else since

    if date_to is not None:
        date_to = parse_date(date_to, 'to')

    if date_to and date_from and date_from > date_to:
        logger.error(
            "The from date can not be larger then the to date.",
        )
        exit(1)

    # Articles are always fetched from our primary server
    s = ctx['NNTPSettings'].nntp_servers[0]
    server = lookup_server(session, s['host'])
    if not server:
        logger.error(
            "The NNTP Server '%s' isn't in the database yet; run "
            "'nr db init' to add it." % s['host'])
        exit(1)

    groups = get_groups(session=session, lookup=groups, watched=watched)
    if not groups:
        logger.error("There were not groups identified for indexing.")
        exit(1)

    # Get our RamDisk if we got one
    ramdisk = ctx['NNTPSettings'].nntp_processing.get('ramdisk')
    if ramdisk:
        if not (isdir(ramdisk) and access(ramdisk, W_OK)):
            logger.warning('Ramdisk "%s" is not accessible.' % (ramdisk))
            # Turn it off so we don't reference it
            ramdisk = None
        else:
            logger.info('Using ramdisk: %s' % (ramdisk))

    if not batch_size:
        # Use our configuration
        batch_size = ctx['NNTPSettings'].nntp_processing.get(
            'header_batch_size', DEFAULT_BATCH_SIZE)

    # Track whether every group was fully fetched
    complete = True

    for name, group_id in groups.items():
        scanner = NNTPGroupScanner(
            ctx['NNTPManager'], name,
            db_file=ctx['NNTPSettings'].group_database(name),
            server=s['host'],
            batch_size=batch_size,
            ramdisk=ramdisk,
            filters=filters,
            index_db=ctx['NNTPSettings'].index_database(),
        )

        try:
            if not scanner.scan(
                    date_from=date_from, date_to=date_to, newest=newest,
                    force=force, limit=batches):
                complete = False

        finally:
            # Even when stopped early, track what we know about the group
            if scanner.low is not None:
                track(session, group_id, server.id, scanner)

            click.echo(str(scanner))

    if not complete:
        logger.warning(
            'Not everything was fetched; run the same command again to '
            'fetch the rest.')
        exit(1)


def track(session, group_id, server_id, scanner):
    """
    Updates the GroupTrack entry for a group after a scan
    """
    entry = session.query(GroupTrack)\
        .filter(GroupTrack.group_id == group_id)\
        .filter(GroupTrack.server_id == server_id).first()

    if not entry:
        # First time we've scanned this group on this server
        entry = GroupTrack(
            group_id=group_id,
            server_id=server_id,
            scan_pointer=scanner.low,
            index_pointer=scanner.low,
        )
        session.add(entry)

    # The server's first and last article number
    entry.low = scanner.low
    entry.high = scanner.high

    if scanner.batches:
        # When we last fetched something
        entry.last_scan = datetime.now()

    session.commit()


@click.command(name='index')
@click.argument('groups', nargs=-1)
@click.option('--watched', '-w', is_flag=True, help='All watched groups.')
@click.option('--settle', is_flag=True,
              help='Count every group as seen up to now, so collections '
                   'from groups you no longer scan can settle too.')
@click.pass_obj
def update_index(ctx, groups, watched, settle):
    """
    Group cached articles into files and collections.

    Works through the articles 'nr update search' cached and works out
    which file (and which collection of files) each one belongs to.  A
    collection can span many groups.  Only articles not indexed before are
    looked at.  Use 'nr nzb list' to see the results and 'nr nzb save' to
    turn any of them into an NZB file.

    """
    settings = ctx['NNTPSettings']
    session = settings.session()
    if not session:
        logger.error("The database is not correctly configured.")
        exit(1)

    groups = get_groups(session=session, lookup=groups, watched=watched)
    if not groups:
        logger.error("There were not groups identified for indexing.")
        exit(1)

    # How we treat collections (see the processing section of the
    # configuration file)
    processing = settings.nntp_processing
    indexer = NNTPIndexer(
        settings.index_database(),
        settle_hours=processing['settle_hours'],
        repost_days=processing['repost_days'],
        filters=load_filters(settings))

    # Track whether every group could be indexed
    complete = True

    try:
        for name, group_id in groups.items():
            db_file = settings.group_database(name)
            if not indexer.index(name, db_file):
                click.echo(
                    "%s: nothing cached yet; run 'nr update search %s' "
                    "first." % (name, name))
                complete = False
                continue

            click.echo('%s: %s' % (name, indexer))

            if processing['raw_retention']:
                # Usenet is big; let go of the old headers we've indexed
                cache = NNTPGroupDatabase.from_file(db_file)
                try:
                    removed = cache.purge(processing['raw_retention'])

                finally:
                    cache.close()

                if removed:
                    click.echo('%s: %d old header(s) removed' % (
                        name, removed))

            # Track when the group was last indexed
            session.query(GroupTrack)\
                .filter(GroupTrack.group_id == group_id)\
                .update({GroupTrack.last_index: datetime.now()})
            session.commit()

        # Work out which collections have formed (across every group)
        if indexer.settle(now=utcnow() if settle else None):
            click.echo('%d collection(s) formed' % indexer.formed)

    finally:
        indexer.close()

    if not complete:
        exit(1)


@click.command(name='archive')
@click.argument('groups', nargs=-1)
@click.option('--watched', '-w', is_flag=True, help='All watched groups.')
@click.option('--max-size', '-m', default=None, type=str,
              help='The biggest article fetched (ie: 64KB); defaults to '
                   'the archive_max_size setting.')
@click.option('--force', is_flag=True,
              help='Ask again for the articles no server had before.')
@click.option('--hooks', '-k', default=None, type=str,
              help='More archive hooks to use (see '
                   'newsreap/hooks/archive/).')
@click.option('--no-default-hooks', is_flag=True,
              help="Don't use the built in archive hooks.")
@click.pass_obj
def update_archive(ctx, groups, watched, max_size, force, hooks,
                   no_default_hooks):
    """
    Keep the small articles of groups so they can be searched offline.

    Fetches the articles 'nr update search' cached the headers of that are
    no bigger than --max-size, and keeps what they say (decoded, so it can
    be searched with 'nr search --posts').  Files they hold (yEnc,
    uuencode, attachments) are only noted, never kept.  Articles kept
    before aren't fetched again.

    """
    settings = ctx['NNTPSettings']
    session = settings.session()
    if not session:
        logger.error("The database is not correctly configured.")
        exit(1)

    groups = get_groups(session=session, lookup=groups, watched=watched)
    if not groups:
        logger.error("There were not groups identified for archiving.")
        exit(1)

    try:
        archiver = NNTPArchiver(
            settings.index_database(), hooks=hooks,
            default_hooks=not no_default_hooks,
            max_size=max_size or
            settings.nntp_processing.get('archive_max_size') or '64KB',
            filters=load_filters(settings))

    except ValueError as e:
        logger.error(str(e))
        exit(1)

    # Track whether every group could be archived
    complete = True

    try:
        for name in groups:
            if not archiver.archive(
                    name, settings.group_database(name),
                    ctx['NNTPManager'], work_dir=settings.work_dir,
                    force=force):
                click.echo(
                    "%s: nothing cached yet; run 'nr update search %s' "
                    "first." % (name, name))
                complete = False
                continue

            click.echo('%s: %s' % (name, archiver))

    finally:
        archiver.close()

    if not complete:
        exit(1)

