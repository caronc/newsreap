# -*- coding: utf-8 -*-
#
# A Codec for handling 7-Zip Files
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

import re

from blist import sortedset
from os.path import basename
from os.path import splitext

from newsreap.NNTPBinaryContent import NNTPBinaryContent
from newsreap.codecs.CodecFile import CodecFile
from newsreap.codecs.CodecFile import CompressionLevel
from newsreap.SubProcess import SubProcess
from newsreap.Utils import random_str
from newsreap.Utils import strsize_to_bytes
from newsreap.Utils import pushd
from newsreap.Utils import rm

# Logging
import logging
from newsreap.Logging import NEWSREAP_CODEC
logger = logging.getLogger(NEWSREAP_CODEC)

# 7-Zip Binary Path
DEFAULT_PATH = '/usr/bin/7za'

# Size to default spliting to if not otherwise specified
DEFAULT_SPLIT_SIZE = strsize_to_bytes('25M')

# Used to detect the 7z part #
#  - supports .7z, 7za
#  - supports .7z0, .7z1, .r02, etc
#  - supports .part00.7z, .part01.7z, etc
SEVEN_ZIP_PART_RE = re.compile(
    '^.*?\.((part|7z|)(?P<part>[0-9]+)(\.7z)?|7z)$',
    re.IGNORECASE,
)

# Return Codes
ERROR_CODE_NORMAL = 0
ERROR_CODE_WARNING = 1
ERROR_CODE_FATAL = 2
ERROR_CODE_CMD_LINE_ERROR = 7
ERROR_CODE_NO_MEMORY = 8
ERROR_CODE_USER_ABORTED = 255


class Codec7Zip(CodecFile):
    """
    Codec7Zip is a wrapper to the systems 7-Zip binary since there is no
    open source code that will otherwise allow us to extract 7-Zip files
    any other way.
    """

    def __init__(self, work_dir=None,
                 bin_path=DEFAULT_PATH,
                 # The volume size identifies how to split up the 7-Zip file
                 # (into multi-parts).  If set to False then no splitting is
                 # done.  If it's set to True, then the Default is used.
                 volume_size=False,
                 # Don't overwrite existing files if one of the same name is
                 # found in the archive. Set this to True if you want to
                 # overwrite matched content.
                 overwrite=False,
                 *args, **kwargs):
        """
        Initialize the Codec
        """
        super(Codec7Zip, self).__init__(work_dir=work_dir, *args, **kwargs)

        # Initalize Paths
        self._bin = bin_path

        # +++++++++++++++++
        # Encoding Settings
        # +++++++++++++++++

        # Exclude the base directory when creating an archive
        self.volume_size = volume_size
        if isinstance(self.volume_size, basestring):
            self.volume_size = int(strsize_to_bytes(self.volume_size))

        if self.volume_size is True:
            self.volume_size = DEFAULT_SPLIT_SIZE

        self.overwrite = overwrite

    def encode(self, content=None, name=None, *args, **kwargs):
        """
        Takes a specified path (and or file) and compresses it. If this
        function is successful, it returns a set of NNTPBinaryContent()
        objects that are 'not' detached.

        The function returns None if it fails in any way

        """

        if content is not None:
            self.add(content)

        # Some simple error checking to save from doing to much here
        if len(self) == 0:
            return None

        if not self.can_exe(self._bin):
            return None

        if not name:
            name = self.name
            if not name:
                name = random_str()

        tmp_path, tmp_file = self.mkstemp(content=name, suffix='.7z')

        # Initialize our command
        execute = [
            # Our Executable 7-Zip Application
            self._bin,
            # Use Add Flag
            'a',
            # Default mode is 7-Zip
            '-t7z',
        ]

        # Password Protection
        if self.password is not None:
            execute.append('-p%s' % self.password)

        # Handle Compression Level
        if self.level is CompressionLevel.Maximum:
            execute.append('-mx9')

        elif self.level is CompressionLevel.Average:
            execute.append('-mx5')

        elif self.level is CompressionLevel.Minimum:
            execute.append('-mx1')

        # Don't prompt for anything
        execute.append('-y')

        if not name:
            name = splitext(basename(tmp_file))[0]

        # Handle 7Z Volume Splitting
        if self.volume_size:
            execute.append('-v%sb' % self.volume_size)

        if self.cpu_cores is not None and self.cpu_cores > 1:
            # create archive using multiple threads
            execute.append('-mmt%d' % self.cpu_cores)

        # Stop Switch Parsing
        execute.append('--')

        # Specify the Destination Path
        execute.append(tmp_file)

        # Add all of our paths now
        for _path in self:
            execute.append(_path)

        # Create our SubProcess Instance
        sp = SubProcess(execute)

        # Start our execution now
        sp.start()

        found_set = None
        while not sp.is_complete(timeout=1.5):

            found_set = self.watch_dir(
                tmp_path,
                prefix=name,
                ignore=found_set,
            )

        # Handle remaining content
        found_set = self.watch_dir(
            tmp_path,
            prefix=name,
            ignore=found_set,
            seconds=-1,
        )

        # Let the caller know our status
        if not sp.successful():
            # Cleanup Temporary Path
            rm(tmp_path)
            return None

        if not len(found_set):
            return None

        # Create a resultset
        results = sortedset(key=lambda x: x.key())

        # iterate through our found_set and create NNTPBinaryContent()
        # objects from them.
        part = 0
        for path in found_set:
            # Iterate over our found files and determine their part
            # information
            _re_results = SEVEN_ZIP_PART_RE.match(path)
            if _re_results:
                if _re_results.group('part') is not None:
                    part = int(_re_results.group('part'))

                else:
                    part += 1

            else:
                part += 1

            content = NNTPBinaryContent(
                path,
                part=part,
                total_parts=len(found_set),
            )

            # Loaded data is by default detached; we want to attach it
            content.attach()

            # Add our attached content to our results
            results.add(content)

        # Clean our are list of objects to archive
        self.clear()

        # Return our
        return results

    def decode(self, content=None, name=None, password=None, *args, **kwargs):
        """
        content must be pointing to a directory containing 7-Zip files that can
        be easily sorted on. Alternatively, path can be of type NNTPContent()
        or a set/list of.

        If no password is specified, then the password configuration loaded
        into the class is used instead.

        An NNTPBinaryContent() object containing the contents of the package
        within a sortedset() object.  All decoded() functions have to return
        a resultset() to be consistent with one another.

        """
        if content is not None:
            self.add(content)

        # Some simple error checking to save from doing to much here
        if len(self) == 0:
            return None

        if not self.can_exe(self._bin):
            return None

        if not password:
            password = self.password

        # Initialize our command
        execute = [
            # Our Executable 7-Zip Application
            self._bin,
            # Use Add Flag
            'x',
            # Assume Yes
            '-y',
        ]

        # Password Protection
        if password is not None:
            execute.append('-p%s' % password)
        else:
            # Do not prompt for password
            execute.append('-p-')

        if self.overwrite:
            # Overwrite files
            execute.append('-aoa')

        else:
            # Don't overwrite files
            execute.append('-aos')

        # Stop Switch Parsing
        execute.append('--')

        if not name:
            name = self.name
            if not name:
                name = random_str()

        for _path in self:
            # Temporary Path
            tmp_path, _ = self.mkstemp(content=name)

            with pushd(tmp_path):
                # Create our SubProcess Instance
                sp = SubProcess(list(execute) + [_path])

                # Start our execution now
                sp.start()

                found_set = None
                while not sp.is_complete(timeout=1.5):

                    found_set = self.watch_dir(
                        tmp_path,
                        ignore=found_set,
                    )

                # Handle remaining content
                found_set = self.watch_dir(
                    tmp_path,
                    ignore=found_set,
                    seconds=-1,
                )

                # Let the caller know our status
                if not sp.successful():
                    # Cleanup Temporary Path
                    rm(tmp_path)
                    return None

                if not len(found_set):
                    logger.warning(
                        '7Z archive (%s) contained no content.' % \
                        basename(_path),
                    )

        # Clean our are list of objects to archive
        self.clear()

        # Return path containing unrar'ed content
        results = NNTPBinaryContent(tmp_path)

        # We intentionally attach it's content
        results.attach()

        # Create a sortedset to return
        _resultset = sortedset(key=lambda x: x.key())
        _resultset.add(results)

        # Return our content
        return _resultset

    def test(self, content=None, password=None):
        """
        content must be pointing to a directory containing rar files that can
        be easily sorted on. Alternatively, path can be of type NNTPContent()
        or a set/list of.

        If no password is specified, then the password configuration loaded
        into the class is used instead.

        This function just tests an archive to see if it can be properly
        extracted using the known password.
        """
        if content is not None:
            paths = self.get_paths(content)

        elif len(self.archive):
            # Get first item in archive
            paths = iter(self.archive)

        else:
            raise AttributeError("Codec7Zip: No 7-Zip file detected.")

        if not self.can_exe(self._bin):
            return None

        if not password:
            password = self.password

        # Initialize our command
        execute = [
            # Our Executable 7-Zip Application
            self._bin,
            # Use Test Flag
            't',
            # Assume Yes
            '-y',
        ]

        # Password Protection
        if password is not None:
            execute.append('-p%s' % password)
        else:
            # Do not prompt for password
            execute.append('-p-')

        # Stop Switch Parsing
        execute.append('--')

        for _path in paths:
            # Create our SubProcess Instance
            sp = SubProcess(list(execute) + [_path])

            # Start our execution now
            sp.start()
            sp.join()

            # Let the caller know our status
            if not sp.successful():
                return False

        return True
