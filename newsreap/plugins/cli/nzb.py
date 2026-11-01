# NewsReap NZB CLI Plugin
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

# Lists the collections 'nr update index' found, saves them as NZB files and
# adds NZB files to the index:
#   nr nzb list [keywords] [--group <group|alias>]
#   nr nzb save <id|gid> [<id|gid> ...] [--output <file or directory>]
#   nr nzb import <nzb file> [<nzb file> ...]
#
# Collections can span many groups; a group (or alias) only narrows down
# what is listed.

import logging
import click
import re
import sys

from os.path import abspath
from os.path import dirname
from os.path import exists
from os.path import isdir
from os.path import join

try:
    from newsreap.Logging import NEWSREAP_CLI
except ImportError:
    # Path
    sys.path.insert(0, dirname(dirname(dirname(dirname(abspath(__file__))))))
    from newsreap.Logging import NEWSREAP_CLI

from newsreap.objects.nntp.Common import get_groups
from newsreap.NNTPIndexer import NNTPIndexer
from newsreap.Utils import bytes_to_strsize

# initialize our logger
logger = logging.getLogger(NEWSREAP_CLI)

NEWSREAP_CLI_PLUGINS = {
    # format:
    # cli short hand group: function prefix
    'nzb': {
        'prefix': 'nzb',
        'desc': 'List, save and import collections (as NZB files)',
    },
}

# Characters we don't put in the NZB file names we make up
UNSAFE_FILENAME_RE = re.compile(r'[^A-Za-z0-9._+\- \[\]()]+', re.ASCII)


def lookup_groups(ctx, groups):
    """
    Returns the names of the groups the aliases or group names given refer
    to (exits if any of them don't exist)
    """
    session = ctx['NNTPSettings'].session()
    if not session:
        logger.error("The database is not correctly configured.")
        exit(1)

    names = set()
    for group in groups:
        found = get_groups(session=session, lookup=group)
        if not found:
            logger.error("The group/alias '%s' does not exist." % group)
            exit(1)

        names.update(found)

    return names


def open_index(ctx):
    """
    Returns an indexer for our index database
    """
    return NNTPIndexer(ctx['NNTPSettings'].index_database())


@click.command(name='list')
@click.argument('keywords', nargs=-1)
@click.option('--group', '-g', 'groups', multiple=True,
              help='Only collections found in this group or alias (can be '
                   'given more than once).')
@click.option('--complete', 'complete', flag_value=True, default=None,
              help='Only collections with every file and segment found.')
@click.option('--incomplete', 'complete', flag_value=False,
              help='Only collections missing a file or segment.')
@click.option('--forming', is_flag=True,
              help='Include collections whose articles are still arriving.')
@click.option('--limit', '-l', type=click.IntRange(min=1),
              help='Show at most this many.')
@click.pass_obj
def nzb_list(ctx, keywords, groups, complete, forming, limit):
    """
    List collections (newest first).

    Every keyword must be in a collection's name (case doesn't matter);
    put a dash in front of a keyword to leave out names that have it:

        nr nzb list holiday --group photos -- -2019

    """
    # The groups we're narrowing it down to (if any)
    names = lookup_groups(ctx, groups) if groups else None

    indexer = open_index(ctx)
    try:
        if indexer.session() is None:
            click.echo(
                "Nothing indexed yet; run 'nr update index' first.")
            return

        for entry in indexer.find(
                keywords, groups=names, complete=complete, forming=forming,
                limit=limit):

            # How much of the collection was found
            percent = 100.0 * entry.segments / entry.segments_expected \
                if entry.segments_expected else 100.0

            files = '%d/%d' % (entry.files, entry.files_expected) \
                if entry.files_expected else '%d' % entry.files

            click.echo('[%6d] %5.1f%% %10s %7s file(s)  %s' % (
                entry.id, min(percent, 100.0),
                bytes_to_strsize(entry.size), files, entry.name))

    finally:
        indexer.close()


@click.command(name='save')
@click.argument('ids', nargs=-1, required=True)
@click.option('--output', '-o',
              help='The NZB file to write (or the directory to write them '
                   'into); defaults to the collection name in the current '
                   'directory.')
@click.option('--force', is_flag=True,
              help='Replace NZB files that already exist.')
@click.pass_obj
def nzb_save(ctx, ids, output, force):
    """
    Save collections as NZB files.

    Use the ids 'nr nzb list' shows (or a collection's GID).

    """
    if output and len(set(ids)) > 1 and not isdir(output):
        logger.error(
            'Saving more than one collection needs --output to be a '
            'directory.')
        exit(1)

    # Track how many we saved
    saved = 0

    indexer = open_index(ctx)
    try:
        for value in dict.fromkeys(ids):
            entry = indexer.get(value)
            if entry is None:
                logger.error('There is no collection %s.' % value)
                continue

            # Work out where it goes
            filename = '%s.nzb' % (
                UNSAFE_FILENAME_RE.sub('_', entry.name).strip() or
                'collection-%d' % entry.id)

            if not output:
                path = filename

            elif isdir(output):
                path = join(output, filename)

            else:
                path = output

            if exists(path) and not force:
                logger.error(
                    "'%s' already exists; use --force to replace it." % path)
                continue

            if indexer.nzb(
                    entry.id, path, work_dir=ctx['NNTPSettings'].work_dir):
                click.echo("Saved '%s'." % path)
                saved += 1

            else:
                logger.error("Could not write '%s'." % path)

    finally:
        indexer.close()

    if saved < len(set(ids)):
        exit(1)


@click.command(name='import')
@click.argument('paths', nargs=-1, required=True,
                type=click.Path(exists=True, dir_okay=False))
@click.option('--name', '-n',
              help='What to call the collection (defaults to the name the '
                   'NZB file gives, or else its file name).')
@click.pass_obj
def nzb_import(ctx, paths, name):
    """
    Add NZB files to the index as collections.

    An NZB file is often the only way to put together a post spread over
    many groups with made up file names.  A post we already have isn't
    added twice.

    """
    # Track whether every file could be read
    failed = 0

    indexer = open_index(ctx)
    try:
        for path in paths:
            entry, added = indexer.import_nzb(path, name=name)
            if entry is None:
                logger.error("Could not import '%s'." % path)
                failed += 1

            elif added:
                click.echo("[%6d] %s (imported)" % (entry.id, entry.name))

            else:
                click.echo("[%6d] %s (already indexed)" % (
                    entry.id, entry.name))

    finally:
        indexer.close()

    if failed:
        exit(1)
