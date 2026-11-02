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

# Reads the articles 'nr update --content' kept (the same as
# 'nr show --local'):
#   nr archive show <message-id> [<message-id> ...]
#
# Find them with 'nr search --posts <keywords>'.

import click
import sys

from os.path import abspath
from os.path import dirname

try:
    from newsreap.plugins.cli.show import DEFAULT_SHOW_LINES
except ImportError:
    # Path
    sys.path.insert(0, dirname(dirname(dirname(dirname(abspath(__file__))))))
    from newsreap.plugins.cli.show import DEFAULT_SHOW_LINES

from newsreap.plugins.cli.show import show

NEWSREAP_CLI_PLUGINS = {
    # format:
    # cli short hand group: function prefix
    'archive': {
        'prefix': 'archive',
        'desc': "Read the articles kept by nr update --content",
    },
}


@click.command(name='show')
@click.argument('message_ids', nargs=-1)
@click.pass_context
def archive_show(ctx, message_ids):
    """
    Show kept articles (the same as 'nr show --local').

    Use the Message-IDs 'nr search --posts' shows (with or without their
    < and >).

    """
    ctx.invoke(show, message_ids=message_ids, local=True,
               lines=DEFAULT_SHOW_LINES, max_size=None)
