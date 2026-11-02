# Simplifies communication to and from an NNTP Server
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

import gevent.monkey
gevent.monkey.patch_all()

import re
from zlib import decompressobj
from zlib import error as ZlibException
from os.path import isdir
from os.path import abspath
from os.path import expanduser
from io import BytesIO
from datetime import datetime
from time import monotonic
from contextlib import contextmanager
from gevent import sleep
from newsreap.SortedSet import sortedset
from sortedcontainers import SortedDict as sorteddict
import weakref

from .NNTPHeader import NNTPHeader
from .NNTPArticle import NNTPArticle
from .NNTPResponse import NNTPResponse
from .NNTPResponse import NNTPResponseCode
from .NNTPStatistics import NNTPStatistics
from .NNTPContent import NNTPContent
from .NNTPSegmentedPost import NNTPSegmentedPost
from .NNTPMetaContent import NNTPMetaContent
from .NNTPFilterBase import NNTPFilterBase
from .NNTPIOStream import NNTPIOStream
from .NNTPIOStream import NNTP_SUPPORTED_IO_STREAMS
from .NNTPIOStream import NNTP_DEFAULT_ENCODING

from .SocketBase import SocketBase
from .SocketBase import SocketException
from .SocketBase import SocketRetryLimit
from .SocketBase import SignalCaughtException
from .Utils import mkdir
from .Utils import SEEK_SET
from .Utils import SEEK_END
from .NNTPnzb import NNTPnzb
from .NNTPSettings import DEFAULT_TMP_DIR
from .NNTPSettings import NNTP_EOL
from .NNTPSettings import NNTP_RETRIES
from .NNTPSettings import NNTP_RETRY_WAIT
from .NNTPSettings import NNTP_STALL_TIMEOUT
from .NNTPSettings import NNTP_MIN_TRANSFER_RATE
from .NNTPSettings import NNTP_SLOW_RATE_WINDOW

# Codecs
# These define the messages themselves.
from .codecs.CodecBase import CodecBase
from .codecs.CodecHeader import CodecHeader
from .codecs.CodecArticleIndex import CodecArticleIndex
from .codecs.CodecArticleIndex import OVERVIEW_EXTRAS
from .codecs.CodecArticleIndex import XoverGrouping
from .codecs.CodecArticleIndex import overview_extras
from .codecs.CodecGroups import CodecGroups
from .codecs.CodecUU import CodecUU
from .codecs.CodecYenc import CodecYenc

# Logging
import logging
from newsreap.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)

# Defines the end of data delimiter
EOL = '\r\n'
EOD = '\r\n.'

# These are applied to the raw (bytes) data read from the server
EOL_RE = re.compile(br'([\r]?\n)', re.ASCII)
EOD_RE = re.compile(br'(\.([\r]?\n))$', re.ASCII)

# The end of data marker (a line holding just a dot) at the very end of
# what we've received
EOD_LINE_RE = re.compile(br'\n\.\r?\n$', re.ASCII)

# Commands that are always answered by a single line even though their
# response code (211) can also start a multi-line response (LISTGROUP)
SINGLE_LINE_COMMAND_RE = re.compile(r'^\s*GROUP\s', re.IGNORECASE | re.ASCII)

# Splitting doesn't work well with the parenthesis
# the below fixes this
EOLS_RE = re.compile(br'[\r]?\n', re.ASCII)

# When data compression in in play, the following delimiter is used
# to deliminate the chunks of compressed data
GZIP_EOL_RE = re.compile(br'\.([\r]?\n)$', re.ASCII)

# How many consecutive misses in a row do we allow while trying to retrieve
# historic messages (based on time) do we allow before assuming that we've
# exceeded the the maximum retention area. This is used in the seek_by_date()
# function. The larger this value; the slower the search is; however the
# greater level of accuracy obtained.
NNTP_FETCH_BY_DATE_MAX_MISSES = 20

# All responses are handled from usenet via the following:
NNTP_RESPONSE_RE = re.compile(
    r'^[^0-9]*(?P<code>[1-9][0-9]{2})[\s:]*(?P<desc>.*[^\s])\s*$',
    re.MULTILINE | re.ASCII,
)

# We use this check when send() is called so that we can prevent
# personal content from being sent to log files
NNTP_AUTHINFO_CHECK = re.compile(
    r'^AUTHINFO[\s]+(?P<key>[^\s]+)[\s]+(?P<value>.+)[\s]*$',
    re.IGNORECASE | re.ASCII,
)

# Group Response (when we switch to a group)
NNTP_GROUP_RESPONSE_RE = re.compile(
    r'(?P<count>[0-9]+)\s+(?P<low>[0-9]+)\s+' +
    r'(?P<high>[0-9]+)\s+(?P<name>.*[^\s]*[^.])(\.|\s)*$', re.ASCII,
)

# Scans against the status message
GZIP_COMPRESS_RE = re.compile(
    r'.*COMPRESS\s*=\s*GZIP.*',
    re.IGNORECASE | re.ASCII,
)

# Scans against the status message to detect if posting is allowed
POSTING_OK_RE = re.compile(
    r'.*POSTING OK.*',
    re.IGNORECASE | re.ASCII,
)

# Ports used by NNTP Servers
NNTP_PORT = 119
NNTP_SSL_PORT = 563

# The number of seconds to grant the remote NNTP Server to
# respond to a request in before aborting the connection
NNTP_RESPONSE_TIMEOUT = 30.0

# The number of seconds to give the remote NNTP Server to
# send it's welcome message before aborting the connection
NNTP_WELCOME_MESSAGE_TIMEOUT = 15.0

# The smallest the NNTP READ Buffer can be. This value must consider
# a worst case scenario where when the buffer is full, we can always
# read at least 1 line (a line defined by having '\n' at the end of
# it. The smaller this value, the faster content previews can occur
NNTP_MIN_READ_BUFFER_SIZE = 1024

# Defines the number of lines to scan into a message
# to try and find the =ybegin entry (identifing a YENC message)
SCAN_FROM_HEAD = 40

# scans from the bottom to the top looking for the ending
# of a yenc message
SCAN_FROM_TAIL = 10

# The number of XOVER Server retries to make in the event the
# query fails
NNTP_XOVER_RETRIES = 5


class NNTPConnection(SocketBase):
    """
        NNTPConnection is a class that wraps and eases the comunication
        to and from an NNTP Server.

        - Usenet Compression supported (GZIP) if the usenet server allows
          for it.

        - SSL Support (you'll ideally want to use the default of TLSv1
          for security reasons; but can support SSLv3 too if the remote
          server can't handle TLSv1).

        - It stores all of it's fetched results in sorted containers
          (sortedcontainers) so they can be searched quickly.

        - can seek_by_date() using a recusive binary style searching
          which can narrow in great time!

        - all socket i/o parsed through a BytesIO block to
          save on the constent allocation strings would normally
          have caused. Content is then further parsed and broken into
          an easy to parse dictionary (stored in a btree) and
          returned for the user for parsing.

        - any filters specified must be of type NNTPFilterBase. If a
          list or tuple is passed in, then each element in the list
          must be inheritied from NNTPFilterBase.
    """
    # 10MB Chunk Sizes read from the stream;  If content is compressed
    # it has the potential to get quite larger.
    MAX_BUFFER_SIZE = 10485760

    # The number of characters to read backwards at a time when scanning
    # for a new line character during situations where we've received
    # partial data we don't want to process yet. A new line delimiter
    # found allows us to safely process what is before it.
    READBACK_CHUNK_SIZE = 128

    def __init__(self, username=None, password=None, secure=False,
                 iostream=NNTPIOStream.RFC3977_GZIP,
                 join_group=False, use_body=False, use_head=True,
                 encoding=None, work_dir=None,
                 filters=None, hooks=None, retries=NNTP_RETRIES,
                 retry_wait=NNTP_RETRY_WAIT,
                 stall_timeout=NNTP_STALL_TIMEOUT,
                 min_rate=NNTP_MIN_TRANSFER_RATE,
                 slow_window=NNTP_SLOW_RATE_WINDOW, *args, **kwargs):
        """
        Initialize NNTP Connection

        secure
        ------
        Secure connection with NNTP Server using a SSL/TLS Encryption

        iostream
        --------
        NewsReap was developed in order to communicate with an NNTP server.
        The idea behind the IOStream is to allow us to handle other protocols
        too. At this time, there are only really 2 values to specify here:
           - NNTPIOStream.RFC3977_GZIP : Support GZIP Compression wrapped
                                          around the standard RFC3977 (NNTP)
                                          protocol.

           - NNTPIOStream.RFC3977      : Standard RFC3977 (NNTP) Protocol

        join_group
        ----------
        Some NNTP Servers have gone out of their way to make sure that all of
        the Message-ID's associated with an article is unique which eliminates
        the need to have to select the group prior to retrieving the article.

        It's obviously slower to do this; so if you're Usenet provider
        doesn't need this option; then don't specify it. It 'will' slow down
        processing if you set this!

        use_body
        --------
        This actually refers to quering the NNTP server for it's articles
        via the BODY command over the ARTICLE.  If this is set to true, only
        the body's content is downloaded.  This is faster; however the trade
        off is using the ARTICLE option hands down the Header information too.

        The Header information is really useful for scanning more details about
        the content we're retrieving.  Sometimes the header information can
        provide us enough information to no bother downloading the rest of the
        content.  Hence having this set to false can actually be faster in
        some cases. By default this is set to False so we can acquire as much
        information on the article we're retrieving as we can dispite the
        small overhead that it comes with.

        retries / retry_wait
        --------------------
        How many more times a failed fetch is tried (reconnecting first) and
        how long to wait before the first retry (doubled each time after).

        stall_timeout / min_rate / slow_window
        --------------------------------------
        When a connection is treated as dead (and retried):
          - stall_timeout: seconds without receiving anything at all
          - min_rate / slow_window: average bytes per second over the window
            dropped below min_rate
        Setting any of these to 0 turns that check off.
        """

        # get connection mode
        if secure:
            kwargs['secure'] = True
            self.protocol = 'nntps'
        else:
            kwargs['secure'] = False
            self.protocol = 'nntp'

        # What the fields after the first seven of an overview (XOVER)
        # line are; asked for once (see overview_format())
        self._overview_fmt = None

        # Default Filters
        self.filters = []

        # Filter Preparation
        if isinstance(filters, NNTPFilterBase):
            self.filters.append(filters)

        elif isinstance(filters, (list, tuple)):
            self.filters.extend([
                x for x in filters if isinstance(x, NNTPFilterBase)
            ])

        if hooks:
            # Force defined hooks into arguments
            kwargs['hooks'] = hooks

        # Initialize the Socket Base Class
        super().__init__(*args, **kwargs)

        # Retry and dead connection detection settings
        self.retries = max(0, int(retries))
        self.retry_wait = max(0.0, float(retry_wait))
        self.stall_timeout = max(0.0, float(stall_timeout))
        self.min_rate = max(0, int(min_rate))
        self.slow_window = max(0.0, float(slow_window))

        # Store the default encoding
        self.encoding = encoding
        if not self.encoding:
            self.encoding = NNTP_DEFAULT_ENCODING

        # Credentials are kept as text; everything sent to the server is
        # encoded using our character set
        self.username = username
        self.password = password

        # Attempt to gzip the connection
        if isinstance(iostream, str):
            try:
                self._iostream = iostream.lower()
                if self._iostream not in NNTP_SUPPORTED_IO_STREAMS:
                    # Default
                    self._iostream = NNTPIOStream.RFC3977_GZIP
                    logger.warning('An unknown iostream was specified; '
                                   "using default: '%s'." % self._iostream)

            except (TypeError, ValueError):
                # Default
                self._iostream = NNTPIOStream.RFC3977_GZIP
                logger.warning('A malformed iostream was specified; '
                               "using default: '%s'." % self._iostream)

        elif iostream:
            # Default
            logger.warning('An invalid iostream was specified; '
                           "using default: '%s'." % self._iostream)
            self._iostream = NNTPIOStream.RFC3977_GZIP

        else:
            # iostream is None (0, or False)
            # used RFC3977 Standards but without Compresssion
            logger.info('An invalid iostream was specified; '
                        "using default: '%s'." % self._iostream)
            self._iostream = NNTPIOStream.RFC3977

        # can_post is a flag that gets set after authenticating
        # with the news server.  The server responds with the
        # posting information which is set in this flag
        self.can_post = False

        # Article Meta information (Extracted from post)
        self.article = {}

        # Article End Of Data
        # This has 3 states:
        #   * False: The end of the article has not been found; scan and
        #            save what we find. We 'always' start in this state
        #   * None:  The end of the article has not been found; but due
        #            to a problem, we don't want anything else that is
        #            returned by the server.  This flag is usually set
        #            during parsing if CRC errors occur as a way of
        #            letting the server complete what it's doing.
        #   * True:  The end of the article has been found; we are in
        #            a somewhat of completed state
        self.article_eod = False

        # Article Filename
        self.article_fname = None

        # Data that has been successfully read from the
        # _buffer is processed (perhaps uncompressed)
        # and placed into the _data stream.
        self._data = BytesIO()

        # A _data length tracker which is used with
        # trying to track of we've processed all of the data yet
        # or not.
        self._data_len = 0

        # Temporary Buffer of read (unprocessed) data
        self._buffer = BytesIO()

        # Our response code (integer) returned by the NNTPServer on the last
        # request made
        self.last_resp_code = None

        # Our response string returned by the NNTPServer on the last request
        # made
        self.last_resp_str = ''

        # A flag set to True if the payload to follow the response is gzipped.
        # GZipped responses require a bit more CPU crunching but much less
        # bandwidth. This value is set to None if there simply wasn't a payload
        # at all (or an initialization value)
        self.payload_gzipped = None

        self.lines = []
        self.line_count = 0

        self.group_name = None
        self.group_count = 0
        self.group_head = 0
        self.group_tail = 0
        self.group_index = 0

        # use HEAD command vs STAT when retrieving information on an article
        # on usenet.
        self.use_head = use_head

        # Use the BODY command vs ARTICLE when retrieving the data itself.
        # The subtle difference is BODY only returns the raw contents where as
        # ARTICLE returns the header information too.  The header information
        # can provide invaluable information on whether the BODY is even worth
        # retrieving in some cases.  It's purely up to the user which they
        # want to use.  It's also possible that some servers won't support
        # one command over the other.
        self.use_body = use_body

        # Some usenet servers do not require the group to be joined prior to
        # fetching it's content.
        self.join_group = join_group

        # A List of backup servers that attempt to fetch missing content; use
        # append() to append to the list.  The entry must be of type
        # NNTPConnection()
        self._backups = []

        # Statistics of everything this connection has done (see
        # NNTPStatistics); each action also returns its own on the response
        self.stats = NNTPStatistics()

        # The statistics of the action in progress (if any)
        self._action = None

        # How our last article fetch went: 'found', 'missing' (no server had
        # it) or 'failed' (something went wrong; it may be there); see
        # fetch_status()
        self.last_fetch = None

        # Used to cache group list responses
        self._grouplist = None

        # Default Working Directory
        # All temporary content is downloaded to this location.  If set to
        # None then the defaults are used instead.
        if work_dir is None:
            self.work_dir = DEFAULT_TMP_DIR
        else:
            self.work_dir = abspath(expanduser(work_dir))

    def append(self, connection, *args, **kwargs):
        """
        Add a backup NNTP Server (Block Account) which is only
        queried in the event content can't be pulled from the
        primary server.
        """
        if not isinstance(connection, NNTPConnection):
            return False

        # block/backup connections are established on demand
        self._backups.append(connection)
        return True

    def connect(self, *args, **kwargs):
        """
        Establishes a connection to an NNTP Server
        """
        if self.connected:
            # nothing to see here
            return True

        # Reset tracking items
        self._soft_reset()

        # initialize our return flag
        status = False

        try:
            # pre_connect hook
            response = self.hooks.call('pre_connect', *args, **kwargs)

            # Allow a response over-ride which forces a denied connection
            if next((r for r in response
                     if r is not False), None) is not False:

                # call _connect() (logging in counts towards our statistics)
                with self._recording() as stats:
                    status = self._connect(*args, **kwargs)
                    if status:
                        # Track the connections we open
                        stats.connections += 1

        except SocketRetryLimit as e:
            # We can not establish a connection and never will
            logger.error('Could not connect to %s: %s' % (self.host, e))
            return False

        finally:
            self.hooks.call(
                'post_connect',
                status=status,
                conection=weakref.proxy(self)
            )

        return status

    def _connect(self, *args, **kwargs):
        """
        _connect() performs the actual connection with little
        error checking.  This is intended so that if we lose the
        connection we can attempt to resume where we left off
        gracefully.
        """

        try:
            if not super().connect(*args, **kwargs):
                # Return Fail
                return False

        except (SocketException, SignalCaughtException):
            # Return Fail
            return False

        # Receive Initial Welcome Message
        response = self._recv(timeout=NNTP_WELCOME_MESSAGE_TIMEOUT)
        if response.code not in NNTPResponseCode.SUCCESS:
            logger.error(
                'Failed to establish a handshake response from server.',
            )
            # server error
            self.close()
            return False

        # Tests for specific flags that exist in the welcome message that
        # give us indication on whether or not we can post to usenet or
        # not.
        if POSTING_OK_RE.search(response.code_str):
            # Toggle can_post flag
            logger.info('NNTP Posting enabled.')
            self.can_post = True
        else:
            logger.info('NNTP Posting disabled.')

        # Authenticate
        response = self.send('AUTHINFO USER %s' % self.username)
        if response.code not in NNTPResponseCode.PENDING:
            # We got a 400 or 500 error, no good
            logger.error('The specified username was not accepted.')
            self.close()
            return False

        response = self.send('AUTHINFO PASS %s' % self.password)
        if response.code not in NNTPResponseCode.SUCCESS:
            # We failed to get a 200 error; assume an authentication error
            logger.error('The specified password was not accepted.')
            self.close()
            return False

        logger.info('NNTP USER/PASS Handshake was successful.')

        if self._iostream == NNTPIOStream.RFC3977_GZIP:
            # Do Compression
            response = self.send('XFEATURE COMPRESS GZIP')
            if response.code not in NNTPResponseCode.SUCCESS:
                # Not supported; flip flag
                logger.warning('NNTP Compression not supported.')
                self._iostream = NNTPIOStream.RFC3977
            else:
                logger.info('NNTP Compression enabled.')

        if self.join_group and self.group_name:
            # Change to our group
            self.group(self.group_name)

        return True

    def post(self, payload, update_headers=True, success_only=False):
        """
        Allows posting content to a NNTP Server

        If success_only is set to True, then the result set returned only
        contains the entries of successfully posted content.  otherwise
        the result set returns the actual response code for each
        entry posted.

        """

        # A sorted list of all articles pulled down
        results = sortedset(key=lambda x: x.key())

        postable = []
        if isinstance(payload, (set, tuple, sortedset, list)):
            # iterate over all items and append them to our resultset
            for entry in payload:
                _results = self.post(entry, update_headers=True)
                if _results is not None:
                    # Append our results
                    results |= _results

        elif isinstance(payload, NNTPArticle):
            postable = [payload, ]

        elif isinstance(payload, NNTPSegmentedPost):
            postable = iter(payload)

        # Initialize a bound respose object
        response = None

        for content in postable:
            if content not in results:

                try:
                    weak_content = weakref.proxy(content)

                except TypeError:
                    # Some types just can't be converted into a weak reference
                    # no problem...
                    weak_content = content

                try:
                    # pre hook
                    _response = self.hooks.call(
                        'pre_post',
                        content=weak_content,
                    )

                    # Allow a response over-ride which forces a denied
                    # post
                    if next((r for r in _response
                             if r is not False), None) is not False:

                        response = self._post(content, update_headers=True)

                    else:
                        response = NNTPResponse(
                            NNTPResponseCode.HOOK_OVERRIDE,
                            'NNTP POST action blocked by hook',
                        )

                except SocketException:
                    # Connection Lost
                    self.close()

                    # Setup Response
                    response = NNTPResponse(
                        NNTPResponseCode.CONNECTION_LOST,
                        'Connection Lost',
                    )

                finally:
                    # Point our body to our content
                    response.body = weakref.proxy(content)

                    self.hooks.call(
                        'post_post',
                        content=weak_content,
                        response=weakref.proxy(response),
                        status=response in NNTPResponseCode.SUCCESS,
                    )

                if success_only:
                    if response in NNTPResponseCode.SUCCESS:
                        results.add(response)
                else:
                    # Store all response types
                    results.add(response)

        # Return our results
        return results

    def _post(self, article, update_headers=True):
        """
        Takes an NNTPArticle() and posts it to an NNTP Server if it can

        The response carries the statistics of the whole post.
        """
        with self._recording() as stats:
            try:
                response = self._post_article(
                    article, update_headers=update_headers)

            except SocketException:
                # The connection was lost part way through
                stats.dropped += 1
                stats.failed += 1
                raise

            if response.code in NNTPResponseCode.SUCCESS:
                # Track what we posted
                stats.posted += 1

            else:
                # Track what we couldn't post
                stats.failed += 1

        # Hand back what this post took
        response.stats = stats
        return response

    def _post_article(self, article, update_headers=True):
        """
        Does the work of posting an article; see _post()
        """

        if not self.connected:
            # Attempt to establish a connection
            if not self.connect():
                logger.error('Could not establish a connetion to NNTP Server.')
                return NNTPResponse(
                    NNTPResponseCode.NO_CONNECTION, 'No Connection',
                )

        if not self.can_post:
            # 480 Transfer permission denied
            return NNTPResponse(480, 'Transfer permission denied.')

        if not isinstance(article, NNTPArticle):
            # Not postable content
                return NNTPResponse(
                    NNTPResponseCode.INVALID_INPUT,
                    'You may only post an NNTPArticle.',
                )

        # get an iterator
        post_iter = article.post_iter(update_headers=update_headers)
        if not post_iter:
            return NNTPResponse(
                NNTPResponseCode.INVALID_INPUT,
                'NNTPArticle() is missing data (not postable).',
            )

        # send the POST keyword to the remote server
        response = self.send('POST')
        if response.code not in NNTPResponseCode.PENDING:
            # We got a 400 or 500 error, no good
            logger.error('Could not post content / %s' % response)
            return response

        # If we reach here we're read to go
        xfer_total_bytes = 0
        chunk = b''
        for chunk in self._stuff(post_iter):
            xfer_bytes = super().send(chunk)
            if xfer_bytes != len(chunk):
                # uh-oh
                return NNTPResponse(436, 'Transfer failed.')
            xfer_total_bytes += xfer_bytes

            # Track what we sent
            self._action.tx_bytes += xfer_bytes

        # Send our end of data marker (a line holding just a dot) and
        # capture our response; send() adds the line ending for us
        response = self.send(
            '.' if chunk.endswith(b'\n') else NNTP_EOL + '.')

        return response

    def group(self, name):
        """
        Changes to a specific group

        This function returns a tuple in the format of:

            (count, head, tail, group_name)

        If an error occurs, then the following is returned:

            (None, None, None, group_name)

        """

        if not self.connected:
            # Attempt to establish a connection
            if not self.connect():
                logger.error('Could not establish a connetion to NNTP Server.')
                return None

        response = self.send('GROUP %s' % name)

        self.group_name = name
        self.group_count = 0
        self.group_head = 0
        self.group_tail = 0
        self.group_index = 0

        if response.code in NNTPResponseCode.SUCCESS:
            match = NNTP_GROUP_RESPONSE_RE.match(response.code_str)
            if match:
                # We matched!
                # return is count, first, end, name
                self.group_count = int(match.group('count'))
                self.group_head = int(match.group('low'))
                self.group_tail = int(match.group('high'))
                self.group_name = match.group('name')
                self.group_index = self.group_head

                logger.info('Using Group: %s.' % self.group_name)
                return (
                    self.group_count,
                    self.group_head,
                    self.group_tail,
                    self.group_name,
                )

        logger.warning('Bad Group: %s' % name)
        return (None, None, None, self.group_name)

    def groups(self, filters=None, lazy=True):
        """
        Retrieves a list of groups from the server and returns them in an easy
        to parse dictionary:

              {
                  group: 'alt.binaries.test',
                  head: 0,
                  tail: 100,
                  count: 100,
                  flags: ['y'],
              }

        If filter(s) are specified, then the list is filtered based on
        the specified entries.

        The filters can be in the form of a string or regular expression.
        If multiple strings are specified (in a tuple or list format) then
        they will all be checked for a match. If fiters are specified then
        at least 1 filter must match.

        To fetch just binaries you could type:
            groups(filters='alt.binaries')

        If lazy is set to true; the last returned fetch is used instead
        which greatly increases the speed when you need to make multiple
        calls to this function. By defaut the lazy flag is enabled.
        """

        if not self.connected:
            # Attempt to establish a connection
            if not self.connect():
                logger.error('Could not establish a connetion to NNTP Server.')
                return None

        # Filter Management
        if not filters:
            filters = tuple()

        elif isinstance(filters, str):
            filters = (filters,)

        elif isinstance(filters, re.Pattern):
            filters = (filters,)

        elif not isinstance(filters, (tuple, list)):
            filters = list(filters)

        # Iterate over each item in the list and compile it now
        _filters = []
        for _filter in filters:
            if isinstance(_filter, re.Pattern):
                _filters.append(_filter)

            elif isinstance(_filter, str):
                try:
                    _filter = r'^.*%s.*$' % re.escape(_filter)
                    _filters.append(
                        re.compile(_filter, flags=re.IGNORECASE | re.ASCII),
                    )
                    logger.debug('Compiled group regex "%s"' % _filter)

                except:
                    logger.error(
                        'Invalid group regular expression: "%s"' % _filter,
                    )
            else:
                logger.error(
                    'Ignored group expression: "%s"' % _filter,
                )

        if self._grouplist is None or not lazy:
            # Send LIST ACTIVE command
            response = self.send('LIST ACTIVE', decoders=[
                CodecGroups(work_dir=self.work_dir),
            ], retries=None)
            if not response.is_success(multiline=True):
                logger.error('Failed to interpret NNTP LIST ACTIVE response.')
                # could not retrieve list
                return None

            # Pop the group list off
            grouplist = response.decoded.pop()
            if not isinstance(grouplist, NNTPMetaContent):
                # could not retrieve list
                logger.error('Failed to fetch NNTP LIST ACTIVE content.')
                return None

            # Lazy Caching for speed
            self._grouplist = grouplist.content
            logger.info('Cached %d group(s)' % len(self._grouplist))

        if _filters:
            cleaned = []
            for x in self._grouplist:
                # Apply filter if nessisary
                if not next((True for f in _filters
                             if f.search(x['group']) is not None), False):
                    # Filters were specified and the matched
                    # group did not match what was specified so
                    # we can move onto the next entry
                    continue

                # if we reach here, then we store our group
                cleaned.append(x)

            logger.info('Retrieved %d (and filtered %d) groups(s)' % (
                len(self._grouplist), len(cleaned),
            ))
            return cleaned

        logger.info('Retrieved %d groups(s)' % (
            len(self._grouplist),
        ))
        return self._grouplist

    def tell(self):
        """
        Returns the current index
        """
        return self.group_index

    def seek_by_date(self, refdate, group=None):
        """
        Similar to the seek() function in the sense it changes the
        current index pointer.  However this one attempts to narrow
        into the index location by a date.

        This function returns the first message id to scan from
        based on the time specified.  If no articles are found
        or something bad happens, false is returned.

        refdate must be a datetime object.

        As a user you don't need to set the last 2 flags. In fact
        it might be more advisable not to unless you know what you're
        doing.  Just call the function by setting the date to what you
        want and let the function manage it's own recursive actions.

            from datetime import datetime
            from datetime import timedelta

            NNTPConnection n(username, password, localhost)
            n.connect(localhost)
            # look back 40 days from 'now'
            n.seek_by_date(datetime.now()-timedelta(days=40))

        Seeking takes on average 15 to 16 queries in a group containing
        a size of 33 million records. So it's pretty optimized in that
        reguard. Here is it's Big-O representation:

            O(log n + log**2 k)
        """

        if group is not None and group != self.group_name:
            # allow us to switch groups if nessisary
            if self.group(group)[0] is None:
                # Could not select group
                logger.error(
                    'DATESEEK Could not select group %s' % group)
                return None

        elif group is not None and self.group_name is None:
            logger.error(
                'DATESEEK XOVER failed due to no group selected.')
            return None

        if not self.connected:
            return None

        index = self._seek_by_date(refdate)
        if index < 0:
            # Nothing was posted at (or after) our date; point just past
            # the last article
            logger.warning(
                'No entries found at (or after) date(%s) in group %s' % (
                    refdate.strftime('%Y.%m.%d %H:%M:%S'),
                    group,
                ),
            )
            self.group_index = self.group_tail + 1
            return self.group_index

        self.group_index = index
        logger.info('Matched index: %d' % (self.group_index))
        return self.group_index

    def _seek_by_date(self, refdate, head=None, tail=None):
        """

        This ideally shouldn't be called directly; users should
        call the seek_by_date() function instead

        refdate must be a datetime object.

        How it's done:
        - Usenet articles are (more or less) numbered in the order they
          were posted, so we binary search the article numbers.
        - Each step fetches a small window of overview (XOVER) entries from
          the middle of what is left and decides which half our date is in.
        - Article numbers (not positions) are used since groups often have
          gaps where articles have expired or were removed.

        Returns the first article number posted at (or after) refdate, or
        -1 if there isn't one.

        """

        lo = self.group_head if head is None else head
        hi = self.group_tail if tail is None else tail

        logger.debug('Scanning for index at %s in %d article(s)' % (
            refdate.strftime('%Y-%m-%d %H:%M:%S'),
            hi - lo,
        ))

        # Our date in the same format as our (date sorted) overview keys
        _refdate = refdate.strftime('%Y%m%d%H%M%S:0000000000')

        # The best match found so far
        best = -1

        window = NNTP_FETCH_BY_DATE_MAX_MISSES

        # How wide a window it took to find articles last time; groups
        # with lots of gaps need wider windows
        found_size = window

        while hi - lo >= window:
            mid = (lo + hi) // 2

            # Grab a few entries starting at our middle; if there is a gap
            # in the article numbers here, keep widening our window
            size = max(window, found_size // 2)
            while True:
                end = min(mid + size - 1, hi)
                response = self.xover(
                    start=mid, end=end, sort=XoverGrouping.BY_TIME)

                if response is None and not self.connected:
                    # We lost our connection
                    return -1

                if response or end >= hi:
                    break

                size *= 2

            found_size = size

            if not response:
                # Nothing exists from our middle onwards
                hi = mid - 1
                continue

            pos = response.bisect_left(_refdate)
            if pos == 0:
                # Everything here is newer; our match is either the first
                # article we saw or something before our middle
                best = min(e['article_no'] for e in response.values())
                hi = mid - 1

            elif pos == len(response):
                # Everything here is older; our match comes afterwards
                lo = max(e['article_no'] for e in response.values()) + 1

            else:
                # Our date falls within what we retrieved
                return response.values()[pos]['article_no']

            if hi - lo < window:
                break

        if lo > hi:
            return best

        # We've narrowed our search down to just a few articles
        response = self.xover(start=lo, end=hi, sort=XoverGrouping.BY_TIME)
        if not response:
            return best

        pos = response.bisect_left(_refdate)
        if pos == len(response):
            # Nothing here was posted at (or after) our date
            return best

        return response.values()[pos]['article_no']

    def seek(self, index, whence=None):
        """
        Sets a default nntp index
        TODO: if wence is set then the index is set with respect
              to the results.

        seek() always returns the current index similar to what tell()
        would return if it was called right afterwards
        """
        if index <= self.group_tail:
            if index >= self.group_head:
                self.group_index = index
            else:
                # Can't be less than the head
                self.group_index = self.group_head
        else:
            # Can't be greater than the tail
            self.group_index = self.group_tail

        return self.group_index

    def next(self, count=50000):
        """
        A wrapper to the xover but using a counter offset based
        on a set index value

        use the seek() function to over-ride the default
        index value (which is usually set to the head of
        group.

        """
        # Control Count gracefully
        count = min(count, self.group_tail - self.group_index)

        if count <= 0:
            # 423: Empty Range
            return NNTPResponse(423, 'Empty Range')

        # Fetch our results and adjust our pointer accordingly
        response = self.xover(self.group_index, self.group_index + count - 1)
        if response:
            self.group_index += count

        # Return results
        return response

    def prev(self, count=50000):
        """
        A wrapper to the xover but using a counter offset based
        on a set index value

        use the seek() function to over-ride the default
        index value (which is usually set to the head of
        group.

        """
        # Control Count gracefully
        count = min(
            count, self.group_tail - self.group_index - self.group_head)

        if count <= 0:
            # 423: Empty Range
            return NNTPResponse(423, 'Empty Range')

        # Fetch our results and adjust our pointer accordingly
        response = self.xover(self.group_index - count, self.group_index - 1)
        if response:
            self.group_index -= count

        # Return results
        return response

    def overview_format(self):
        """
        Returns what the fields after the first seven of an overview (XOVER)
        line are, as LIST OVERVIEW.FMT says (ie: ('Xref:full', )); asked
        for once and kept.  OVERVIEW_EXTRAS is used when the server can't
        say.
        """
        if self._overview_fmt is not None:
            return self._overview_fmt

        response = self.send('LIST OVERVIEW.FMT')
        if response is not None and response.code == 215:
            try:
                lines = response.body.getvalue().decode(
                    self.encoding, 'replace').splitlines()

            except (AttributeError, TypeError):
                # Nothing came back to read
                lines = []

            self._overview_fmt = overview_extras(lines)

        else:
            # An older server; nearly all of them send an Xref
            logger.debug('LIST OVERVIEW.FMT not supported by %s.' % self)
            self._overview_fmt = OVERVIEW_EXTRAS

        return self._overview_fmt

    def xover(self, group=None, start=None, end=None,
              sort=XoverGrouping.BY_POSTER_TIME):
        """
        xover
        Returns a NNTPRequest object

        RFC: http://tools.ietf.org/html/rfc2980
        list is a list of tuples, one for each article in the range delimited

        If the start or end time are set to `None` then they default to the
        low/high (head or tail) watermark respectively.

        by the start and end article numbers. Each tuple is of the form
        (article number, subject, poster, date, id, references, size, lines).

        If the start or end time is a datetime variable, then additional
        overhead is performed to find these locations in the group identified.

        Content is parsed as follows:
              {
                  id: u'the unique identifier',
                  article_no: 12345678,
                  poster: u'the poster's information',
                  date: datetime() object,
                  subject: 'a subject line',
                  size: 2135  // the message size in bytes
                  lines: 53   // the number of lines
                  group: u'alt.group.one'
                  xgroups : {
                      // references the Message-ID (id) per cross post
                       u'alt.group.two': 987654321,
                       u'alt.group.three': 12341234,
                  }
              }
        """

        if group is not None and group != self.group_name:
            # allow us to switch groups if nessisary
            if self.group(group)[0] is None:
                # Could not select group
                logger.error('XOVER Could not select group %s' % group)
                return None

        elif group is not None and self.group_name is None:
            logger.error('XOVER failed due to no group selected.')
            return None

        # Tracks whether a date decided our range
        by_date = False

        if end is None:
            end = self.group_tail

        elif isinstance(end, datetime):
            # Get the end by date (never past our last article)
            end = min(self.seek_by_date(end), self.group_tail)
            by_date = True

        # Default Ranges (if not otherwise specified)
        if start is None:
            start = self.group_head

        elif isinstance(start, datetime):
            # Get the start by date
            start = self.seek_by_date(start)
            by_date = True

        if start > end and by_date:
            # Nothing was posted within the dates asked for
            return sorteddict()

        if start > end:
            # 423: Empty Range
            logger.error('Invalid XOVER start/end (%d/%d) range.' % (
                start,
                end,
            ))
            return None

        elif not (self.group_head and start >= self.group_head):
            # 423: Empty Range
            logger.error('Invalid XOVER head/start (%d/%d) range.' % (
                self.group_head,
                start,
            ))
            return None

        elif not (self.group_tail and end <= self.group_tail):
            # 423: Empty Range
            logger.error('Invalid XOVER tail/end (%d/%d) range.' % (
                self.group_tail,
                end,
            ))
            return None

        try:
            # pre hook
            _response = self.hooks.call(
                'pre_xover',
                group=group,
                start=start,
                end=end,
            )

            # Allow a response over-ride which forces a denied xover
            if next((r for r in _response
                     if r is not False), None) is not False:

                # What the server says its overview lines hold
                fields = self.overview_format()

                response = self.send(
                    'XOVER %d-%d' % (start, end),
                    decoders=[
                        CodecArticleIndex(
                            filters=self.filters,
                            sort=sort,
                            encoding=self.encoding,
                            work_dir=self.work_dir,
                            fields=fields,
                        ),
                    ],
                    retries=NNTP_XOVER_RETRIES,
                )

            else:
                response = NNTPResponse(
                    NNTPResponseCode.HOOK_OVERRIDE,
                    'NNTP XOVER action blocked by hook',
                )

        finally:

            try:
                weak_response = weakref.proxy(response)

            except TypeError:
                # Some types just can't be converted into a weak reference
                # no problem...
                weak_response = response

            self.hooks.call(
                'post_xover',
                group=group,
                start=start,
                end=end,
                response=weak_response,
                status=response in NNTPResponseCode.SUCCESS_MULTILINE,
            )

        if response.code == 423:
            # The server has no articles in the range we asked for
            return sorteddict()

        if response.code not in NNTPResponseCode.SUCCESS_MULTILINE:
            logger.error('Failed to interpret NNTP XOVER response.')
            return None

        try:
            # return our decoded content
            content = response.decoded.pop().content

        except IndexError:
            # The server had no articles in the range we asked for
            return sorteddict()

        # Track the headers we received (the command's own statistics were
        # already added to our total, or to the action we're part of)
        response.stats.headers += len(content)
        (self._action or self.stats).headers += len(content)
        return content

    def stat(self, id, full=None, group=None):
        """
        A wrapper to _stat that allows us to manage hooks

        """

        if full is None:
            # default
            full = self.use_head

        if self.join_group and group is not None and group != self.group_name:
            # allow us to switch groups if nessisary
            if self.group(group)[0] is None:
                # Could not select group
                logger.error('Could not select group %s' % group)
                return None

        # Initialize a bound object
        response = None

        try:
            _response = self.hooks.call(
                'pre_stat',
                content=id,
                full=True,
                group=self.group_name,
            )

            # Allow a response over-ride which forces a denied stat
            if next((r for r in _response
                     if r is not False), None) is not False:

                response = self._stat(id=id, full=full)

            else:
                response = NNTPResponse(
                    NNTPResponseCode.HOOK_OVERRIDE,
                    'NNTP STAT action blocked by hook',
                )

        finally:
            # Prepare our status for our hook call
            status = False
            if isinstance(response, NNTPHeader):
                status = response.article_exists()

            try:
                weak_response = weakref.proxy(response)

            except TypeError:
                # Some types just can't be converted into a weak reference
                # no problem...
                weak_response = response

            self.hooks.call(
                'post_stat',
                content=id,
                full=full,
                group=self.group_name,
                response=weak_response,
                status=status,
            )

        # Return our response
        return response

    def _stat(self, id, full=None):
        """
        A Simple check to return True of False on whether an article
        exists or not. The function returns:
            False:  if the article does not exist.
            None:   if there was no way to determine the answer.

            Otherwise the function returns a dictionary containing the
            stats the news server had on the file in question.

            if Full is left to None, then the results will be based
            on the defaut use_head function.
        """

        if not full:
            response = self.send('STAT <%s>' % id, retries=None)
            if response.is_success(multiline=False):
                # we're good to go, return what we do know so it fits
                results = NNTPHeader()
                results['Message-ID'] = id
                return results

        else:
            # Force decoders to just be the header
            response = self.send(
                'HEAD <%s>' % id,
                decoders=[
                    CodecHeader(
                        encoding=self.encoding,
                        work_dir=self.work_dir,
                    ),
                ],
                retries=None,
            )

            if response.is_success(multiline=True):
                # Return our content
                return response.decoded.pop()

        if response.code in NNTPResponseCode.NO_ARTICLE:
            if self._backups:
                # Try our backup servers in the sequential order they were
                # added in; if they all fail; then we return None
                logger.warning(
                    'ARTICLE <%s> not found; checking backups.' % id,
                )
                return next((
                    result for result in (
                        b.stat(id=id, full=full, group=self.group_name)
                        for b in self._backups)
                    if result not in (None, False)), False)

            logger.warning('ARTICLE <%s> not found.' % id)
            return False

        elif response.code in NNTPResponseCode.SERVER_ERROR:
            # We have a problem
            self.close()
            logger.error('NNTP Error %s' % response)
            if self._backups:
                # Try our backup servers in the sequential order they were
                # added in; if they all fail; then we return None
                return next((
                    result for result in (
                        b.stat(id=id, full=full, group=self.group_name)
                        for b in self._backups)
                    if result not in (None, False)), None)
            return None

        # Return
        return True

    def get(self, id, work_dir=None, decoders=None, group=None, max_bytes=0):
        """
        A wrapper to the _get call allowing support for more then one type
        of object (oppose to just _get() which only accepts the message id

        This function returns the articles as a sortedset() containing the
        downloaded content based on what was passed in.

        max_bytes is set to the number of bytes you want to have received
        before you automatically abort the connection and flag the object as
        having only been partially complete  Use this option when you need to
        inspect the first bytes of a binary file. Set this to zero to download
        the entire thing (this is the default value)

        """
        if work_dir is None:
            # Default
            work_dir = self.work_dir

        if isinstance(id, str):
            # We're dealing a Message-ID (Article-ID)
            return self._get(
                id=id,
                work_dir=work_dir,
                decoders=decoders,
                group=group,
                max_bytes=max_bytes,
            )

        # A sorted list of all articles pulled down
        results = sortedset(key=lambda x: x.key())

        if isinstance(id, (set, tuple, sortedset, list)):
            # iterate over all items and append them to our resultset
            for entry in id:
                _results = self._get(
                    id=id.id,
                    work_dir=work_dir,
                    decoders=decoders,
                    group=group,
                    max_bytes=max_bytes,
                )
                if _results is not None:
                    # Append our results
                    results |= _results

        elif isinstance(id, NNTPArticle):
            # Support NNTPArticle Objects if they have an id defined
            if id.id:
                return self._get(
                    id=id.id,
                    work_dir=work_dir,
                    decoders=decoders,
                    group=group,
                    max_bytes=max_bytes,
                )

        elif isinstance(id, NNTPSegmentedPost):
            # Support NNTPSegmentedPost() Objects

            for part_no, _article in enumerate(id):
                # We need to download each article define
                if self.join_group:
                    for group in id.groups:
                        # Try each group
                        article = self._get(
                            id=_article,
                            work_dir=work_dir,
                            decoders=decoders,
                            group=group,
                            max_bytes=max_bytes,
                        )

                        if article:
                            # Found
                            break

                    # If we reach here we failed to fetch the item, so
                    # we'll try the next group
                    logger.warning(
                        'Failed to fetch segment #%.2d (%s)' % (
                            _article.no,
                            _article.filename,
                        ),
                    )

                    # mark our article invalid as part of it's response
                    article._is_valid = False

                else:
                    # Fetch our content
                    article = self._get(
                        id=_article,
                        work_dir=work_dir,
                        decoders=decoders,
                        max_bytes=max_bytes,
                    )

                if article:
                    if len(article) == 0:
                        logger.warning(
                            'No content found in segment #%.2d (%s)' % (
                                _article.no, _article.filename,
                            ),
                        )

                else:
                    # Uh oh, we failed to get anything; so just add
                    # our current article generated from the nzb file.
                    # Mark the object invalid and re-add it
                    logger.warning(
                        'Failed to retrieve segment (%s)' % (
                            id.filename,
                        ),
                    )

                    # mark our article invalid as part of it's response
                    article._is_valid = False

                # Store information from our nzb_article over top of
                # the contents in the new article we retrieved
                _article.load(article)

                # Store our article
                results.add(_article)

        elif isinstance(id, NNTPnzb):
            # We're dealing with an NZB File
            if not id.is_valid():
                return None

            # We iterate over each segment defined int our NZBFile and merge
            # them into 1 file. We do this until we've processed all the
            # segments and we return a list of articles
            for segpost_no, segpost in enumerate(id):
                for article in iter(segpost):
                    _results = self.get(article)
                    if _results is not None:
                        # Append our results
                        results |= _results

        else:
            # Unsupported
            return None

        # Return our results
        return results

    def _get_wrapper(self, id, work_dir, decoders=None, group=None,
                     max_bytes=0):
        """
        A wrapper to the __get() function so that we can capture it
        with our hooks

        """
        # Initialize a bound object
        response = None
        try:
            weak_content = weakref.proxy(id)

        except TypeError:
            # Some types just can't be converted into a weak reference
            # no problem...
            weak_content = id

        try:
            _response = self.hooks.call(
                'pre_get',
                work_dir=work_dir,
                group=group,
                max_bytes=max_bytes,
                content=weak_content,
            )

            # Allow a response over-ride which forces a denied stat
            if next((r for r in _response
                     if r is not False), None) is not False:

                response = self._get(
                    id=id,
                    work_dir=work_dir,
                    group=group,
                    max_bytes=max_bytes,
                )

            else:
                response = NNTPResponse(
                    NNTPResponseCode.HOOK_OVERRIDE,
                    'NNTP GET action blocked by hook',
                )

        finally:
            # Prepare our status for our hook call
            status = False
            if isinstance(response, NNTPArticle):
                status = response.header.article_exists()

            try:
                weak_response = weakref.proxy(response)

            except TypeError:
                # Some types just can't be converted into a weak reference
                # no problem...
                weak_response = response

            self.hooks.call(
                'post_get',
                content=id,
                group=self.group_name,
                response=weak_response,
                status=status,
            )

        # Return our response
        return response

    def _get(self, id, work_dir, decoders=None, group=None, max_bytes=0):
        """
        Download a specified message to the work_dir specified. This function
        returns an NNTPArticle() object if it can; see _get_article() for
        details.

        The article returned carries the statistics of fetching it.
        """
        with self._recording() as stats:
            article = self._get_article(
                id=id,
                work_dir=work_dir,
                decoders=decoders,
                group=group,
                max_bytes=max_bytes,
            )

        if article is not None and article.stats is not stats:
            # A backup server found it; add what we tried to what it took
            article.stats += stats

        return article

    def _get_article(self, id, work_dir, decoders=None, group=None,
                     max_bytes=0):
        """
        Download a specified message to the work_dir specified. This function
        returns an NNTPArticle() object if it can.

        None is returned if the content could not be retrieved due to an error
        or DMCA.

        This function will always attempt to retrieve missing content from any
        identified backup servers in the order they were saved.

        max_bytes is set to the number of bytes you want to have received
        before you automatically abort the connection and flag the object as
        having only been partially complete  Use this option when you need to
        inspect the first bytes of a binary file. Set this to zero to download
        the entire thing (this is the default value)

        """

        # Until we know better
        self.last_fetch = 'failed'

        if self.join_group and group is not None and group != self.group_name:
            # allow us to switch groups if nessisary
            if self.group(group)[0] is None:
                # Could not select group
                logger.error('Could not select group %s' % group)
                return None

        if not isdir(work_dir) and not mkdir(work_dir):
            logger.error('Could not create directory %s' % work_dir)
            return None

        if decoders is None:
            # Prepare a default list of decoders
            decoders = list()
            if not self.use_body:
                # BODY calls pull down header information too
                decoders.append(
                    CodecHeader(encoding=self.encoding, work_dir=work_dir),
                )

            decoders.extend([
                # Yenc Encoder/Decoder
                CodecYenc(work_dir=work_dir, max_bytes=max_bytes),
                # UUEncoder/Decoder
                CodecUU(work_dir=work_dir, max_bytes=max_bytes),
            ])

        elif decoders is False:
            # Disable all decoders
            decoders = list()

        elif isinstance(decoders, CodecBase):
            decoders = [decoders, ]

        if self.use_body:
            # Body returns the contents past the header;  Hence there will be
            # no header information to parse if this is the option used.

            # This is faster, but does not allow for some extra checking
            # we can do.
            response = self.send('BODY <%s>' % id, decoders=decoders, retries=None)
        else:
            # Article retrives the same content as Body plus the Header
            # too.
            response = self.send('ARTICLE <%s>' % id, decoders=decoders, retries=None)

        if response.is_success(multiline=True):
            # we're good to go!
            pass

        elif response.code in NNTPResponseCode.NO_ARTICLE:
            logger.warning('ARTICLE <%s> not found.' % id)
            if self._backups:
                # Try our backup servers in the sequential order they were
                # added in; if they all fail; then we return None
                article = next(
                    (result for result in (
                        b.get(id, work_dir, group=group)
                        for b in self._backups)
                     if result is not None), None)
                if article is not None:
                    # A backup server had it (and tracked fetching it)
                    self.last_fetch = 'found'
                    return article

            # Track the articles the server doesn't have
            self._action.missing += 1
            self.last_fetch = 'missing'
            return None

        else:  # response.code in NNTPResponseCode.SERVER_ERROR:
            # Close our connection
            self.close()

            logger.error('NNTP Fetch <%s> / %s' % (id, response))

            if self._backups:
                # Try our backup servers in the sequential order they were
                # added in; if they all fail; then we return None
                article = next(
                    (result for result in (
                        b.get(id, work_dir, group=group)
                        for b in self._backups)
                     if result is not None), None)
                if article is not None:
                    # A backup server had it (and tracked fetching it)
                    self.last_fetch = 'found'
                    return article

            # Track the articles we couldn't fetch
            self._action.failed += 1
            self.last_fetch = 'failed'
            return None

        # If we reach here, we have data we can work with; build our article
        # using the content retrieved.
        article = NNTPArticle(id=id, work_dir=work_dir)
        article.load(response)

        # Track what we fetched; the article carries the statistics of the
        # whole fetch (not just the command above)
        self._action.fetched += 1
        article.stats = self._action
        self.last_fetch = 'found'

        # Return the content retrieved
        return article

    def fetch_status(self):
        """
        Returns how our last article fetch went (see last_fetch); put it
        after a 'get' in the same NNTPConnectionRequest to learn why an
        article wasn't returned
        """
        return self.last_fetch

    def send(self, command, timeout=None, decoders=None, retries=0):
        """
        A Simple wrapper for sending NNTP commands to the server

        send() always returns the result of _recv() an internal function
        used to parse the content returned from the NNTP Server.

        article is only used if a list is passed in and the expected
        results are a multi-line response. It is populated with the
        data returned.

        retries is how many more times the command is sent (on a fresh
        connection) if the connection drops, stalls or gets garbage back.
        Set it to None to use the connection's own setting.  Only use
        retries for commands that are safe to send twice (not POST).
        """

        with self._recording() as stats:
            response = self._send(
                command, decoders=decoders, retries=retries, stats=stats)

        # Hand back what this command took
        response.stats = stats
        return response

    def _send(self, command, decoders=None, retries=0, stats=None):
        """
        Sends a command (retrying it if we can) and returns the response;
        see send() for details.

        - stats: the NNTPStatistics() our retries are tracked in
        """
        if not self.connected:
            # Attempt to establish a connection
            if not self.connect():
                logger.error('Could not establish a connetion to NNTP Server.')
                return NNTPResponse(
                    NNTPResponseCode.NO_CONNECTION,
                    'No Connection',
                )

        match = NNTP_AUTHINFO_CHECK.match(command)
        if not match:
            logger.debug('send(%s)' % command)

        else:
            logger.debug('send(AUTHINFO %s ********)' % (
                match.group('key'),
            ))

        if retries is None:
            # Use our connection defaults
            retries = self.retries

        attempt = 0
        while True:
            # A dropped connection forgets our group so we remember it here
            group = self.group_name

            response = self._exchange(command, decoders=decoders)
            if attempt >= retries or response.code not in (
                    NNTPResponseCode.FETCH_ERROR,
                    NNTPResponseCode.BAD_RESPONSE,
                    NNTPResponseCode.NO_CONNECTION,
                    NNTPResponseCode.CONNECTION_LOST):
                return response

            # If we reach here; our return code is considered recoverable.
            # We start again on a fresh connection since we can't trust
            # what is left over on this one.
            attempt += 1
            wait = self.retry_wait * (2 ** (attempt - 1))

            # Track our retries and the time we spend waiting on them
            stats.retries += 1
            stats.retry_wait += wait
            logger.warning(
                'Received NNTP error %d (%s); retrying in %.1fs... (%d/%d)' % (
                    response.code, response.code_str, wait, attempt, retries))

            # Reset our decoders for re-use
            for decoder in (decoders or []):
                decoder.reset()

            self.close()
            sleep(wait)

            if self.connect() and group and self.group_name != group:
                # Rejoin the group we were in
                self.group(group)

    def _exchange(self, command, decoders=None):
        """
        Sends a command once and returns the response the server gave
        """
        if not self.connected:
            return NNTPResponse(
                NNTPResponseCode.NO_CONNECTION,
                'No Connection',
            )

        # Soft reset in preparation for returned results
        self._soft_reset()

        # Some commands never get a multi-line response
        self._single_line = \
            SINGLE_LINE_COMMAND_RE.match(command) is not None

        if command.strip() != '.':
            # Track the commands we send (the dot ending a post is part of
            # the article, not a command)
            self._action.commands += 1

        try:
            sent = super().send(command + EOL)
            if not sent:
                return NNTPResponse(
                    NNTPResponseCode.BAD_RESPONSE,
                    'Invalid Command: "%s"' % command,
                )

            # Track what we sent
            self._action.tx_bytes += sent

            response = self._recv(timeout=0, decoders=decoders)

        except SocketException:
            # Connection Lost
            self.close()
            response = NNTPResponse(
                NNTPResponseCode.CONNECTION_LOST,
                'Connection Lost',
            )

        # Track why the command failed (if it did)
        if response.code == NNTPResponseCode.CONNECTION_LOST:
            if response.code_str == 'Connection Stalled':
                self._action.stalled += 1

            elif response.code_str == 'Connection Too Slow':
                self._action.too_slow += 1

            else:
                self._action.dropped += 1

        elif response.code in (
                NNTPResponseCode.FETCH_ERROR,
                NNTPResponseCode.BAD_RESPONSE):
            self._action.bad_replies += 1

        return response

    def _recv(self, decoders=None, timeout=None):
        """ Receive data, return #bytes, done, skip

        The response always returns the content in the format of
         (code, data)

        if response is set to an NNTPResponse object, it's populated
        with the data retrieved.
        """

        if not self.connected:
            logger.debug('_recv() %d: %s' % (
                NNTPResponseCode.NO_CONNECTION, 'No Connection',
            ))
            return NNTPResponse(
                NNTPResponseCode.NO_CONNECTION, 'No Connection',
            )

        if self.article_eod is True:
            # We've completed
            return NNTPResponse(self.last_resp_code, self.last_resp_str)

        # Pointers used to help identify the start and end
        # of the datablock read from the News Server
        head_ptr = 0

        # Pointer into the _buffer at the last new line
        # as we don't want to grab content from a line that hasn't
        # completely downloaded yet
        tail_ptr = 0

        # Tracks the size of the buffer
        total_bytes = 0

        # Our response object
        response = None

        # Our decoded result
        decoded = None

        # The maximum allowable bytes we can parse before we abort
        # this is calculated from the decoders
        max_bytes = 0

        if not decoders:
            decoders = []

        elif isinstance(decoders, CodecBase):
            decoders = [decoders, ]

        if len(decoders):
            # If at least 1 decoder is defined, we scan it for it's
            # max_bytes() value which tells us the maximum # of bytes we're
            # expecting before completing the transaction (early).  max_bytes()
            # is set from get() and _get() calls.

            # If we determine the end user has specified a max_bytes value,
            # then we adjust the total buffer read size.  This allows us to
            # force an earlier processing.  The minimum buffer size can never
            # be less than 2048
            max_bytes = max([d.max_bytes() for d in decoders])
            if max_bytes > 0:
                if max_bytes < NNTP_MIN_READ_BUFFER_SIZE:
                    self.MAX_BUFFER_SIZE = NNTP_MIN_READ_BUFFER_SIZE
                else:
                    self.MAX_BUFFER_SIZE = min(
                        [max_bytes, self.MAX_BUFFER_SIZE])

        logger.debug('Read Buffer set to %d bytes' % self.MAX_BUFFER_SIZE)

        #  We track the last codec activated using the codec_active
        #  variable.
        codec_active = None

        # Used to spot a connection that has stalled or slowed to a crawl
        idle_limit = timeout or self.stall_timeout
        last_data = window_start = monotonic()
        window_bytes = 0

        while not self.article_eod:
            # This loop really only takes effect if we aren't forced
            # to just wait for a server command line. Otherwise
            # we break out the first second we have data to process

            self._buffer.seek(0, SEEK_END)

            try:
                data = self.read(
                    max_bytes=self.MAX_BUFFER_SIZE - total_bytes,
                    timeout=max(idle_limit - (monotonic() - last_data), 0.01)
                    if idle_limit else timeout, retry_wait=None,
                )

            except (SocketException, SignalCaughtException):
                logger.debug('_recv() Connection Lost')
                return NNTPResponse(
                    NNTPResponseCode.CONNECTION_LOST,
                    'Connection Lost',
                )

            now = monotonic()
            if not data:
                if not self.connected:
                    logger.debug('_recv() Connection Lost')
                    return NNTPResponse(
                        NNTPResponseCode.CONNECTION_LOST,
                        'Connection Lost',
                    )

                if now - last_data < idle_limit:
                    # Keep waiting
                    continue

                logger.warning(
                    'Connection stalled; nothing received in %.1fs.' % (
                        now - last_data))
                self.close()
                return NNTPResponse(
                    NNTPResponseCode.CONNECTION_LOST,
                    'Connection Stalled',
                )

            last_data = now
            if self._action is not None:
                # Track what we receive
                self._action.rx_bytes += len(data)

            if self.min_rate and self.slow_window:
                # Check our average speed once every window
                window_bytes += len(data)
                if now - window_start >= self.slow_window:
                    rate = window_bytes / (now - window_start)
                    if rate < self.min_rate:
                        logger.warning(
                            'Connection too slow; averaged %d byte(s) per '
                            'second over %.1fs.' % (rate, now - window_start))
                        self.close()
                        return NNTPResponse(
                            NNTPResponseCode.CONNECTION_LOST,
                            'Connection Too Slow',
                        )

                    window_start = now
                    window_bytes = 0

            self._buffer.write(data)

            # Some Stats (TODO)
            _bytes = self._buffer.tell() - total_bytes
            total_bytes += _bytes
            logger.debug('_recv() %d byte(s) read.' % (_bytes))

            # # DEBUG START
            # self._buffer.seek(head_ptr, SEEK_SET)
            # logger.debug('Characters "%s"' % \
            #     ", ".join(['0x%0x' % ord(b) for b in self._buffer.read()]))
            # # DEBUG END

            ##################################################################
            #                                                                #
            #  Step: 1: Get the status code from the NNTP Server if we don't #
            #           already have it.                                     #
            #                                                                #
            ##################################################################
            if not self.last_resp_code:

                # Reset our payload flag
                self.payload_gzipped = None

                # Seek to head of buffer
                self._buffer.seek(head_ptr, SEEK_SET)

                # Extract Header Response
                data = self._buffer.readline()

                # Only the first time do we remove the first entry
                # because this is our header
                match = NNTP_RESPONSE_RE.match(
                    data.decode(self.encoding).strip())
                if match:
                    # Generate Reponse code
                    self.last_resp_code = int(match.group('code'))
                    self.last_resp_str = match.group('desc')

                    # Adjust head ptr to end of line
                    head_ptr = self._buffer.tell()

                else:
                    if self.connected:
                        # Generate erroneous reponse code
                        self.last_resp_code = NNTPResponseCode.FETCH_ERROR
                        if len(data):
                            self.last_resp_str = 'Bad Response: (len=%d)' % (
                                len(data),
                            )
                        else:
                            self.last_resp_str = 'Bad Response: <nothing>'

                        # Drop connection
                        self.close()

                    else:
                        self.last_resp_code = NNTPResponseCode.CONNECTION_LOST
                        if len(data):
                            self.last_resp_str = \
                                'Connection Lost: (len=%d)' % (len(data))
                        else:
                            self.last_resp_str = 'Connection Lost: <nothing>'

                    # Toggle End of Article and Flush Results
                    self.article_eod = True

                    logger.debug('_recv() %d: %s' % (
                        self.last_resp_code, self.last_resp_str))

                    # Return our response
                    return NNTPResponse(
                        self.last_resp_code, self.last_resp_str,
                    )

                logger.debug('_recv() %d: %s' % (
                    self.last_resp_code, self.last_resp_str))

                # Detect Compression from NNTP response if possible
                if GZIP_COMPRESS_RE.search(self.last_resp_str):
                    logger.debug('_recv() Detected compressed payload.')
                    # GZIP response type; toggle our flag
                    self.payload_gzipped = True

                else:
                    # Uncompressed response type; toggle our flag
                    self.payload_gzipped = False

            # Non-Multiline responses mean were done now.
            if self._single_line or self.last_resp_code \
                    not in NNTPResponseCode.SUCCESS_MULTILINE:
                # We're done; flush results and move on
                self.article_eod = True

                # Safety: just clean anything remaining
                while self.can_read():
                    try:
                        self.read(timeout=timeout, retry_wait=None)

                    except (SocketException, SignalCaughtException):
                        # Connection lost
                        return NNTPResponse(
                            NNTPResponseCode.NO_CONNECTION,
                            'Connection Lost',
                        )

                # Return our response
                return NNTPResponse(self.last_resp_code, self.last_resp_str)

            # Store our can_read() flag; a response that already ends with
            # the end of data marker is complete so there is no need to
            # wait around for more
            if self._has_eod(total_bytes, head_ptr):
                can_read = False

            else:
                can_read = self.can_read(3)

            if response is None:
                # Initialize our response object on first iteration
                # through loop
                response = NNTPResponse(
                    self.last_resp_code,
                    self.last_resp_str,
                    work_dir=self.work_dir,
                )

            # We have multi-line code to store fill our buffer before
            # proceeding.
            if can_read and total_bytes < self.MAX_BUFFER_SIZE:
                # Keep storing content until we've either reached the end
                # or filled our buffer
                # logger.debug('_recv() Data pending on server...')
                continue

            ##################################################################
            #                                                                #
            #  Step: 2: Check for END OF DATA (EOD) and END OF LINE (EOL).   #
            #           If we find any; trim them and mark flags accordingly #
            #                                                                #
            ##################################################################

            # Initialize our EOL Flag
            eol = False

            if not can_read and total_bytes > 0:
                # Offset
                offset = min(5, total_bytes)

                # The below accomplishes the same results as abs()
                # but does it at a much faster speed (screw looking pythonic)
                if offset > 0:
                    offset = -offset

                self._buffer.seek(offset, SEEK_END)

                # Now we read what we can from the buffer
                data = self._buffer.read()

                if offset == -5:
                    # Adjust offset if nessisary
                    offset += 1

                # Check for the End of Data
                eod_results = EOD_RE.search(data)
                if eod_results:
                    # We can truncate here to trim the EOD off
                    self._buffer.truncate(
                        total_bytes - len(eod_results.group(1)),
                    )

                    # Correct Total Length
                    total_bytes = self._buffer.seek(0, SEEK_END)

                    # Toggle flag so we can break out
                    self.article_eod = True

                    # Toggle our end of line flag incase it doesn't
                    # match below (this is okay)
                    eol = True

                    # Update Offset
                    offset = min(2, total_bytes)

                    # The below accomplishes the same results as abs()
                    # does but at a much faster speed (screw looking
                    # pythonic)
                    if offset > 0:
                        offset = -offset

                    self._buffer.seek(offset, SEEK_END)

                    # Now we read what we can from the buffer
                    data = self._buffer.read()

                # Check for end of line
                eol_results = EOL_RE.search(data)
                if eol_results:
                    # We can truncate here to trim the EOL off
                    self._buffer.truncate(
                        total_bytes - len(eol_results.group(1)),
                    )

                    # Correct Total Length
                    total_bytes = self._buffer.seek(0, SEEK_END)

                    # We have our end of line
                    eol = True

                    if self.last_resp_code in NNTPResponseCode.SUCCESS \
                       and not self.can_read():
                        # If we're still not seeing more data pending on the
                        # server; then we can safely assume we're done. This
                        # handles the situations where we retrieved a
                        # multi-line response code yet we didn't get one.
                        #
                        # the GROUP NNTP server command is known to return 211
                        # which just returns 1 line, yet the command LISTGROUP
                        # also returns 211 too and returns a listing
                        self.article_eod = True

            ##################################################################
            #                                                                #
            #  Step: 3: We now have to handle situations where we never      #
            #           found the EOD or EOL markers. This is a perfectly    #
            #           okay situation so long as our buffer is full.        #
            #                                                                #
            #           If our buffer is full, then we need to mark the tail #
            #           of our buffer because we don't want to handle the    #
            #           content that hasn't completely downloaded yet.       #
            #                                                                #
            ##################################################################

            # the tail_ptr is used to track the last officially completed line
            # as we don't want to process a line that hasn't completely
            # downloaded yet.
            tail_ptr = total_bytes

            if not self.payload_gzipped and \
               total_bytes >= self.MAX_BUFFER_SIZE and not eol:
                # We have a full buffer and there is most likely
                # a lot more content to still download; we need to find
                # the last new line

                # Number of characters to look back (chunk):
                chunk_size = self.READBACK_CHUNK_SIZE

                # Current Matched offset (<0 means no match yet)
                offset = -1

                # Our reference pointer
                ref_ptr = tail_ptr - head_ptr
                while offset < 0:
                    # Keep iterating backwards by chunk sizes
                    # until we get a match

                    # a few checks to make sure we don't look past
                    # our head_ptr
                    chunk_size = min(ref_ptr - head_ptr, chunk_size)
                    ref_ptr -= (chunk_size + 1)

                    if ref_ptr < head_ptr:
                        # Nothing more to look back at; we reached
                        # the head of our buffer. do nothing; just let
                        # process all of the data we received.
                        break

                    chunk_ptr = self._buffer.seek(ref_ptr, SEEK_SET)
                    offset = self._buffer.read(chunk_size).rfind(b'\n')
                    if offset >= 0:
                        # We found the new line; safe our offset and break
                        tail_ptr = offset + chunk_ptr
                        break

            if not self.payload_gzipped and not self.article_eod \
                    and (tail_ptr - head_ptr) > 0 \
                    and total_bytes < self.MAX_BUFFER_SIZE \
                    and tail_ptr < self.MAX_BUFFER_SIZE and self.can_read(1):
                # Astraweb is absolutely terrible for sending a little
                # bit more data a few seconds later. This is a final
                # call to try to handle these stalls just before the last
                # few bytes are sent.
                continue

            # Ensure we're pointing at the head of our buffer
            self._buffer.seek(head_ptr, SEEK_SET)

            # Compression Support
            if (tail_ptr - head_ptr) > 0:
                if self.payload_gzipped is True:

                    dc_obj = decompressobj()
                    try:
                        self._data.write(self._unstuff(dc_obj.decompress(
                            self._buffer.read(tail_ptr - head_ptr),
                        )))
                        logger.debug("NNTP ZLIB decompression successful.")

                    except ZlibException:
                        # Decompression error; since compression is only used
                        # when retrieving server-side listings; it's best to
                        # just alert the end user and move along
                        logger.error(
                            '_recv() %d byte(s) ZLIB decompression failure.'
                            % (_bytes),
                        )

                        # Convert our response to that of an response Fetch
                        # Error
                        return NNTPResponse(
                            NNTPResponseCode.FETCH_ERROR,
                            'Fetch Error',
                        )
                else:
                    # No compression
                    self._data.write(self._unstuff(
                        self._buffer.read(tail_ptr - head_ptr)))

                # Truncate our original buffer by striping out what was already
                # processed from it
                self._buffer = BytesIO(self._buffer.read())

                # Correct Total Length
                total_bytes = self._buffer.tell()

            ##################################################################
            #                                                                #
            #  Step: 4: This step processes the extra content founds from    #
            #           the NNTP stream based on the decoders passed in.     #
            #                                                                #
            ##################################################################

            self._data_len = self._data.seek(0, SEEK_END)
            d_head = self._data.seek(0, SEEK_SET)
            while d_head < self._data_len and self.connected:

                if codec_active is None:
                    # Scan our decoders (sequentially) and detect our match
                    # If we get a match, then we want to save it in the
                    # codec_active variable. We use this to track the data
                    # found for processing.

                    # Get our data
                    d_head = self._data.tell()
                    data = self._data.readline()

                    # This line scans the line of data we read and determines
                    # what kind of data it is (yEnc, Headers, etc) based on the
                    # decoders passed into _recv()
                    codec_active = next(
                        (d for d in decoders
                            if d.detect(data) is not None), None)
                    if codec_active:
                        logger.debug('Decoding using %s' % type(codec_active))

                # Based on previous check; we may actually have an active codec
                # now if we don't have one yet; well want to store the content
                # into our body and move along
                if codec_active is None:
                    # All data matched that no decoder took ownership off is
                    # saved into our body
                    response.body.write(data)

                    # Update d_head value
                    d_head = self._data.tell()

                    # Track lines processed
                    self.line_count += 1
                    continue

                # Adjust pointer for processing
                self._data.seek(d_head, SEEK_SET)

                # Begin decoding content
                decoded = codec_active.decode(self._data)

                # Adjust our pointer
                d_head = self._data.tell()

                # If we're at the end of our buffer; we can go ahead and clear
                # it
                if d_head >= self._data_len:
                    # Reset our data object once we're done parsing
                    self._data.truncate(0)
                    # Adjust pointer for processing
                    self._data.seek(0, SEEK_SET)

                # A little Decoder 101 for anyone reading my code; the below
                # identifies the possible return types from a Codec
                # (specifically the Decoder):
                #
                #     - NNTPContent:  We're done with the decoder, everything
                #                     was correctly processed. we're handled
                #                     and NNTPContent object.
                #
                #     - True:         We're still expecting more content
                #                     before we're finished, don't adjust
                #                     this from being the 'active' decoder!
                #
                #     - False:        Oh boy; we had a problem and we
                #                     couldn't deal with it. We're finished
                #                     with the decoder; the data is bad.
                #
                #     - None:         A graceful way of saying that we're done
                #                     with the decoder. Like an abort if you
                #                     will
                #
                if decoded is None:
                    # The Codec has completed and has nothing to return for
                    # storing. We gracefully move along at this point.
                    logger.debug(
                        'Decoding complete (no results) / %s' % codec_active,
                    )

                    # we're done; do nothing more
                    codec_active = None

                    continue

                elif decoded is True:
                    # We're expecting more data a long as the End of Data
                    # (EOD) flag hasn't been picked up.
                    if not self.article_eod:
                        logger.debug(
                            'Expecting more data to build results with...',
                        )
                        continue

                    # If we reach here, we've reached the end of the line
                    # a half (or possibly full block of data), but we just
                    # need to close off what we have. fall through and
                    # handle our codec; the HEAD call for example doesn't
                    # return an empty line after the the header like expected
                    # so it's normal to reach here.  Corrupted stuff still
                    # needs to be saved; and we can hope the par files can
                    # rebuild it if it is infact damaged.
                    if isinstance(codec_active, CodecBase):
                        # Store our decoded content (complete or not)
                        decoded = codec_active.decoded

                # If we got here, our content was good; we can safely
                # toggle our codec_active back to off since we're going to
                # be expecting more data now
                logger.debug('Decoding completed. %s' % codec_active)
                codec_active = None

                if not isinstance(decoded, NNTPContent):
                    # We ignore any other return type, Decoders should always
                    # return an NNTPContent type; anything else is considered
                    # moot
                    continue

                # Add to our NNTPContent() to our decoded set associated with
                # our NNTPResponse() object
                response.decoded.add(decoded)

                if not isinstance(decoded, NNTPMetaContent):
                    # Print a representative string into the body to identify
                    # the content parsed out (and decoded)

                    if max_bytes > 0:
                        # We're instructed to only retrieve 'some' content and
                        # abort for peaking purposes; so we're done now.
                        self.close()
                        return response

        # Track lines processed
        self.line_count += len(self.lines)

        logger.debug('Returning Response %s' % response)
        return response

    @contextmanager
    def _recording(self):
        """
        Records the statistics of an action (a command, a fetch, a post)

        - Yields the NNTPStatistics() object the action is tracked in.
        - An action started inside another one (the commands making up a
          post, logging in again during a retry) is added to the outer
          action when it finishes.
        - Outer actions are timed and added to our running total (stats).
        """
        # Remember the action we're inside of (if any)
        outer = self._action
        stats = self._action = NNTPStatistics()
        started = stats.start()

        try:
            yield stats

        finally:
            # Go back to the outer action
            self._action = outer

            if outer is None:
                # Only outer actions are timed; otherwise the time spent
                # would be counted once for each level
                stats.stop(started)
                self.stats += stats

            else:
                stats.stop()
                outer += stats

    def close(self):
        """
        Drop the connection
        """

        # Reset our other flags
        self.group_name = None
        self.group_count = 0
        self.group_head = 0
        self.group_tail = 0
        self.group_index = 0

        # Reset Can Post Flag (if set)
        self.can_post = False

        if self.connected:
            logger.debug('Closing Connection')
            try:
                # Prevent Recursion by calling parent send()
                super().send('QUIT' + EOL)
            except:
                pass

        # SocketBase close() tries to grab the last
        # content on the socket before closing it
        return super().close()

    def _stuff(self, chunks):
        """
        Prepares content for posting (RFC 3977 dot stuffing)

        - Text is encoded using our character set.
        - Lines starting with a dot get a second one so the server doesn't
          mistake them for the end of the article.
        """
        line_start = True
        for chunk in chunks:
            if isinstance(chunk, str):
                chunk = chunk.encode(self.encoding)

            if not chunk:
                continue

            if line_start and chunk[:1] == b'.':
                chunk = b'.' + chunk

            chunk = chunk.replace(b'\n.', b'\n..')
            line_start = chunk[-1:] == b'\n'
            yield chunk

    def _unstuff(self, data):
        """
        Restores lines the server dot stuffed (RFC 3977); a line starting
        with two dots is given back with just one
        """
        if not data:
            return data

        # Track where this data leaves off (based on what was received); a
        # line made up of a single dot (so far) may still be followed by
        # the dot that was stuffed
        newline = data.rfind(b'\n')
        if newline >= 0:
            dot_line = data[newline + 1:] == b'.'
        else:
            dot_line = self._line_start and data == b'.'

        line_start = data[-1:] == b'\n'

        if self._line_start and data[:2] == b'..':
            data = data[1:]

        elif self._dot_line and data[:1] == b'.':
            # The previous data ended with the first dot of a stuffed line
            data = data[1:]

        self._line_start = line_start
        self._dot_line = dot_line
        return data.replace(b'\n..', b'\n.')

    def _has_eod(self, total_bytes, head_ptr=0):
        """
        Returns True if our buffer ends with the end of data marker

        - head_ptr is where the response data starts in our buffer
        """
        if total_bytes < 3:
            return False

        self._buffer.seek(-min(5, total_bytes), SEEK_END)
        tail = self._buffer.read()

        if not self.payload_gzipped:
            return EOD_LINE_RE.search(tail) is not None

        if not tail.endswith(b'.\r\n'):
            return False

        # Compressed data can hold anything, so make sure the compressed
        # block in front of our marker is really complete
        self._buffer.seek(head_ptr, SEEK_SET)
        dc_obj = decompressobj()
        try:
            dc_obj.decompress(self._buffer.read(total_bytes - head_ptr - 3))

        except ZlibException:
            return False

        return dc_obj.eof and not dc_obj.unused_data

    def _soft_reset(self):
        """
        Reset tracking items
        """
        self.article = {}
        self.article_eod = False
        self._single_line = False

        # Whether the next data we receive starts a new line (and whether
        # the line so far is just a single dot)
        self._line_start = True
        self._dot_line = False
        self.article_fname = None

        # Reset our Buffers (back to the start too; truncating alone leaves
        # the position where it was, and what is written next would follow
        # a gap of NUL bytes)
        self._buffer.seek(0)
        self._buffer.truncate(0)
        self._data.seek(0)
        self._data.truncate(0)
        self._data_len = 0

        # Our response code (integer) returned by the NNTPServer on the last
        # request made
        self.last_resp_code = None

        # Our response string returned by the NNTPServer on the last request
        # made
        self.last_resp_str = ''

        # A flag set to True if the payload to follow the response is gzipped.
        # GZipped responses require a bit more CPU crunching but much less
        # bandwidth. This value is set to None if there simply wasn't a payload
        # at all (or an initialization value)
        self.payload_gzipped = None

        self.lines = []
        self.line_count = 0

        # Empty the receive buffer if anything is pending
        while self.can_read():
            try:
                self.read()

            except (SocketException, SignalCaughtException):
                pass

    def _hard_reset(self, wait=True):
        """
        Drop the connection and re-establish it
        """
        self.close()
        self.connect()

    def __del__(self):
        """
        Handle Deconstruction
        """
        self.close()

    def __str__(self):
        return '%s://%s@%s:%d' % (
            self.protocol, self.username, self.host, self.port)

    def __repr__(self):
        """
        Return a printable object
        """
        return '<NNTPConnection id=%d url="%s://%s@%s:%d" />' % (
            id(self),
            self.protocol,
            self.username,
            self.host,
            self.port,
        )
