# -*- coding: utf-8 -*-
#
# A dummy decoder to handle just raw writes used to represent a message body
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

from lib.NNTPAsciiContent import NNTPAsciiContent
from lib.codecs.CodecBase import CodecBase

# Logging
import logging
from lib.Logging import NEWSREAP_CODEC
logger = logging.getLogger(NEWSREAP_CODEC)

# Defines the new line delimiter
EOL = '\r\n'

# The following is used to determine we're not dealing with any
# binary characters on the line
ASCII_CHARACTERS = bytearray([7, 8, 9, 10, 12, 13, 27]) + \
        bytearray(range(0x20, 0x7f)) + \
        bytearray(range(0x80, 0x100))

class CodecBody(CodecBase):
    """
    This is the codec used to store general content parsed that is not encoded
    on an NNTP Server.
    """

    def __init__(self, descriptor=None, tmp_dir=None, *args, **kwargs):
        super(CodecBody, self).__init__(descriptor=descriptor,
            tmp_dir=tmp_dir, *args, **kwargs)

        # Our Ascii Object we can reference while we store our
        # text content
        self.decoded = NNTPAsciiContent(
            filename='.message',
            tmp_dir=self.tmp_dir,
        )


    def detect(self, line, relative=True):
        """
        A Simple function that can be used to determine if there is
        ascii on the line.

        It returns None if there is no ascii characters on the line otherwise
        it returns an empty dictionary since there are no meta-keys to extract
        # from a common line

        """
        is_binary = lambda bytes: bool(bytes.translate(None, line))
        if is_binary:
            # We're dealing with binary data
            return None

        # We always match this type, but we also always return
        # an empty dictionary
        return {}


    def decode(self, stream):
        """ Decode body decoding always stops at the end
            of the line.
        """

        # Read in our data
        data = stream.readline()
        if not data:
            # We're Done; returns the number of bytes decoded
            return self._decoded

        # Convert lines to separated by cr & lf
        decoded = data.rstrip() + EOL

        # Line Tracking
        self._lines += 1

        # Track the number of bytes decoded
        self._decoded += len(decoded)

        # Write data to out stream
        self.decoded.write(decoded)

        # Returns the number of bytes decoded
        return self._decoded


    def reset(self):
        """
        Reset our decoded content
        """
        super(CodecBody, self).reset()

        # Reset our decoded content
        self.decoded = NNTPAsciiContent(
            filename='.message',
            tmp_dir=self.tmp_dir,
        )


    def __str__(self):
        """
        Return a printable version of the codec
        """
        return repr(self)


    def __repr__(self):
        """
        Return a printable object
        """
        return '<CodecBody lines_processed=%d />' % (
            self._lines,
        )
