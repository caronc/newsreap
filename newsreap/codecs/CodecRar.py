# -*- coding: utf-8 -*-
#
# A Codec for handling RAR Files
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

from blist import sortedset
from os.path import basename
from os.path import splitext
from os.path import exists

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

# Rar Path
DEFAULT_RAR_PATH = next((c for c in (
    # Fedora, CentOS, and RedHat
    '/usr/bin/rar',
    # Ubuntu/Debian
    '/usr/local/bin/rar',
) if exists(c)), None)

# UnRar Path
DEFAULT_UNRAR_PATH = next((c for c in (
    # Fedora, CentOS, and RedHat
    '/usr/bin/unrar',
    # Ubuntu/Debian
    '/usr/local/bin/unrar',
) if exists(c)), None)

# Size to default spliting to if not otherwise specified
DEFAULT_SPLIT_SIZE = strsize_to_bytes('25M')

# Used to detect the rar part #
#  - supports .rar
#  - supports .r00, .r01, .r02, etc
#  - supports .part00.rar, .part01.rar, etc
RAR_PART_RE = re.compile(
    '^.*?\.((part|r)(?P<part>[0-9]+)(\.rar)?|rar)$',
    re.IGNORECASE,
)


class CodecRar(CodecFile):
    """
    CodecRar is a wrapper to the systems rar binary since there is no
    open source code that will otherwise allow us to extract rar
    files any other way
    """

    def __init__(self, work_dir=None,
                 rar_path=DEFAULT_RAR_PATH,
                 unrar_path=DEFAULT_UNRAR_PATH,
                 # Recovery Record Percentage (default at 5%)
                 recovery_record='5p',
                 # The volume size identifies how to split up the RAR file
                 # (into multi-parts).  If set to False then no splitting is
                 # done.  If it's set to True, then the Default is used.
                 volume_size=False,
                 # Even if the RAR is damaged, keep going if you can; Set this
                 # to True if you actually want this to happen. Broken files
                 # are written to as much as they can and extraction continues
                 # as long as it can.
                 keep_broken=False,
                 # Don't overwrite existing files if one of the same name is
                 # found in the archive. Set this to True if you want to
                 # overwrite matched content.
                 overwrite=False,
                 # Update the dates associated with the files extracted to be
                 # current.
                 freshen=False,
                 *args, **kwargs):
        """
        Initialize the Codec
        """
        super(CodecRar, self).__init__(work_dir=work_dir, *args, **kwargs)

        # Initalize Paths
        self._rar = rar_path
        self._unrar = unrar_path

        # +++++++++++++++++
        # Encoding Settings
        # +++++++++++++++++

        # Recovery record
        # For RAR 4.x
        #   The parameter can be either the number of recovery sectors
        #     (n=1… 524288) or percent of archive size if '%' or 'p'
        self.recovery_record = recovery_record
        if isinstance(self.recovery_record, int):
            self.recovery_record = str(self.recovery_record)

        # Exclude the base directory when creating an archive
        self.volume_size = volume_size
        if isinstance(self.volume_size, basestring):
            self.volume_size = int(strsize_to_bytes(self.volume_size))

        if self.volume_size is True:
            self.volume_size = DEFAULT_SPLIT_SIZE

        self.keep_broken = keep_broken
        self.overwrite = overwrite
        self.freshen = freshen

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

        if not self.can_exe(self._rar):
            return None

        if not name:
            name = self.name
            if not name:
                name = random_str()

        tmp_path, tmp_file = self.mkstemp(content=name, suffix='.rar')

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

        # Exclude base directory from archive
        execute.append('-ep1')

        if not name:
            name = splitext(basename(tmp_file))[0]

        # Now place content within directory identifed by it's name
        execute.append('-ap%s' % name)

        # Handle RAR Volume Splitting
        if self.volume_size:
            execute.append('-v%sb' % self.volume_size)

        # Handle Recovery Record
        if self.recovery_record is not None:
            execute.append('-rr%s' % self.recovery_record)

        if self.cpu_cores is not None and self.cpu_cores > 1:
            # create archive using multiple threads
            execute.append('-mt%d' % self.cpu_cores)

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
            _re_results = RAR_PART_RE.match(path)
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
        content must be pointing to a directory containing rar files that can
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

        if not self.can_exe(self._unrar):
            return None

        if not password:
            password = self.password

        # Initialize our command
        execute = [
            # Our Executable RAR Application
            self._unrar,
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

        if self.keep_broken:
            # Keep Broken Flag
            execute.append('-kb')

        if self.overwrite:
            # Overwrite files
            execute.append('-o+')

        else:
            # Don't overwrite files
            execute.append('-o-')

        if self.freshen:
            # Freshen files
            execute.append('-f')

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
                        'RAR archive (%s) contained no content.' %
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
            raise AttributeError("CodecRar: No rar file detected.")

        if not self.can_exe(self._unrar):
            return None

        if not password:
            password = self.password

        # Initialize our command
        execute = [
            # Our Executable RAR Application
            self._unrar,
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

        if self.keep_broken:
            # Keep Broken Flag
            execute.append('-kb')

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
