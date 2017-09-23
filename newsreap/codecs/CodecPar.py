# -*- coding: utf-8 -*-
#
# A Codec for handling PAR Files
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
from os.path import dirname

from newsreap.NNTPBinaryContent import NNTPBinaryContent
from newsreap.codecs.CodecFile import CodecFile
from newsreap.SubProcess import SubProcess
from newsreap.Utils import strsize_to_bytes
from newsreap.Utils import pushd
from newsreap.Utils import rm

# Logging
import logging
from newsreap.Logging import NEWSREAP_CODEC
logger = logging.getLogger(NEWSREAP_CODEC)

# Par Path
DEFAULT_PAR2_PATH = '/usr/bin/par2'

# Used to detect the par part #
#  - supports .vol07+08.par2 (the data block)
#  - supports .par2 (The index file)
PAR_PART_RE = re.compile(
    '^.*?\.(vol(?P<index>\d+)\+(?P<count>\d+)\.)?par2?$',
    re.IGNORECASE,
)

# Size to default spliting to if not otherwise specified
DEFAULT_BLOCK_SIZE = strsize_to_bytes('300K')

# By default we create recovery records for up to 5% of the total data
DEFAULT_RECOVERY_PERCENT = 5


class ParReturnCode(object):
    """
    Converts the return values of the par2 command line to interpretable
    response types.
    """

    # This is the return type if there simply isn't any damage to the contents
    NoParFiles = -1

    # This is the return type if there simply isn't any damage to the contents
    NoRepairRequired = 0

    # Repairable return type occurs if the content is damaged but 'can' be
    # repaired.
    Repairable = 1

    # This is returned if the content is unrepairable (not enough blocks)
    Unrepairable = 2


class ParVersion(object):
    """Defines the PAR Versions; presently only PAR2 exists but there are
    enough rumors of a developing PAR3 format that there may or may not
    be a different binary or different set of arguments.
    """
    Two = 2

# All Versions defined above must be also dadded to the list below
PAR_VERSIONS = (ParVersion.Two, )


class CodecPar(CodecFile):
    """
    CodecPar is a wrapper to the systems rar binary since there is no
    open source code that will otherwise allow us to extract rar
    files any other way
    """

    def __init__(self, work_dir=None,
                 par_path=DEFAULT_PAR2_PATH,
                 par_version=ParVersion.Two,
                 # Recovery Record Percentage (default at 5%)
                 recovery_percent=DEFAULT_RECOVERY_PERCENT,

                 # The block size should be set to the size of the article
                 # being posted on usenet.  For example, if each article is
                 # 300K, then that is what the block_size should be set to.

                 # If set to False then no splitting is done.  If it's set to
                 # True, then the Default is used.
                 block_size=True,
                 *args, **kwargs):
        """
        Initialize the Codec
        """
        super(CodecPar, self).__init__(work_dir=work_dir, *args, **kwargs)

        # Initalize Paths
        self._par = par_path

        self._par_version = par_version
        if self._par_version not in PAR_VERSIONS:
            raise AttributeError(
                'CodecPar: Invalid PAR Version specified %s' % \
                str(self._par_version))

        # +++++++++++++++++
        # Encoding Settings
        # +++++++++++++++++

        self.block_size = block_size
        if isinstance(self.block_size, basestring):
            self.block_size = int(strsize_to_bytes(self.block_size))

        if self.block_size is True:
            self.block_size = DEFAULT_BLOCK_SIZE

        self.recovery_percent = recovery_percent
        if self.recovery_percent:
            if self.recovery_percent < 0 or self.recovery_percent > 100:
                raise AttributeError(
                    'CodecPar: Invalid recovery record percent %s%%.' % \
                    self.recovery_percent)

    def encode(self, content=None, *args, **kwargs):
        """
        Takes a specified path (and or file) and creates par2 files based on
        it. If this function is successful, it returns a set of
        NNTPBinaryContent() objects identifying the PAR2 files generated
        based on the passed in content.

        The function returns None if it fails in any way.

        """

        if content is not None:
            self.add(content)

        # Some simple error checking to save from doing to much here
        if len(self) == 0:
            return None

        if not self.can_exe(self._par):
            return None

        for target in self.archive:
            # Base entry on first file in the list
            name = basename(target)
            target_dir = dirname(target)

            #tmp_path, tmp_file = self.mkstemp(content=name, suffix='.par2')

            # Initialize our command
            execute = [
                # Our Executable PAR Application
                self._par,
                # Use Create Flag
                'create',
            ]

            # Handle PAR Block Size
            if self.block_size:
                execute.append('-s%s' % self.block_size)

            if self.recovery_percent:
                execute.append('-r%d' % self.recovery_percent)

            if self.cpu_cores is not None and self.cpu_cores > 1:
                # to repair concurrently - uses multiple threads
                execute.append('-t+')

            # Stop Switch Parsing
            execute.append('--')

            # Now add our target (we can only do one at a time which i why we
            # loop) and run our setups
            execute.append(target)

            found_set = sortedset()
            with pushd(target_dir):
                # Create our SubProcess Instance
                sp = SubProcess(execute)

                # Start our execution now
                sp.start()

                while not sp.is_complete(timeout=1.5):

                    found_set = self.watch_dir(
                        target_dir,
                        prefix=name,
                        regex=PAR_PART_RE,
                        ignore=found_set,
                    )

            # Handle remaining content
            found_set = self.watch_dir(
                target_dir,
                prefix=name,
                regex=PAR_PART_RE,
                ignore=found_set,
                seconds=-1,
            )

            # Let the caller know our status
            if not sp.successful():
                # We're done; we failed
                return None

            if not len(found_set):
                # We're done; we failed
                return None

            # Create a resultset
            results = sortedset(key=lambda x: x.key())

            part = 0
            # iterate through our found_set and create NNTPBinaryContent()
            # objects from them.
            for path in found_set:
                # Iterate over our found files and determine their part
                # information
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

    def decode(self, content=None, *args, **kwargs):
        """
        content must be pointing to a directory containing par files that can
        be easily retrieved. Alternatively, path can be of type NNTPContent()
        or a set/list of.

        An sortedset of NNTPBinaryContent() objects are returned containing
        any new content that was generated as a result of the par2 call

        If an error occurs then None is returned.

        """
        if content is not None:
            self.add(content)

        # Some simple error checking to save from doing to much here
        if len(self) == 0:
            return None

        if not self.can_exe(self._par):
            return None

        # filter our results by indexes
        indexes = self.__filter_pars(self.archive, indexes=True, volumes=False)

        # Initialize our command
        execute = [
            # Our Executable PAR Application
            self._par,
            # Use Repair
            'repair',
        ]

        if self.cpu_cores is not None and self.cpu_cores > 1:
            # to repair concurrently - uses multiple threads
            execute.append('-t+')

        # Stop Switch Parsing
        execute.append('--')

        results = sortedset(key=lambda x: x.key())
        for _path in indexes:

            # Get the directory the par file resides in
            par_path = dirname(_path)

            with pushd(par_path):
                # create a before snapshot
                before_snapshot = self.watch_dir(
                    par_path,
                    seconds=-1,
                )

                # Create our SubProcess Instance
                sp = SubProcess(list(execute) + [basename(_path)])

                # Start our execution now
                sp.start()

                # Track files after
                after_snapshot = sortedset()
                while not sp.is_complete(timeout=1.5):

                    after_snapshot = self.watch_dir(
                        par_path,
                        ignore=after_snapshot,
                    )

                # Handle remaining content
                after_snapshot = self.watch_dir(
                    par_path,
                    ignore=after_snapshot,
                    seconds=-1,
                )

                # Add any new files detected to our result set otherwise we
                # just return an empty set
                total_parts = after_snapshot - before_snapshot
                for no, path in enumerate(total_parts):
                    content = NNTPBinaryContent(
                        path,
                        part=no+1,
                        total_parts=len(total_parts),
                    )
                    # Loaded data is by default detached; we want to attach it
                    content.attach()

                    # Add our attached content to our results
                    results.add(content)

                # Let the caller know our status
                if not sp.successful():
                    return None

        # Clean our are list of objects to archive
        self.clear()

        return results

    def test(self, content=None):
        """
        content must be pointing to a directory containing par files that can
        be easily sorted on. Alternatively, path can be of type NNTPContent()
        or a set/list of.

        This function just tests an archive to see if it can be properly
        prepared (it is effectively a wrapper to verify)

        If anything but True is returned then there was a problem verifying
        the results and a code identified in ParReturnCode() is returned
        instead.
        """
        if content is not None:
            paths = self.get_paths(content)

        elif len(self.archive):
            # Get first item in archive
            paths = iter(self.archive)

        else:
            raise AttributeError("CodecPar: No par file detected.")

        if not self.can_exe(self._par):
            return None

        # filter our results by indexes
        indexes = self.__filter_pars(paths, indexes=True, volumes=False)

        if not len(indexes):
            logger.warning('Archive contained no PAR files.')
            return ParReturnCode.NoParFiles

        # Initialize our command
        execute = [
            # Our Executable PAR Application
            self._par,
            # Use Test Flag
            'verify',
        ]

        if self.cpu_cores is not None and self.cpu_cores > 1:
            # to checksum concurrently - uses multiple threads
            execute.append('-t+')

        # Stop Switch Parsing
        execute.append('--')

        for _path in indexes:

            # Get the directory the par file resides in
            par_path = dirname(_path)

            with pushd(par_path):
                # Create our SubProcess Instance
                sp = SubProcess(list(execute) + [basename(_path)])

                # Start our execution now
                sp.start()
                sp.join()

                # Let the caller know our status
                if sp.response_code() is not ParReturnCode.NoRepairRequired:
                    return sp.response_code()

        return True

    def __filter_pars(self, content, indexes=True, volumes=False):
        """Iterate over passed in content and return a sortedset() containing
        only the items identified to be filtered on.
        """

        # Create a set to store our results in
        results = sortedset()
        for path in self.get_paths(content):
            _re_match = PAR_PART_RE.match(path)
            if _re_match is not None:
                # Now sort indexes from volumes
                if _re_match.group('index') is None and \
                   _re_match.group('count') is None:
                    # We're dealing with an index
                    if indexes is True:
                        results.add(path)

                else:
                    # We're dealing with an volue
                    if volumes is True:
                        results.add(path)

        # Return matches
        return results
