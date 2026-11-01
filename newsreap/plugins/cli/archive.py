# NewsReap Archive CLI Plugin
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

# Reads the articles 'nr update archive' kept:
#   nr archive show <message-id> [<message-id> ...]
#
# Find them with 'nr search --posts <keywords>'.

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

from newsreap.NNTPArchiver import NNTPArchiver
from newsreap.Utils import bytes_to_strsize
from newsreap.objects.index.ArchivedPost import ArchiveKind

# initialize our logger
logger = logging.getLogger(NEWSREAP_CLI)

NEWSREAP_CLI_PLUGINS = {
    # format:
    # cli short hand group: function prefix
    'archive': {
        'prefix': 'archive',
        'desc': 'Read the articles kept by nr update archive',
    },
}

# How each kind of post is described
KIND_NAMES = {
    ArchiveKind.Text: 'text',
    ArchiveKind.Binary: 'binary',
    ArchiveKind.Missing: 'missing (no server had it)',
    ArchiveKind.Refused: 'refused by a hook',
}


@click.command(name='show')
@click.argument('message_ids', nargs=-1, required=True)
@click.pass_obj
def archive_show(ctx, message_ids):
    """
    Show archived articles.

    Use the Message-IDs 'nr search --posts' shows (with or without their
    < and >).

    """
    # Track how many we found
    found = 0

    archiver = NNTPArchiver(
        ctx['NNTPSettings'].index_database(), default_hooks=False)
    try:
        for message_id in dict.fromkeys(message_ids):
            post = archiver.get(message_id)
            if post is None:
                logger.error('There is no archived article %s.' % message_id)
                continue

            found += 1
            if found > 1:
                # Keep them apart
                click.echo('')

            click.echo('Subject:    %s' % post.subject)
            click.echo('From:       %s' % post.poster)
            if post.posted:
                click.echo('Date:       %s UTC' % post.posted.strftime(
                    '%Y-%m-%d %H:%M:%S'))
            click.echo('Message-ID: <%s>' % post.message_id)
            click.echo('Groups:     %s' % ', '.join(
                archiver.groups(post.message_id)))
            if post.references:
                click.echo('References: %s' % post.references)
            click.echo('Kind:       %s' % KIND_NAMES.get(post.kind, '?'))
            if post.encoding:
                click.echo('Encoding:   %s' % post.encoding)

            for entry in archiver.files(post.message_id):
                details = [entry.codec]
                if entry.content_type:
                    details.append(entry.content_type)
                if entry.size:
                    details.append(bytes_to_strsize(entry.size))
                if entry.total:
                    details.append('part %s/%s' % (entry.part, entry.total))
                elif entry.part:
                    details.append('part %s' % entry.part)
                if entry.crc32:
                    details.append('crc32 %s' % entry.crc32)

                click.echo('File:       %s (%s)' % (
                    entry.filename or '(no name)', ', '.join(details)))

            if post.text:
                click.echo('')
                click.echo(post.text)

    finally:
        archiver.close()

    if found < len(set(message_ids)):
        exit(1)
