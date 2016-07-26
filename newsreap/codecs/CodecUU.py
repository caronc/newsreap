# -*- coding: utf-8 -*-
#
# A Codec for handling UU encoded messages
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
from os.path import basename
from binascii import a2b_uu
from binascii import Error as BinAsciiError

from newsreap.NNTPBinaryContent import NNTPBinaryContent
from newsreap.Utils import SEEK_SET
from newsreap.codecs.CodecBase import CodecBase

# Logging
import logging
from ..Logging import NEWSREAP_CODEC
logger = logging.getLogger(NEWSREAP_CODEC)

# Check for begin and end
UUENCODE_RE = re.compile(
    r'^\s*((?P<key_1>begin)\s+(?P<perm>[0-9]{3,4})?[\s\'"]*(?P<name>.+)[\'"]*|' +\
    r'(?P<key_2>end)|' +\
    r'(?P<key_3>`)' +\
    ')\s*$',
    re.IGNORECASE,
)

# This is applied to the regular expression matches to convert
# key matches into 1
UUENCODE_KEY_MAP = {
    'key_1': 'key', 'key_2': 'key', 'key_3': 'key',
    'perm':  'perm', 'name': 'name',
}


class CodecUU(CodecBase):
    """
    This codec is used to manage all UUEncoded content found on an NNTP Server.
    """
    def __init__(self, tmp_dir=None, *args, **kwargs):
        super(CodecUU, self).__init__(tmp_dir=tmp_dir, *args, **kwargs)

        # Used for internal meta tracking when using the decode()
        self._meta = {}

        # Our Binary Object we can reference while we decode
        # content
        self.decoded = None

    def detect(self, line, relative=True):
        """
        A Simple function that can be used to determine if there is
        uuencoding content on the line being checked.

        If relative is set to true, we additionally check the line
        content against content relative to the decoding process (`What are
        we expecting to have right now?`). For example, the `end` token would
        be ignored if we haven't received a `begin` first.

        It returns None if there is no uuencoding key line, otherwise
        it returns a dictionary of the keys and their mapped values.

        """
        uuencode_re = UUENCODE_RE.match(line)
        if not uuencode_re:
            return None

        # Merge Results
        f_map = dict((UUENCODE_KEY_MAP[k], v) \
                    for k, v in uuencode_re.groupdict().iteritems() if v)

        if relative:
            # detect() relative to what has been decoded
            if f_map['key'] in self._meta:
                # We already processed this key
                return None

            if f_map['key'] == 'end' and 'begin' not in self._meta:
                # We can't handle this key
                return None

        # Tidy filename (whitespace)
        if 'name' in f_map:
            f_map['name'] = basename(f_map['name']).strip()

        # Permission Flag (Octal Type)
        if 'perm' in f_map:
            try:
                f_map['perm'] = int(f_map['perm'], 8)
            except (TypeError, ValueError):
                # Eliminate bad key
                del f_map['perm']

        return f_map

    def decode(self, stream):
        """ Decode some data and decode the data
            to descriptor identified (by the stream)
        """

        while self.decode_loop():
            # fall_back ptr
            ptr = stream.tell()

            # Read in our data
            data = stream.readline()
            if not data:
                # We're done
                break

            # Total Line Tracking
            self._total_lines += 1

            # Detect a uuenc line
            _meta = self.detect(data, relative=False)
            if _meta is not None:
                #
                # We just read a uu keyword token such as
                # begin, or end
                #
                if _meta['key'] in self._meta:
                    # We already processed this key; uh oh
                    # Fix our stream
                    stream.seek(ptr, SEEK_SET)

                    # Fix our line count
                    self._total_lines -= 1

                    # we're done
                    break

                if _meta['key'] == 'end' and 'begin' not in self._meta:
                    # Why did we get an end before a begin?
                    # Just ignore it and keep going
                    continue

                # store our key
                self._meta[_meta['key']] = _meta

                if 'end' in self._meta:
                    # Mark the binary as being valid
                    self.decoded._is_valid = True

                    # We're done!
                    break

                elif _meta['key'] == 'begin':

                    if 'name' not in _meta:
                        # Why did we get a begin before a part
                        # Just ignore it and keep going
                        continue

                    # Create our binary instance
                    self.decoded = NNTPBinaryContent(
                        filename=_meta['name'],
                        tmp_dir=self.tmp_dir,
                    )

                    # Open our file for writing
                    self.decoded.open()
                continue

            if 'begin' not in self._meta:
                # We haven't found the start yet which means we should just
                # keep going until we find it
                continue

            try:
                decoded = a2b_uu(data)

            except BinAsciiError:
                ## Workaround for broken uuencoders by
                ## Fredrik Lundh (taken from uu.py code)
                nbytes = (((ord(data[0])-32) & 63) * 4 + 5) / 3

                try:
                    decoded = a2b_uu(data[:nbytes])

                except BinAsciiError:
                    # Log corruption
                    logger.warning(
                        "Corruption on line %d." % \
                                 self._lines,
                    )

                    # Line Tracking
                    self._lines += 1

                    # keep storing our data
                    continue

                # CRC Calculations
                self._calc_crc(decoded)

            # Line Tracking
            self._lines += 1

            # Track the number of bytes decoded
            self._decoded += len(decoded)

            # Write data to out stream
            self.decoded.write(decoded)

        # Reset our meta tracking
        self._meta = {}

        if  self.decoded:
            # close article when complete
            self.decoded.close()

        # Return what we do have
        return self.decoded

    def reset(self):
        """
        Reset our decoded content
        """
        super(CodecUU, self).reset()

        # Used for internal meta tracking when using the decode()
        self._meta = {}

        # Our Binary Object we can reference while we decode
        # content
        self.decoded = None

    def __lt__(self, other):
        """
        Sorts by filename
        """
        return str(self) < str(other)

    def __str__(self):
        """
        Return a printable version of the file being read
        """

        # Build a string using the data we know
        if 'begin' in self._meta:
            fname = self._meta.get('name', 'Unknown.File')
        else:
            fname = 'Undetermined.File'

        return '%s' % (
            fname
        )

    def __repr__(self):
        """
        Return a printable object
        """
        return '<CodecUU lines_processed=%d />' % (
            self._lines,
        )
