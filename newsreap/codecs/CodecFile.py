# -*- coding: utf-8 -*-
#
# A Base Codec Class for deciphering data that is requiring an external file
#
# Copyright (C) 2016 Chris Caron <lead2gold@gmail.com>
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

import errno
from blist import sortedset

from os.path import isdir
from os.path import isfile
from os.path import exists
from os.path import join
from os.path import basename
from os.path import abspath
from os.path import expanduser

from os import X_OK
from os import access

from tempfile import mkdtemp

from newsreap.Utils import mkdir
from newsreap.NNTPContent import NNTPContent
from newsreap.NNTPArticle import NNTPArticle
from newsreap.NNTPBinaryContent import NNTPBinaryContent
from newsreap.NNTPSettings import DEFAULT_TMP_DIR
from newsreap.Utils import random_str
from newsreap.Utils import bytes_to_strsize
from newsreap.Utils import find
from os.path import splitext

# Logging
import logging
from newsreap.Logging import NEWSREAP_CODEC
logger = logging.getLogger(NEWSREAP_CODEC)


class CompressionLevel(object):
    """
    Support general compression level settings so that the calling user doesn't
    have to be aware of the different types supported by the actual executable.
    """

    # Maximum Compression will be slower to use and generate the most i/o
    # but will overall save the most disk space.
    Maximum = u'+++'

    # The average setting is what the actual executable being called would
    # have otherwise defaulted to.  It's not nessisarily the highest
    # compression level, but it's not the worst either.
    Average = u'~'

    # This will cause larger files to be generated (thus taking up more disk
    # space and posting space/time).  However the file generation itself will
    # be very fast (with respect to the other levels)
    Minimum = u'---'

# Tuple of supported Compression Levels
COMPRESSION_LEVELS = (
    CompressionLevel.Maximum,
    CompressionLevel.Average,
    CompressionLevel.Minimum,
)

# TODO: Move PAR2 stuff into it's own CodecPar2 file that way it
# can be optionally chained with other Codecs

# TODO: Check that the work_dir is never that of the encoding path
# (for obvious reasons)

# Path to the par2 binary file
PAR2_BINARY = '/usr/bin/par2'

class CodecFile(object):
    """
    CodecFile compliments CodecBase by wrapping the codecs that can only
    be accessed through an outside binary file located on the system.
    """

    def __init__(self, work_dir=None, password=None,
                 level=CompressionLevel.Average, *args, **kwargs):
        """
        The dir identfies the directory to store our sessions in
        until they can be properly handled.
        """

        # If the password is set to None then it is presumed that
        # you don't want to use it.  Keep in mind that setting this
        # to an empty string presumes that you want to set a blank
        # password (but a password none the less)
        self.password = password

        # Compression Level
        self.level = level
        if self.level not in COMPRESSION_LEVELS:
            # Bad compression level specified
            logger.error(
                'Invalid CodecFile compression specified (%s)' % str(level),
            )
            raise AttributeError("Invalid compression level specified.")

        if work_dir is None:
            self.work_dir = DEFAULT_TMP_DIR

        else:
            self.work_dir = abspath(expanduser(work_dir))

        if not isdir(self.work_dir):
            # create directory
            if mkdir(self.work_dir):
                logger.info('Created directory: %s' % self.work_dir)

            else:
                logger.error('Failed to created directory: %s' % self.work_dir)
                # Should not continue under this circumstance
                raise IOError((
                    errno.EACCES,
                    'Failed to create directory: %s' % self.work_dir,
                ))

        # Contains a list of paths to be archived
        self.archive = set()

    def add(self, path):
        """
        Adds files, directories, NNTPContent() and NNTPArticle objects
        to archive.
        """

        # duplicates are ignored in a blist and therefore
        # we just capture the length of our list before
        # and after so that we can properly return a True/False
        # value
        _bcnt = len(self.archive)


        if isinstance(path, basestring):
            # Support Directories and filenames

            # Tidy our path
            path = abspath(expanduser(path))

            if exists(path):
                if isdir(path):
                    #  We can't have the work_dir be inside of the path
                    if self.work_dir.startswith(path, 0, len(self.work_dir)):
                        # path does not exist
                        logger.warning(
                            "Codec path includes work_dir (skipping): '%s'" % path)
                        return False

                # We're good if we get here
                self.archive.add(path)

            else:
                # path does not exist
                logger.warning(
                    "Codec path does not exist (skipping): '%s'" % path)
                return False

        elif isinstance(path, NNTPContent):
            if not path.filepath:
                logger.warning(
                    "Codec content does map to any data (skipping)")
                return False

            # Support NNTPContent() objects
            self.add(path.filepath)

        elif isinstance(path, NNTPArticle):
            # Support NNTPArticle() objects
            if not len(path):
                logger.warning(
                    "Codec article does not contain any content (skipping)")
                return False

            for content in path:
                self.add(content)

        elif isinstance(path, (sortedset, set, tuple, list)):
            # Support lists by recursively calling ourselves
            if not len(path):
                logger.warning(
                    "Codec entries do not contain any content (skipping)")
                return False

            for c in path:
                self.add(c)

        return len(self.archive) > _bcnt

    def clear(self):
        """
        clears out all content added to our internal archive
        """
        self.archive.clear()

    def encode(self, content=None, *args, **kwargs):
        """
        Takes a specified content (dir or file) and compresses it. If this
        function is successful, it returns a set of NNTPBinaryContent()
        objects that are 'not' detached. Which means if they go out of scope,
        the compressed content will be lost.

        If this function fails, or there is nothing to encode, the function
        should return None.

        the content passed into should be passed into the self.add() call
        if it's not set to None otherwise. The content encoded is always
        that of what is in the self.archive sortedset.

        """
        raise NotImplementedError(
            "CodecFile() inheriting class is required to impliment compress()"
        )

    def decode(self, content, *args, **kwargs):
        """
        content must be a path containing rar files or at the very least
        NNTPContent() objects (or set of) containing rar files.

        """
        raise NotImplementedError(
            "CodecFile() inheriting class is required to impliment decompress()"
        )

    def generate_pars(self, path, *args, **kwargs):
        """
        Provide the path to where content can be found and PAR2 files will be
        generated. A list of NNTPBinaryContent() objects will be returned plus
        one NNTPAsciiContent() containing the PAR2 meta file.

        """
        # TODO

    def can_exe(self, fpath):
        """
        Can test if path exists and is executable
        """
        return isfile(fpath) and access(fpath, X_OK)

    def mkstemp(self, path=None,  suffix='.tmp', prefix='_tmp_'):
        """
        A wrapper to mkstemp that only handles reference to the filepath/name
        itself. It creates a unique subdirectory that it generates the new
        temporary file within that can be referenced.

        If a path is specified, then the function parses out the directory
        infront of it and possibly a prefix at the end and swaps it with the
        prefix specified.  This is just an easier way of manipulating a
        filename or directory name that was recently pulled from an
        NNTPContent() object.

        This function returns both the temporary directory created and the
        temporary file prepared.

        """

        # Create a temporary directory to work in
        tmp_path = mkdtemp(prefix='_nr.codec-', dir=self.work_dir)
        tmp_file = None

        if isinstance(path, basestring):
            tmp_file = join(
                tmp_path,
                '%s%s' % (splitext(basename(path))[0], suffix,
            ))

        elif isinstance(path, NNTPContent):
            # use the filename based on the path
            if path.filename:
                tmp_file = join(
                    tmp_path,
                    '%s%s' % (splitext(basename(path.filename))[0], suffix,
                ))

        elif isinstance(path, NNTPArticle):
            if len(path) > 0:
                if path[0].filename:
                    tmp_file = join(
                        tmp_path,
                        '%s%s' % (
                            splitext(basename(path[0].filename))[0],
                            suffix,
                    ))

        if tmp_file is None:
            # Fall back
            tmp_file = join(
                tmp_path,
                '%s%s' % (random_str(), suffix),
            )

        return tmp_path, tmp_file

    def watch_dir(self, path, prefix='', ignore=None, seconds=15):
        """Monitors a directory for files that have been added/changed

            path: is the path to monitor
            ignore: is a set of files already parsed
            seconds: is how long it takes a file to go untouched for before
              we presume it has been completely written to disk.
        """

        if ignore is None:
            ignore = set()

        findings = find(
            path, fsinfo=True,
            prefix_filter=prefix,
            min_depth=1, max_depth=1,
            case_sensitive=True,
        )

        findings = [
            (p, f['size'], f['created'], f['modified'])
                for p, f in findings.items()
                  if (f['modified'] - f['created']).total_seconds() >= seconds
                    and f['basename'] not in ignore
        ]

        # Sort list by created date
        findings.sort(key=lambda x: x[3])

        for f in findings:
            logger.info('Created %s (size=%s)' % (
                f, bytes_to_strsize(f[1]),
            ))
            # Add to our filter list
            ignore.add(f[0])

        # Return our ignore list (which is acutally also a found list)
        return ignore

    def __iter__(self):
        """
        Grants usage of the next()
        """

        # Ensure our stream is open with read
        return iter(self.archive)

    def __len__(self):
        """
        Returns the number of archive content entries found
        """
        return len(self.archive)

    def __str__(self):
        """
        Return a printable version of the codec
        """
        return repr(self)

    def __repr__(self):
        """
        Return an unambigious version of the objec
        """
        return '<CodecFile work_dir="%s" clevel="%s" archives="%d" />' % (
            self.work_dir,
            self.level,
            len(self.archive),
        )
