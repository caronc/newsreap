# -*- coding: utf-8 -*-
#
# NewsReap NNTP Get CLI Plugin
#
# Copyright (C) 2015-2017 Chris Caron <lead2gold@gmail.com>
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

# get allows you to pull content directly from usenet if you know
# the message-id, or nzbpath
#
# If you add the --inspect flag then only details surrounding what
# you specified is fetched from usenet

import click
import sys

from os.path import abspath
from os.path import dirname
from os.path import isfile
from os.path import isdir

# Logging
import logging
try:
    from newsreap.Logging import NEWSREAP_CLI
except ImportError:
    # Path
    sys.path.insert(0, dirname(dirname(dirname(abspath(__file__)))))
    from newsreap.Logging import NEWSREAP_CLI

logger = logging.getLogger(NEWSREAP_CLI)

# Define our function
NEWSREAP_CLI_PLUGINS = 'get'

@click.command()
@click.pass_obj
@click.option('--group', type=basestring,
              default=None,
              help="Identify the group to reference")
@click.option('--workdir', type=basestring, default=None,
              help="A directory we can manage our fetched content from.")
@click.option('--inspect', default=False, flag_value=True,
              help="Inspect the results only")
@click.argument('sources', nargs=-1)
def get(ctx, group, workdir, inspect, sources):
    """
    Retrieves content from Usenet when provided a NZB-File, A directory
    containing NZB-File(s), and/or a Message-ID
    """
    session = ctx['NNTPSettings'].session()
    if not session:
        logger.error("The database is not correctly configured.")
        exit(1)

    if len(sources) == 0:
        logger.error("You must specify at least one source.")
        exit(1)

    # Link to our NNTP Manager
    mgr = ctx['NNTPManager']

    for source in sources:
        if isfile(source):
            logger.debug("Scanning file '%s'." % (source))
            # TODO: Deal with NZB-File
            pass

        elif isdir(source):
            logger.debug("Scanning directory '%s'." % (source))
            # TODO: Deal with a directory containing NZB-File(s)
            pass

        else:
            logger.debug("Retrieving Message-ID '%s'." % (source))
            # Download content by its Message-ID
            if not inspect:
                mgr.get(source, work_dir=workdir, group=group)
            else:
                # Acquire our response
                response = mgr.stat(source, full=True, group=group)
                if response:
                    print('****')
                    for k,v in response.iteritems():
                        print('%s: %s' % (k,v))

