# -*- encoding: utf-8 -*-
#
# A base testing class/library to test the Utils functions
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

import sys
if 'threading' in sys.modules:
    #  gevent patching since pytests import
    #  the sys library before we do.
    del sys.modules['threading']

import gevent.monkey
gevent.monkey.patch_all()

from os.path import abspath
from os.path import dirname
from os.path import isdir
from os.path import join
from os import chmod
from os import getcwd
import errno

import re
from itertools import chain

try:
    from tests.TestBase import TestBase

except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

from newsreap.Utils import strsize_to_bytes
from newsreap.Utils import bytes_to_strsize
from newsreap.Utils import stat
from newsreap.Utils import mkdir
from newsreap.Utils import pushd


class Utils_Test(TestBase):
    """
    Testing the Utils class
    """

    def test_strsize_n_bytes(self):
        """
        A formatting tool to make bytes more readable for an end user
        """
        # Garbage Entry
        assert strsize_to_bytes(None) is None
        assert strsize_to_bytes("0J") is None
        assert strsize_to_bytes("") is None
        assert strsize_to_bytes("totalgarbage") is None

        # Allow integers
        assert strsize_to_bytes(0) == 0
        assert strsize_to_bytes(1024) == 1024

        # Good Entries
        assert strsize_to_bytes("0B") == 0
        assert strsize_to_bytes("0") == 0
        assert strsize_to_bytes("10") == 10
        assert strsize_to_bytes("1K") == 1024
        assert strsize_to_bytes("1M") == 1024*1024
        assert strsize_to_bytes("1G") == 1024*1024*1024
        assert strsize_to_bytes("1T") == 1024*1024*1024*1024

        # Spaces between units and value are fine too
        assert strsize_to_bytes(" 0         B ") == 0
        assert strsize_to_bytes("  1       K  ") == 1024
        assert strsize_to_bytes("   1     M   ") == 1024*1024
        assert strsize_to_bytes("    1   G    ") == 1024*1024*1024
        assert strsize_to_bytes("     1 T     ") == 1024*1024*1024*1024

        # Support Byte character
        assert strsize_to_bytes("1KB") == 1024
        assert strsize_to_bytes("1MB") == 1024*1024
        assert strsize_to_bytes("1GB") == 1024*1024*1024
        assert strsize_to_bytes("1TB") == 1024*1024*1024*1024

        # Support bit character
        assert strsize_to_bytes("1Kb") == 1000
        assert strsize_to_bytes("1Mb") == 1000*1000
        assert strsize_to_bytes("1Gb") == 1000*1000*1000
        assert strsize_to_bytes("1Tb") == 1000*1000*1000*1000

        # Garbage Entry
        assert bytes_to_strsize(None) is None
        assert bytes_to_strsize('') is None
        assert bytes_to_strsize('GARBAGE') is None

        # Good Entries
        assert bytes_to_strsize(0) == "0.00B"
        assert bytes_to_strsize(1) == "1.00B"
        assert bytes_to_strsize(1024) == "1.00KB"
        assert bytes_to_strsize(1024*1024) == "1.00MB"
        assert bytes_to_strsize(1024*1024*1024) == "1.00GB"
        assert bytes_to_strsize(1024*1024*1024*1024) == "1.00TB"

        # Support strings too
        assert bytes_to_strsize("0") == "0.00B"
        assert bytes_to_strsize("1024") == "1.00KB"

    def test_stat(self):
        """
        Stat makes it easier to disect the file extension, filesystem info
        and mime information.
        """

        general_keys = ('extension', 'basename', 'filename', 'dirname')
        filesys_keys = ('created', 'modified', 'accessed', 'size')
        mime_keys = ('mime',)

        # Test a file that doesn't exist
        tmp_file = join(self.tmp_dir, 'Utils_Test.stat', 'missing_file')
        stats = stat(tmp_file)
        assert stats is None
        stats = stat(tmp_file, fsinfo=False)
        assert stats is None
        stats = stat(tmp_file, fsinfo=False, mime=False)
        assert stats is None

        # Create Temporary file 1MB in size
        tmp_file = join(self.tmp_dir, 'Utils_Test.stat', '1MB.rar')
        assert self.touch(tmp_file, size='1MB')

        stats = stat(tmp_file)

        # This check basically makes sure all of the expected keys
        # are in place and that there aren't more or less
        k_iter = chain(mime_keys, filesys_keys, general_keys)
        k_len = len(mime_keys) + len(filesys_keys) + len(general_keys)
        assert isinstance(stats, dict) is True
        assert len([k for k in k_iter if k not in stats.keys()]) == 0
        assert k_len == len(stats)

        # Filesize should actually match what we set it as
        assert bytes_to_strsize(stats['size']) == "1.00MB"
        # different OS's and variations of python can yield different
        # results.  We're trying to just make sure that we find the
        # rar keyword in the mime type
        assert re.search(
            'application/.*rar.*',
            stats['mime'],
            re.IGNORECASE,
        ) is not None

        # Create Temporary file 1MB in size
        tmp_file = join(self.tmp_dir, 'Utils_Test.stat', '2MB.zip')
        assert self.touch(tmp_file, size='2MB')

        stats = stat(tmp_file)

        # This check basically makes sure all of the expected keys
        # are in place and that there aren't more or less
        k_iter = chain(mime_keys, filesys_keys, general_keys)
        k_len = len(mime_keys) + len(filesys_keys) + len(general_keys)
        assert isinstance(stats, dict) is True
        assert len([k for k in k_iter if k not in stats.keys()]) == 0
        assert k_len == len(stats)

        # Filesize should actually match what we set it as
        assert bytes_to_strsize(stats['size']) == "2.00MB"

        assert re.search(
            'application/.*zip.*',
            stats['mime'],
            re.IGNORECASE,
        ) is not None

        # Test different variations
        stats = stat(tmp_file, mime=False)

        # This check basically makes sure all of the expected keys
        # are in place and that there aren't more or less
        k_iter = chain(filesys_keys, general_keys)
        k_len = len(filesys_keys) + len(general_keys)
        assert isinstance(stats, dict) is True
        assert len([k for k in k_iter if k not in stats.keys()]) == 0
        assert k_len == len(stats)

        # Test different variations
        stats = stat(tmp_file, fsinfo=False, mime=True)

        # This check basically makes sure all of the expected keys
        # are in place and that there aren't more or less
        k_iter = chain(mime_keys, general_keys)
        k_len = len(mime_keys) + len(general_keys)
        assert isinstance(stats, dict) is True
        assert len([k for k in k_iter if k not in stats.keys()]) == 0
        assert k_len == len(stats)

        # Test different variations
        stats = stat(tmp_file, fsinfo=False, mime=False)

        # This check basically makes sure all of the expected keys
        # are in place and that there aren't more or less
        k_iter = chain(general_keys)
        k_len = len(general_keys)
        assert isinstance(stats, dict) is True
        assert len([k for k in k_iter if k not in stats.keys()]) == 0
        assert k_len == len(stats)

    def test_mkdir(self):
        """
        Just a simple wrapper to makedirs, but tries a few times before
        completely aborting.

        """

        work_dir = join(self.tmp_dir, 'Utils_Test.mkdir', 'dirA')
        # The directory should not exist
        assert isdir(work_dir) is False

        # mkdir() should be successful
        assert mkdir(work_dir) is True

        # The directory should exist now
        assert isdir(work_dir) is True

        # mkdir() gracefully handles 2 calls to the same location
        assert mkdir(work_dir) is True

        # Create Temporary file 1KB in size
        tmp_file = join(self.tmp_dir, 'Utils_Test.mkdir', '2KB')
        assert self.touch(tmp_file, size='2KB')

        # Now the isdir() will still return False because there is a file
        # there now, not a directory
        assert isdir(tmp_file) is False
        # mkdir() will fail to create a directory in place of file
        assert mkdir(tmp_file) is False

        # And reference a new directory (not created yet) within
        new_work_dir = join(work_dir, 'subdir')

        # Confirm our directory doesn't exist
        assert isdir(new_work_dir) is False

        # Now we'll protect our original directory
        chmod(work_dir, 0000)

        # mkdir() will fail because of permissions, but incase it doesn't work
        # as planned, just store the result in a variable.  We'll flip our
        # permission back first
        result = mkdir(new_work_dir)

        # reset the permission
        chmod(work_dir, 0700)

        # Our result should yield a failed result
        assert result is False

        # Confirm that the directory was never created:
        assert isdir(new_work_dir) is False

    def test_pushd_popd(self):
        """
        Just a simple wrapper to makedirs, but tries a few times before
        completely aborting.

        """

        # Temporary directory to work with
        work_dir = join(self.tmp_dir, 'Utils_Test.pushd', 'newdir')

        # Ensure it doesn't already exist
        assert isdir(work_dir) is False

        # Store our current directory
        cur_dir = getcwd()

        try:
            with pushd(work_dir):
                # We should throw an exeption here and never make it to the assert
                # call below
                assert False

        except OSError, e:
            # Directory doesn't exist
            assert e[0] is errno.ENOENT
            assert getcwd() == cur_dir

        # Now we'll make the directory
        with pushd(work_dir, create_if_missing=True):
            # We're in a new directory
            assert getcwd() == work_dir

        # We're back to where we were
        assert getcwd() == cur_dir

        try:
            with pushd(work_dir, create_if_missing=True):
                # We're in a new directory
                assert getcwd() == work_dir
                # Throw an exception
                raise Exception

        except Exception:
            # We're back to where we were
            assert getcwd() == cur_dir
