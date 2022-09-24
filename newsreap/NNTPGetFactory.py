# -*- coding: utf-8 -*-
#
# NNTPGetFactory is an object that manages NNTP articles retrievals
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

import weakref
from collections import deque
from tqdm import tqdm

from os import getcwd
import sys
from os.path import isfile
from os.path import join
from os.path import abspath
from os.path import expanduser
from os.path import basename
from os.path import dirname
from os.path import splitext
from StringIO import StringIO

from .NNTPGroup import NNTPGroup
from .NNTPArticle import MESSAGE_ID_RE
from .NNTPArticle import NNTPArticle
from .NNTPSegmentedPost import NNTPSegmentedPost
from .NNTPnzb import NNTPnzb
from .NNTPnzb import NZBParseMode
from .NNTPGetDatabase import NNTPGetDatabase
from .NNTPConnection import NNTPConnection
from .NNTPHeader import NNTPHeader
from .NNTPManager import NNTPManager
from .Utils import bytes_to_strsize
from .Utils import mkdir
from .Utils import rm
from .decorators import hook

# Logging
import logging
from newsreap.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)


class NNTPGetFactory(object):
    """
    Simplifies the retrieval of items from an NNTP Server

    """
    # The default hook path to load modules from if specified by name
    default_hook_path = join(dirname(abspath(__file__)), 'hooks', 'get')

    # Message-ID Retrieval File Extension
    # Content retrieved by Message-ID will have this extension appended
    # to it's name
    message_id_file_extension = '.msg'

    # Used for calculating queue sizes
    xfer_rate_max_queue_size = 20

    def __init__(self, connection=None, hooks=None, groups=None,
                 *args, **kwargs):
        """
        Initializes an NNTPGetFactory object

        hooks are called if specific functions exist in the module defined by
        the hook. You can specfy as many hooks as you want.1

        """

        # A boolean that allows us to enable/disable certain parts of our
        # factory depending on whether or not we loaded content successfully
        self._loaded = False

        # A pointer to an NZB-File object if it exists
        self.nzb = None

        # The name of the content being retrieved
        self.name = None

        # The groups you may wish to attempt to locate the desired content in
        self.groups = NNTPGroup.split(groups)

        # The absolute path to the base directory the content should be
        # downloaded to.
        self.path = None

        # The base path is where the user original pointed to. This is the root
        # directory that will contain our downloaded content (in another
        # directory inside of it).
        self.base_path = None

        # The temporary path content is downloaded to before it is placed
        # in the path identified above.
        self.tmp_path = None

        # A link to either an NNTPManager or NNTPConnection object
        # This only referenced for the upload and verify stages
        self.connection = connection
        if not self.connection:
            self.connection = NNTPManager()

        # Setup our HookManager for managing hooks
        self.hooks = self.connection.hooks

        if hooks:
            # load our hooks
            self.hooks.add(hooks, ('.', self.default_hook_path))

        # The path to our database for managing our staged content
        self.db_path = None

        # The sqlalchemy engine reference
        self.engine = None

        # our Database object
        self._db = None

        # A weak reference to our returned results so that we can print
        # and or access our results from.  This gets cleared on each
        # subsiquent call to load.
        self.results = None

        # Used for monitoring our transfer speeds
        self.xfer_rate = deque()

        # Average transfer rate
        self.xfer_rate_avg = 0

        # Total bytes received
        self.xfer_rx_total = 0

        # Our tqdm status bar reference
        self.xfer_tqdm = None

        # Reset our transfer health back to 100%
        self.xfer_health = 100

        # Track our errors if and/or when they occur
        self.err_stream = StringIO()

        # Add our internal hooks
        self.hooks.add(self.transfer_count)
        self.hooks.add(self.transfer_rates)

    @hook(name='socket_read')
    def transfer_rates(self, xfer_bytes, xfer_time, **kwargs):
        """
        calculate and monitor our transfer speeds

        """

        self.xfer_rate.append(xfer_bytes / xfer_time)
        if len(self.xfer_rate) > self.xfer_rate_max_queue_size:
            # bump oldest off of our list
            self.xfer_rate.popleft()

        # Calculate our average speed
        self.xfer_rate_avg = sum(self.xfer_rate) / float(len(self.xfer_rate))

        if self.xfer_tqdm is not None:
            # Track the total bytes received
            self.xfer_rx_total += xfer_bytes

            # Update our progress bar
            self.xfer_tqdm.update(xfer_bytes)

    @hook(name='post_get')
    def transfer_count(self, status, **kwargs):
        """
        used to track downloaded content

        """
        if status is not True:
            # TODO: we need to adjust our health and mode
            pass


    def load(self, source, hooks=True, groups=None, path=None,
             *args, **kwargs):
        """
        Takes a Message-ID or NZB-File (aource) and retrieves the content

        If hooks is set to True, it will continue using whatever hooks are
        already in place.  Otherwise you can define your own hooks here

        The path becomes our base_path if specified, otherwise it is
        determined based on the source passed in.  If the source is a
        Message-ID, then the path is the current working directory we're
        in.

        """
        # Ensure we're not loaded
        self._loaded = False

        # Reset a few variables that may not get reset later
        self.nzb = None
        self.name = None
        self.path = None
        self.tmp_path = None
        self.db_path = None
        self.engine = None
        self._db = None
        self.results = None

        # Reset our transfer rate queue
        self.xfer_rate.clear()

        # Destory our tqdm object (if present)
        self.xfer_tqdm = None

        # Reset our transfer average rate
        self.xfer_rate_avg = 0

        # Reset the total bytes received
        self.xfer_rx_total = 0

        # Track the transfer health; if we lose blocks
        # we need to enable the download of parchive files
        # to help
        self.xfer_health = 100

        # Start by defining our base path
        self.base_path = path

        if groups is not None:
            # Update our groups if new ones were specified
            self.groups = NNTPGroup.split(groups)

        if hooks is not True:
            # Update our hooks
            self.hooks.reset()
            self.hooks.add(hooks, ('.', self.default_hook_path))

            if self.connection:
                # Set our hooks onto our conection object
                self.connection.hooks(self.hooks)

        if self.base_path:
            # Tidy it up
            self.base_path = abspath(expanduser(abspath))

        if isfile(source):
            if not self.base_path:
                self.base_path = dirname(abspath(expanduser(source)))

            # We're dealing wth an NZB-File
            self.name = splitext(basename(source))[0]

            # Our final Download directory
            self.path = join(self.base_path, self.name)

        else:
            # Check if we're a Message-ID
            try:
                msgid = MESSAGE_ID_RE.match(source).group('id')

            except AttributeError:
                logger.error("'{}' is not NNTP retrievable.".format(source))
                return False

            if not self.base_path:
                self.base_path = getcwd()

            # We're dealing with a Message-ID
            self.name = msgid

            # Our final download directory
            self.path = self.base_path

        if not mkdir(self.base_path):
            logger.error(
                "Could not create '{}'; aborting.".format(self.base_path))
            return False

        # A temporary directory we will work with until we're done
        self.tmp_path = join(self.base_path, '{}.tmp'.format(self.name))

        # The path to our database for managing our retrieved content
        self.db_path = join(self.base_path, '{}.db'.format(self.name))
        self.engine = 'sqlite:///%s' % self.db_path
        self._db = None

        logger.debug("Scanning NZB-File '%s'." % (basename(source)))

        if isfile(source):
            # Load our NZB-File using all of the variables we've initialized
            # above.
            self.nzb = NNTPnzb(
                source,
                work_dir=self.tmp_path,
            )

            # Validate it
            if not self.nzb.is_valid():
                # Check that the file is valid
                logger.error(
                    "Invalid NZB-File '{}'.".format(basename(source)))
                return False

            # Load our size into our progress bar object
            self.xfer_tqdm = tqdm(total=self.nzb.size(), unit_scale=True)

        # It's safe to toggle our flag now
        self._loaded = True
        return True

    def download(self, commit_on_file=True, *args, **kwargs):
        """
        A Wrapper to _download() as this allows us to call our post_hooks
        properly each time.

        """

        if self._loaded is False:
            # Content must be loaded!
            return False

        # Initialize our return state
        status = False

        if isinstance(self.nzb, NNTPnzb):
            weaknzb = weakref.proxy(self.nzb)

        else:
            weaknzb = None

        try:
            response = self.hooks.call(
                'pre_download',
                name=self.name,
                path=self.path,
                nzb=weaknzb,
            )

            if next((r for r in response
                     if r is not False), None) is not False:

                # perform our connect
                status = self._download(
                    commit_on_file=commit_on_file, *args, **kwargs)

            else:
                logger.warning("Download aborted by pre_download() hook.")
                # abort specified; set status to None
                status = None

        finally:
            try:
                weak_results = weakref.proxy(self.results)

            except TypeError:
                # Some types just can't be converted into a weak reference
                # Either that, or we're already in a weakref format
                # no problem...
                weak_results = self.results

            self.hooks.call(
                'post_download',
                name=self.name,
                path=self.path,
                status=status,
                results=weak_results,
            )

        return status

    def _download(self, commit_on_file=True, *args, **kwargs):
        """
        Download our content
        """

        if not isinstance(self.connection, (NNTPConnection, NNTPManager)):
            logger.error("No connection object defined for download.")
            return False

        if self.nzb is None:
            # We are dealing with a Message-ID; retrieval occurs without codecs
            logger.debug("Handling Message-ID '%s'." % (self.name))

            response = self.connection.get(
                self.name, work_dir=self.path, decoders=False,
                group=self.groups)

            if response is None:
                logger.error("Could not retrieve Message-ID to '%s'." % (
                    self.name,
                ))
                return False

            response.body.filename = '{filename}{ext}'.format(
                filename=self.name,
                ext=self.message_id_file_extension,
            )

            if not response.body.save(self.path):
                logger.error("Could not save Message-ID to '%s'." % (
                    self.path,
                ))
                return False

            return True

        # If we're not dealing with an Message-ID

        # Acquire our session
        session = self.session()
        if not session:
            logger.error(
                "{} could not be accessed.".format(
                    self.engine,
                ),
            )
            return False

        if not mkdir(self.tmp_path):
            logger.error(
                "Could not create '{}'; aborting.".format(self.tmp_path))
            return False

        # Load our NZB-File into memory
        if not self.nzb.load():
            logger.warning(
                "Failed to load NZB-File '%s'." % (self.nzb.filename))
            return False

        # Start off by ignoring par files for speed
        self.nzb.mode(NZBParseMode.IgnorePars)

        # We are dealing with an NZB-File if we get here
        response = self.connection.get(self.nzb, work_dir=self.tmp_path)

        # Deobsfucate re-scans the existing NZB-Content and attempts to pair
        # up filenames to their records (if they exist).  A refresh does
        # nothing unless it has downloaded content to compare against, but
        # in this case... we do
        self.nzb.deobsfucate()

        # Initialize our status flag
        status = True

        for segment in self.nzb:
            # Now for each segment entry in our nzb file, we need to
            # combine it as one; but we need to get our filename's
            # straight. We will try to build the best name we can from
            # each entry we find.

            # Track our segment count
            seg_count = len(segment)

            if not segment.join():
                # We failed to join
                if segment.filename:
                    # Toggle our return status
                    status = False

                    logger.warning(
                        "Failed to assemble segment '%s' (%s)." % (
                            segment.filename,
                            segment.strsize(),
                        ),
                    )
                    continue

                else:
                    # Toggle our return status
                    status = False

                    logger.warning(
                        "Failed to assemble segment (%s)." % (
                            segment.strsize(),
                        ),
                    )
            else:
                # Toggle our return status
                status = False

                # We failed to join
                logger.debug("Assembled '%s' len=%s (parts=%d)." % (
                    segment.filename, segment.strsize(), seg_count))

            if segment.save(filepath=self.path):
                logger.info(
                    "Successfully saved %s (%s)" % (
                        segment.filename,
                        segment.strsize(),
                    ),
                )
            else:
                # Toggle our return status
                status = False

                logger.error(
                    "Failed to save %s (%s)" % (
                        segment.filename,
                        segment.strsize(),
                    ),
                )

        # Return our status
        return status

    def headers(self, source=None, *args, **kwargs):
        """
        A Wrapper to _headers() as this allows us to call our header_hooks
        properly each time.

        """

        # Reset our results
        self.results = None

        if self._loaded is False:
            # Content must be loaded!
            return False

        # Initialize our return state
        status = False

        # initialize our response object
        response = None

        if source is None:
            # Store our source
            source = self.name if self.nzb is None else self.nzb

        try:
            weak_source = weakref.proxy(source)

        except TypeError:
            # Some types just can't be converted into a weak reference
            # no problem...
            weak_source = source

        try:
            status = self.hooks.call(
                'pre_headers',
                name=self.name,
                path=self.path,
                source=weak_source,
            )

            if next((r for r in status if r is not False), None) is not False:
                response = self._headers(source=source, *args, **kwargs)

            else:
                logger.warning("Inspect aborted by pre_headers() hook.")
                # abort specified; set response to None
                response = None

        finally:

            try:
                # self.results is populated from wthin  _headers()
                weak_result = weakref.proxy(self.results)

            except TypeError:
                # Some types just can't be converted into a weak reference
                # no problem...
                weak_result = self.results

            self.hooks.call(
                'post_headers',
                name=self.name,
                path=self.path,
                response=response,
                results=weak_result,
                source=weak_source,
            )

        return response

    def _headers(self, source, *args, **kwargs):
        """
        Retrieves the header information associated with our source.

        """

        # Header response default return
        response = True

        # Used to track multi-header fetches
        header_list = []

        if isinstance(source, basestring):
            # We are dealing with a Message-ID
            # return our NNTPHeader object
            logger.debug("Handling Message-ID '%s'." % (self.name))

            self.results = self.connection.stat(
                source, full=True, group=self.groups)

            if isinstance(self.results, NNTPHeader):
                # We're done; return True
                return True

            # We failed, return False
            return False

        elif isinstance(source, NNTPnzb):
            # If we get here, we're dealing with an nzb file; iterate over our
            # segments
            for s_idx in range(len(source)):

                # Now iterate over each article within our segment
                for a_idx in range(len(source[s_idx])):

                    # Acquire our Message-ID
                    msgid = source[s_idx][a_idx].msgid()

                    # Acquire our Group(s)
                    group = source[s_idx][a_idx].groups

                    # We can do our query in a non-blocking way if we're using
                    # an NNTPManager object
                    if isinstance(self.connection, NNTPManager):
                        # Get our article header
                        header_list.append((
                            source[s_idx][a_idx],
                            self.connection.stat(
                                msgid,
                                full=True,
                                group=group,
                                block=False,
                            ),
                        ))

                    elif isinstance(self.connection, NNTPConnection):
                        # Otherwise we need to block
                        headers = self.connection.stat(
                            msgid,
                            full=True,
                            group=group,
                        )

                        if headers:
                            # Store our returned header into our article
                            source[s_idx][a_idx].header = headers

                        else:
                            # Ensure our header is empty
                            source[s_idx][a_idx].header.clear()
                            response = False

        elif isinstance(source, NNTPSegmentedPost):

            # iterate over each article within our segment
            for a_idx in range(len(source)):

                # Acquire our Message-ID
                msgid = source[a_idx].msgid()

                # Acquire our Group(s)
                group = source[s_idx][a_idx].groups

                # We can do our query in a non-blocking way if we're using
                # an NNTPManager object
                if isinstance(self.connection, NNTPManager):
                    # Get our article header
                    header_list.append((
                        source[a_idx],
                        self.connection.stat(
                            msgid,
                            full=True,
                            group=group,
                            block=False,
                        ),
                    ))

                elif isinstance(self.connection, NNTPConnection):
                    # Otherwise we need to block
                    headers = self.connection.stat(
                        msgid,
                        full=True,
                        group=group,
                    )

                    if headers:
                        # Store our returned header into our article
                        source[a_idx].header = headers

                    else:
                        # Ensure our header is empty
                        source[a_idx].header.clear()
                        response = False

        elif isinstance(source, NNTPArticle):

            # Fetch our article
            if isinstance(self.connection, NNTPManager):
                # Get our article header in a non-blocking mode
                header_list.append((source, self.connection.stat(
                    source.msgid(),
                    full=True,
                    group=source.groups,
                    block=False,
                )))

            elif isinstance(self.connection, NNTPConnection):
                # NNTPConnection objects are sequential; fetch our
                # article in a blocking state
                headers = self.connection.stat(
                    source.msgid(),
                    full=True,
                    group=source.groups,
                )

                if headers:
                    # Store our returned header into our article
                    source.header = headers

                else:
                    # Ensure our header is empty
                    source.header.clear()
                    response = False

        # At this point we should have a bunch of articles fetching
        # content.  We will wait until they've completed
        if isinstance(self.connection, NNTPManager):
            # Block until our uploads have finished and report them
            # accordingly
            for article, _connection in header_list:
                # Ensure we're done
                _connection.wait()

                if not isinstance(_connection.response[0], NNTPHeader):
                    # Ensure our header is empty
                    article.header.clear()
                    response = False
                    continue

                # Store our header
                article.header = _connection.response[0]

        # Set our results object so we can reference if need be
        try:
            self.results = weakref.proxy(source)

        except TypeError:
            # Some types just can't be converted into a weak reference
            # no problem...
            self.results = source

        return response

    def inspect(self, source=None, size=128, *args, **kwargs):
        """
        A Wrapper to _inspect() as this allows us to call our inspect
        hooks properly each time.

        """

        # Reset our results
        self.results = None

        if self._loaded is False:
            # Content must be loaded!
            return False

        # initialize our response object
        response = None

        if source is None:
            # Store our source
            source = self.name if self.nzb is None else self.nzb

        try:
            weak_source = weakref.proxy(source)

        except TypeError:
            # Some types just can't be converted into a weak reference
            # no problem...
            weak_source = source

        try:
            response = self.hooks.call(
                'pre_inspect',
                name=self.name,
                path=self.path,
                source=weak_source,
            )

            if next((r for r in response
                     if r is not False), None) is not False:
                response = self._inspect(
                    source=source, size=size, *args, **kwargs)

            else:
                logger.warning("Inspect aborted by pre_inspect() hook.")
                # abort specified; set response to None
                response = None

        finally:

            try:
                # self.results is populated from wthin  _inspect()
                weak_result = weakref.proxy(self.results)

            except TypeError:
                # Some types just can't be converted into a weak reference
                # no problem...
                weak_result = self.results

            self.hooks.call(
                'post_inspect',
                name=self.name,
                path=self.path,
                response=response,
                results=weak_result,
                source=weak_source,
            )

        return response

    def _inspect(self, source, size, *args, **kwargs):
        """
        Inspect a entry on a NNTP Server

        size represents the number of bytes you want to read from the posted
        content.  The way that inspections are done could be considered abuse
        by some NNTP Providers.  Use this option with caution.

        """

        # Some basic error checking
        if not isinstance(size, int):
            return False

        if size <= 0:
            return False

        if isinstance(source, basestring):
            # We are dealing with a Message-ID
            # return our NNTPHeader object
            logger.debug("Handling Message-ID '%s'." % (self.name))

            # Preview our Message by Message-ID only; do not download
            self.results = self.connection.get(
                source,
                work_dir=self.tmp_path,
                group=self.groups,
                max_bytes=size,
            )

            if isinstance(self.results, NNTPArticle):
                # We're done; return True
                return True

            # We failed, return False
            return False

    def clean(self, *args, **kwargs):
        """
        A Wrapper to _clean() as this allows us to call our hooks
        properly each time.
        """
        if self._loaded is False:
            # Content must be loaded!
            return False

        # Initialize our return state
        status = False

        try:
            response = self.hooks.call(
                'pre_clean',
                name=self.name,
                path=self.path,
                tmp_path=self.tmp_path,
            )

            if next((r for r in response
                     if r is not False), None) is not False:
                status = self._clean(*args, **kwargs)

            else:
                logger.warning("Cleanup aborted by pre_clean() hook.")
                # abort specified; set status to None
                status = None

        finally:
            self.hooks.call(
                'post_clean',
                name=self.name,
                path=self.path,
                status=status,
            )

        return status

    def _clean(self, *args, **kwargs):
        """
        Eliminate all content in the temporary working directory for a
        given prepable download
        """
        if not rm(self.tmp_path):
            logger.error(
                "Could not remove temporary directory '%s'." %
                self.tmp_path)

        if not rm(self.db_path):
            logger.error(
                "Could not remove temporary database '%s'." %
                self.db_path)

            return False
        return True

    def str(self, headers=True, inspect=True):
        """
        Iterates over our result object and returns it's contents

        if headers are defined, then the headers are printed to the screen

        """

        # Create a stream object we can write to
        hdr_stream = StringIO()

        def prep_header(article):
            # NNTP Header object
            if headers:
                hdr_stream.write('****\n' + article.header.str() + '\n')

            if inspect:
                if article.body:
                    # Display the message body
                    hdr_stream.write(
                        '****\n' +
                        article.body.getvalue().strip() +
                        '\n',
                    )

                # Display the head of the decoded message
                hdr_stream.write('****\n')
                hdr_stream.write('Mime-Type: %s\n' % (
                    article.decoded[0].mime().type(),
                ))
                hdr_stream.write(article.decoded[0].hexdump())

        if self.results is None:
            # Nothing to do
            return ''

        if isinstance(self.results, (NNTPHeader, NNTPArticle)):
            self.results = (self.results, )

        for entry in self.results:
            # Iterate over all of our entries and build our stream
            if isinstance(entry, NNTPHeader):
                # NNTP Header object
                if headers:
                    hdr_stream.write(
                        '****\n' + entry.str() + '\n')

            elif isinstance(entry, NNTPnzb):
                for segment in entry:
                    for article in segment:
                        prep_header(article)

            elif isinstance(entry, NNTPSegmentedPost):
                for article in entry:
                    prep_header(article)

            elif isinstance(entry, NNTPArticle):
                prep_header(entry)

        # Return our stream object(s)
        response = str()
        if self.headers:
            response += hdr_stream.getvalue()

        return response

    def session(self, reset=False):
        """
        Returns a database session
        """
        if not self._loaded:
            return False

        if not isfile(self.db_path):
            reset = True

        if self._db and reset is True:
            self._db = None

        if self._db is None:
            # Reset our database
            self._db = NNTPGetDatabase(engine=self.engine, reset=reset)

        # Acquire our session
        return self._db.session()
