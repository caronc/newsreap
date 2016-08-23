# -*- coding: utf-8 -*-
#
# A Codec for handling yEnc encoded NNTP Articles
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

from newsreap.NNTPBinaryContent import NNTPBinaryContent
from newsreap.Utils import SEEK_SET

from newsreap.codecs.CodecBase import BIN_MASK
from newsreap.codecs.CodecBase import E_ERROR
from newsreap.codecs.CodecBase import E_CRC32
from newsreap.codecs.CodecBase import CodecBase

# Logging
import logging
from newsreap.Logging import NEWSREAP_CODEC
logger = logging.getLogger(NEWSREAP_CODEC)


# Check for =ybegin, =yend and =ypart
YENC_RE = re.compile(
    # Standard yEnc structure
    r'^\s*(=y((?P<key_1>begin|part|end))2?(' +\
        r'(\s+part=(?P<part_1>[0-9]+))?(\s+total=(?P<total>[0-9]+))?' +\
        r'(\s+line=(?P<line>[0-9]+))?(\s+size=(?P<size_1>[0-9]+))?' +\
        r'(\s+name=[\s\'"]*(?P<name_1>.+)[\'"]*)?|' +\

        r'(\s+size=(?P<size_2>[0-9]+))?(\s+part=(?P<part_2>[0-9]+))?' +\
        r'(\s+pcrc32=(?P<pcrc32_1>[A-Za-z0-9]+))?' +\
        r'(\s+crc32=(?P<crc32_1>[A-Za-z0-9]+))?|' +\

        r'(\s+begin=(?P<begin>[0-9]+))?(\s+end=(?P<end>[0-9]+))?|' +\
        r'(\s+size=(?P<size_3>[0-9]+))?(\s+part=(?P<part_3>[0-9]+)\s+)?' +\
        r'(\s+pcrc32=(?P<pcrc32_2>[A-Za-z0-9]+))?' +\
        r'(\s+crc32=(?P<crc32_2>[A-Za-z0-9]+))?' +\
    r'))\s*$',
    re.IGNORECASE,
)

# This is applied to the regular expression matches to convert
# key matches into 1
YENC_KEY_MAP = {
    'begin': 'begin', 'key_1': 'key',
    'end': 'end', 'line': 'line',
    'part_1': 'part', 'part_2': 'part', 'part_3': 'part',
    'size_1': 'size', 'size_2': 'size', 'size_3': 'size',
    'name_1': 'name', 'total': 'total',
    'pcrc32_1': 'pcrc32', 'pcrc32_2': 'pcrc32',
    'crc32_1': 'crc32', 'crc32_2': 'crc32',
}

class YencError(Exception):
    """ Class for specific yEnc errors
    """
    def __str__(self):
        return "yEnc.Error: %d:%s\n" % (
            self.code, self.value)

try:
    # Yenc Support
    from _yenc import decode_string
    FAST_YENC_SUPPORT = True

    # Monkey Patch CodecError (assumes yEnc v0.4)
    import yenc
    yenc.Error = YencError
    yenc.E_ERROR = E_ERROR
    yenc.E_CRC32 = E_CRC32
    yenc.BIN_MASK = BIN_MASK

except ImportError:
    # Yenc Support not available; so to make things easy
    # the below code was based on the yEnc libraries.  But
    # the part that is blisterily fast (Written in C) will
    # be writting in python (a much slower solution)
    FAST_YENC_SUPPORT = False

    # A Translation Map
    YENC42 = ''.join(map(lambda x: chr((x-42) & 255), range(256)))

    # a map that identifies all of the special keywords used by
    # yEnc which need a special conversion done to them before
    # We use the curses.ascii table to make our code easier to read
    from curses import ascii
    YENC_DECODE_SPECIAL_MAP = dict([('=%s' % chr(k+64), chr(k)) for k in (
        # Non-Printable
        ascii.NUL, ascii.LF, ascii.CR, ascii.SP, ascii.TAB,

        # Printable
        ord('.'), ord('='),
    )] + [
        # Ignore Types (we simply ignore these types if they are found)
        (chr(ascii.LF), ''), (chr(ascii.CR), ''),
    ])

    # Compile our map into a decode table
    YENC_DECODE_SPECIAL_RE = re.compile(
        r'(' + r'|'.join(YENC_DECODE_SPECIAL_MAP.keys()) + r')',
    )


class CodecYenc(CodecBase):

    def __init__(self, descriptor=None, tmp_dir=None, *args, **kwargs):
        super(CodecYenc, self).__init__(descriptor=descriptor,
            tmp_dir=tmp_dir, *args, **kwargs)

        # Tracks part no; defaults to 1 and shifts if it's determined
        # that we're another part
        self._part_no = 1

        # Used for internal meta tracking when using the decode()
        self._meta = {}

        # Our Binary Object we can reference while we decode
        # content
        self.decoded = None

    def detect(self, line, relative=True):
        """
        A Simple function that can be used to determine if there is
        yEnc content on the line being checked.

        If relative is set to true, we additionally check the line
        content against content relative to the decoding process (`What are
        we expecting to have right now?`). For example, the `end` token would
        be ignored if we haven't received a `begin` first.

        It returns None if there is no yEnc key line, otherwise
        it returns a dictionary of the keys and their mapped values.

        """
        yenc_re = YENC_RE.match(line)
        if not yenc_re:
            return None

        # Merge Results
        f_map = dict((YENC_KEY_MAP[k], v) \
                    for k, v in yenc_re.groupdict().iteritems() if v)

        # Tidy filename (whitespace)
        if 'name' in f_map:
            f_map['name'] = basename(f_map['name']).strip()

        if relative:
            # detect() relative to what has been decoded
            if f_map['key'] in self._meta:
                # We already processed this key
                return None

            if f_map['key'] == 'end' and 'begin' not in self._meta:
                # We can't handle this key
                return None

            if f_map['key'] == 'part' and 'begin' not in self._meta:
                # We can't handle this key
                return None

        # Integer types
        for kw in ['line', 'size', 'total', 'begin', 'end', 'part']:
            if kw in f_map:
                try:
                    f_map[kw] = int(f_map[kw])

                except (TypeError, ValueError):
                    # Eliminate bad kw
                    del f_map[kw]

        return f_map

    def decode(self, stream):
        """ Decode some data and decode the data
            to descriptor identified (by the stream)
        """

        # We need to parse the content until we either reach
        # the end of the file or get to an 'end' tag
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

            # Detect a yEnc line
            _meta = self.detect(data, relative=False)
            if _meta is not None:
                #
                # We just read a yEnc keyword token such as
                # begin, part, or end
                #
                if _meta['key'] in self._meta:
                    # We already processed this key; uh oh
                    # Fix our stream
                    stream.seek(ptr, SEEK_SET)

                    # Fix our line count
                    self._total_lines -= 1

                    # We're done
                    break

                if _meta['key'] == 'end' and \
                   len(set(('begin', 'part')) - set(self._meta)) == 2:
                    # Why did we get an end before a begin or part?
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
                    # Depending on the version of yEnc we're using binary
                    # content starts now; thefore we create our binary
                    # instance now

                    if 'name' not in _meta:
                        # Why did we get a begin before a part
                        # Just ignore it and keep going
                        continue

                    # Save part no globally if present (for sorting)
                    self._part_no = _meta.get('part', 1)

                    # Create our binary instance
                    self.decoded = NNTPBinaryContent(
                        filepath=_meta['name'],
                        part=self._part_no,
                        tmp_dir=self.tmp_dir,
                    )

                elif _meta['key'] == 'part':

                    if 'begin' not in self._meta:
                        # we must have a begin if we have a part
                        # This is a messed up message; treat this
                        # as junk and keep going
                        continue

                    # Save part no globally if present (for sorting)
                    self._part_no = _meta.get('part', self._part_no)

                    # Update our Binary File if nessisary
                    self.decoded.part = self._part_no

                continue

            if len(set(('begin', 'part')) - set(self._meta)) == 2:
                # We haven't found the start yet which means we should just
                # keep going until we find it
                continue

            if FAST_YENC_SUPPORT:
                try:
                    decoded, self._crc, self._escape = \
                        decode_string(data, self._crc, self._escape)

                except YencError:
                    logger.warning(
                        "Corruption on line %d." % \
                        self._lines,
                    )

                    # Line Tracking
                    self._lines += 1

                    # keep storing our data
                    continue

            else:
                # The slow and painful way, the below looks complicated
                # but it really isn't at the the end of the day; yEnc is
                # pretty basic;
                #  - first we need to translate the special keyword tokens
                #    that are used by the yEnc language. We also want to
                #    ignore any trailing white space or new lines. This
                #    occurs by applying our DECODE_SPECIAL_MAP to the line
                #    being processed.
                #
                #  - finally we translate the remaining characters by taking
                #    away 42 from their value.
                #
                decoded = YENC_DECODE_SPECIAL_RE.sub(
                    lambda x: YENC_DECODE_SPECIAL_MAP[x.group()], data,
                ).translate(YENC42)

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

        # Reset part information
        self._part_no = 1

        if self.decoded:
            # close article when complete
            self.decoded.close()

        # Return what we do have
        return self.decoded

    def reset(self):
        """
        Reset our decoded content
        """
        super(CodecYenc, self).reset()

        # Tracks part no; defaults to 1 and shifts if it's determined
        # that we're another part
        self._part_no = 1

        # Used for internal meta tracking when using the decode()
        self._meta = {}

        # Our Binary Object we can reference while we decode
        # content
        self.decoded = None

    def __lt__(self, other):
        """
        Sorts by part number
        """
        return self._part_no < other._part_no

    def __str__(self):
        """
        Return a printable version of the file being read
        """

        # Build a string using the data we know
        if self.decoded:
            return str(self.decoded)

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
        return '<CodecYenc lines_processed=%d />' % (
            self._lines,
        )
