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

try:
    from newsreap.objects.nntp.Server import Server

except ImportError:
    # Path
    sys.path.insert(0, dirname(dirname(dirname(abspath(__file__)))))
    from newsreap.objects.nntp.Server import Server

from newsreap.objects.nntp.GroupTrack import GroupTrack
from newsreap.objects.nntp.Group import Group

from newsreap.NNTPSettings import SQLITE_DATABASE_EXTENSION
from newsreap.Utils import find
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


@click.command(name='reset')
@click.pass_obj
def database_reset(ctx):
    """
    Reset's the database based on the current configuration
    """
    logger.info('Resetting database ...')
    ctx['NNTPSettings'].open(reset=True)

    db_path = join(ctx['NNTPSettings'].cfg_path, 'cache', 'search')
    logger.debug('Scanning %s for databases...' % db_path)
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
    db_path = join(ctx['NNTPSettings'].cfg_path, 'cache', 'search')
    logger.debug('Scanning %s for databases...' % db_path)
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

    # Get a list of watched groups
    groups = dict(session.query(Group.name, Group.id)\
                    .filter(Group.watch==True).all())

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
