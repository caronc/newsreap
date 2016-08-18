# -*- encoding: utf-8 -*-
#
# Test the NNTPContent Object
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

from os.path import join
from os.path import dirname
from os.path import isfile
from os.path import abspath
from os import unlink
from os import urandom
from io import BytesIO

try:
    from tests.TestBase import TestBase

except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

from newsreap.NNTPAsciiContent import NNTPAsciiContent
from newsreap.NNTPBinaryContent import NNTPBinaryContent
from newsreap.NNTPSettings import DEFAULT_BLOCK_SIZE as BLOCK_SIZE


class NNTPContent_Test(TestBase):
    """
    A Class for testing NNTPContent; This is the data found
    within an NNTPArticle.

    One NNTPArticle can effectively contain many NNTPContent
    entries within it.
    """

    def test_general_features(self):
        """
        Detaching makes managing a file no longer managed by this
        NNTPContent. Test that this works

        """
        # No parameters should create a file
        aa = NNTPAsciiContent()
        ba = NNTPBinaryContent()

        # open a temporary file
        aa.open()
        ba.open()

        # Test Files
        aa_filepath = aa.filepath
        ba_filepath = ba.filepath
        assert isfile(aa_filepath) is True
        assert isfile(ba_filepath) is True

        # Test Length
        assert len(aa) == 0
        assert len(ba) == 0

        # Test that files are destroyed if the object is
        del aa
        del ba

        # Files are destroyed
        assert isfile(aa_filepath) is False
        assert isfile(ba_filepath) is False

        # Test some parameters out during initialization
        aa = NNTPAsciiContent(
            filename="ascii.file",
            part=2,
            tmp_dir=self.tmp_dir,
        )

        ba = NNTPBinaryContent(
            filename="binary.file",
            part="10",
            tmp_dir=self.tmp_dir,
        )

        # Check our parts
        assert aa.part == 2

        # Strings are converted okay
        assert ba.part == 10

        # open a temporary file
        aa.open()
        ba.open()

        # files don't exist yet
        assert isfile(join(self.tmp_dir, "binary.file")) is False
        assert isfile(join(self.tmp_dir, "ascii.file")) is False

        aa_filepath = aa.filepath
        ba_filepath = ba.filepath

        # Save our content
        aa.save()
        ba.save()

        # check that it was created okay
        assert isfile(join(self.tmp_dir, "binary.file")) is True
        assert isfile(join(self.tmp_dir, "ascii.file")) is True

        # Temporary files are gone (moved from the save() command above)
        assert isfile(aa_filepath) is False
        assert isfile(ba_filepath) is False

        # They were never the same after the save()
        assert aa_filepath != aa.filepath
        assert ba_filepath != ba.filepath

        # However after save is called; the filepath is updated to reflect
        # the proper path; so this is still true
        assert isfile(aa.filepath) is True
        assert isfile(ba.filepath) is True

        # Even after the objects are gone
        del aa
        del ba

        # Files still exist even after the objects displayed
        assert isfile(join(self.tmp_dir, "binary.file")) is True
        assert isfile(join(self.tmp_dir, "ascii.file")) is True

        # Cleanup
        unlink(join(self.tmp_dir, "ascii.file"))
        unlink(join(self.tmp_dir, "binary.file"))


    def test_ascii_article_iterations(self):
        """
        Ascii Content can be loaded straight from file and can be processed
        in a for loop.
        """

        # Content
        aa = NNTPAsciiContent()

        assert aa.load('unknown_file') is False

        temp_file = join(self.tmp_dir,'NNTPContent_Test-test_iterations.tmp')

        with open(temp_file, 'wb') as fd:
            fd.write('Line 1\n')
            fd.write('Line 2\n')
        assert isfile(temp_file) == True

        assert aa.load(temp_file) is True

        # Loaded files area always valid
        assert aa.is_valid() is True

        # Ascii Content read line by line
        lineno = 1
        for line in aa:
            assert line == 'Line %d\n' % (lineno)
            lineno += 1

        # Remove article
        del aa
        # Files are not attached by default so our temp file
        # should still exist
        assert isfile(temp_file) == True

        # however if we attach it
        aa = NNTPAsciiContent()
        assert aa.load(temp_file, detached=False) is True
        # our file still exists of course
        assert isfile(temp_file) == True
        del aa
        # but now it doesn't
        assert isfile(temp_file) == False

    def test_binary_article_iterations(self):
        """
        Binary Content can be loaded straight from file and can be processed
        in a for loop.
        """

        # Create a BytesIO Object
        bobj = BytesIO()

        # Fill our BytesIO object with random junk at least
        # 4x our expected block size
        for _ in range(4):
            bobj.write(urandom(BLOCK_SIZE))

        # Write just '1' more bytes so we ``overflow`` and require
        # a 5th query later
        bobj.write('0')

        # Content
        ba = NNTPBinaryContent()

        # No items means not valid
        assert ba.is_valid() is False

        assert ba.load('unknown_file') is False

        # a failed load means not valid
        assert ba.is_valid() is False

        temp_file = join(self.tmp_dir, 'NNTPContent_Test-test_iterations.tmp')

        with open(temp_file, 'wb') as fd:
            fd.write(bobj.getvalue())

        assert isfile(temp_file) == True

        assert ba.load(temp_file) is True

        # Binary Content read by chunk size
        chunk = 4
        for line in ba:
            if chunk > 0:
                assert len(line) == BLOCK_SIZE
            else:
                # 5th query
                assert len(line) == 1
            chunk -= 1

        # We should have performed 5 chunk requests and
        # -1 more since we decrement the chunk one last time
        # before we're done
        assert chunk == -1

        # Confirm our size is reading correctly too
        assert len(ba) == (BLOCK_SIZE*4)+1

        # Remove article

        del ba
        # Files are not attached by default so our temp file
        # should still exist
        assert isfile(temp_file) == True

        # however if we attach it
        ba = NNTPAsciiContent()
        assert ba.load(temp_file, detached=False) is True
        # our file still exists of course
        assert isfile(temp_file) == True
        del ba
        # but now it doesn't
        assert isfile(temp_file) == False
