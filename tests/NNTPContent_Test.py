# -*- encoding: utf-8 -*-
#
# Test the NNTPContent Object
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

from blist import sortedset
from os.path import join
from os.path import isdir
from os.path import exists
from os.path import dirname
from os.path import isfile
from os.path import abspath
from os import unlink
from os import urandom
from io import BytesIO

from filecmp import cmp as compare

try:
    from tests.TestBase import TestBase

except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

from newsreap.NNTPAsciiContent import NNTPAsciiContent
from newsreap.NNTPBinaryContent import NNTPBinaryContent
from newsreap.NNTPContent import NNTPContent
from newsreap.NNTPSettings import DEFAULT_BLOCK_SIZE as BLOCK_SIZE
from newsreap.Utils import strsize_to_bytes
from newsreap.Utils import bytes_to_strsize
from newsreap.Utils import mkdir
from newsreap.Utils import stat


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
            filepath="ascii.file",
            part=2,
            work_dir=self.tmp_dir,
        )

        ba = NNTPBinaryContent(
            filepath="binary.file",
            part="10",
            work_dir=self.tmp_dir,
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

        # Grab a copy of these file paths so we can check them later
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

        # Successfully loaded files area always valid
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

        # We'll create another object
        aa = NNTPAsciiContent()
        assert aa.load(temp_file) is True
        # Successfully loaded files are never attached
        assert aa.is_attached() is False
        # our file still exists of course
        assert isfile(temp_file) == True
        del aa
        assert isfile(temp_file) == True

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

        # We'll create another object
        ba = NNTPAsciiContent()
        assert ba.load(temp_file) is True
        # Successfully loaded files are never attached
        assert ba.is_attached() is False
        # our file still exists of course
        assert isfile(temp_file) == True
        # we'll detach it
        ba.detach()
        # Still all is good
        assert isfile(temp_file) == True
        # Check that we're no longer attached
        assert ba.is_attached() is False
        # Now, once we delete our object, the file will be gone for good
        del ba
        # it's gone for good
        assert isfile(temp_file) == True

    def test_invalid_split_cases(self):
        """
        Test errors that are generated out of the split function
        """
        work_dir = join(self.tmp_dir, 'NNTPContent_Test.chunk')
        # Now we want to load it into a NNTPContent object
        content = NNTPContent(work_dir=work_dir)

        # Nothing to split gives an error
        assert content.split() is None

        tmp_file = join(self.tmp_dir, 'NNTPContent_Test.chunk', '5K.rar')
        assert not isfile(tmp_file)
        assert self.touch(tmp_file, size='1MB')
        assert isfile(tmp_file)

        # Now we want to load it into a NNTPContent object
        content = NNTPContent(filepath=tmp_file, work_dir=self.tmp_dir)

        # No size to split on gives an error
        assert content.split(size=0) is None
        assert content.split(size=-1) is None
        assert content.split(size=None) is None
        assert content.split(size='bad_string') is None

        # Invalid Memory Limit
        assert content.split(mem_buf=0) is None
        assert content.split(mem_buf=-1) is None
        assert content.split(mem_buf=None) is None
        assert content.split(mem_buf='bad_string') is None

    def test_split(self):
        """
        Test the split() function
        """
        # First we create a 1MB file
        tmp_file = join(self.tmp_dir, 'NNTPContent_Test.chunk', '1MB.rar')
        # File should not already exist
        assert isfile(tmp_file) is False
        # Create our file
        assert self.touch(tmp_file, size='1MB')
        # File should exist now
        assert isfile(tmp_file) is True

        # Now we want to load it into a NNTPContent object
        content = NNTPContent(filepath=tmp_file, work_dir=self.tmp_dir)

        # Loaded files are always detached; The following is loaded
        # because the path exists.
        assert content.is_attached() is False

        # We'll split it in 2
        results = content.split(strsize_to_bytes('512K'))

        # Tests that our results are expected
        assert isinstance(results, sortedset)
        assert len(results) == 2

        # We support passing the string format directly in too
        results = content.split('512K')
        # Tests that our results are expected
        assert isinstance(results, sortedset)
        assert len(results) == 2

        # Now lets merge them into one again
        content = NNTPContent(work_dir=self.tmp_dir)
        assert content.load(results) is True

        # NNTPContent() sets as well as individual objects passed into
        # load are always attached by default
        assert content.is_attached() is True

        # Our combined file should be the correct filesize
        assert len(content) == strsize_to_bytes('1M')

        # Once we save our object, it is no longer attached
        assert content.save(filepath=tmp_file) is True

        # Now our content is no longer attached
        assert content.is_attached() is False

        # we'll re-attach it
        content.attach()

        # Our file will be gone now if we try to delete it
        assert content.is_attached() is True

        assert isfile(tmp_file) is True
        del content
        assert isfile(tmp_file) is False

    def test_checksum(self):
        """Test the assorted checksums supported
        """

        # First we create a 1MB file
        tmp_file = join(
            self.tmp_dir, 'NNTPContent_Test.checksum', 'tmpa.tmp')
        # File should not already exist
        assert isfile(tmp_file) is False
        # Create a random file
        assert self.touch(tmp_file, size='1MB', random=True) is True
        # File should exist now
        assert isfile(tmp_file) is True

        # Now we want to load it into a NNTPContent object
        content = NNTPContent(filepath=tmp_file, work_dir=self.tmp_dir)

        md5 = content.md5()
        sha1 = content.sha1()
        sha256 = content.sha256()

        assert md5 is not None
        assert sha1 is not None
        assert sha256 is not None

        tmp_file_2 = join(
            self.tmp_dir, 'NNTPContent_Test.checksum', 'tmp2.rar')
        # File should not already exist
        assert isfile(tmp_file_2) is False
        # We'll create a copy of our file
        assert content.save(filepath=tmp_file_2, copy=True) is True
        # Now it should
        assert isfile(tmp_file_2) is True
        # Now we'll open the new file we created
        content_2 = NNTPContent(filepath=tmp_file_2, work_dir=self.tmp_dir)

        md5_2 = content_2.md5()
        sha1_2 = content_2.sha1()
        sha256_2 = content_2.sha256()

        assert md5_2 is not None
        assert sha1_2 is not None
        assert sha256_2 is not None

        # files should be the same
        assert md5 == md5_2
        assert sha1 == sha1_2
        assert sha256 == sha256_2

    def test_saves(self):
        """
        Saving allows for a variety of inputs, test that they all
        check out okay
        """
        # First we create a 1MB file
        tmp_file = join(
            self.tmp_dir, 'NNTPContent_Test.save', 'testfile.tmp')
        # File should not already exist
        assert isfile(tmp_file) is False
        # Create a random file
        assert self.touch(tmp_file, size='5MB', random=True) is True
        # File should exist now
        assert isfile(tmp_file) is True
        # Now we want to load it into a NNTPContent object
        content = NNTPContent(filepath=tmp_file, work_dir=self.tmp_dir)
        # Test our file exists
        assert len(content) == strsize_to_bytes('5M')
        # By default our load makes it so our file is NOT attached
        assert content.is_attached() is False
        # as we can't make a copy of our current file on top of the old
        _filepath = content.path()
        assert content.save(copy=True) is True
        assert content.path() == _filepath
        assert content.path() == tmp_file

        # File should still be detached
        assert content.is_attached() is False
        # Filepath shoul still not have changed
        # Let's attach it
        content.attach()
        assert content.is_attached() is True

        # If we call save() a copy parameter, there should be no change
        assert content.save(copy=True) is True
        # Still no change
        assert content.is_attached() is True

        # Now lets actually copy it to a new location
        tmp_file_copy = join(
            self.tmp_dir, 'NNTPContent_Test.save', 'testfile.copy.tmp')
        # File should not already exist
        assert isfile(tmp_file_copy) is False
        # call save using our copy variable and new filename
        assert content.save(tmp_file_copy, copy=True) is True
        # File should exist now
        assert isfile(tmp_file_copy) is True
        # Old File should still exist too
        assert isfile(tmp_file) is True
        # Path should still be the old path and not the new
        assert content.path() == tmp_file
        # Still no change in attachment
        assert content.is_attached() is True

        # Create a new file now
        tmp_file_copy2 = join(
            self.tmp_dir, 'NNTPContent_Test.save', 'testfile.copy2.tmp')
        assert isfile(tmp_file_copy2) is False
        # call save with copy set to false; This performs an official
        # move (adjusting our internal records.
        assert content.save(tmp_file_copy2, copy=False) is True
        # Old File should no longer exist
        assert isfile(tmp_file) is False
        # Content should be detached
        assert content.is_attached() is False
        # New file should exist
        assert isfile(tmp_file_copy2) is True
        assert content.path() != _filepath
        assert content.path() == tmp_file_copy2

    def test_writes(self):
        """
        More overhead then a normal write() but none the less, using the
        write() in this class keeps things simple since the file is
        automatically opened if it was otherwise closed
        """

        # First we create a 1MB file
        tmp_file = join(self.tmp_dir, 'NNTPContent_Test.write', 'tmp.file')
        # File should not already exist
        assert isfile(tmp_file) is False

        # Now we want to create our NNTPContent() object surrouding this
        # file that does not exist.
        content = NNTPContent(filepath=tmp_file, work_dir=dirname(tmp_file))
        # It's worth noting that this file will 'still' not exist
        assert isfile(tmp_file) is False
        # we'll write data

        data = 'hello\r\n'
        content.write(data)

        # It's worth noting that this file will ''STILL'' not exist
        assert isfile(tmp_file) is False

        # Save content
        assert content.save() is True

        # Now the file 'will' exist
        assert isfile(tmp_file) is True

        # Open our file and verify it is the data we saved.
        with open(tmp_file) as f:
            data_read = f.read()
        assert data == data_read


    def test_directory_support(self):
        """
        NNTPContent objects can wrap directories too

        """
        my_dir = join(self.tmp_dir, 'NNTPContent', 'my_dir')
        assert isdir(my_dir) is False
        assert mkdir(my_dir) is True
        assert isdir(my_dir) is True

        #  Now create our NNTPContent Object against our directory
        obj = NNTPContent(
            filepath=my_dir,
            work_dir=self.tmp_dir,
        )

        # Directories are never attached by default
        assert obj.is_attached() is False

        # Deleting the object means the directory remains behind
        del obj
        assert isdir(my_dir) is True

        # However
        obj = NNTPContent(
            filepath=my_dir,
            work_dir=self.tmp_dir,
        )
        assert obj.is_attached() is False
        obj.attach()
        assert obj.is_attached() is True

        # Now we've attached the NNTPContent to the object so deleting the
        # object destroys the directory
        del obj
        assert exists(my_dir) is False
        assert isdir(my_dir) is False

    def test_copy(self):
        """
        The copy function allows us to duplicate an existing NNTPContent
        object without obstructing the original.  Copied content is
        always attached; so if the object falls out of scope; so does
        the file.
        """
        my_dir = join(self.tmp_dir, 'NNTPContent', 'test_copy')
        assert isdir(my_dir) is False
        assert mkdir(my_dir) is True
        assert isdir(my_dir) is True

        #  Now create our NNTPContent object witin our directory
        obj = NNTPContent(
            filepath='myfile',
            work_dir=my_dir,
        )

        # Content is attached by default
        assert obj.is_attached() is True
        obj.detach()
        assert obj.is_attached() is False

        new_dir = join(my_dir, 'copy')
        assert isdir(new_dir) is False

        # Create a copy of our object
        obj_copy = obj.copy()

        # Successfully loaded files are never attached reguardless
        # of the original copy
        assert obj_copy.is_attached() is True

        # Reattach the original so it dies when this test is over
        obj.attach()
        assert obj.is_attached() is True

        # Create a copy of our copy
        obj_copy2 = obj_copy.copy()
        assert obj_copy2.is_attached() is True
        assert isfile(obj_copy2.path())
        _path = obj_copy2.path()
        del obj_copy2
        assert not isfile(_path)

        assert isfile(obj_copy.path())
        _path = obj_copy.path()
        del obj_copy
        assert not isfile(_path)

        assert isfile(obj.path())
        _path = obj.path()
        del obj
        assert not isfile(_path)

        # now lets do a few more tests but with actual files this time
        tmp_file = join(my_dir, '2MB.zip')
        assert self.touch(tmp_file, size='2MB')

        #  Now create our NNTPContent object witin our directory
        obj = NNTPContent(
            filepath=tmp_file,
            work_dir=my_dir,
        )
        assert isfile(obj.path())

        obj_copy = obj.copy()
        assert isfile(obj_copy.path())
        stats = stat(obj_copy.path())
        assert bytes_to_strsize(stats['size']) == "2.00MB"

        # Compare that our content is the same
        assert compare(obj_copy.path(), obj.path())

        # note that the filenames are NOT the same so we are dealing with 2
        # distinct copies here
        assert isfile(obj_copy.path())
        assert isfile(obj.path())
        assert obj.path() != obj_copy.path()
