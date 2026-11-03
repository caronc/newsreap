# A codec for SFV files (a list of files and their CRC32 checksums)
#
# Copyright (C) 2026 Chris Caron <lead2gold@gmail.com>
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
An SFV ("simple file verification") file is posted alongside a release so
the files can be checked once downloaded.  Each line names a file and
gives its CRC32 checksum (8 hex digits); lines starting with ';' are
comments:

    ; a comment
    release.part01.rar 1a2b3c4d

They also give the real names of the files, which is useful when a post
hides them.  Lines that aren't readable are left out.
"""

import re
from os import walk
from os.path import basename
from os.path import dirname
from os.path import isdir
from os.path import isfile
from os.path import join
from zlib import crc32

from newsreap.NNTPAsciiContent import NNTPAsciiContent
from newsreap.NNTPContent import NNTPContent
from newsreap.SortedSet import sortedset
from newsreap.Utils import random_str
from newsreap.codecs.CodecAscii import CodecAscii
from newsreap.codecs.CodecFile import CodecFile
from newsreap.codecs.CodecZip import CodecZip

# Logging
import logging
from newsreap.Logging import NEWSREAP_CODEC
logger = logging.getLogger(NEWSREAP_CODEC)

# Defines the new line delimiter
EOL = b'\r\n'

# An SFV file
SFV_FILE_RE = re.compile(r'^.*[^/\\]\.sfv$', re.IGNORECASE)

# A line naming a file: its name, then (after a space) its checksum
SFV_LINE_RE = re.compile(
    r'^\s*(?P<name>.*?)\s+(?P<crc32>[0-9a-fA-F]{8})\s*$')

# The biggest SFV file read (they're a line per file)
MAX_SFV_SIZE = 16777216

# How much of a file is read at a time while working out its checksum
BLOCK_SIZE = 1048576


class SFVStatus:
    """
    What test() found for each file
    """
    # The checksum matched
    Ok = 'ok'

    # The checksum didn't match (the file is damaged)
    Bad = 'bad'

    # The file isn't there
    Missing = 'missing'


class CodecSFV(CodecFile):
    """
    Reads, writes and checks SFV files (see the module description)
    """

    def __init__(self, work_dir=None, *args, **kwargs):
        super().__init__(work_dir=work_dir, *args, **kwargs)

        # What the last test() found: {name: SFVStatus}
        self.results = {}

    @staticmethod
    def read(data, filename=None):
        """
        Returns {name: crc32} (crc32 in lower case hex) for the text of an
        SFV file (bytes or str), in the order listed
        """
        if isinstance(data, bytes):
            data = CodecAscii.to_text(data, filename=filename)[0]

        found = {}
        for no, line in enumerate(data.splitlines(), start=1):
            if not line.strip() or line.lstrip().startswith(';'):
                # Blank, or a comment
                continue

            entry = SFV_LINE_RE.match(line)
            name = entry.group('name').strip().strip('"') if entry else ''
            if not name:
                logger.debug('SFV line %d is not readable; left out.' % no)
                continue

            found[name.replace('\\', '/')] = entry.group('crc32').lower()

        return found

    @staticmethod
    def checksum(path):
        """
        Returns the CRC32 checksum of a file (lower case hex); None if it
        can't be read
        """
        crc = 0
        try:
            with open(path, 'rb') as fp:
                while True:
                    data = fp.read(BLOCK_SIZE)
                    if not data:
                        break
                    crc = crc32(data, crc)

        except OSError as e:
            logger.warning('Could not read %s: %s' % (path, e))
            return None

        return '%08x' % (crc & 0xffffffff)

    def _files(self, path):
        """
        Iterates over (name, path) for a file, or every file in a folder
        (named relative to it)
        """
        if not isdir(path):
            yield basename(path), path
            return

        for root, dirs, files in walk(path):
            # The same order every time
            dirs.sort()
            for filename in sorted(files):
                full = join(root, filename)
                yield full[len(path):].lstrip('/'), full

    def encode(self, content=None, name=None, *args, **kwargs):
        """
        Writes an SFV file for the files (and folders) added; returns a
        sortedset() holding it (an attached NNTPAsciiContent), or None if
        a file couldn't be read
        """
        if content is not None:
            self.add(content)

        if len(self) == 0:
            return None

        if not name:
            name = self.name or random_str()

        lines = []
        for path in self:
            for filename, full in self._files(path):
                crc = self.checksum(full)
                if crc is None:
                    return None

                lines.append(('%s %s' % (filename, crc)).encode('utf-8'))

        encoded = NNTPAsciiContent(
            filepath='%s.sfv' % basename(name), work_dir=self.work_dir,
            unique=True)
        encoded.write(EOL.join(lines) + EOL)
        encoded.close()

        # Removed when it is no longer used
        encoded.attach()

        results = sortedset(key=lambda x: x.key())
        results.add(encoded)

        self.clear()
        return results

    def _load(self, path):
        """
        Returns {name: crc32} from an SFV file; None if it can't be read
        """
        try:
            with open(path, 'rb') as fp:
                data = fp.read(MAX_SFV_SIZE + 1)

        except OSError as e:
            logger.error('Could not read %s: %s' % (path, e))
            return None

        if len(data) > MAX_SFV_SIZE:
            logger.error('%s is too big to be an SFV file.' % path)
            return None

        return self.read(data, filename=path)

    def decode(self, content=None, *args, **kwargs):
        """
        Reads the SFV files added (paths, NNTPContent or a list of them);
        returns {name: crc32} for every file they list, or None if one
        couldn't be read
        """
        if isinstance(content, (bytes, str)) and not isfile(str(content)):
            # The text of an SFV file
            return self.read(content)

        if isinstance(content, NNTPContent) and not content.filepath:
            return self.read(content.getvalue())

        if content is not None:
            self.add(content)

        if len(self) == 0:
            return None

        found = {}
        for path in self:
            listed = self._load(path)
            if listed is None:
                return None
            found.update(listed)

        self.clear()
        return found

    def test(self, content=None):
        """
        Checks the files an SFV file (or the ones added) lists, looking for
        them next to it; returns True when every one is there and matches.
        What was found for each is kept in `results`.
        """
        if content is not None:
            paths = self.get_paths(content)

        elif len(self.archive):
            paths = iter(self.archive)

        else:
            raise AttributeError('CodecSFV: No SFV file given.')

        self.results = {}
        for path in paths:
            listed = self._load(path)
            if listed is None:
                return False

            for filename, expected in listed.items():
                # Only ever looked for next to the SFV file
                safe = CodecZip.safe_path(filename)
                full = join(dirname(path), *safe.split('/')) if safe else ''
                if not full or not isfile(full):
                    self.results[filename] = SFVStatus.Missing
                    continue

                self.results[filename] = SFVStatus.Ok \
                    if self.checksum(full) == expected else SFVStatus.Bad

        return bool(self.results) and all(
            status == SFVStatus.Ok for status in self.results.values())

    def __repr__(self):
        """
        Return an unambigious version of the object
        """
        return '<CodecSFV work_dir="%s" files="%d" />' % (
            self.work_dir, len(self.archive))
