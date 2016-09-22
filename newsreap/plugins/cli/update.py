# -*- coding: utf-8 -*-
#
# NewsReap NNTP Database Caching Functions
#
# Copyright (C) 2015 Chris Caron <lead2gold@gmail.com>
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
from os.path import join
from os.path import isdir
from os.path import exists
from os import rename
from os import unlink
from os import access
from os import W_OK
from datetime import datetime
from datetime import timedelta
from dateutil.parser import parse

from shutil import copyfile as copy
from shutil import move

from newsreap.objects.nntp.Group import Group
from newsreap.objects.nntp.GroupAlias import GroupAlias
from newsreap.objects.nntp.GroupTrack import GroupTrack
from newsreap.objects.nntp.Server import Server
from newsreap.objects.nntp.Common import get_groups
from newsreap.objects.group.Article import Article
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.exc import IntegrityError

from newsreap.NNTPConnection import XoverGrouping
from newsreap.NNTPGroupDatabase import NNTPGroupDatabase
from newsreap.NNTPConnection import NNTPConnection
from newsreap.NNTPConnectionRequest import NNTPConnectionRequest

from newsreap.Utils import mkdir

# Logging
import logging
from newsreap.Logging import NEWSREAP_CLI
logger = logging.getLogger(NEWSREAP_CLI)

NEWSREAP_CLI_PLUGINS = {
    # format:
    # cli short hand group: function prefix
    'update': {
        'prefix': 'update',
        'desc': 'Cache/Index to Database management',
    },
}

@click.command(name='groups')
@click.pass_obj
def update_groups(ctx):
    """
    Cache all Usenet groups.

    The following function queries all servers defined and caches their
    statistics to the database for caching (speed).  This function can be
    called as often as you wish to re-caches the data in the database.

    """

    # Empty Results Set
    results = []

    # Use our Database first if it exists
    session = ctx['NNTPSettings'].session()
    if not session:
        logger.error('Could not acquire a database connection.')
        exit(1)

    # Get Group Listings for all of our servers
    for s in ctx['NNTPSettings'].nntp_servers:
        # get our server (if it's kept in the database)
        server = session.query(Server)\
            .filter(Server.host == s['host'])\
            .filter(Server.port == s['port']).first()

        if not server:
            continue

        # apply our groups
        con = NNTPConnection(**s)
        results = con.groups()
        if not results:
            continue

        # First make sure we've got the group added
        for r in results:
            group = session.query(Group)\
                .filter(Group.name == r['group']).first()

            # Convert flag list back into a string
            flags = ''.join(r['flags'])

            if not group:
                # Add it if not
                group = Group(name=r['group'], count=r['count'], flags=flags)
                session.add(group)
                continue

            else:
                # Update flags if nessisary
                session.query(Group)\
                        .filter(Group.name == r['group'])\
                        .update({
                            Group.count: r['count'],
                            Group.flags: flags,
                        })

            # Using our group; update any tracking settings (if present)
            # we don't care if we can't.
            session.query(GroupTrack)\
                .filter(GroupTrack.group_id == group.id)\
                .filter(GroupTrack.server_id == server.id).update({
                    GroupTrack.high: r['high'],
                    GroupTrack.low: r['low'],
                })

        session.commit()
    return


# Define our functions below
# all functions are prefixed with what is identified
# above or they are simply ignored.
@click.command(name='search')
@click.argument('groups', nargs=-1)
@click.option('--date-from', '-f', help='Date From')
@click.option('--date-to', '-t', help='Date To')
@click.option('--watched', '-w', is_flag=True, help='All watched groups.')
@click.pass_obj
def update_search(ctx, groups, date_from, date_to, watched):
    """
    Cache specified articles.

    Articles are cached into their own database due to the sheer size of
    the content within each group.

    """
    # TODO: Support loading by date ranges (from and to)
    # TODO: Support loading by X articles from front or X articles from back
    # TODO: Support loading data by X days back
    # TODO: Support loading data by X days back
    # TODO: Support specifing how many entries to process. Hence if someone
    #       only does a --count=1 (or -c), then only 1 batch is processed.
    #       Support specifying the batch sizes otherwise we use the config
    #       file which is already in place --batch (or -b).
    # TODO: GroupTrack needs to be smarter and not block until the fetch
    #       is repeated on a failure. Each batch loaded should update the
    #       main database and track it's successful fetch. If it's fetch
    #       runs into another, then the 2 tables can be combined into a larger
    #       one.
    #
    #       GroupIndex example:
    #           The below is what the table might look like; you can see we
    #           successfully loaded 100 to 199, and 300 to 399
    #           a.b.test.group:
    #               <id>  <low>   <high>
    #                 1    100      199
    #                 1    300      399
    #
    #           If we fill the void (200 to 299), the table should restructure
    #           itself to look like this:
    #               <id>  <low>   <high>
    #                 1    100      399
    #
    #           Basically the more entries in this table, the more holes/gaps
    #           we have, but we can use this to adjust our batches when we
    #           collide with content that is already fetched.  If a reset
    #           switch is specified (or a reset is detected because the
    #           database is missing, then this table should be included in
    #           the reset too!!)
    #
    # TODO: Support a --force (-f) switch which forces a re-fetch of the
    #       specified ranges defined that override the GroupIndex table.
    #
    # Use our Database first if it exists
    session = ctx['NNTPSettings'].session()
    if not session:
        logger.error('Could not acquire a database connection.')
        exit(1)

    if not len(ctx['NNTPSettings'].nntp_servers) > 0:
        logger.error("There are no servers defined.")
        exit(1)


    if date_from:
        try:
            date_from = parse(date_from, fuzzy=True)

        except TypeError:
            logger.error(
                "An invalid from date/time was specified: %s" % str(date_from),
            )
            exit(1)

    if date_to:
        try:
            date_to = parse(date_to, fuzzy=True)

        except TypeError:
            logger.error(
                "An invalid to date/time was specified: %s" % str(date_to),
            )
            exit(1)


    if date_to and date_from and date_from > date_to:
        logger.error(
            "The from date can not be larger then the to date.",
        )
        exit(1)

    # Store Primary server
    s = ctx['NNTPSettings'].nntp_servers[0]
    try:
        _server = session.query(Server)\
            .filter(Server.host == s['host'])\
            .filter(Server.port == s['port']).first()

    except (InvalidRequestError, OperationalError):
        # Database isn't set up
        logger.error("The database is not correctly configured.")
        exit(1)

    if not _server:
        logger.error("Server entry is not in the database.")
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

    for name, _id in groups.iteritems():

        db_path = join(ctx['NNTPSettings'].cfg_path, 'cache', 'search')
        db_file = '%s.db' % join(db_path, name)
        if not isdir(db_path):
            if not mkdir(db_path):
                logger.error("Failed to create directory %s" % db_path)
                exit(1)
            logger.info("Created directory %s" % db_path)

        if not access(db_path, W_OK):
            logger.error('The directory "%s" is not accessible.' % db_path)
            exit(1)

        reset = not exists(db_file)

        ram_db_file = None
        if ramdisk:
            # Create a ramdisk db
            ram_db_file = '%s.db' % join(ramdisk, name)

            # Remove the existing file if it's there
            try:
                unlink(ram_db_file)

            except OSError:
                # No problem; the file just doesn't already exist
                pass

            engine = 'sqlite:///%s' % ram_db_file

            if not reset:
                # Database exists, and ramdisk exists, and we're not
                # reseting anything... copy existing database onto
                # ramdisk for processing
                logger.debug(
                    'Transfering %s database to ramdisk.' % (
                        name,
                ))
                copy(db_file, ram_db_file)
                logger.info(
                    'Transfered %s database to ramdisk.' % (name),
                )
        else:
            engine = 'sqlite:///%s' % db_file


        db = NNTPGroupDatabase(engine=engine, reset=reset)
        group_session = db.session()
        if not group_session:
            logger.warning("The database %s not be accessed." % db_file)
            continue

        # TODO:
        # Get current index associated with our primary group so we can
        # begin fetching from that point.  The index "MUST" but the one
        # associated with our server hostname. If one doesn't exist; create
        # it initialized at 0
        logger.debug('Retrieving information on group %s' % (name))
        gt = session.query(GroupTrack)\
                .filter(GroupTrack.group_id==_id)\
                .filter(GroupTrack.server_id==_server.id).first()

        if gt is None:
            # Not found
            logger.error('Failed to retrieve information on group %s' % (name))
            continue

        logger.info('Successfully retrieved information on group %s' % (name))

        # Initialize our high/low variables
        low = gt.high
        high = gt.low

        if not gt or reset:
            # Get an connection to work with
            con = ctx['NNTPManager'].get_connection()

            _, low, high, _ = con.group(name)
            if low is None:
                # Could not set group
                logger.warning("Could not access group '%s' on '%s'." % (
                    name,
                    _server.host,
                ))
                continue

            # Create a GroupTrack object using the group info
            gt = GroupTrack(
                group_id=_id,
                server_id=_server.id,
                low=low,
                high=high,
                scan_pointer=low,
                index_pointer=low,
            )
            group_session.commit()
            session.add(gt)

        # starting pointer
        cur = gt.scan_pointer + 1

        requests = []
        if date_to:
            requests.append(
                ctx['NNTPManager'].\
                    seek_by_date(
                        date_to+timedelta(seconds=1), group=name, block=False))
            # Mark our item
            requests[-1]._watermark = 'high'

        if date_from:
            requests.append(
                ctx['NNTPManager'].\
                    seek_by_date(
                        date_from, group=name, block=False))
            # Mark our item
            requests[-1]._watermark = 'low'

        while len(requests):
            # Wait for requeest to complete
            requests[-1].wait()

            # we have a request at this point
            request = requests.pop()
            if not request:
                continue

            # Store our watermark so we update the correct entry
            watermark = request._watermark

            # Retrieve our response
            response = request.response.pop()
            if response is None:
                # We got an error in our response; take an early
                # exit for now
                logger.error(
                    'An unhandled server response was received: %s.' % (
                        response,
                ))

            # Store our watermark (high/low)
            if watermark == 'low':
                low = response
                # Store our current pointer at the starting point we found
                cur = low + 1

            elif watermark == 'high':
                high = response

        if high <= cur:
            # Skip
            continue

        # Drop all indexes; this makes inserts that much faster
        # TODO: make the header_batch_size a entry in NNTPSettings since it's
        # so powerful and allows pulling down multiple things at once
        # Retrieve a list of articles from the database in concurrent blocks
        # Scan them and place them into the NNTPGroupDatabase()
        batch_size = ctx['NNTPSettings']\
                .nntp_processing.get('header_batch_size', 5000)

        logger.info('Fetching from %d to %d [%d article(s)]' % (
                    cur, high, (high-cur+1),
        ))
        # Initialize our batch
        batch = list()

        # Parse the Database URL
        db_url = db.parse_url()

        if db_url['schema'].lower() == 'sqlite':
            # db_url['path'] contains the full path to the database file
            logger.info('Optimizing update for an SQLite database.')
            # SQLite Speed changes
            db._engine.execute('PRAGMA journal_mode = MEMORY')
            db._engine.execute("PRAGMA temp_store = MEMORY")
            db._engine.execute('PRAGMA synchronous = OFF')
            # 2 GB of RAM used for Caching for speed
            db._engine.execute('PRAGMA cache_size = 2000000')

        # we'll re-add them later
        for index in Article.__table__.indexes:
            try:
                index.drop(bind=db._engine)
                logger.info('Dropping Article Index "%s"' % index.name)

            except OperationalError:
                # The index is probably already dropped
                pass

        while high > cur:
            # Figure out our bach size
            inc = min(batch_size-1, high-cur)
            logger.debug('Pushing XOVER batch %d-%d (inc=%d)' % (
                cur, cur+inc, inc+1,
            ))

            # Prepare our batch list
            batch.append((cur, cur+inc, ctx['NNTPManager'].xover(
                group=name, start=cur, end=cur+inc,
                sort=XoverGrouping.BY_ARTICLE_NO,
                block=False,
            )))

            # Increment our pointer
            cur += inc + 1

        # Reverse sort the list since we know the first items pushed will be
        # the first ones completed. we want to pop items from the batch in the
        # same order we pushed them on:
        #       batch = list(reversed(batch))

        # The below is faster than the above for reversing a list then using
        # the reversed() function (and does just that: reverses the results)
        batch = batch[::-1]

        logger.info('%d Article batches prepared (batch size=%d).' % (
            len(batch),
            batch_size,
        ))
        # Now we process the entries
        while len(batch):

            # Block until results the oldest item added to the queue
            # (usually the first one to return) is done:
            batch[-1][-1].wait()

            # If we reach here, we've got a request object to work
            # with
            low, high, request = batch.pop()
            if not request:
                continue

            response = request.response.pop()
            if response is None:
                # We got an error in our response; take an early
                # exit for now
                logger.error(
                    'An unhandled server response was received: %s.' % (
                        response,
                ))

                # Reverse our list again
                batch = batch[::-1]
                while len(batch) > 0:
                    _, _, request = batch.pop()
                    request.abort()
                break

            logger.debug(
                'Retrieved (XOVER) batch %d-%d (%d articles).' % (
                    low, high, len(response),
                ))
            # Get the current time for our timer
            cur_time = datetime.now()

            # For output logging
            load_speed = 'fast'

            try:
                # Try the fast way; this will always succeed unless
                # we're dealing with a messed up table
                db._engine.execute(
                    Article.__table__.insert(), [{
                        "message_id": article['id'],
                        "article_no": article['article_no'],
                        "subject": article['subject'],
                        "poster": article['poster'],
                        "size": article['size'],
                        "lines": article['lines'],
                        "date": article['date'],
                        "score": article['score'],
                    } for article in response.itervalues()]
                )

            except (OperationalError, IntegrityError):
                logger.debug('Preparing for a slow load of %d items' % \
                             len(response))
                for article in response.itervalues():
                    # Store our batch into the database and update
                    # our pointer
                    try:
                        group_session.merge(Article(
                            message_id=article['id'],
                            article_no=article['article_no'],
                            subject=article['subject'],
                            poster=article['poster'],
                            size=article['size'],
                            lines=article['lines'],
                            posted_date=article['date'],
                            score=article['score'],
                        ))

                    except OperationalError, e:
                        logger.error(
                            'A database operational error occured.'
                        )
                        logger.debug('Exception: %s' % str(e))
                        exit(1)

                    except TypeError, e:
                        logger.error(
                            'Failed to save article: %s.' % \
                            str(article),
                        )
                        logger.debug('Exception: %s' % str(e))
                        exit(1)

                group_session.commit()
                load_speed = 'slow'

            # Update our marker
            # TODO: Do NOT update the marker if we have a ramdisk; in that
            #       case it needs to be updated 'after' the batch has
            #       completed.
            session.query(GroupTrack)\
                .filter(GroupTrack.group_id==_id)\
                .filter(GroupTrack.server_id==_server.id)\
                    .update({
                        GroupTrack.scan_pointer: high,
                        GroupTrack.last_scan: datetime.now(),
                    })

            # Save this now as it allows for Cntrl-C or aborts
            # To take place and we'll resume from where we left off
            session.commit()

            # Calculate Processing Time
            delta_time = datetime.now() - cur_time
            delta_time = (delta_time.days * 86400) + delta_time.seconds \
                         + (delta_time.microseconds/1e6)
            logger.info(
                'Cached %d article(s) in %s sec(s) [mode=%s, remaining=%d].' % (
                    len(response),
                    delta_time,
                    load_speed,
                    len(batch),
            ))

        # Recrete all indexes
        for index in Article.__table__.indexes:
            #if index.name.startswith('ix_'):
                # We want to avoid the primary key
            #db._engine
            try:
                index.create(bind=db._engine)
                logger.info('Recreated Article Index "%s"' % index.name)
            except OperationalError:
                # The index has probably already been recreated
                pass

        # Close our database content
        group_session.close()

        if ramdisk:
            # Move content back as a .new extension
            _new_db_file = '%s.new' % db_file
            # Move existing database to .old extension
            _old_db_file = '%s.old' % db_file
            try:
                unlink('%s.new' % db_file)
            except OSError:
                # File doesn't exist; no problem
                pass

            # TODO: Add try/catch blocks and handle cases where we can't move
            # our new database in place.

            # Move new database into place
            logger.debug(
                'Transfering %s database to local storage.' % (name),
            )
            move(ram_db_file, _new_db_file)
            logger.info(
                'Transfered %s database to local storage.' % (name),
            )
            # Rename existing database to old (for fall back)
            rename(db_file, _old_db_file)
            # Place new database into place
            rename(_new_db_file, db_file)
            # Safely remove the old database
            unlink(_old_db_file)


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

    if watched:
        _groups = session.query(Group.name)\
                    .filter(Group.watch==True).all()
        if not _groups:
            logger.error("There are no current groups being watched.")
            exit(1)
        groups = set(groups) | set([ g[0] for g in _groups ])

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

        _id = session.query(Group.id).filter(Group.name==_group).first()
        if not _id:
            # No problem; let us use the alias too
            _id = session.query(Group.id).join(GroupAlias)\
                    .filter(GroupAlias.name==_group).first()
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
