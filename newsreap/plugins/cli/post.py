# -*- coding: utf-8 -*-
#
# NewsReap NNTP Posting CLI Plugin
#
# Copyright (C) 2017 Chris Caron <lead2gold@gmail.com>
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

from os.path import abspath
from os.path import dirname

try:
    from newsreap.NNTPPostFactory import NNTPPostFactory

except ImportError:
    # Path
    sys.path.insert(0, dirname(dirname(dirname(abspath(__file__)))))
    from newsreap.NNTPPostFactory import NNTPPostFactory

# Logging
import logging
from newsreap.Logging import NEWSREAP_CLI
logger = logging.getLogger(NEWSREAP_CLI)

NEWSREAP_CLI_PLUGINS = {
    # format:
    # cli short hand group: function prefix
    'post': {
        'prefix': 'post',
        'desc': """
        Group File Posting

        Post files to Usenet

        """,
    },
}


# Define our functions below
# all functions are prefixed with what is identified
# above or they are simply ignored.
@click.command(name='post')
@click.option('--groups', '-g', default=None, type=str,
              help='The group(s) to post content to.')
@click.option('--prep', '-P', default=False, is_flag=True,
              help='pre-prepare our posting content')
@click.option('--stage', '-S', default=False, is_flag=True,
              help='stage our content for posting')
@click.option('--clean', '-C', default=False, is_flag=True,
              help='stage our content for posting')
@click.option('--upload', '-U', default=False, is_flag=True,
              help='upload our staged content to NNTP Server')
@click.option('--verify', '-V', default=False, is_flag=True,
              help='verify that content was uploaded to NNTP Server')
@click.option('--split-size', '-s', default=None, type=str,
              help='The maximum article size before splitting')
@click.option('--archive-size', '-a', default=None, type=str,
              help='The maximum archive size before splitting')
@click.option('--hooks', '-k', default=None, type=str,
              help='Specify some hook(s) to load')
@click.argument('paths', nargs=-1)
@click.pass_obj
def post(ctx, groups, prep, stage, upload, verify, clean, split_size,
         archive_size, paths, hooks):
    """
    Posts content found in specified paths to usenet.

    Each identifed path is treated as a separate post and causes a separate
    NZB-File to be generated from it.  If the specified path is a directory
    then the content within it is posted.

    """
    if not paths:
        logger.error("You must specify at least one path to post.")
        exit(1)

    session = ctx['NNTPSettings'].session()
    if not session:
        logger.error("The database is not correctly configured.")
        exit(1)

    # If all flags are set to False (their default) then this implies
    # we perform all stages
    all_stages = not (prep or stage or upload or verify or clean)
    if all_stages:
        # Set our flags accordingly
        prep = True
        stage = True
        upload = True
        verify = True
        clean = True

    if not split_size:
        # by default use what we have defined
        split_size = ctx['NNTPSettings'].nntp_posting.get('max_article_size')

    if not archive_size:
        # by default use what we have defined
        archive_size = ctx['NNTPSettings'].nntp_posting.get('max_archive_size')

    # Default poster
    poster = ctx['NNTPSettings'].nntp_posting.get('poster')

    # Default subject
    subject = ctx['NNTPSettings'].nntp_posting.get('subject')

    # Link to our NNTP Manager
    mgr = ctx['NNTPManager']

    # Initialize our PostFactory
    pf = NNTPPostFactory(connection=mgr, hooks=hooks)

    # initialize our return code to zero (0) which means okay
    # but we'll toggle it if we have any sort of failure
    return_code = 0
    for path in paths:

        if not pf.load(path):
            return_code = 1
            continue

        if prep:
            if not pf.prepare(archive_size=archive_size):
                return_code = 1
                continue

        if stage:
            if not pf.stage(groups, split_size=split_size, poster=poster,
                            subject=subject):
                return_code = 1
                continue

        if upload:
            if not pf.upload():
                return_code = 1
                continue

        if verify:
            if not pf.verify():
                return_code = 1
                continue

        if clean:
            if not pf.clean():
                return_code = 1
                continue

    # return our return code
    exit(return_code)
