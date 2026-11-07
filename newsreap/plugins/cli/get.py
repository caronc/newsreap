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

# Download what an NZB file (or an indexed collection) holds, put it back
# together (par2 repairs what is damaged) and extract it into a directory
# of its own in your Downloads directory:
#   nr get release.nzb
#   nr get 42                      # a collection's id (see nr search)
#   nr get release.nzb --password secret --no-cleanup
#
# Running it again carries on where it stopped.  A Message-ID downloads
# that one article as it is (--headers and --inspect show what it holds).

import logging
import click
import sys

from os import makedirs
from os.path import abspath
from os.path import expanduser
from os.path import isdir
from os.path import dirname
from os.path import basename
from os.path import isfile
from os.path import join

try:
    from newsreap.Logging import NEWSREAP_CLI
except ImportError:
    # Path
    sys.path.insert(0, dirname(dirname(dirname(dirname(abspath(__file__))))))
    from newsreap.Logging import NEWSREAP_CLI

from newsreap.NNTPDownloader import NNTPDownloader
from newsreap.NNTPIndexer import NNTPIndexer
from newsreap.Utils import parse_bool
from newsreap.NNTPGetFactory import NNTPGetFactory

# initialize our logger
logger = logging.getLogger(NEWSREAP_CLI)

# Define our function
NEWSREAP_CLI_PLUGINS = 'get'


@click.command()
@click.pass_obj
@click.option('--group', type=str,
              default=None,
              help="Identify the group to reference")
@click.option('--workdir', type=str, default=None,
              help="A directory we can manage our fetched content from.")
@click.option('--headers', default=False, flag_value=True,
              help="Return header details")
@click.option('--inspect', default=False, flag_value=True,
              help="Inspect the first few bytes of the body only")
@click.option('--hooks', '-k', default=None, type=str,
              help='Specify one or more hooks to load')
@click.option('--output', '-o', default=None,
              help='Where the finished download goes (in a directory of its '
                   'own); the downloads: download_dir setting, or your '
                   'Downloads directory, otherwise.')
@click.option('--password', '-p', default=None,
              help='The password of its archives (the ones its NZB file '
                   'gives are tried too).')
@click.option('--repair/--no-repair', default=None,
              help='Repair damaged files with their par2 files (the '
                   'default).')
@click.option('--extract/--no-extract', default=None,
              help='Extract its archives (the default).')
@click.option('--no-cleanup', 'cleanup', flag_value=False, default=None,
              help='Keep what was downloaded (the staging directory) once '
                   'it is all put together.')
@click.option('--retry-missing', is_flag=True,
              help='Ask again for parts no server had, even ones that '
                   "can't have arrived since (they are otherwise only asked "
                   'for again while the post is new, or once you add a '
                   'server).')
@click.argument('sources', nargs=-1)
def get(ctx, group, workdir, headers, inspect, sources, hooks, output,
        password, repair, extract, cleanup, retry_missing):
    """
    Download what NZB files (or indexed collections) hold.

    Each source is an NZB file, a collection's id (as 'nr search' shows
    it) or a Message-ID.  What an NZB lists is downloaded into a staging
    directory of its own, put back together, repaired with its par2 files
    when something is damaged, and extracted into a directory named after
    it in your Downloads directory (keeping the layout its archives held).

    \b
    Running it again carries on where it stopped: what was already
    fetched is kept, and anything cut short is fetched again (Ctrl-C
    keeps what is on its way, then stops).  A part no
    server had (or only had damaged) is asked for again while the post is
    new, or once you add a server (--retry-missing asks regardless).  A
    file that isn't all there is still put together, with a .ERRORS file
    beside it saying what is wrong.
    If something can't be opened (a password that isn't known, or damage
    par2 couldn't fix), the files put together are placed for you to look
    at and nothing is removed.

    A Message-ID downloads that one article as it is (--headers and
    --inspect show what it holds instead).
    """
    if len(sources) == 0:
        logger.error("You must specify at least one source.")
        exit(1)

    if not (headers or inspect):
        # NZB files and collections are downloaded and put together; a
        # Message-ID is fetched as it is (below)
        nzbs = [s for s in sources if isfile(s) or s.strip().isdigit()]
        sources = [s for s in sources if s not in nzbs]
        failed = download(ctx, nzbs, hooks, output, password, repair,
                          extract, cleanup, retry_missing)
        if not sources:
            exit(1 if failed else 0)

    else:
        failed = 0

    # Link to our NNTP Manager
    mgr = ctx['NNTPManager']

    # Initialize our GetFactory
    gf = NNTPGetFactory(connection=mgr, hooks=hooks, groups=group)

    # initialize our return code to zero (0) which means okay
    # but we'll toggle it if we have any sort of failure
    return_code = 1 if failed else 0

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
        print((gf.str(headers=headers, inspect=inspect)))

    # return our return code
    exit(return_code)


def download(ctx, sources, hooks, output, password, repair, extract,
             cleanup, retry_missing=False):
    """
    Downloads NZB files (or indexed collections, by their id) with an
    NNTPDownloader; returns how many didn't work out
    """
    settings = ctx['NNTPSettings']
    downloads = settings.nntp_downloads

    def chosen(given, key):
        # The command line wins over the downloads: setting
        return given if given is not None else parse_bool(
            downloads.get(key), True)

    downloader = NNTPDownloader(
        ctx['NNTPManager'], settings.staging_dir(),
        abspath(expanduser(output)) if output else settings.download_dir(),
        password=password, repair=chosen(repair, 'repair'),
        extract=chosen(extract, 'extract'),
        cleanup=chosen(cleanup, 'cleanup'), hooks=hooks,
        retry_window=settings.retry_window(), retry_missing=retry_missing)

    failed = 0
    for source in sources:
        nzb_file, name = source, None
        if not isfile(source):
            # An indexed collection; written out as an NZB file first
            nzb_file, name = collection_nzb(ctx, source)
            if nzb_file is None:
                failed += 1
                continue

        try:
            done = downloader.download(nzb_file, name=name)

        except KeyboardInterrupt:
            # What came is kept in the staging directory
            click.echo('Stopped; run it again to carry on.', err=True)
            sys.exit(130)

        click.echo('%s: %s' % (name or basename(source), downloader))
        if done:
            click.echo('Saved in %s' % downloader.folder)
            continue

        failed += 1
        logger.error('%s: %s.' % (name or basename(source),
                                  downloader.problem))
        if downloader.folder:
            click.echo('What could be put together is in %s' % (
                downloader.folder))
        if downloader.staging and isdir(downloader.staging):
            click.echo('What was downloaded is kept in %s (running this '
                       'again carries on from there)' % downloader.staging)

    return failed


def collection_nzb(ctx, value):
    """
    Writes an indexed collection out as an NZB file (in our staging
    directory); returns (its path, the collection's name), or (None, None)
    if there is no such collection
    """
    settings = ctx['NNTPSettings']
    indexer = NNTPIndexer(settings.index_database())
    try:
        entry = indexer.get(value) if indexer.session() is not None \
            else None
        if entry is None:
            logger.error('There is no collection %s.' % value)
            return None, None

        makedirs(settings.staging_dir(), exist_ok=True)
        path = join(settings.staging_dir(), 'collection-%d.nzb' % entry.id)
        if not indexer.nzb(entry.id, path, work_dir=settings.work_dir):
            logger.error('Collection %s could not be written out.' % value)
            return None, None

        return path, entry.name

    finally:
        indexer.close()
