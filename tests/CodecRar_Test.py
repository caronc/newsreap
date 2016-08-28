# -*- encoding: utf-8 -*-
#
# Test the RAR Codec
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

from blist import sortedset
from os.path import join
from os.path import dirname
from os.path import isfile
from os.path import isdir
from os.path import abspath

try:
    from tests.TestBase import TestBase

except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

from newsreap.codecs.CodecRar import CodecRar
from newsreap.NNTPBinaryContent import NNTPBinaryContent
from newsreap.NNTPContent import NNTPContent
from newsreap.NNTPArticle import NNTPArticle


class CodecRar_Test(TestBase):
    """
    A Unit Testing Class for testing/wrapping the external
    Rar/Unrar tools
    """
    def test_rar_errors(self):
        """
        Test that we fail under certain conditions

        """
        # Generate temporary folder to work with
        work_dir = join(self.tmp_dir, 'CodecRar_Test.rar.fail', 'work')

        # Now we want to prepare a folder filled with temporary content
        # Note: this directory is horrible because it's 'within' our work_dir
        # as a result, adding content should not succeed
        source_dir = join(work_dir, 'test')

        # Initialize Codec (without volume_size disables it)
        cr = CodecRar(work_dir=work_dir)

        # No files
        assert len(cr) == 0

        tmp_file = join(source_dir, 'temp_file_non-existant')
        assert isfile(tmp_file) is False
        # We can't add content that does not exist
        assert cr.add(tmp_file) is False

        # Still No files
        assert len(cr) == 0

        # However directories can not cross into our work directory
        tmp_dir = dirname(work_dir)

        # We intentionally pick a directory that has the work_dir
        # as a child within it
        assert isdir(tmp_dir)

        # Denied adding the file because it would include the work_dir
        # if we did
        assert cr.add(tmp_file) is False

        # Temporary file (within directory denied in previous check)
        tmp_file = join(tmp_dir, 'temp_file')
        assert isfile(tmp_file) is False

        # Create our temporary file now
        self.touch(tmp_file, size='120K', random=True)
        assert isfile(tmp_file) is True

        # This file is within our work_dir but we're still okay because
        # it's accessing the file explicitly and the fact it's a file
        # and not a directory
        assert cr.add(tmp_file) is True

        # Now we'll have 1 entry in our list
        assert len(cr) == 1

        # You can't add duplicates
        assert cr.add(tmp_file) is False

        # We still have 1 entry
        assert len(cr) == 1

        # Empty NNTPContent() can not be added
        content = NNTPContent(unique=True, work_dir=self.tmp_dir)

        # Can't do it
        assert cr.add(content) is False

        # Store some data
        content.write('some data\r\n')

        # Now we can add it because it has data in it
        assert cr.add(content) is True

        # We now have 2 entries
        assert len(cr) == 2

        # We can't add duplicates
        assert cr.add(content) is False

        # We still have 2 entries
        assert len(cr) == 2

        # Empty NNTPArticle() can not be added
        article = NNTPArticle(work_dir=self.tmp_dir)

        # Can't do it
        assert cr.add(article) is False

        # If we add content that's already been added, nothing
        # new will happen either
        assert article.add(content) is True

        # Still can't do (only because it was already added)
        assert cr.add(article) is False

        # We still have 2 entries
        assert len(cr) == 2

        # New Empty NNTPContent() can not be added
        content = NNTPContent(unique=True, work_dir=self.tmp_dir)

        # We'll add our new content to our article
        assert article.add(content) is True

        # Our new content has no data associated with it, so this should
        # still fail
        assert cr.add(article) is False

        # We still have 2 entries
        assert len(cr) == 2

        # Store some new data
        content.write('some new data\r\n')

        # Our new content within our article now has data so this will work
        assert cr.add(article) is True

        # We now have 3 entries
        assert len(cr) == 3

    def test_rar_single_file(self):
        """
        Test that we can rar content

        """
        # Generate temporary folder to work with
        work_dir = join(self.tmp_dir, 'CodecRar_Test.rar', 'work')

        # Initialize Codec
        cr = CodecRar(work_dir=work_dir)

        # Now we want to prepare a folder filled with temporary content
        source_dir = join(
            self.tmp_dir, 'CodecRar_Test.rar.single', 'my_source'
        )
        assert isdir(source_dir) is False

        # create some dummy file entries
        for i in range(0, 10):
            # Create some temporary files to work with in our source
            # directory
            tmp_file = join(source_dir, 'DSC_IMG%.3d.jpeg' % i)
            self.touch(tmp_file, size='120K', random=True)
            # Add our file to the encoding process
            cr.add(tmp_file)

        # Now we want to compress this content
        content = cr.encode()

        # We should have successfully encoded our content
        assert isinstance(content, sortedset)
        assert len(content) == 1
        assert isinstance(content[0], NNTPBinaryContent)

    def test_rar_multi_files(self):
        """
        Test that we can rar content into multiple files

        """
        # Generate temporary folder to work with
        work_dir = join(self.tmp_dir, 'CodecRar_Test.rar.multi', 'work')

        # Initialize Codec
        cr = CodecRar(work_dir=work_dir, volume_size='100K')

        # Now we want to prepare a folder filled with temporary content
        source_dir = join(self.tmp_dir, 'CodecRar_Test.rar', 'my_source')
        assert isdir(source_dir) is False

        # create some dummy file entries
        for i in range(0, 10):
            # Create some temporary files to work with in our source
            # directory
            tmp_file = join(source_dir, 'DSC_IMG%.3d.jpeg' % i)
            self.touch(tmp_file, size='100K', random=True)
            # Add our file to the encoding process
            cr.add(tmp_file)

        # Now we want to compress this content
        content = cr.encode()

        # We should have successfully encoded our content
        assert isinstance(content, sortedset)
        assert len(content) == 11
        for c in content:
            assert isinstance(c, NNTPBinaryContent)
