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
#   nr nzb import <nzb file> [<nzb file> ...] [--merge]
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
from newsreap.CliOptions import matching
from newsreap.CliOptions import ranked
from newsreap.NNTPIndexer import NNTPIndexer

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
@click.option('--case-sensitive', '-c', is_flag=True,
              help='Keywords must match the case too.')
@ranked
@matching
@click.pass_obj
def nzb_list(ctx, keywords, groups, complete, forming, limit,
             case_sensitive, rank, terms):
    """
    List collections (newest first).

    Every keyword must be in a collection's name or one of its file
    subjects (case doesn't matter unless --case-sensitive is given); put a
    dash in front of a keyword to leave out the ones that have it:

        nr nzb list holiday --group photos -- -2019

    See 'nr search' for more ways to search.

    """
    # The groups we're narrowing it down to (if any)
    names = lookup_groups(ctx, groups) if groups else None
    for term in terms:
        if term.field == 'body':
            logger.error("--%s only works with 'nr search --posts'." % (
                term.option))
            exit(1)

    indexer = open_index(ctx)
    try:
        if indexer.session() is None:
            click.echo(
                "Nothing indexed yet; run 'nr update index' first.")
            return

        for entry in indexer.find(
                keywords, groups=names, complete=complete, forming=forming,
                limit=limit, case_sensitive=case_sensitive, terms=terms,
                **rank):
            click.echo(indexer.summary(entry))

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
@click.option('--merge', is_flag=True,
              help='Replace the collections that share its articles with '
                   'it; their details (ie: passwords) are kept unless it '
                   'gives new ones.')
@click.pass_obj
def nzb_import(ctx, paths, name, merge):
    """
    Add NZB files to the index as collections.

    An NZB file is the real post (not our best guess from scanning), so it
    takes over any scanned collection that has its articles.  It is often
    the only way to put together a post spread over many groups with made
    up file names.  A post already imported isn't added twice.

    An article only belongs to one collection; the first NZB file imported
    keeps it.  Another NZB file of the same post only adds the details it
    has that we don't (ie: another password).  With --merge it replaces
    what we had instead: its files become the collection's (extra par2
    files, or bloat, are added; files it doesn't list are let go of) and
    the details we had are kept unless it gives new ones.

    """
    # Track whether every file could be read
    failed = 0

    indexer = open_index(ctx)
    try:
        for path in paths:
            entry, added = indexer.import_nzb(path, name=name, merge=merge)
            if entry is None:
                logger.error("Could not import '%s'." % path)
                failed += 1
                continue

            if indexer.merging:
                status = 'merged: %d file(s) added, %d dropped' % (
                    indexer.merged, indexer.dropped)
                if indexer.replaced:
                    status += '; replaced %s' % ', '.join(
                        '#%d' % i for i in indexer.replaced)

            elif added:
                status = 'imported'

            else:
                status = 'already indexed'

            if indexer.details:
                status += ', %d detail(s) added' % indexer.details

            click.echo("[%6d] %s (%s)" % (entry.id, entry.name, status))

            if indexer.left_out:
                logger.warning(
                    "'%s' lists %d file(s) collection #%d doesn't have; "
                    "use --merge to add them:" % (
                        path, len(indexer.left_out), entry.id))
                for subject in indexer.left_out:
                    logger.warning('  %s' % subject)

        # Scanned collections that lost articles to an NZB file form again
        indexer.settle()

    finally:
        indexer.close()

    if failed:
        exit(1)
