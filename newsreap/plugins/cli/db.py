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
import sqlite3

import sys
from os import listdir
from os import unlink
from os.path import abspath
from os.path import dirname
from os.path import join
from os.path import isdir
from os.path import isfile

try:
    from newsreap.CliOptions import confirmed

except ImportError:
    # Path
    sys.path.insert(0, dirname(dirname(dirname(dirname(abspath(__file__))))))
    from newsreap.CliOptions import confirmed

from newsreap.plugins.cli.status import show_details
from newsreap.Database import DatabaseUnavailable
from newsreap.Database import sqlite_file
from newsreap.Database import sqlite_optimize
from newsreap.NNTPSettings import SQLITE_DATABASE_EXTENSION
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
    Adds the servers in our configuration that the database doesn't have
    yet
    """
    settings = ctx['NNTPSettings']
    try:
        settings.add_servers(s['host'] for s in settings.nntp_servers)

    except DatabaseUnavailable:
        logger.error('Could not acquire a database connection.')
        exit(1)


# Define our functions below
# all functions are prefixed with what is identified
# above or they are simply ignored.
@click.command(name='optimize')
@click.pass_obj
def database_optimize(ctx):
    """
    Compact the SQLite databases and freshen what they know of their tables.

    The settings database, the index (when it is an SQLite file) and every
    group's header cache are compacted (VACUUM), giving back the space of
    what was removed, and analysed (ANALYZE) so searches stay quick.  Run
    it when nothing else is using them; one that is busy is skipped.
    """
    settings = ctx['NNTPSettings']
    paths = [sqlite_file(settings.nntp_database.get('engine')),
             sqlite_file(settings.index_database())]

    folder = join(settings.cache_dir, 'search')
    if isdir(folder):
        paths.extend(join(folder, name) for name in sorted(listdir(folder))
                     if name.endswith(SQLITE_DATABASE_EXTENSION))

    saved = 0
    failed = False
    for path in dict.fromkeys(p for p in paths if p and isfile(p)):
        try:
            before, after = sqlite_optimize(path)

        except (sqlite3.Error, OSError) as e:
            logger.warning('Skipped %s (%s).' % (path, e))
            failed = True
            continue

        saved += max(0, before - after)
        click.echo('%s: %s -> %s' % (
            path, bytes_to_strsize(before), bytes_to_strsize(after)))

    click.echo('%s given back.' % bytes_to_strsize(saved))
    if failed:
        exit(1)


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
    Start the settings database and the header caches over.

    Every server, group, alias and watched group is forgotten, along with
    how far each group was fetched, and every cached header is removed.
    The index (collections and kept posts) is left alone; 'nr update
    groups' fills the groups in again.  You are asked first (-y answers
    yes).
    """
    if not confirmed(
            ctx, 'Forget every group, alias and watched group, and remove '
                 'every cached header?'):
        click.echo('Nothing was removed.')
        return

    logger.info('Resetting database ...')
    ctx['NNTPSettings'].open(reset=True)
    __db_prep(ctx)

    db_path = join(ctx['NNTPSettings'].cache_dir, 'search')
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
    Show everything kept so far, in numbers.

    \b
    - how big each database is
    - how much went to and from each server, and how fast: today,
      yesterday, the last 7 days and every day kept, next to a usual day
      (a warning shows when today is much slower, or fails much more)
    - what the index holds (collections, files, kept posts)
    - every group with cached headers (or that is watched): its headers,
      cache size, collections and archived posts, and when it was last
      scanned and indexed (W marks a watched group)

    See 'nr status' for the setup, and 'nr group ls' for the groups and
    their settings.
    """
    try:
        show_details(ctx['NNTPSettings'])

    except DatabaseUnavailable:
        click.echo(
            "The database isn't set up yet; run 'nr db init' first.")
        exit(1)
