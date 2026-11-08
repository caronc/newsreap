# NewsReap Delete CLI Plugin
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

# Removes collections 'nr update' found (the ones you don't want to
# see again):
#   nr delete <id|gid> [<id|gid> ...]
#
# Everything kept of its articles goes with it: their cached headers, the
# posts the archive kept, and their scores and tags.  The collection is
# remembered as deleted, so articles that belong to it (or an NZB file of
# it) don't bring it back.

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

from newsreap.CliOptions import confirmed
from newsreap.NNTPIndexer import NNTPIndexer
from newsreap.NNTPIndexer import Purged

# initialize our logger
logger = logging.getLogger(NEWSREAP_CLI)

NEWSREAP_CLI_PLUGINS = {
    # format:
    # cli short hand group: function prefix
    'delete': {
        'prefix': 'delete',
        'desc': 'Remove indexed collections',
    },
}


@click.command(name='delete')
@click.argument('ids', nargs=-1, required=True)
@click.pass_obj
def delete(ctx, ids):
    """
    Remove indexed collections.

    Use the ids 'nr nzb ls' shows (or a collection's GID).  Everything kept
    of their articles goes too: the cached headers, the posts kept
    offline, and their scores and tags.

    """
    # Track how many we removed, and what else went with them
    removed = 0
    purged = Purged(0, 0, 0, 0, 0)

    indexer = NNTPIndexer(ctx['NNTPSettings'].index_database())
    try:
        # What would go, shown before anything is removed
        found = {}
        for value in dict.fromkeys(ids):
            entry = indexer.get(value) if indexer.session() is not None \
                else None
            if entry is None:
                logger.error('There is no collection %s.' % value)
                continue
            found[entry.id] = entry.name

        for collection_id, title in found.items():
            click.echo('[%d] %s' % (collection_id, title))

        if found and not confirmed(
                ctx, 'Remove %d collection(s)?' % len(found)):
            click.echo('Nothing was removed.')
            return

        for collection_id, title in found.items():

            if indexer.delete(collection_id, group_database=ctx[
                    'NNTPSettings'].group_database):
                click.echo('Removed [%d] %s' % (collection_id, title))
                removed += 1
                purged = purged.plus(indexer.purged._replace(
                    articles=0, files=0, collections=0))

            else:
                logger.error(
                    'Could not remove collection %d.' % collection_id)

    finally:
        indexer.close()

    if str(purged):
        click.echo('Also removed %s.' % (purged, ))

    if removed < len(set(ids)):
        exit(1)
