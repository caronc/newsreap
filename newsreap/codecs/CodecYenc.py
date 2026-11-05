# A Codec for handling yEnc encoded NNTP Articles
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

import re
from collections import deque
from zlib import crc32
from os import fsdecode
from os import fsencode
from os.path import basename

from newsreap.NNTPContent import NNTPContent
from newsreap.NNTPBinaryContent import NNTPBinaryContent
from newsreap.NNTPAsciiContent import NNTPAsciiContent
from newsreap.Utils import SEEK_SET
from newsreap.Utils import SEEK_END

from newsreap.codecs.CodecBase import BIN_MASK
from newsreap.codecs.CodecBase import E_ERROR
from newsreap.codecs.CodecBase import E_CRC32
from newsreap.codecs.CodecBase import CodecBase
from newsreap.codecs.CodecBase import output_name

# Logging
import logging
from newsreap.Logging import NEWSREAP_CODEC
logger = logging.getLogger(NEWSREAP_CODEC)

# Defines the new line delimiter
EOL = b'\r\n'

# Check for =ybegin, =yend and =ypart
YENC_RE = re.compile(
    # Standard yEnc structure
    br'^\s*(=y((?P<key_1>begin|part|end))2?('
    # group 1
    br'(\s+part=(?P<part_1>[0-9]+))?(\s+total=(?P<total>[0-9]+))?'
    br'(\s+line=(?P<line>[0-9]+))?(\s+size=(?P<size_1>[0-9]+))?'
    br'(\s+name=[\s\'"]*(?P<name_1>.+)[\'"]*)?|'

    # group 2
    br'(\s+size=(?P<size_2>[0-9]+))?(\s+part=(?P<part_2>[0-9]+))?'
    br'(\s+pcrc32=(?P<pcrc32_1>[A-Za-z0-9]+))?'
    br'(\s+crc32=(?P<crc32_1>[A-Za-z0-9]+))?|'

    # group 3
    br'(\s+begin=(?P<begin>[0-9]+))?(\s+end=(?P<end>[0-9]+))?|'
    br'(\s+size=(?P<size_3>[0-9]+))?(\s+part=(?P<part_3>[0-9]+)\s+)?'
    br'(\s+pcrc32=(?P<pcrc32_2>[A-Za-z0-9]+))?'
    br'(\s+crc32=(?P<crc32_2>[A-Za-z0-9]+))?'
    # end
    br'))\s*$',
    re.IGNORECASE | re.ASCII,
)

# A yEnc keyword line (=ybegin, =ypart or =yend, in any case) and its
# attributes, which posters write in any order; name= is always last and
# runs to the end of the line
YENC_LINE_RE = re.compile(
    br'^\s*=y(?P<key>begin|part|end)2?(?P<rest>\s.*)?$',
    re.IGNORECASE | re.ASCII | re.DOTALL)
YENC_NAME_RE = re.compile(br'(^|\s)name=', re.IGNORECASE | re.ASCII)
YENC_ATTRIBUTE_RE = re.compile(
    br'(?P<name>[a-z0-9]+)=(?P<value>\S*)', re.IGNORECASE | re.ASCII)

# The attributes we use (any other is left out)
YENC_ATTRIBUTES = frozenset((
    'part', 'total', 'line', 'size', 'begin', 'end', 'pcrc32', 'crc32'))

# This is applied to the regular expression matches to convert
# key matches into 1 (YENC_RE is kept as it was; detect() reads lines with
# YENC_LINE_RE)
YENC_KEY_MAP = {
    'begin': 'begin', 'key_1': 'key',
    'end': 'end', 'line': 'line',
    'part_1': 'part', 'part_2': 'part', 'part_3': 'part',
    'size_1': 'size', 'size_2': 'size', 'size_3': 'size',
    'name_1': 'name', 'total': 'total',
    'pcrc32_1': 'pcrc32', 'pcrc32_2': 'pcrc32',
    'crc32_1': 'crc32', 'crc32_2': 'crc32',
}

# The default amount of memory to work with within the yEnc buffer
# The larger this value, the faster the decoding process however
# it stacks with other threads (if any) also using this.
DEFAULT_BUFFER_SIZE = 1048576

# Used to parse subject lines of NZB File entries
NZB_SUBJECT_PARSE = (
    # description [x/y] - "fname" yEnc (a/b)
    # description - "fname" yEnc (a/b)
    # description - fname yEnc (a/b)
    # "description" - fname yEnc (a/b)
    # "description" - fname yEnc (a/b) size
    # "description" - fname yEnc (/b)
    # fname yEnc (/b)
    # fname yEnc (a/b)
    # "fname" yEnc (/b)
    # "fname" yEnc (a/b)
    re.compile(
        r"^(([\"'\s]*(?P<desc>(\s*[^\"'\[(])+)"
        r"([\"'\s-]+[\[(]?(?P<index>\d+)\/(?P<count>\d+)[)\]]?)?)?"
        r"[\"'\s-]+)?(?P<fname>[^\"']+)[\"'\s-]+yEnc\s+[\[(]?"
        r"(?P<yindex>\d+)?\/"
        r"(?P<ycount>\d+)[\])]?([+\s]+?(?P<size>\s*\d+))?\s*$",
            re.IGNORECASE | re.ASCII,
    )
)


class YencError(Exception):
    """ Class for specific yEnc errors
    """
    def __str__(self):
        return "yEnc.Error: %d:%s\n" % (
            self.code, self.value)


# The number of encoded characters written per line before a line break
# is added (matches the classic yEnc C library)
YENC_LINESIZE = 128

# Every byte is shifted by 42 when encoded (and back again when decoded)
YENC_ENCODE_TABLE = bytes((x + 42) & 0xff for x in range(256))
YENC_DECODE_TABLE = bytes((x - 42) & 0xff for x in range(256))

# Encoded characters that only need to be escaped at the start (or end)
# of a line
YENC_TAB = ord('\t')
YENC_SPACE = ord(' ')
YENC_DOT = ord('.')

# An escape character (=) followed by the escaped character
YENC_ESCAPE_RE = re.compile(br'=(.)', re.DOTALL | re.ASCII)

# What each escaped character turns back into (before the 42 shift is
# removed)
YENC_UNESCAPE_MAP = {
    bytes((ord('='), x)): bytes(((x - 64) & 0xff, )) for x in range(256)}

# The most data (in bytes) decoded at one time
YENC_DECODE_BLOCK_SIZE = 262144


# The escape sequence for every (already shifted) encoded byte
YENC_ESCAPE_MAP = [bytes((ord('='), (x + 64) & 0xff)) for x in range(256)]

# An escape character as it appears in our (shifted) data
YENC_EQUAL = ord('=')


def yenc_encode(data, column=0, linesize=YENC_LINESIZE):
    """
    yEnc encodes a block of bytes.

    - column is where the previous block left off on its last line.
    - A line break (CRLF) is added every `linesize` characters.
    - Returns a tuple of (encoded_bytes, column)
    """
    # A line needs room for at least one escaped character
    linesize = max(linesize, 2)

    # Shift every byte, then escape the characters that always need it.
    # '=' is escaped first so the escapes we add are left alone. After this
    # every '=' in our data starts a 2 character escape sequence.
    data = data.translate(YENC_ENCODE_TABLE) \
        .replace(b'=', b'=}') \
        .replace(b'\x00', b'=@') \
        .replace(b'\n', b'=J') \
        .replace(b'\r', b'=M')

    results = []
    index = 0
    total = len(data)
    while index < total:
        if column == 0:
            byte = data[index]
            if byte == YENC_TAB or byte == YENC_SPACE or byte == YENC_DOT:
                # These can't start a line
                results.append(YENC_ESCAPE_MAP[byte])
                column = 2
                index += 1
                if column >= linesize:
                    results.append(b'\r\n')
                    column = 0
                continue

        if column < linesize - 1:
            # Copy everything up to (but not including) the last column
            end = index + (linesize - 1 - column)
            if end >= total:
                end = total

            elif data[end - 1] == YENC_EQUAL:
                # Never split an escape sequence across lines
                end += 1

            results.append(data[index:end])
            column += end - index
            index = end

        else:
            # The last character on the line
            byte = data[index]
            if byte == YENC_EQUAL:
                results.append(data[index:index + 2])
                index += 2

            elif byte == YENC_TAB or byte == YENC_SPACE:
                # These can't end a line
                results.append(YENC_ESCAPE_MAP[byte])
                index += 1

            else:
                results.append(data[index:index + 1])
                index += 1

            column = linesize

        if column >= linesize:
            results.append(b'\r\n')
            column = 0

    return b''.join(results), column


def yenc_decode(data, escape=False):
    """
    Decodes a block of yEnc encoded bytes.

    - Line breaks (CR and LF) are ignored.
    - escape tells us the previous block ended with an escape character.
    - Returns a tuple of (decoded_bytes, escape)
    """
    data = data.translate(None, b'\r\n')
    if escape:
        data = b'=' + data

    if b'=' not in data:
        # Nothing is escaped
        return data.translate(YENC_DECODE_TABLE), False

    # An odd number of trailing escape characters means the last one
    # escapes the first character of the next block
    trailing = len(data) - len(data.rstrip(b'='))
    escape = bool(trailing % 2)
    if escape:
        data = data[:-1]

    decoded = YENC_ESCAPE_RE.sub(
        lambda m: YENC_UNESCAPE_MAP[m.group(0)], data,
    ).translate(YENC_DECODE_TABLE)

    return decoded, escape


class CodecYenc(CodecBase):

    def __init__(self, descriptor=None, work_dir=None,
                 linelen=128, *args, **kwargs):
        super().__init__(
                descriptor=descriptor, work_dir=work_dir, *args, **kwargs)

        # Used for internal meta tracking when using the decode()
        self._meta = {}

        # Our Binary Object we can reference while we decode
        # content
        self.decoded = None

        # Used for encoding; This defines the maximum number of (encoded)
        # characters to display per line.
        self.linelen = linelen

    def parse_article(self, subject, *args, **kwargs):
        """
        Takes a an article header and returns it's parsed content if it's
        successful. Otherwise it returns None.
        """

        matched = NZB_SUBJECT_PARSE.match(subject)
        if matched is None:
            # subject is not parsable
            return None

        results = {}

        # Trim results
        if matched.group('desc') is not None:
            results['desc'] = re.sub('[\\s-]+$', '', matched.group('desc'),
                                     flags=re.ASCII)
        if matched.group('fname') is not None:
            results['fname'] = matched.group('fname').strip()

        # Support conversion of integers
        for _attr in ['index', 'count', 'yindex', 'ycount', 'size']:
            if matched.group(_attr) is not None:
                results[_attr] = int(matched.group(_attr))

        return results

    def encode(self, content, mem_buf=DEFAULT_BUFFER_SIZE):
        """
        Encodes an NNTPContent object passed in
        """

        if isinstance(content, NNTPContent):
            # Create our ascii instance
            _encoded = NNTPAsciiContent(
                filepath=content.filename,
                part=content.part,
                total_parts=content.total_parts,
                sort_no=content.sort_no,
                work_dir=self.work_dir,
                # We want to ensure we're working with a unique attached file
                unique=True,
            )

        else:
            # If we reach here, we presume our content is a filename

            # Create our ascii instance
            _encoded = NNTPAsciiContent(
                filepath=content,
                work_dir=self.work_dir,
                # We want to ensure we're working with a unique attached file
                unique=True,
            )

            # Convert our content object into an NNTPContent object
            content = NNTPContent(
                filepath=content,
                work_dir=self.work_dir,
            )

        # yEnc (v1.3) begin
        fmt_ybegin = b'=ybegin part=%d total=%d line=%d size=%d name=%s' % (
            content.part, content.total_parts, self.linelen,
            len(content), fsencode(content.filename),
        )

        # yEnc part
        fmt_ypart = b'=ypart begin=%d end=%d' % (
            content.begin() + 1,
            content.end(),
        )

        # The crc32 of the whole file (if we're one part of it); this is
        # only worked out once and then remembered by the parent
        parent_crc = None
        if isinstance(content._parent, NNTPContent):
            parent_crc = content._parent.crc32()

        # Write =ybegin line
        _encoded.write(fmt_ybegin + EOL)
        # Write =ypart line
        _encoded.write(fmt_ypart + EOL)

        if not content.open():
            return None

        # Column is used for encoding; it tracks how far along the current
        # line we are so lines are broken every `linelen` characters
        column = 0

        # The crc32 of this part is worked out as we read it
        crc = 0

        # We need to parse the content until we either reach
        # the end of the file or get to an 'end' tag
        while True:
            # Read in our data
            data = content.stream.read(mem_buf)
            if not data:
                # We're done
                break

            crc = crc32(data, crc)

            # Encoded data already has its line breaks in place
            results, column = yenc_encode(data, column, self.linelen)
            _encoded.write(results)

        # We're done reading our data
        content.close()

        if column:
            # Finish off our last (partial) line
            _encoded.write(EOL)

        # yEnc end
        fmt_yend = b'=yend size=%d part=%d pcrc32=%08x' % (
            len(content), content.part, crc & BIN_MASK)

        if parent_crc is not None:
            fmt_yend += b' crc32=%s' % parent_crc.encode('ascii')

        # Write footer
        _encoded.write(fmt_yend + EOL)

        if _encoded:
            # close article when complete
            _encoded.close()

        # Return our encoded object
        return _encoded

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
        if isinstance(line, str):
            line = fsencode(line)

        yenc_re = YENC_LINE_RE.match(line.rstrip(b'\r\n'))
        if not yenc_re:
            return None

        f_map = {'key': yenc_re.group('key').decode('ascii').lower()}
        rest = yenc_re.group('rest') or b''
        if f_map['key'] == 'begin':
            found = YENC_NAME_RE.search(rest)
            if found:
                # Filenames are decoded the same way the operating system
                # does so that any byte sequence can be represented; any
                # quotes around it are taken off
                f_map['name'] = basename(fsdecode(
                    rest[found.end():]).strip().strip('\'"').strip())
                rest = rest[:found.start()]
                if not f_map['name']:
                    # name= with nothing after it isn't yEnc
                    return None

        for entry in YENC_ATTRIBUTE_RE.finditer(rest):
            name = entry.group('name').decode('ascii').lower()
            if name in YENC_ATTRIBUTES and name not in f_map:
                f_map[name] = entry.group('value').decode('ascii')
                if name not in ('pcrc32', 'crc32') and \
                        not f_map[name].isdigit():
                    # A number that isn't one; this isn't yEnc
                    return None

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

        # Lines we've read ahead that still need to be looked at one at a
        # time, and the stream position just after the last one we used
        pending = deque()
        pos = None

        # We need to parse the content until we either reach
        # the end of the file or get to an 'end' tag
        while self.decode_loop():
            if not pending and self._max_bytes <= 0 \
                    and self.decoded is not None \
                    and 'end' not in self._meta \
                    and ('begin' in self._meta or 'part' in self._meta):
                # We're in the middle of the encoded data; decode a whole
                # block of lines at once
                pos = stream.tell()
                lines = stream.readlines(YENC_DECODE_BLOCK_SIZE)
                if not lines:
                    # We're done for now
                    return True

                block = b''.join(lines)
                if b'=y' not in block:
                    decoded, self._escape = \
                        yenc_decode(block, self._escape)

                    # CRC Calculations
                    self._calc_crc(decoded)

                    # Line Tracking
                    self._lines += len(lines)
                    self._total_lines += len(lines)

                    # Track the number of bytes decoded
                    self._decoded += len(decoded)

                    # Write data to out stream
                    self.decoded.write(decoded)

                    # Account for the extra lines we just handled
                    self.decode_loop(len(lines) - 1)
                    continue

                # A yEnc keyword is in here; handle these lines one by one
                pending.extend(lines)

            if pending:
                # fall_back ptr
                ptr = pos

                data = pending.popleft()
                pos += len(data)

            else:
                # fall_back ptr
                ptr = stream.tell()

                # Read in our data
                data = stream.readline()
                if not data:
                    # We're done for now
                    return True

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
                    pending.clear()

                    # Fix our line count
                    self._total_lines -= 1

                    # We're done
                    break

                if _meta['key'] in ('end', 'part') and \
                        'begin' not in self._meta:
                    # Why did we get an end or part before a begin?
                    # Just ignore it and keep going
                    continue

                # store our key
                self._meta[_meta['key']] = _meta

                if 'end' in self._meta:
                    # Valid when what we decoded is what the poster sent: the
                    # part's crc32 (or the file's, for a single part)
                    expected = _meta.get('pcrc32') or (
                        _meta.get('crc32')
                        if self._meta['begin'].get('total', 1) == 1 else None)
                    self.decoded._is_valid = expected is None or \
                        expected.lower().rjust(8, '0') == self.crc32()

                    if not self.decoded._is_valid:
                        logger.warning(
                            'yEnc part %d of %s is damaged (crc32 %s, '
                            'expected %s).' % (
                                self._part, self.decoded.filename,
                                self.crc32(), expected.lower()))

                    # We're done!
                    break

                elif _meta['key'] == 'begin':
                    # Depending on the version of yEnc we're using binary
                    # content starts now; thefore we create our binary
                    # instance now

                    # Save part no globally if present (for sorting)
                    self._part = _meta.get('part', 1)

                    # A new part; its crc32 starts again
                    self._crc = BIN_MASK

                    # Create our binary instance (in our work directory,
                    # whatever name the article gives; never a file of
                    # the same name that is already there)
                    self.decoded = NNTPBinaryContent(
                        filepath=output_name(_meta.get('name')),
                        part=self._part,
                        work_dir=self.work_dir,
                        unique=True,
                    )

                elif _meta['key'] == 'part':

                    if 'begin' not in self._meta:
                        # we must have a begin if we have a part
                        # This is a messed up message; treat this
                        # as junk and keep going
                        continue

                    # Save part no globally if present (for sorting)
                    self._part = _meta.get('part', self._part)

                    # Update our Binary File if nessisary
                    self.decoded.part = self._part

                    # Where the part goes in its file (begin counts from 1;
                    # end is the last byte, so it is where the part stops)
                    if isinstance(_meta.get('begin'), int) and \
                            isinstance(_meta.get('end'), int):
                        self.decoded._begin = max(0, _meta['begin'] - 1)
                        self.decoded._end = _meta['end']

                continue

            if len(set(('begin', 'part')) - set(self._meta)) == 2:
                # We haven't found the start yet which means we should just
                # keep going until we find it
                continue

            decoded, self._escape = yenc_decode(data, self._escape)

            # CRC Calculations
            self._calc_crc(decoded)

            # Line Tracking
            self._lines += 1

            # Track the number of bytes decoded
            self._decoded += len(decoded)

            # Write data to out stream
            self.decoded.write(decoded)

            if self._max_bytes > 0 and self._decoded >= self._max_bytes:
                # If we specified a limit and hit it then we're done at
                # this point. Before we do so; advance to the end of our
                # stream
                stream.seek(0, SEEK_END)

                # We're done
                break

        if pending:
            # Leave the lines we didn't need for whoever reads next
            stream.seek(pos, SEEK_SET)

        # Reset our meta tracking
        self._meta = {}

        # Reset part information
        self._part = 1

        if self.decoded:
            # close article when complete
            self.decoded.close()

        # Return what we do have
        return self.decoded

    def reset(self):
        """
        Reset our decoded content
        """
        super().reset()

        # Tracks part no; defaults to 1 and shifts if it's determined
        # that we're another part
        self._part = 1

        # Used for internal meta tracking when using the decode()
        self._meta = {}

        # Our Binary Object we can reference while we decode
        # content
        self.decoded = None

    def __lt__(self, other):
        """
        Sorts by part number
        """
        return self._part < other._part

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
