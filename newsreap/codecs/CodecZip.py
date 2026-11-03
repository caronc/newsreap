# A codec for ZIP archives (built into Python; no outside tool needed)
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
ZIP files are read and written with Python's own zipfile module.

Archives found on Usenet can't be trusted, so extracting is careful:
- every file stays inside the folder it is extracted to (names like
  '../../etc/passwd' or '/etc/passwd' are refused)
- links to other files are left out
- a file bigger than the archive says it is is refused (a "zip bomb"
  lies about its sizes), as is anything past max_size in all
- a damaged file (it fails its checksum) or a wrong password fails the
  whole extraction
ZIP files encrypted with AES (which zipfile can't read) are handed to
Codec7Zip when the 7-Zip tool is installed.  Python can't write password
protected ZIP files; use CodecRar or Codec7Zip for those.
"""

import re
import zipfile
from shutil import move
from os import walk
from os.path import basename
from os.path import dirname
from os.path import exists
from os.path import isdir
from os.path import join
from os.path import relpath

from newsreap.NNTPBinaryContent import NNTPBinaryContent
from newsreap.SortedSet import sortedset
from newsreap.Utils import mkdir
from newsreap.Utils import random_str
from newsreap.Utils import rm
from newsreap.Utils import strsize_to_bytes
from newsreap.codecs.CodecFile import CodecFile
from newsreap.codecs.CodecFile import CompressionLevel

# Logging
import logging
from newsreap.Logging import NEWSREAP_CODEC
logger = logging.getLogger(NEWSREAP_CODEC)

# A ZIP file (ie: test.zip); split archives (.z01, .z02, ...) can't be read
# by zipfile
ZIP_FILE_RE = re.compile(r'^.*[^/\\]\.zip$', re.IGNORECASE)

# The compression level used for each of ours
ZIP_LEVELS = {
    CompressionLevel.Maximum: 9,
    CompressionLevel.Average: 6,
    CompressionLevel.Minimum: 1,
}

# How much of a file is copied at a time while extracting
BLOCK_SIZE = 1048576

# What a file's mode says when it is a link to another file
S_IFMT = 0o170000
S_IFLNK = 0o120000


class CodecZip(CodecFile):
    """
    Reads and writes ZIP archives (see the module description)
    """

    def __init__(self, work_dir=None, overwrite=False, max_size=None,
                 *args, **kwargs):
        """
        - overwrite: replace a file extracted earlier by one of the same
          name (otherwise the first one is kept)
        - max_size: the most extracted from one archive (ie: '4GB'); None
          for no limit
        """
        super().__init__(work_dir=work_dir, *args, **kwargs)

        self.overwrite = overwrite

        self.max_size = strsize_to_bytes(max_size) if max_size else None
        if max_size and not self.max_size:
            raise AttributeError("'%s' isn't a size (ie: 4GB)." % max_size)

    @staticmethod
    def safe_path(name):
        """
        Returns the path (relative, with / between folders) a file in an
        archive is extracted to; None if it would land outside the folder
        it is extracted to
        """
        parts = []
        for part in name.replace('\\', '/').split('/'):
            if part in ('', '.'):
                continue

            if part == '..' or ':' in part or '\x00' in part:
                # Leaves the folder, names a drive (ie: C:) or is broken
                return None

            parts.append(part)

        if not parts or name.replace('\\', '/').startswith('/'):
            return None

        return '/'.join(parts)

    def encode(self, content=None, name=None, *args, **kwargs):
        """
        Puts the files (and folders) added into a new ZIP file; returns a
        sortedset() holding it (an attached NNTPBinaryContent), or None if
        that couldn't be done
        """
        if content is not None:
            self.add(content)

        if len(self) == 0:
            return None

        if self.password is not None:
            logger.error(
                'ZIP files made here can not have a password; use rar or '
                '7-Zip instead.')
            return None

        if not name:
            name = self.name or random_str()

        tmp_path, tmp_file = self.mkstemp(content=name, suffix='.zip')

        try:
            with zipfile.ZipFile(
                    tmp_file, 'w', compression=zipfile.ZIP_DEFLATED,
                    compresslevel=ZIP_LEVELS[self.level]) as archive:

                for path in self:
                    if not isdir(path):
                        archive.write(path, arcname=basename(path))
                        continue

                    # A folder is stored with its own name at the top
                    top = dirname(path.rstrip('/'))
                    for root, dirs, files in walk(path):
                        # The same order every time
                        dirs.sort()
                        for filename in sorted(files):
                            full = join(root, filename)
                            archive.write(full, arcname=relpath(full, top))

        except (OSError, zipfile.LargeZipFile) as e:
            logger.error('Could not create %s: %s' % (basename(tmp_file), e))
            rm(tmp_path)
            return None

        results = sortedset(key=lambda x: x.key())
        content = NNTPBinaryContent(tmp_file)

        # Removed when it is no longer used
        content.attach()
        results.add(content)

        self.clear()
        return results

    def _extract(self, path, tmp_path, password):
        """
        Extracts one ZIP file into tmp_path; returns True if it worked,
        None if zipfile can't read how it is encrypted, False otherwise
        """
        total = 0
        try:
            with zipfile.ZipFile(path) as archive:
                if password is not None:
                    archive.setpassword(password.encode('utf-8'))

                for info in archive.infolist():
                    target = self.safe_path(info.filename)
                    if target is None:
                        logger.warning(
                            "%s: '%s' would be put outside of the folder; "
                            "left out." % (basename(path), info.filename))
                        continue

                    if info.is_dir():
                        mkdir(join(tmp_path, target))
                        continue

                    if (info.external_attr >> 16) & S_IFMT == S_IFLNK:
                        logger.warning(
                            "%s: '%s' is a link; left out." % (
                                basename(path), info.filename))
                        continue

                    target = join(tmp_path, target)
                    if exists(target) and not self.overwrite:
                        logger.warning(
                            "%s: '%s' is in it twice; the first was "
                            "kept." % (basename(path), info.filename))
                        continue

                    if not mkdir(dirname(target)):
                        logger.error(
                            "Could not create '%s'." % dirname(target))
                        return False

                    written = 0
                    with archive.open(info) as source, \
                            open(target, 'wb') as output:
                        while True:
                            data = source.read(BLOCK_SIZE)
                            if not data:
                                break

                            written += len(data)
                            total += len(data)
                            if written > info.file_size:
                                logger.error(
                                    "%s: '%s' is bigger than the archive "
                                    "says; refused." % (
                                        basename(path), info.filename))
                                return False

                            if self.max_size and total > self.max_size:
                                logger.error(
                                    '%s holds more than %d bytes; '
                                    'refused.' % (
                                        basename(path), self.max_size))
                                return False

                            output.write(data)

        except NotImplementedError:
            # ie: AES encryption, or a compression method zipfile lacks
            return None

        except RuntimeError as e:
            # A password is needed (or the one given is wrong)
            logger.error('%s: %s' % (basename(path), e))
            return False

        except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError,
                OSError, ValueError) as e:
            # Damaged, cut short, or a file failed its checksum
            logger.error('%s could not be read: %s' % (basename(path), e))
            return False

        return True

    def decode(self, content=None, name=None, password=None, *args,
               **kwargs):
        """
        Extracts the ZIP files added (paths, NNTPContent or a list of
        them); returns a sortedset() holding the folder they were extracted
        to (an attached NNTPBinaryContent), or None if one couldn't be
        """
        if content is not None:
            self.add(content)

        if len(self) == 0:
            return None

        if password is None:
            password = self.password

        tmp_path, _ = self.mkstemp(content=name or self.name or random_str())

        for path in self:
            result = self._extract(path, tmp_path, password)
            if result is None:
                # Encrypted in a way only 7-Zip can read
                from newsreap.codecs.Codec7Zip import Codec7Zip
                seven = Codec7Zip(
                    work_dir=self.work_dir, overwrite=self.overwrite)
                if not seven.can_exe(seven._bin):
                    logger.error(
                        '%s needs the 7-Zip tool to be extracted.' %
                        basename(path))
                    rm(tmp_path)
                    return None

                found = seven.decode(path, password=password)
                if not found:
                    rm(tmp_path)
                    return None

                # What 7-Zip extracted (a folder of its own)
                for extracted in found:
                    extracted.detach()
                    for root, _, files in walk(extracted.path()):
                        for filename in files:
                            source = join(root, filename)
                            target = join(
                                tmp_path, relpath(source, extracted.path()))
                            if mkdir(dirname(target)) and (
                                    self.overwrite or not exists(target)):
                                move(source, target)
                    rm(extracted.path())
                continue

            if not result:
                rm(tmp_path)
                return None

        self.clear()

        results = NNTPBinaryContent(tmp_path)

        # Removed when it is no longer used
        results.attach()

        _resultset = sortedset(key=lambda x: x.key())
        _resultset.add(results)
        return _resultset

    def test(self, content=None, password=None):
        """
        Checks the ZIP files given (or added) can be extracted (every file
        passes its checksum); returns True if they all can
        """
        if content is not None:
            paths = self.get_paths(content)

        elif len(self.archive):
            paths = iter(self.archive)

        else:
            raise AttributeError('CodecZip: No ZIP file given.')

        if password is None:
            password = self.password

        for path in paths:
            try:
                with zipfile.ZipFile(path) as archive:
                    if password is not None:
                        archive.setpassword(password.encode('utf-8'))

                    if archive.testzip() is not None:
                        # A damaged file
                        return False

            except (NotImplementedError, RuntimeError, zipfile.BadZipFile,
                    zipfile.LargeZipFile, EOFError, OSError, ValueError):
                # Can't be read (damaged, a password is needed, or an
                # encryption zipfile doesn't know)
                return False

        return True

    def __repr__(self):
        """
        Return an unambigious version of the object
        """
        return '<CodecZip work_dir="%s" clevel="%s" archives="%d" />' % (
            self.work_dir,
            self.level,
            len(self.archive),
        )
