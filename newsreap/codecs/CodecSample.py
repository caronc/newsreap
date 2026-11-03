# A starting point for a new codec (copy it, rename it, fill it in)
#
# Copyright (C) 2015-2026 Chris Caron <lead2gold@gmail.com>
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
"""
A small, working codec to copy when writing a new one.

It handles a made up format: text between a begin and an end line.

    =sample begin notes.txt
    any lines of text
    =sample end

To make your own:

- Copy this file to CodecSomething.py and rename the class to match.
- Change SAMPLE_RE to find the lines that start and end your format.
- Change what decode() does with the lines in between (ie: decode them
  and write the bytes they hold).
- Keep reset() calling super().reset(); the same codec is reset and used
  again when an article is fetched again.
- Hand it to NNTPConnection.get(..., decoders=[CodecSomething()]).
"""

import re
from os import fsdecode
from os import fsencode
from os.path import basename

from newsreap.NNTPAsciiContent import NNTPAsciiContent
from newsreap.NNTPContent import NNTPContent
from newsreap.Utils import SEEK_END
from newsreap.Utils import SEEK_SET
from newsreap.codecs.CodecBase import CodecBase

# Logging
import logging
from ..Logging import NEWSREAP_CODEC
logger = logging.getLogger(NEWSREAP_CODEC)

# What ends each line we write
EOL = b'\r\n'

# The begin (with its file name) and end lines of our format
SAMPLE_RE = re.compile(
    br'^\s*=sample\s+((?P<begin>begin)\s+(?P<name>\S.*?)|(?P<end>end))\s*$',
    re.IGNORECASE | re.ASCII,
)


class CodecSample(CodecBase):
    """
    Decodes (and encodes) the text between '=sample begin <name>' and
    '=sample end' lines; see the module description
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # What we have read so far (see reset())
        self.reset()

    def detect(self, line, relative=True):
        """
        Returns what a line says if it is one of ours: {'key': 'begin',
        'name': ...} or {'key': 'end'}; None otherwise.

        With `relative`, a line that makes no sense now is left alone (ie:
        an end line before any begin line).
        """
        if isinstance(line, str):
            line = fsencode(line)

        found = SAMPLE_RE.match(line)
        if not found:
            return None

        if found.group('begin'):
            # File names are read the way the operating system would
            meta = {'key': 'begin', 'name': basename(
                fsdecode(found.group('name'))).strip()}

        else:
            meta = {'key': 'end'}

        if relative and (meta['key'] in self._meta or (
                meta['key'] == 'end' and 'begin' not in self._meta)):
            # Already seen, or out of place
            return None

        return meta

    def decode(self, stream):
        """
        Reads lines from the stream, keeping the text between our begin and
        end lines; returns the NNTPAsciiContent it went into.

        Returns True when the stream ran out before the end line (more of
        the article is on its way; decode() is called again with it).
        """
        while self.decode_loop():
            # Where this line started (to hand it back if it isn't ours)
            ptr = stream.tell()

            line = stream.readline()
            if not line:
                # Wait for more
                return True

            self._total_lines += 1

            meta = self.detect(line, relative=False)
            if meta is not None:
                if meta['key'] in self._meta:
                    # The start of the next block; leave it for whoever
                    # reads next
                    stream.seek(ptr, SEEK_SET)
                    self._total_lines -= 1
                    break

                if meta['key'] == 'end' and 'begin' not in self._meta:
                    # An end with no begin; nothing to do with it
                    continue

                self._meta[meta['key']] = meta
                if meta['key'] == 'end':
                    # A complete block
                    self.decoded._is_valid = True
                    break

                # Where what we decode is written
                self.decoded = NNTPAsciiContent(
                    filepath=meta['name'], work_dir=self.work_dir)
                self.decoded.open()
                continue

            if 'begin' not in self._meta:
                # Not inside a block yet
                continue

            # Our format keeps the text as it is; a real codec decodes the
            # line here (ie: a2b_uu(line))
            data = line.rstrip(b'\r\n') + EOL

            self._calc_crc(data)
            self._lines += 1
            self._decoded += len(data)
            self.decoded.write(data)

            if self._max_bytes > 0 and self._decoded >= self._max_bytes:
                # We have all we were asked for; skip the rest
                stream.seek(0, SEEK_END)
                break

        self._meta = {}
        if self.decoded:
            self.decoded.close()

        return self.decoded

    def encode(self, content, name=None):
        """
        Returns an NNTPAsciiContent holding `content` (bytes, text or an
        NNTPContent) in our format; None if it can't be read

        - name: the file name written on the begin line (the content's own
          name when not given)
        """
        if isinstance(content, NNTPContent):
            name = name or content.filename
            data = content.getvalue()

        else:
            data = fsencode(content) if isinstance(content, str) else content

        if data is None:
            return None

        name = basename(name or 'sample.txt')
        encoded = NNTPAsciiContent(
            filepath='%s.sample' % name, work_dir=self.work_dir)
        encoded.open(mode='wb')
        encoded.write(b'=sample begin ' + fsencode(name) + EOL)
        for line in data.splitlines():
            encoded.write(line + EOL)
        encoded.write(b'=sample end' + EOL)
        encoded.close()
        return encoded

    def reset(self):
        """
        Forgets what was read, so the codec can be used again
        """
        super().reset()

        # The begin and end lines read so far (by key)
        self._meta = {}

    def __lt__(self, other):
        """
        Sorts by file name
        """
        return str(self) < str(other)

    def __str__(self):
        """
        The name of the file being read
        """
        return self._meta.get('begin', {}).get('name', 'Undetermined.File')

    def __repr__(self):
        """
        Return a printable object
        """
        return '<CodecSample lines_processed=%d />' % self._lines
