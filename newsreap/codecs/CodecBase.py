# -*- coding: utf-8 -*-
#
# A Base Codec Class for deciphering data read from the an NNTP Server
#
# Copyright (C) 2015 Chris Caron <lead2gold@gmail.com>
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

from binascii import crc32
from os.path import join
from os.path import isdir

from newsreap.Utils import mkdir
from gevent import sleep

# Logging
import logging
from newsreap.Logging import NEWSREAP_CODEC
logger = logging.getLogger(NEWSREAP_CODEC)

# Adapted from python-yenc but following suite to make everything behave the
# same way
E_ERROR = 64
E_CRC32 = 65

# The mask to apply to all CRC checking
BIN_MASK = 0xffffffffL

DEFAULT_TMP_DIR = join('.nntp', 'tmp')


class CodecBase(object):

    def __init__(self, tmp_dir=None, throttle_cycles=8000, throttle_time=0.2,
                 *args, **kwargs):
        """
        The dir identfies the directory to store our sessions in
        until they can be properly handled.

        Throttling is used to give a break to the thread and not consume all
        of the cpu usage while their are still other threads in play.

        """

        # CRC Masking
        self._crc = BIN_MASK

        # Track the number of bytes decoded
        self._decoded = 0
        self._escape = 0

        # Our Decoded content should get placed here
        self.decoded = None

        if tmp_dir is None:
            self.tmp_dir = DEFAULT_TMP_DIR
        else:
            self.tmp_dir = tmp_dir

        if not isdir(self.tmp_dir):
            # create directory
            if mkdir(self.tmp_dir):
                logger.info('Created directory: %s' % self.tmp_dir)
            else:
                logger.error('Failed to created directory: %s' % self.tmp_dir)

        # Tracks the lines processed
        self._lines = 0

        # Tracks the lines scanned
        self._total_lines = 0

        # Throttle relief
        self.throttle_cycles = throttle_cycles
        self.throttle_time = throttle_time
        self.throttle_iter = 0


    def decode_loop(self):
        """
        Throttle system usage

        This call should exist within the while() loop of all decode() calls
        """
        # TODO: Generate some statistics here too based on lines processed

        self.throttle_iter += 1
        if ((self.throttle_cycles%self.throttle_iter) == 0):
            sleep(self.throttle_time)
        return True


    def _calc_crc(self, decoded):
        """
        Calculate the CRC based on the decoded content passed in
        """
        self._escape = crc32(decoded, self._escape)
        self._crc = (self._escape ^ -1)


    def detect(self, line, relative=True):
        """
        A Simple function that can be used to determine if there is
        content on the line matches what the codec handles

        The relative flag should be used for doing additional internal
        checking with the line presented.  It's to be interpreted as
        relative to the decoder.

        For example if we get an 'end' token before receiving a 'begin' token,
        we may want to fail and ignore the data. This is what relative is for
        since it checks for a matched token relative to what we might expect.
        Without the relative switch the line is just processed (it's contents
        returned).

        It returns None if there is the codec doesn't match otherwise
        it returns a dictionary of the keys and their mapped values.

        """
        # Until this is over-ridden, it is always assumed the codec
        # can not handle the the line in question
        return None


    def decode(self, stream):
        """
        A function must be written that will read from the
        stream and decode the contents.

        decode should always return a type NNTPContent() or
        something that inherits from the class. There is
        an exception made with Header processing since it
        returns a type NNTPHeader().

        The function should return True when it is possible
        for more content to exist but you processed all that
        was available in the current stream. It tells the calling
        library to keep passing in more data as it's read next time.
        This the decode() function must be smart enough to handle
        partial content and be able to resume when more content
        is passed to it.

        Any other value returned (None, False, etc) will be
        treated as though the decoding flat out failed. It will be
        reset() so that it can process new content.

        """
        return False


    def encode(self, stream):
        """
        A function must be written that will read from the
        stream and encodes the contents
        """
        return False


    def len(self):
        """ Returns the total number of decoded bytes
        """
        return self._decoded


    def crc32(self):
        """ Returns the calculated crc32 string for the decoded data.
        """
        return "%08x" % (self._crc ^ BIN_MASK)


    def reset(self, *args, **kwargs):
        """ Simply resets the internal variables of the class
            so that it can be re-used without being re-initialized
        """
        self._crc = BIN_MASK
        self._decoded = 0
        self._escape = 0

        # Tracks the lines processed
        self._lines = 0

        # Tracks the lines scanned
        self._total_lines = 0

        # Reset our Decoded Content
        self.decoded = None

        # Reset our throttle iterator
        self.throttle_iter = 0

        return None


    def __str__(self):
        """
        Return a printable version of the codec
        """
        return repr(self)


    def __repr__(self):
        """
        Return a printable object
        """
        return '<CodecBase lines_processed=%d />' % (
            self._lines,
        )
