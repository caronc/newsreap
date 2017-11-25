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
# If you add the --inspect flag then you'll peak at the first few
#   bytes of the file(s)

import logging
import click
import sys

from os.path import abspath
from os.path import dirname
from os.path import basename
from os.path import splitext
from os.path import isfile
from os.path import join

try:
    from newsreap.Logging import NEWSREAP_CLI
except ImportError:
    # Path
    sys.path.insert(0, dirname(dirname(dirname(dirname(abspath(__file__))))))
    from newsreap.Logging import NEWSREAP_CLI

from newsreap.NNTPnzb import NNTPnzb
from newsreap.Mime import Mime
from newsreap.NNTPGetFactory import NNTPGetFactory

# initialize our logger
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
@click.option('--headers', default=False, flag_value=True,
              help="Return header details")
@click.option('--inspect', default=False, flag_value=True,
              help="Inspect the first few bytes of the body only")
@click.option('--hooks', '-k', default=None, type=str,
              help='Specify one or more hooks to load')
@click.argument('sources', nargs=-1)
def get(ctx, group, workdir, headers, inspect, sources, hooks):
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

    # Initialize our GetFactory
    gf = NNTPGetFactory(connection=mgr, hooks=hooks, groups=group)

    # initialize our return code to zero (0) which means okay
    # but we'll toggle it if we have any sort of failure
    return_code = 0

    for source in sources:

        if not gf.load(source):
            return_code = 1
            continue

        if not (headers or inspect):
            # We're just here to fetch content
            if not gf.download():
                # our download failed
                return_code = 1

            # Move on
            continue

        if inspect:
            # inspect will pull headers as well
            response = gf.inspect()
            if not response:
                # our retrieval failed; this is the case if we had a problem
                # communicating with our server.  Successfully connecting by
                # finding out the article simply doesn't exist does not cause
                # headers() to fail.
                return_code = 1
                continue

        elif headers:
            # We just want to retrive our headers
            response = gf.headers()
            if not response:
                # our retrieval failed; this is the case if we had a problem
                # communicating with our server.  Successfully connecting by
                # finding out the article simply doesn't exist does not cause
                # headers() to fail.
                return_code = 1
                continue

        # Print our response
        print(gf.str(headers=headers, inspect=inspect))

    # return our return code
    exit(return_code)
