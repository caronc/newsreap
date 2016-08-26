# -*- coding: utf-8 -*-
#
# A Codec for parsing header information from an NNTP Server
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

import re

from newsreap.NNTPHeader import NNTPHeader
from newsreap.Utils import SEEK_SET
from newsreap.NNTPIOStream import NNTP_DEFAULT_ENCODING
from newsreap.codecs.CodecBase import CodecBase

# Logging
import logging
from newsreap.Logging import NEWSREAP_CODEC
logger = logging.getLogger(NEWSREAP_CODEC)

# Check for begin and end
HEADER_RE = re.compile(
    r'^\s*(?P<key>[a-z0-9-]+)\s*:\s*(?P<value>.*[^\s])\s*$',
    re.IGNORECASE,
)

# Common Details detected from the message Header
NNTP_NUKED_MSG = re.compile(
    r'^x-(dmca|removed|cancel?(led)?|blocked)',
    re.IGNORECASE,
)


class CodecHeader(CodecBase):
    """
    This class is used for interpreting NNTP Headers
    """
    def __init__(self, descriptor=None, encoding=None, *args, **kwargs):
        super(CodecHeader, self).__init__(descriptor=descriptor,
            *args, **kwargs)

        # Used for internal meta tracking when using the decode()
        self.decoded = NNTPHeader()

        # Initialize Header Parsed Flag; This is used to ensure
        # the decoding of headers is only performed once
        self.header_parsed = False

        # The character set encoding usenet content is retrieved in
        if encoding is None:
            self.encoding = NNTP_DEFAULT_ENCODING
        else:
            self.encoding = encoding

    def detect(self, line, relative=True):
        """
        A Simple function that can be used to determine if this is a
        header entry or not.

        If relative is set to true, we additionally check the line
        content against content relative to the decoding process (`What are
        we expecting to have right now?`). For example, if we already
        successfully parsed a header, then we would return None because
        we're not expecting any more header data at this point.

        It returns None if there is no header key line, otherewise
        it returns a dictionary of the headers in the form of
        keys/value pairs

        """

        header_re = HEADER_RE.match(line)
        if not header_re:
            return None

        if relative and self.header_parsed:
            # Content has already been parsed, we now disable
            # this function
            return None

        return {
            'key': header_re.group('key').decode(self.encoding),
            'value': header_re.group('value').decode(self.encoding),
        }

    def decode(self, stream):
        """ read from the specified stream and process
            all of the headers. Returns a NNTPHeader object
            when completed.

            Header decoding works as follows:
                1. we ignore all white space and blank lines
                   at the head of the decoding process.
                2. once the first header is found, we then
                   process one header after another until an
                   empty line is found telling us to stop
                   or if we detect a line that simply isn't
                   decodable anymore.
        """
        if self.header_parsed:
            return self.decoded

        ws_head = True
        while self.decode_loop():

            # fall_back ptr
            ptr = stream.tell()

            # Read in our data
            data = stream.readline()
            if not data:
                if not len(self.decoded):
                    # Not a header we're dealing with
                    return None

                # We're done
                break

            # Total Line Tracking
            self._total_lines += 1

            # Detect a header line
            header = self.detect(data, relative=False)
            if header is None:
                # Check for White Space
                if not len(data.strip()):
                    # We found white space if we get here

                    if ws_head:
                        # We ignore all white space at head
                        # so we keep going
                        continue
                else:
                    # We found data that may need to be processed
                    # by someting else; we want to backtrack our
                    # pointer.
                    stream.seek(ptr, SEEK_SET)

                    # Fix our line count
                    self._total_lines -= 1

                # We're done!
                self.header_parsed = True

                # Return our Header
                return self.decoded

            # Processing Line Tracking
            self._lines += 1

            # We processed meta data, therefore we have started reading in
            # header content.  We want to toggle the ws_head flag so that
            # we no longer accept white space as acceptable content
            if ws_head:
                ws_head = False

            # Now we store our key/value pair into our header
            # To keep things fast; we specifically place content directly
            # into the content object to avoid the extra un-nessisary
            # function call per line
            self.decoded[header['key']] = header['value']

        # Returns true because we're still expecting more content
        return True

    def is_valid(self):
        """
        A simple function that returns whether or not the block just read
        checks out okay.

        The function returns True if content is valid, False if it isn't
        and None if there isn't enough information to make a valid guess.
        """

        if not self.header_parsed:
            # We never decoded anything
            logger.debug("%s has not been decoded." % self)
            return None

        # If we reach here; we've decoded the file, the next thing
        # we need to do is check for some common entries that
        # would oherwise have made it so it's not valid
        if next((True for k in self.decoded.iterkeys() \
                 if NNTP_NUKED_MSG.match(k) is not None), False):
            # Headers check failed!
            return False

        # Nothing bad to indicate bad headers
        return True

    def reset(self):
        """
        Reset our decoded content
        """
        super(CodecHeader, self).reset()

        # Reset Our Result set
        self.decoded = NNTPHeader()

        # Initialize Header Parsed Flag; This is used to ensure
        # the decoding of headers is only performed once
        self.header_parsed = False

    def __setitem__(self, key, item):
        """
        Mimic Dictionary:  dict[key] = value
        """
        self.decoded[key] = item

    def __getitem__(self, key):
        """
        Mimic Dictionary:  value = dict[key]
        """
        return self.decoded[key]

    def __delitem__(self, key):
        """
        Mimic Dictionary:  del dict[key]
        """
        del self.decoded[key]

    def __str__(self):
        """
        Return a printable version of the codec
        """
        return repr(self)

    def __len__(self):
        """
        Returns the dictionary length
        """
        return len(self.decoded)

    def __repr__(self):
        """
        Return a printable object
        """
        return '<CodecHeader lines_processed=%d />' % (
            self._lines,
        )
