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
from dateutil.parser import parse

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
from newsreap.objects.nntp.GroupAlias import GroupAlias
from newsreap.objects.nntp.GroupTrack import GroupTrack
from newsreap.objects.nntp.Server import Server
from newsreap.objects.nntp.Common import get_groups

from newsreap.NNTPConnection import NNTPConnection
from newsreap.NNTPGroupScanner import NNTPGroupScanner
from newsreap.NNTPGroupScanner import DEFAULT_BATCH_SIZE

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
    Turns a date the user gave us into a datetime (exits if it can't)

    - value: the text given (ie: 2024, 2024.02, 2024-02-01 10:00)
    - name: the option it came from (for the error message)
    """
    try:
        return parse(value, fuzzy=True)

    except (ValueError, OverflowError, TypeError):
        logger.error("An invalid %s date was specified: %s" % (name, value))
        exit(1)


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
              help='Only articles posted on or after this date.')
@click.option('--date-to', '-t',
              help='Only articles posted on or before this date.')
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

    # Prepare our dates
    if date_from:
        date_from = parse_date(date_from, 'from')

    if days:
        # The last X days
        since = datetime.now() - timedelta(days=days)
        date_from = max(date_from, since) if date_from else since

    if date_to:
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


# Define our functions below
# all functions are prefixed with what is identified
# above or they are simply ignored.
@click.command(name='index')
@click.argument('groups', nargs=-1)
@click.option('--watched', '-w', is_flag=True, help='All watched groups.')
@click.pass_obj
def update_index(ctx, groups, watched):
    """
    TODO Updating Indexes (Generate NZBFiles).

    If a group(s) is/are specified on the command line, then just those
    are indexed as well.

    """
    session = ctx['NNTPSettings'].session()
    if not session:
        logger.error("The database is not correctly configured.")
        exit(1)

    # Track our database updates
    pending_commits = 0

    # PEP8 E712 does not allow us to make a comparison to a boolean value
    # using the == instead of the keyword 'in'.  However SQLAlchemy
    # requires us to do just because that's how the amazing tool works.
    # so to get around the pep8 error, we'll just define a variable equal
    # to True and then we can compare to it
    pep8_e712 = True

    if watched:
        _groups = session.query(Group.name)\
                         .filter(Group.watch == pep8_e712).all()
        if not _groups:
            logger.error("There are no current groups being watched.")
            exit(1)
        groups = set(groups) | set([g[0] for g in _groups])

    if not groups:
        logger.error("There were not groups identified for indexing.")
        exit(1)

    # Maintain a list of completed groups; This allows us to not
    # parse a group twice
    completed = list()

    for group in groups:
        _group = group.lower().strip()
        if not _group:
            continue

        _id = session.query(Group.id).filter(Group.name == _group).first()
        if not _id:
            # No problem; let us use the alias too
            _id = session.query(Group.id)\
                         .join(GroupAlias)\
                         .filter(GroupAlias.name == _group).first()
            if not _id:
                logger.warning("The group '%s' does not exist." % group)
                continue

        if _id in completed:
            # We've indexed this group
            continue

        # TODO Index Group based on It's placeholders in GroupTrack

        # Append to completed list (this prevents us from processing entries
        # twice)
        completed.append(_id)

    if pending_commits > 0:
        # commit our results
        session.commit()
