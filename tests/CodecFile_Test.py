# -*- coding: utf-8 -*-
#
# Test the Base Class File Codec
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

from newsreap.codecs.CodecFile import CodecFile
from newsreap.NNTPBinaryContent import NNTPBinaryContent
from newsreap.NNTPContent import NNTPContent
from newsreap.NNTPArticle import NNTPArticle
from newsreap.Utils import mkdir


class CodecFile_Test(TestBase):
    """
    A Unit Testing Class for testing/wrapping the external
    Rar/Unrar tools
    """
    def test_get_paths(self):
        """
        Test that we fail under certain conditions

        """
        # Generate temporary folder to work with
        work_dir = join(self.tmp_dir, 'CodecFile_Test', 'work')

        # Initialize Codec (without volume_size disables it)
        cr = CodecFile(work_dir=work_dir)

        # create some dummy file entries
        tmp_files = set()
        for i in range(0, 10):
            # Create some temporary files to work with in our source
            # directory
            tmp_file = join(work_dir, 'DSC_IMG%.3d.jpeg' % i)
            self.touch(tmp_file, size='120K', random=True)

            # Add a file to our tmp_files list
            tmp_files.add(tmp_file)

        # Non-existant file reference
        invalid_file = join(self.tmp_dir, 'non-existant-file')
        assert isfile(invalid_file) is False

        content = NNTPContent(
            join(work_dir, 'testfile'),
            work_dir=self.tmp_dir,
        )
        content.write('test data')

        # Empty NNTPArticle() can not be added
        article = NNTPArticle(work_dir=self.tmp_dir)

        # New Empty NNTPContent() can not be added
        article_content = NNTPContent(
            join(work_dir, 'testfile2'),
            work_dir=self.tmp_dir,
        )
        # Store some new data
        article_content.write('some more test data')

        # We'll add our new content to our article
        assert article.add(content) is True

        # save path
        sub_dir = join(work_dir, 'subdir')
        assert mkdir(sub_dir) is True
        assert isdir(sub_dir) is True

        # string work
        assert len(cr.get_paths(self.tmp_dir)) == 1
        assert cr.get_paths(self.tmp_dir).pop() == self.tmp_dir

        # Sub-directories that exist within a root directory also included are
        # removed
        assert len(cr.get_paths([self.tmp_dir, sub_dir])) == 1
        assert cr.get_paths([self.tmp_dir, sub_dir]).pop() == self.tmp_dir

        # Invalid files/dirs are not found
        assert len(cr.get_paths(invalid_file)) == 0

        # Create a list of many assorted type of items
        __set = set([
            work_dir,
            sub_dir,
            article_content,
            content,
            invalid_file,
        ]) | set(tmp_files)

        # At the end of the day, the work_dir includes all of the sub-content
        # and the invalid_file is simply just tossed. However because our
        # NNTPContent() and NNTPArticle() files are stored outside of our
        # work_dir, they will also be included in the results
        results = cr.get_paths(__set)
        assert len(results) == 3
        assert work_dir in results
        assert content.filepath in results
        assert article_content.filepath in results

        # Now if we did the same test but without the work_dir directory, then
        # we'd have a much larger list; we'll work with lists this time to
        # show that we support them too
        __list = [
            sub_dir,
            article_content,
            content,
            invalid_file,
        ]
        __list.extend(tmp_files)

        results = cr.get_paths(__list)
        # +1 for content
        # +1 for sub_dir
        assert len(results) == (len(tmp_files) + len(article) + 2)
        for f in tmp_files:
            # Each file in our tmp_files will be in our results
            assert f in results
        assert content.filepath in results
        assert article_content.filepath in results
        assert sub_dir in results
