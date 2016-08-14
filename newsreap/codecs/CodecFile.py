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

from blist import sortedset

from os.path import isdir
from os.path import exists

from newsreap.Utils import mkdir
from newsreap.code.CodecFile import DEFAULT_TMP_DIR
from newsreap.NNTPContent import NNTPContent
from newsreap.NNTPBinaryContent import NNTPBinaryContent

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

# Path to the par2 binary file
PAR2_BINARY = '/usr/bin/par2'

class CodecFile(object):
    """
    CodecFile compliments CodecBase by wrapping the codecs that can only
    be accessed through an outside binary file located on the system.
    """

    def __init__(self, tmp_path=None, password=None,
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

        if tmp_path is None:
            self.tmp_path = DEFAULT_TMP_DIR
        else:
            self.tmp_path = tmp_path

        if not isdir(self.tmp_path):
            # create directory
            if mkdir(self.tmp_path):
                logger.info('Created directory: %s' % self.tmp_path)
            else:
                logger.error('Failed to created directory: %s' % self.tmp_path)
                ## Should not continue under this circumstance
                # raise IOError((
                #     errno.EACCES,
                #     'Failed to create directory: %s' % self.tmp_path,
                # ))

        # Contains a list of paths to be archived
        self.archive = sortedset(key=lambda x: x.key())

    def add(self, path):
        """
        Adds files and directories to archive
        """

        # duplicates are ignored in a blist and therefore
        # we just capture the length of our list before
        # and after so that we can properly return a True/False
        # value
        _bcnt = len(self.decoded)

        if isinstance(path, NNTPContent):
            self.archive.add(path.filepath)

        elif isinstance(path, basestring):
            if exists(path):
                self.archive.add(path)

        return len(self.archive) > _bcnt

    def compress(self, path, split=False, *args, **kwargs):
        """
        Takes a specified path (and or file) and compresses it. If this
        function is successful, it returns a set of NNTPBinaryContent()
        objects that are 'not' detached. Which means if they go out of scope,
        the compressed content will be lost.

        """
        raise NotImplementedError(
            "CodecFile() inheriting class is required to impliment compress()"
        )

    def decompress(self, path, *args, **kwargs):
        """
        path must be pointing to a directory where the produced rar files
        shall be placed in.
        easily sorted on. Alternatively, path can be of type NNTPContent() or
        a set/list of.

        If no password is specified, then the password configuration loaded
        into the class is used instead.
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

    def __iter__(self):
        """
        Grants usage of the next()
        """

        # Ensure our stream is open with read
        return iter(self.decoded)

    def __len__(self):
        """
        Returns the number of decoded content entries found
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
        return '<CodecFile tmp_path="%s" clevel="%s" archives="%d" />' % (
            self.tmp_path,
            self.level,
            len(self.archive),
        )
