# -*- coding: utf-8 -*-
#
# A Codec for handling RAR Files
#
# Copyright (C) 2015-2016 Chris Caron <lead2gold@gmail.com>
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

from os.path import abspath
from tempfile import mkdtemp
from shutil import rmtree

from newsreap.NNTPBinaryContent import NNTPBinaryContent
from newsreap.codecs.CodecFile import CodecFile
from newsreap.codecs.CodecFile import CompressionLevel
from newsreap.SubProcess import SubProcess
from newsreap.Utils import mkdir

# Logging
import logging
from newsreap.Logging import NEWSREAP_CODEC
logger = logging.getLogger(NEWSREAP_CODEC)

# Rar Path
DEFAULT_RAR_PATH = '/usr/bin/rar'

# UnRar Path
DEFAULT_UNRAR_PATH = '/usr/bin/unrar'

# Size to default spliting to if not otherwise specified
DEFAULT_SPLIT_SIZE_MB = 25


class CodecRar(CodecFile):
    """
    CodecRar is a wrapper to the systems rar binary since there is no
    open source code that will otherwise allow us to extract rar
    files any other way
    """

    def __init__(self, work_dir=None,
                 rar_path=DEFAULT_RAR_PATH,
                 unrar_path=DEFAULT_UNRAR_PATH, *args, **kwargs):
        """
        Initialize the Codec
        """
        super(CodecRar, self).__init__(work_dir=work_dir, *args, **kwargs)

        # Initalize Paths
        self._rar = rar_path
        self._unrar = unrar_path

        # +++++++++++++++++++++++
        # Compression Settings
        # +++++++++++++++++++++++

        # Recovery record
        # For RAR 4.x
        #   The parameter can be either the number of recovery sectors
        #     (n=1… 524288) or percent of archive size if '%' or 'p'
        # We default to 5%
        self.recovery_record = kwargs.get('recovery_record', '5p')

        # Exclude the base directory when creating an archive
        self.exclude_base_dir = kwargs.get('exclude_base_dir', False)

        # Exclude the base directory when creating an archive
        self.volume_size = kwargs.get('volume_size', False)

    def compress(self, path, split=False, *args, **kwargs):
        """
        Takes a specified path (and or file) and compresses it. If this
        function is successful, it returns a set of NNTPBinaryContent()
        objects that are 'not' detached. Which means if they go out of scope,
        the compressed content will be lost.

        """

        # Some simple error checking to save from doing to much here
        if len(self) == 0:
            return False

        # Create the path if it does't exist already
        if not mkdir(path):
            return False

        # Create a temporary path to work in
        tmp_path = mkdtemp(dir=abspath(path))

        # Initialize our command
        execute = [
            # Our Executable RAR Application
            self._rar,
            # Use Add Flag
            'a',
        ]

        # Password Protection
        if self.password is not None:
            execute.append('-p%s' % self.password)

        # Handle Compression Level
        if self.level is CompressionLevel.Maximum:
            execute.append('-m5')

        elif self.level is CompressionLevel.Average:
            execute.append('-m3')

        elif self.level is CompressionLevel.Minimum:
            execute.append('-m0')

        # Exclude base directory from names
        if self.exclude_base_dir:
            execute.append('-ep1')

        # Handle RAR Volume Splitting
        if self.volume_size is True:
            execute.append('-v%s' % DEFAULT_SPLIT_SIZE_MB)

        elif self.volume_size:
            execute.apend('-v%s' % split)

        # Handle Recovery Record
        if self.recovery_record is not None:
            execute.append('-rr%s' % self.recovery_record)

        # Specify the Destination Path
        execute.append(tmp_path)

        # Add all of our paths now
        for _path in self:
            execute.append(_path)

        # Create our SubProcess Instance
        sp = SubProcess(execute)

        # Start our execution now
        sp.start()

        # TODO: I spent so much time threading this for a reason.  Break every
        # now and then and report status, check disk space maybe?

        # For now... just wait for it to finish
        sp.join()

        # Let the caller know our status
        if not sp.successful():
            # Cleanup Temporary Path
            rmtree(tmp_path)
            return False

        content = []
        # TODO: Build list of compressed RAR Files and return them as
        # NNTPBinaryContent()

        # TODO: move these files out of tmp_path and place them in path

        # Cleanup Temp Path
        rmtree(tmp_path)

        # TODO: Return list of NNTPBinaryContent() objects created

    def decompress(self, path, password=None, *args, **kwargs):
        """
        path must be pointing to a directory containing rar files that can be
        easily sorted on. Alternatively, path can be of type NNTPContent() or
        a set/list of.

        If no password is specified, then the password configuration loaded
        into the class is used instead.

        """
        # TODO

    def __repr__(self):
        """
        Return a printable object
        """
        return '<CodecRar />'
