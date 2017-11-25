# -*- coding: utf-8 -*-
#
# NewsReap Database CLI Plugin
#
# Copyright (C) 2015-2016 Chris Caron <lead2gold@gmail.com>
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
from os import listdir
from os import unlink
from os.path import abspath
from os.path import dirname
from os.path import join
from os.path import isfile
from sqlalchemy.exc import OperationalError
try:
    from newsreap.objects.nntp.Server import Server

except ImportError:
    # Path
    sys.path.insert(0, dirname(dirname(dirname(dirname(abspath(__file__))))))
    from newsreap.objects.nntp.Server import Server

from newsreap.objects.nntp.Group import Group

from newsreap.NNTPSettings import SQLITE_DATABASE_EXTENSION
from newsreap.Utils import find
from newsreap.Utils import pushd
from newsreap.Utils import bytes_to_strsize

# Logging
import logging
from newsreap.Logging import NEWSREAP_CLI
logger = logging.getLogger(NEWSREAP_CLI)

NEWSREAP_CLI_PLUGINS = {
    # format:
    # cli short hand group: function prefix
    'db': {
        'prefix': 'database',
        'desc': 'Database management',
    },
}


def __db_prep(ctx):
    """
    A generic database initialization
    """

    session = ctx['NNTPSettings'].session()
    if not session:
        logger.error('Could not acquire a database connection.')
        exit(1)

    changes = False
    # Get Group Listings for all of our servers
    for s in ctx['NNTPSettings'].nntp_servers:
        # get our server (if it's kept in the database)
        server = session.query(Server)\
            .filter(Server.host == s['host']).first()

        if not server:
            # Add it if it doesn't exist
            session.add(Server(
                # The name field is for display purposes only; for now we just
                # use the hostname.
                name=s['host'],

                # Define our host
                host=s['host'],
            ))

            # Toggle change flag
            changes = True

    if changes:
        session.commit()


# Define our functions below
# all functions are prefixed with what is identified
# above or they are simply ignored.
@click.command(name='optimize')
@click.pass_obj
def database_optimize(ctx):
    pass


@click.command(name='init')
@click.pass_obj
def database_init(ctx):
    """
    Initializes the database if it's not already
    """
    ctx['NNTPSettings'].open(reset=False)
    __db_prep(ctx)


@click.command(name='reset')
@click.pass_obj
def database_reset(ctx):
    """
    Reset's the database based on the current configuration
    """
    logger.info('Resetting database ...')
    ctx['NNTPSettings'].open(reset=True)
    __db_prep(ctx)

    db_path = join(ctx['NNTPSettings'].base_dir, 'cache', 'search')
    logger.debug('Scanning %s for databases...' % db_path)
    with pushd(db_path, create_if_missing=True):
        for entry in listdir(db_path):
            db_file = join(db_path, entry)
            if not isfile(db_file):
                continue

            try:
                unlink(db_file)
                logger.info('Removed %s ...' % entry)
            except:
                logger.warning('Failed to remove %s ...' % entry)


@click.command(name='status')
@click.pass_obj
def database_status(ctx):
    """
    displays details on the current database store
    """
    db_path = join(ctx['NNTPSettings'].work_dir, 'cache', 'search')
    logger.debug('Scanning %s for databases...' % db_path)
    with pushd(db_path, create_if_missing=True):
        results = find(
            db_path,
            suffix_filter=SQLITE_DATABASE_EXTENSION,
            fsinfo=True,
            max_depth=1,
        )

    # Use our Database first if it exists
    session = ctx['NNTPSettings'].session()
    if not session:
        logger.error('Could not acquire a database connection.')
        exit(1)

    # PEP8 E712 does not allow us to make a comparison to a boolean value
    # using the == instead of the keyword 'in'.  However SQLAlchemy
    # requires us to do just because that's how the amazing tool works.
    # so to get around the pep8 error, we'll just define a variable equal
    # to True and then we can compare to it
    pep8_e712 = True

    try:
        # Get a list of watched groups
        groups = dict(session.query(Group.name, Group.id)
                      .filter(Group.watch == pep8_e712).all())

    except OperationalError:
        # Get a list of watched groups
        logger.warning('The database does not appear to be initialized.')
        logger.info('Try running: "nr db init" first.')
        exit(0)

    if not len(results):
        logger.info('There are no groups configured to be watched.')
        exit(0)

    for _, meta in results.iteritems():
        # Open up the database
        flags = ''
        if meta['filename'] in groups:
            flags += 'W'

        print('%-65s %-10s %s' % (
            meta['filename'],
            bytes_to_strsize(meta['size']),
            flags,
        ))
