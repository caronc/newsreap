# -*- coding: utf-8 -*-
#
# Test the PAR Codec
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

import sys
if 'threading' in sys.modules:
    #  gevent patching since pytests import
    #  the sys library before we do.
    del sys.modules['threading']

import gevent.monkey
gevent.monkey.patch_all()

import unittest
import os

from blist import sortedset
from os.path import join
from os.path import dirname
from os.path import isdir
from os.path import abspath

try:
    from tests.TestBase import TestBase

except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

from newsreap.codecs.CodecPar import CodecPar
from newsreap.NNTPBinaryContent import NNTPBinaryContent


class CodecPar_Test(TestBase):
    """
    A Unit Testing Class for testing/wrapping the external
    Par tools
    """

    def test_par_detection(self):
        """
        Tests the rar file detection process
        """
        from newsreap.codecs.CodecPar import PAR_PART_RE

        result = PAR_PART_RE.match('/path/to/test.mpg.par2')
        assert result is not None
        assert result.group('index') is None
        assert result.group('count') is None

        result = PAR_PART_RE.match('/path/to/test.mpg.vol00+01.par2')
        assert result is not None
        assert result.group('index') == '00'
        assert result.group('count') == '01'

    @unittest.skipIf(os.environ.get("TRAVIS") == "true",
                     "Skipping this test on Travis CI.")
    def test_par_single_file(self):
        """
        Test that we can par content

        """
        # Generate temporary folder to work with
        work_dir = join(self.tmp_dir, 'CodecPar_Test.par', 'work')

        # Initialize Codec
        cr = CodecPar(work_dir=work_dir)

        # Now we want to prepare a folder filled with temporary content
        source_dir = join(
            self.tmp_dir, 'CodecPar_Test.par.single', 'my_source'
        )
        assert isdir(source_dir) is False

        # create a dummy file
        tmp_file = join(source_dir, 'dummy.rar')
        self.touch(tmp_file, size='1M', random=True)
        # Add our file to the encoding process
        cr.add(tmp_file)

        # Now we want to compress this content
        content = cr.encode()

        # We should have successfully encoded our content
        assert isinstance(content, sortedset)
        assert len(content) == 2
        for c in content:
            assert isinstance(c, NNTPBinaryContent)
            # Content must be attached
            assert c.is_attached() is True

    @unittest.skipIf(os.environ.get("TRAVIS") == "true",
                     "Skipping this test on Travis CI.")
    def test_par_repair(self):
        """
        Test that we can repair a file

        """
        # Generate temporary folder to work with
        work_dir = join(self.tmp_dir, 'CodecPar_Test.par.repair', 'work')

        # Initialize Codec
        cr = CodecPar(work_dir=work_dir)

        # Now we want to prepare a folder filled with temporary content
        source_dir = join(
            self.tmp_dir, 'CodecPar_Test.par.repair', 'my_source'
        )
        assert isdir(source_dir) is False

        # create a dummy file
        tmp_file = join(source_dir, 'dummy.rar')
        self.touch(tmp_file, size='5M', random=True)

        # Add our file to the encoding process
        cr.add(tmp_file)

        # Now we want to compress this content
        content = cr.encode()

        # We should have successfully encoded our content
        assert isinstance(content, sortedset)
        assert len(content) == 2

        # We intentionally move the files into the same directory
        # This isn't done automatically because it's possible to create par2
        # files using files in different directories.  It wouldn't be right
        # to assume we're always working out of the same directory (even
        # though most of the time we would be)
        for c in content:
            c.save(source_dir)
            # Keep our files attached for now
            c.attach()

        # Now we want to damage our file
        with open(tmp_file, 'rb+') as f:
            f.write('garble')

        # Verify our content
        result = cr.test(content)

        # Now we want to fix our data
        result = cr.decode(content)
        assert isinstance(result, sortedset)
