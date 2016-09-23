# -*- coding: utf-8 -*-
#
# Simplifies communication to and from an NNTP Server
#
# Copyright (C) 2015-2016 Chris Caron <lead2gold@gmail.com>
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
from blist import sortedset

from newsreap.NNTPHeader import NNTPHeader
from newsreap.NNTPArticle import NNTPArticle
from newsreap.NNTPResponse import NNTPResponse
from newsreap.NNTPResponse import NNTPResponseCode
from newsreap.NNTPContent import NNTPContent
from newsreap.NNTPSegmentedPost import NNTPSegmentedPost
from newsreap.NNTPMetaContent import NNTPMetaContent
from newsreap.NNTPFilterBase import NNTPFilterBase
from newsreap.NNTPIOStream import NNTPIOStream
from newsreap.NNTPIOStream import NNTP_SUPPORTED_IO_STREAMS
from newsreap.NNTPIOStream import NNTP_DEFAULT_ENCODING

from newsreap.SocketBase import SocketBase
from newsreap.SocketBase import SocketException
from newsreap.SocketBase import SocketRetryLimit
from newsreap.SocketBase import SignalCaughtException
from newsreap.Utils import mkdir
from newsreap.Utils import SEEK_SET
from newsreap.Utils import SEEK_END
from newsreap.NNTPnzb import NNTPnzb
from newsreap.NNTPSettings import DEFAULT_TMP_DIR

# Codecs
# These define the messages themselves.
from newsreap.codecs.CodecBase import CodecBase
from newsreap.codecs.CodecHeader import CodecHeader
from newsreap.codecs.CodecArticleIndex import CodecArticleIndex
from newsreap.codecs.CodecArticleIndex import XoverGrouping
from newsreap.codecs.CodecGroups import CodecGroups
from newsreap.codecs.CodecUU import CodecUU
from newsreap.codecs.CodecYenc import CodecYenc

# Logging
import logging
from newsreap.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)

# Defines the end of data delimiter
EOL = '\r\n'
EOD = '\r\n.'
EOL_RE = re.compile(r'([\r]?\n)')
EOD_RE = re.compile(r'(\.([\r]?\n))$')

# Splitting doesn't work well with the parenthesis
# the below fixes this
EOLS_RE = re.compile(r'[\r]?\n')

# How many consecutive misses in a row do we allow while trying to retrieve
# historic messages (based on time) do we allow before assuming that we've
# exceeded the the maximum retention area. This is used in the seek_by_date()
# function. The larger this value; the slower the search is; however the
# greater level of accuracy obtained.
NNTP_FETCH_BY_DATE_MAX_MISSES = 20

# All responses are handled from usenet via the following:
NNTP_RESPONSE_RE = re.compile(
    r'^[^0-9]*(?P<code>[1-9][0-9]{2})[\s:]*(?P<desc>.*[^\s])\s*$',
    re.MULTILINE,
)

# Group Response (when we switch to a group)
NNTP_GROUP_RESPONSE_RE = re.compile(
    r'(?P<count>[0-9]+)\s+(?P<low>[0-9]+)\s+' +
    r'(?P<high>[0-9]+)\s+(?P<name>.*[^\s]*[^.])(\.|\s)*$',
)

# Scans against the status message
GZIP_COMPRESS_RE = re.compile(
    r'.*COMPRESS\s*=\s*GZIP.*',
    re.IGNORECASE,
)

# Scans against the status message to detect if posting is allowed
POSTING_OK_RE = re.compile(
    r'.*POSTING OK.*',
    re.IGNORECASE,
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

# Defines the number of lines to scan into a message
# to try and find the =ybegin entry (identifing a YENC message)
SCAN_FROM_HEAD = 40

# scans from the bottom to the top looking for the ending
# of a yenc message
SCAN_FROM_TAIL = 10

# The number of XOVER Server retries to make in the event the
# query fails
NNTP_XOVER_RETRIES = 5

# RAW_TEXT_MESSAGE = re.compile(
#     r'^message-id:\s*<?([^>]+)>?\s*$',
# )

# Used with BytesIO seek().  These variables
# are part of python v2.7 but included here for python v2.6
# support too.

# TODO: Change all references of 'id' to article_id; this sadly exists
#      everywhere, id is a reserved function id() and is and is pretty
#      ambiguous.  article_id would explicity identify what the id
#      represents


class NNTPConnection(SocketBase):
    """
        NNTPConnection is a class that wraps and eases the comunication
        to and from an NNTP Server.

        - Usenet Compression supported (GZIP) if the usenet server allows
          for it.

        - SSL Support (you'll ideally want to use the default of TLSv1
          for security reasons; but can support SSLv3 too if the remote
          server can't handle TLSv1).

        - It uses btree+ to store all of it's fetched results using a
          fantastic addon called blist which never maded it into
          python (https://www.python.org/dev/peps/pep-3128/)

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
                 filters=None, *args, **kwargs):
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
        """

        # get connection mode
        if secure:
            kwargs['secure'] = True
            self.protocol = 'nntps'
        else:
            kwargs['secure'] = False
            self.protocol = 'nntp'

        # Default Filters
        self.filters = []

        # Filter Preparation
        if isinstance(filters, NNTPFilterBase):
            self.filters.append(filters)

        elif isinstance(filters, (list, tuple)):
            self.filters.extend([
                x for x in filters if isinstance(x, NNTPFilterBase)
            ])

        # Initialize the Socket Base Class
        super(NNTPConnection, self).__init__(*args, **kwargs)

        # Store the default encoding
        self.encoding = encoding
        if not self.encoding:
            self.encoding = NNTP_DEFAULT_ENCODING

        # SSL Does not like unicode characters, so we convert the content
        # to a characterset so we don't get any handshaking errors
        if isinstance(username, unicode):
            username = username.encode(self.encoding)
        self.username = username

        # SSL Does not like unicode characters, so we convert the content
        # to a characterset so we don't get any handshaking errors
        if isinstance(password, unicode):
            password = password.encode(self.encoding)
        self.password = password

        # Attempt to gzip the connection
        if isinstance(iostream, basestring):
            try:
                self._iostream = iostream.lower()
                if self._iostream not in NNTP_SUPPORTED_IO_STREAMS:
                    # Default
                    self._iostream = NNTPIOStream.RFC3977_GZIP
                    logger.warning('An unknown iostream was specified; ' +
                                   "using default: '%s'." % self._iostream)

            except (TypeError, ValueError):
                # Default
                self._iostream = NNTPIOStream.RFC3977_GZIP
                logger.warning('A malformed iostream was specified; ' +
                                   "using default: '%s'." % self._iostream)

        elif iostream:
            # Default
            logger.warning('An invalid iostream was specified; ' +
                "using default: '%s'." % self._iostream)
            self._iostream = NNTPIOStream.RFC3977_GZIP

        else:
            # iostream is None (0, or False)
            # used RFC3977 Standards but without Compresssion
            logger.info('An invalid iostream was specified; ' +
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

        self.last_resp_code = None
        self.last_resp_str = ''

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

        try:
            # call _connect()
            result = self._connect(*args, **kwargs)

        except SocketRetryLimit:
            # We can not establish a connection and never will
            return False

        return result


    def _connect(self, *args, **kwargs):
        """
        _connect() performs the actual connection with little
        error checking.  This is intended so that if we lose the
        connection we can attempt to resume where we left off
        gracefully.
        """

        try:
            if not super(NNTPConnection, self).connect(*args, **kwargs):
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

    def post(self, payload):
        """
        Allows posting content to a NNTP Server

        """
        if not self.can_post:
            # 480 Transfer permission denied
            return (480, 'Transfer permission denied.')

        response = self.send('POST')
        if response.code not in NNTPResponseCode.PENDING:
            # We got a 400 or 500 error, no good
            logger.error('Could not post content / %s' % response)
            return response

        # The server
        # header=('From: %s' % terry@richard.geek.org.au

        # STUB: TODO
        # payload should be a class that takes all the required
        # items nessisary to post. It should also take a BytesIO
        # stream it can use to to upload with.  Or a filename
        # and the class will take care of opening it, streaming it's
        # contents and closing it afterwards.
        return NNTPResponse(239, 'Article transferred OK')

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

        if isinstance(name, unicode):
            # content should be byte encoded
            name = name.encode(self.encoding)

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

        elif isinstance(filters, basestring):
            filters = (filters,)

        elif isinstance(filters, re._pattern_type):
            filters = (filters,)

        elif not isinstance(filters, (tuple, list)):
            filters = list(filters)

        # Iterate over each item in the list and compile it now
        _filters = []
        for filter in filters:
            if isinstance(filter, re._pattern_type):
                _filters.append(filter)

            elif isinstance(filter, basestring):
                try:
                    filter = r'^.*%s.*$' % re.escape(filter)
                    _filters.append(
                        re.compile(filter, flags=re.IGNORECASE),
                    )
                    logger.debug('Compiled group regex "%s"' % filter)

                except:
                    logger.error(
                        'Invalid group regular expression: "%s"' % filter,
                    )
            else:
                logger.error(
                    'Ignored group expression: "%s"' % filter,
                )

        if self._grouplist is None or not lazy:
            # Send LIST ACTIVE command
            response = self.send('LIST ACTIVE', decoders=[
                CodecGroups(work_dir=self.work_dir),
            ])
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
                if not next((True for f in _filters \
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
            logger.warning(
                'No entries found at (or after) date(%s) in group %s' % (
                    refdate.strftime('%Y.%m.%d %H:%M:%S'),
                    group,
            ))
            self.group_index = self.group_head
            return self.group_head

        self.group_index = index
        logger.info('Matched index: %d' % (self.group_index))
        return self.group_index

    def _seek_by_date(self, refdate, head=None, tail=None):
        """

        This ideally shouldn't be called directly; users should
        call the seek_by_date() function instead

        refdate must be a datetime object.

        How it's done:
        Using recursion; we drill down to a specific location but
        since usenet 'sort-of' sorted by time. Once we get within
        X records (defined by a global variable) we need to scan
        within those results to match our time (or as close as we
        can get to it without going over. Since our content is
        stored in a btree; this is relatively speedy.

        """

        if head is None:
            # Initial Entry
            head = self.group_head

        if tail is None:
            # Initial Entry
            tail = self.group_tail

        logger.debug('Scanning for index at %s in %d article(s)' % (
            refdate.strftime('%Y-%m-%d %H:%M:%S'),
            tail-head,
        ))

        # initalize filter count
        filter_count = tail - head

        # Get the middle
        start = head + (filter_count/2) - (NNTP_FETCH_BY_DATE_MAX_MISSES/2)
        end = start + min(NNTP_FETCH_BY_DATE_MAX_MISSES, filter_count)

        response = self.xover(
            start=start,
            end=end-1,
            sort=XoverGrouping.BY_TIME,
        )

        if response is None:
            # Nothing Retrieved
            return -1
            # if response.code != 423:
            #    # 423 means there were no more items to fetch
            #    # this is a common error that even this class produces
            #    # and therefore we do not need to create a verbose
            #    # message from it.
            #    logger.error('NNTP Server responded %s' % response)
            # return -1

        # Deal with our response
        if len(response):
            # Get string equivalent
            _refdate = refdate.strftime('%Y%m%d%H%M%S:0000000000')

            # Get keys
            # (this returns an blist.sortedset() object): Big O(n)
            # This is 'very' fast operation since we're not dealing with a
            # dict()
            _refkeys = response.keys()

            # logger.debug('total=%d, left=%s, right=%s, ref=%s' % (
            #    end-start,
            #    _refkeys[0],
            #    _refkeys[-1],
            #    _refdate,
            # ))

            #
            # Decisions
            #
            if end-start < NNTP_FETCH_BY_DATE_MAX_MISSES and \
               len(_refkeys) < NNTP_FETCH_BY_DATE_MAX_MISSES:
                # We're in great shape if we reach here
                # It means we've narrowed off our search to just
                # the small window for our match
                return _refkeys.bisect_left(_refdate) + start

            elif _refkeys[-1] < _refdate:
                # look closer to the tail (our right)
                logger.debug('%s > %s / JUMP RIGHT' % (
                    _refdate,
                    _refkeys[-1],
                ))

                if tail-end > 0:
                    result = self._seek_by_date(refdate, end-1, tail)
                else:
                    result = tail

                while result < 0:
                    # SHIFT LEFT
                    logger.debug('SHIFT LEFT')
                    tail -= ((tail - end) / 2)
                    if tail-end > 0:
                        result = self._seek_by_date(refdate, end-1, tail)
                    else:
                        result = tail
                        break

                return result

            elif _refkeys[0] >= _refdate:
                # look closer to the front
                logger.debug('%s < %s / JUMP LEFT' % (
                    _refdate,
                    _refkeys[0],
                ))

                if start-head > 1:
                    result = self._seek_by_date(refdate, head, start)
                else:
                    result = start

                while result < 0:
                    # SHIFT RIGHT
                    logger.debug('SHIFT RIGHT')
                    head += ((start - head) / 2)
                    if start-head > 1:
                        result = self._seek_by_date(refdate, head, start)
                    else:
                        result = start
                        break

                return result

        # We recursively scanned too far in one
        # direction; we need to
        return -1

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
        count = min(count, self.group_tail-self.group_index)

        if count <= 0:
            # 423: Empty Range
            return NNTPResponse(423, 'Empty Range')

        # Fetch our results and adjust our pointer accordingly
        response = self.xover(self.group_index, self.group_index+count-1)
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
        count = min(count, self.group_tail-self.group_index-self.group_head)

        if count <= 0:
            # 423: Empty Range
            return NNTPResponse(423, 'Empty Range')

        # Fetch our results and adjust our pointer accordingly
        response = self.xover(self.group_index-count, self.group_index-1)
        if response:
            self.group_index -= count

        # Return results
        return response

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
                  subject: u'a subject line in unicode',
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

        if end is None:
            end = self.group_tail

        elif isinstance(end, datetime):
            # Get the end by date
            end = self.seek_by_date(end)

        # Default Ranges (if not otherwise specified)
        if start is None:
            start = self.group_head

        elif isinstance(start, datetime):
            # Get the start by date
            start = self.seek_by_date(start)

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

        response = self.send(
            'XOVER %d-%d' % (start, end),
            decoders=[
                CodecArticleIndex(
                    filters=self.filters,
                    sort=sort,
                    encoding=self.encoding,
                    work_dir=self.work_dir,
                ),
            ],
            retries=NNTP_XOVER_RETRIES,
        )

        if response.code not in NNTPResponseCode.SUCCESS_MULTILINE:
            logger.error('Failed to interpret NNTP XOVER response.')
            return None

        try:
            # return our decoded content
            return response.decoded.pop().content

        except IndexError:
            # TODO: Debug; wtf is going on
            return None

    def stat(self, id, full=None, group=None):
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

        if full is None:
            # default
            full = self.use_head

        if self.join_group and group is not None and group != self.group_name:
            # allow us to switch groups if nessisary
            if self.group(group)[0] is None:
                # Could not select group
                logger.error('Could not select group %s' % group)
                return None

        if not full:
            response = self.send('STAT <%s>' % id)
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
                    CodecHeader(encoding=self.encoding, work_dir=self.work_dir),
                ],
            )

            if response.is_success(multiline=True):
                # Return our content
                return response.decoded.pop().content

        if response.code in NNTPResponseCode.NO_ARTICLE:
            if self._backups:
                # Try our backup servers in the sequential order they were
                # added in; if they all fail; then we return None
                logger.warning(
                    'ARTICLE <%s> not found; checking backups.' % id,
                )
                return next((b.article for b in self._backups \
                        if b.stat(id, group) not in (None, False)), None)

            logger.warning('ARTICLE <%s> not found.' % id)
            return False

        elif response.code in NNTPResponseCode.SERVER_ERROR:
            # We have a problem
            self.close()
            logger.error('NNTP Error %s' % response)
            if self._backups:
                # Try our backup servers in the sequential order they were
                # added in; if they all fail; then we return None
                return next((b.article for b in self._backups \
                        if b.stat(id, group) not in (None, False)), None)
            return False

        # Return
        return True

    def get(self, id, work_dir=None, group=None):
        """
        A wrapper to the _get call allowing support for more then one type
        of object (oppose to just _get() which only accepts the message id

        This function returns the articles as a sortedset() containing the
        downloaded content based on what was passed in.

        """
        if work_dir is None:
            # Default
            work_dir = self.work_dir

        if isinstance(id, basestring):
            # We're dealing a Message-ID (Article-ID)
            return self._get(id=id, work_dir=work_dir, group=group)

        # A sorted list of all articles pulled down
        results = sortedset(key=lambda x: x.key())

        if isinstance(id, (set, tuple, sortedset, list)):
            # iterate over all items and append them to our resultset
            for entry in id:
                _results = self._get(id=id.id, work_dir=work_dir, group=group)
                if _results is not None:
                    # Append our results
                    results |= _results

        elif isinstance(id, NNTPArticle):
            # Support NNTPArticle Objects if they have an id defined
            if id.id:
                return self._get(id=id.id, work_dir=work_dir, group=group)

        elif isinstance(id, NNTPSegmentedPost):
            # Support NNTPSegmentedPost() Objects
            # Anything defined in an NNTPSegmentPost object will always
            # over-ride any content retrieved (filenames, etc)

            # Get segment count
            total_parts = len(id)

            for part_no, _article in enumerate(id):
                # We need to download each article define
                if self.join_group:
                    for group in id.groups:
                        # Try each group
                        article = self._get(
                            id=_article,
                            work_dir=work_dir,
                            group=group,
                        )

                        if article:
                            # Found
                            break

                    # If we reach here, we failed to fetch the item, so
                    # we'll try the next group
                    logger.warning(
                        'Failed to fetch segment #%.2d (%s)' % \
                        (_article.no, _article.filename),
                    )

                    # mark our article invalid as part of it's response
                    article._is_valid = False

                else:
                    # Fetch our content
                    article = self._get(
                        id=_article,
                        work_dir=work_dir,
                    )

                if article:
                    # Store information from our nzb_article over top of
                    # the contents in the new article we retrieved
                    if len(article) == 0:
                        logger.warning(
                            'No content found in segment #%.2d (%s)' % \
                            (_article.no, _article.filename),
                        )

                    else:
                        # over-ride content based on data provided by
                        # NNTPSegmentedFile object
                        article.decoded[0].filename = id.filename
                        article.decoded[0].part = part_no + 1
                        article.decoded[0].total_parts = total_parts

                else:
                    # Uh oh, we failed to get anything; so just add
                    # our current article generated from the nzb file.
                    # Mark the object invalid and re-add it
                    logger.warning(
                        'Failed to retrieve segment (%s)' % \
                        id.filename,
                    )

                    # mark our article invalid as part of it's response
                    article._is_valid = False

                # Store our article
                results.add(article)

            if len(results):
                # At this point we have a segment ready for post-processing
                return results

        elif isinstance(id, NNTPnzb):
            # We're dealing with an NZB File
            if not id.is_valid():
                return None

            # We iterate over each segment defined int our NZBFile and merge
            # them into 1 file. We do this until we've processed all the
            # segments and we return a list of articles
            for no, seg in enumerate(id):
                # A sorted set of segments (all segments making up multiple
                # NNTPContent() objects

                # Get segment count
                total_parts = len(seg)
                # Recursively call ourselves to proccess NNTPSegmentedPost()
                # objects
                _results = self.get(seg)

                if _results:
                    # for article organization, we want to ensure our content
                    # is ordered sequentially as it's defined in the NZBFile
                    for article in _results:
                        # Bump article no count to allow ordering
                        article.no += no

                    # Store our results
                    results |= _results

            if len(results):
                # At this point we have a segment ready for post-processing
                return results

        # Unsupported
        return None

    def _get(self, id, work_dir, group=None):
        """
        Download a specified message to the work_dir specified.
        This function returns an NNTPArticle() object if it can.

        None is returned if the content could not be retrieved due
        to an error or DMCA.

        This function will always attempt to retrieve missing content
        from any identified backup servers in the order they were
        saved.

        """

        if self.join_group and group is not None and group != self.group_name:
            # allow us to switch groups if nessisary
            if self.group(group)[0] is None:
                # Could not select group
                logger.error('Could not select group %s' % group)
                return None

        if not isdir(work_dir) and not mkdir(work_dir):
            logger.error('Could not create directory %s' % work_dir)
            return None

        # Prepare our Decoders
        decoders = list()
        if not self.use_body:
            # BODY calls pull down header information too
            decoders.append(
                CodecHeader(encoding=self.encoding, work_dir=work_dir),
            )

        decoders.extend([
            # Yenc Encoder/Decoder
            CodecYenc(work_dir=work_dir),
            # UUEncoder/Decoder
            CodecUU(work_dir=work_dir),
        ])

        if self.use_body:
            # Body returns the contents past the header;  Hence there will be
            # no header information to parse if this is the option used.

            # This is faster, but does not allow for some extra checking
            # we can do.
            response = self.send('BODY <%s>' % id, decoders=decoders)
        else:
            # Article retrives the same content as Body plus the Header
            # too.
            response = self.send('ARTICLE <%s>' % id, decoders=decoders)

        if response.is_success(multiline=True):
            # we're good to go!
            pass

        elif response.code in NNTPResponseCode.NO_ARTICLE:
            logger.warning('ARTICLE <%s> not found.' % id)
            if self._backups:
                # Try our backup servers in the sequential order they were
                # added in; if they all fail; then we return None
                return next((b.article for b in self._backups \
                        if b.get(id, work_dir, group) is not None), None)
            return None

        else:  # response.code in NNTPResponseCode.SERVER_ERROR:
            # Close our connection
            self.close()

            logger.error('NNTP Fetch <%s> / %s' % (id, response))

            if self._backups:
                # Try our backup servers in the sequential order they were
                # added in; if they all fail; then we return None
                return next((b.article for b in self._backups \
                        if b.get(id, work_dir, group) is not None), None)
            return None

        # If we reach here, we have data we can work with; build our article
        # using the content retrieved.
        article = NNTPArticle(id=id, work_dir=work_dir)
        article.load_response(response)

        # Return the content retrieved
        return article

    def send(self, command, timeout=None, decoders=None, retries=0):
        """
        A Simple wrapper for sending NNTP commands to the server

        send() always returns the result of _recv() an internal function
        used to parse the content returned from the NNTP Server.

        article is only used if a list is passed in and the expected
        results are a multi-line response. It is populated with the
        data returned.
        """

        if not self.connected:
            # Attempt to establish a connection
            if not self.connect():
                logger.error('Could not establish a connetion to NNTP Server.')
                return NNTPResponse(
                    NNTPResponseCode.NO_CONNECTION,
                    'No Connection',
                )

        logger.debug('send(%s)' % command)

        total_retries = retries
        while True:
            # Soft reset in preparation for returned results
            self._soft_reset()

            if not super(NNTPConnection, self).send(command + EOL):
                break

            try:
                response = self._recv(
                    timeout=timeout,
                    decoders=decoders,
                )

            except SocketException:
                # Connection Lost
                self.close()

                # Setup Response
                response = NNTPResponse(
                    NNTPResponseCode.CONNECTION_LOST,
                    'Connection Lost',
                )

            if retries > 0:
                # We have a retry left; depending on the severity of our return
                # code, we may try again
                if response.code in (
                    NNTPResponseCode.FETCH_ERROR,
                    NNTPResponseCode.BAD_RESPONSE,
                    NNTPResponseCode.NO_CONNECTION,
                    NNTPResponseCode.CONNECTION_LOST):
                    # If we reach here; our return code is considered
                    # recoverable;

                    # decrement our retry counter
                    retries -= 1

                    # Reset our decoders for re-use
                    for decoder in decoders:
                        decoder.reset()

                    logger.warning(
                        'Received NNTP error %d; retrying... (%d/%d)' % (
                            response.code,
                            total_retries-retries,
                            total_retries,
                        ))
                    continue

            return response

        return NNTPResponse(
            NNTPResponseCode.BAD_RESPONSE,
            'Invalid Command: "%s"' % command,
        )

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

        if not decoders:
            decoders = []

        elif isinstance(decoders, CodecBase):
            decoders = [decoders, ]

        while not self.article_eod:
            # This loop really only takes effect if we aren't forced
            # to just wait for a server command line. Otherwise
            # we break out the first second we have data to process

            self._buffer.seek(0, SEEK_END)

            try:
                self._buffer.write(self.read(
                    max_bytes=self.MAX_BUFFER_SIZE-total_bytes,
                    timeout=timeout,
                ))

            except (SocketException, SignalCaughtException):
                logger.debug('_recv() Connection Lost')
                return NNTPResponse(
                    NNTPResponseCode.CONNECTION_LOST,
                    'Connection Lost',
                )

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

                # Seek to head of buffer
                self._buffer.seek(head_ptr, SEEK_SET)

                # Extract Header Response
                data = self._buffer.readline()

                # Only the first time do we remove the first entry
                # because this is our header
                match = NNTP_RESPONSE_RE.match(data.strip())
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
                            self.last_resp_str = 'Connection Lost: (len=%d)' \
                                % (len(data),
                            )
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

            # Non-Multiline responses mean were done now.
            if self.last_resp_code not in NNTPResponseCode.SUCCESS_MULTILINE:
                # We're done; flush results and move on
                self.article_eod = True

                # Safety: just clean anything remaining
                while self.can_read():
                    try:
                        self.read()

                    except (SocketException, SignalCaughtException):
                        # Connection lost
                        return NNTPResponse(
                            NNTPResponseCode.NO_CONNECTION,
                            'Connection Lost',
                        )

                # Return our response
                return NNTPResponse(self.last_resp_code, self.last_resp_str)

            # Store our can_read() flag
            can_read = self.can_read()

            # Initialize our response object
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
                offset = min(3, total_bytes)

                # The below accomplishes the same results as abs()
                # but does it at a much faster speed (screw looking pythonic)
                if offset > 0:
                    offset = -offset

                self._buffer.seek(offset, SEEK_END)

                # Now we read what we can from the buffer
                data = self._buffer.read()

                if offset == -3:
                    # Adjust offset if nessisary
                    offset += 1

                # Check for the End of Data
                eod_results = EOD_RE.search(data)
                if eod_results:
                    # We can truncate here to trim the EOD off
                    self._buffer.truncate(
                        total_bytes-len(eod_results.group(1)),
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
                        total_bytes-len(eol_results.group(1)),
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

            if total_bytes >= self.MAX_BUFFER_SIZE and not eol:
                # We have a full buffer and there is most likely
                # a lot more content to still download; we need to find
                # the last new line

                # Number of characters to look back (chunk):
                # TODO: Make this a global/configurable variable
                chunk_size = 128

                # Current Matched offset (<0 means no match yet)
                offset = -1

                # Our reference pointer
                ref_ptr = tail_ptr - head_ptr
                while offset < 0:
                    # Keep iterating backwards by chunk sizes
                    # until we get a match

                    # a few checks to make sure we don't look past
                    # our head_ptr
                    chunk_size = min(ref_ptr-head_ptr, chunk_size)
                    ref_ptr -= (chunk_size + 1)

                    if ref_ptr < head_ptr:
                        # Nothing more to look back at; we reached
                        # the head of our buffer. do nothing; just let
                        # process all of the data we received.
                        break

                    chunk_ptr = self._buffer.seek(ref_ptr, SEEK_SET)
                    offset = self._buffer.read(chunk_size).rfind('\n')
                    if offset >= 0:
                        # We found the new line; safe our offset and break
                        tail_ptr = offset + chunk_ptr
                        break

            if (tail_ptr-head_ptr) > 0 and \
                tail_ptr < self.MAX_BUFFER_SIZE and self.can_read(1):
                # Astraweb is absolutely terrible for sending a little
                # bit more data a few seconds later. This is a final
                # call to try to handle these stalls just before the last
                # few bytes are sent.
                continue

            # Ensure we're pointing at the head of our buffer
            self._buffer.seek(head_ptr, SEEK_SET)

            # Compression Support
            if (tail_ptr-head_ptr) > 0:
                if self._iostream == NNTPIOStream.RFC3977_GZIP and \
                        GZIP_COMPRESS_RE.search(self.last_resp_str):

                    dc_obj = decompressobj()
                    try:
                        self._data.write(dc_obj.decompress(
                            self._buffer.read(tail_ptr-head_ptr),
                        ))
                        logger.debug("NNTP ZLIB decompression successful.")

                    except ZlibException:
                        # Decompression error; since compression is only used
                        # when retrieving server-side listings; it's best to
                        # just alert the end user and move along
                        logger.warning(
                            '_recv() %d byte(s) ZLIB decompression failure.' \
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
                    self._data.write(self._buffer.read(tail_ptr-head_ptr))

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

            #  We track the last codec activated using the codec_active
            #  variable.
            codec_active = None

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
                    codec_active = next((d for d in decoders
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
                result = codec_active.decode(self._data)

                # Adjust our pointer
                d_head = self._data.tell()

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
                if result is None:
                    # The Codec has completed and has nothing to return for
                    # storing. We gracefully move along at this point.
                    logger.debug(
                        'Decoding complete (no results) / %s' % codec_active,
                    )

                    # we're done; do nothing more
                    codec_active = None

                    continue

                elif result is True:
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
                        result = codec_active.decoded

                # If we got here, our content was good; we can safely
                # toggle our codec_active back to off since we're going to
                # be expecting more data now
                logger.debug('Decoding completed. %s' % codec_active)
                codec_active = None

                if not isinstance(result, NNTPContent):
                    # We ignore any other return type, Decoders should always
                    # return an NNTPContent type; anything else is considered
                    # moot
                    continue

                # Add to our NNTPContent() to our decoded set associated with
                # our NNTPResponse() object
                response.decoded.add(result)

                if not isinstance(result, NNTPMetaContent):
                    # Print a representative string into the body to identify
                    # the content parsed out (and decoded)
                    response.body.write(repr(result) + EOL)

        # Track lines processed
        self.line_count += len(self.lines)

        logger.debug('Returning Response %s' % response)
        return response

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
            try:
                # Prevent Recursion by calling parent send()
                super(NNTPConnection, self).send('QUIT' + EOL)
            except:
                pass

        # SocketBase close() tries to grab the last
        # content on the socket before closing it
        return super(NNTPConnection, self).close()

    def _soft_reset(self):
        """
        Reset tracking items
        """
        self.article = {}
        self.article_eod = False
        self.article_fname = None

        # Reset our Buffers
        self._buffer.truncate(0)
        self._data.truncate(0)
        self._data_len = 0

        self.last_resp_code = None
        self.last_resp_str = ''
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

    def __unicode__(self):
        return u'%s://%s@%s:%d' % (
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
