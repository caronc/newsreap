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
# If you add the --headers flag then only details surrounding what
# you specified is fetched from usenet

import click
import sys

from os.path import abspath
from os.path import dirname
from os.path import basename
from os.path import splitext
from os.path import isfile
from os.path import join

# Logging
import logging
try:
    from newsreap.Logging import NEWSREAP_CLI
except ImportError:
    # Path
    sys.path.insert(0, dirname(dirname(dirname(abspath(__file__)))))
    from newsreap.Logging import NEWSREAP_CLI

logger = logging.getLogger(NEWSREAP_CLI)

from newsreap.NNTPnzb import NNTPnzb
from newsreap.Utils import hexdump

# Define our function
NEWSREAP_CLI_PLUGINS = 'get'


@click.command()
@click.pass_obj
@click.option('--group', type=basestring,
              default=None,
              help="Identify the group to reference")
@click.option('--workdir', type=basestring, default=None,
              help="A directory we can manage our fetched content from.")
@click.option('--headers', default=False, flag_value=True,
              help="Return header details")
@click.option('--inspect', default=False, flag_value=True,
              help="Inspect the first few bytes of the body only")
@click.argument('sources', nargs=-1)
def get(ctx, group, workdir, headers, inspect, sources):
    """
    Retrieves content from Usenet when provided a NZB-File and/or a Message-ID
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
            logger.debug("Scanning NZB-File '%s'." % (source))

            # Open up our NZB-File
            nzb = NNTPnzb(source)

            if not nzb.is_valid():
                # Check that the file is valid
                logger.warning("Skipping invalid NZB-File '%s'." % (source))
                continue

            if headers or inspect:
                #  Scan each element in our NZB-File
                for article in nzb:
                    for segment in article:
                        if headers:
                            # Inspect our article header
                            response = mgr.stat(
                                segment.msgid(),
                                full=True,
                                group=group,
                            )

                            if response:
                                print('****')
                                print(response.str())

                        if inspect:
                            # Inspect our article body
                            response = mgr.get(
                                segment.msgid(),
                                work_dir=workdir,
                                group=group,
                                max_bytes=128,
                            )

                            if response is None:
                                logger.warning(
                                    "No Response Retrieved (from %s)." % (
                                        segment.msgid()),
                                )
                                continue

                            if response.body:
                                # Display our message body (if one is set)
                                print('****')
                                print(response.body.getvalue().strip())

                            if response and len(response):
                                # Display any binary content:
                                print('****')
                                print(hexdump(response.decoded[0].getvalue()))

                continue

            # If we reach here, we need to download the contents
            # associated with the NZB-File
            if not workdir:
                workdir = join(
                    abspath(dirname(source)),
                    splitext(basename(source))[0],
                )

            response = mgr.get(nzb, work_dir=workdir)

        else:
            logger.debug("Handling Message-ID '%s'." % (source))
            # Download content by its Message-ID

            if headers or inspect:
                if headers:
                    # Inspect our Message-ID only; do not download
                    response = mgr.stat(source, full=True, group=group)
                    if response:
                        print('****')
                        print(response.str())

                if inspect:
                    # Preview our Message by Message-ID only; do not download
                    response = mgr.get(
                        source,
                        work_dir=workdir,
                        group=group,
                        max_bytes=128,
                    )
                    if response is None:
                        logger.warning(
                            "No Response Retrieved (from %s)." % (
                                source),
                        )
                        continue

                    if response.body:
                        # Display our message body (if one is set)
                        print('****')
                        print(response.body.getvalue().strip())

                    if response and len(response):
                        # Display any binary content:
                        print('****')
                        print(hexdump(response.decoded[0].getvalue()))

                # Move along
                continue

            # If we reach here, we need to download the contents
            # associated with the Message-ID
            if not workdir:
                # Get Default Working Directory
                workdir = basename(source)

            response = mgr.get(source, work_dir=workdir, group=group)
