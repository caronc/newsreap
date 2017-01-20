# -*- encoding: utf-8 -*-
#
# Test the NNTPSegmentedPost Object
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

from os.path import isdir
from os.path import dirname
from os.path import abspath
from os.path import join

try:
    from tests.TestBase import TestBase

except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

from newsreap.NNTPSegmentedPost import NNTPSegmentedPost
from newsreap.NNTPArticle import NNTPArticle
from newsreap.Utils import mkdir


class NNTPSegmentedPost_Test(TestBase):
    """
    A Class for testing NNTPSegmentedPost which handles all the XML
    parsing and simple iterations over our XML files.

    """

    @classmethod
    def test_general_features(cls):
        """
        NNTPSegmentedPost manage a list of NNTPArticles

        Test the basic funtionality of the object

        """
        # create an object
        segobj = NNTPSegmentedPost('mytestfile')

        # Not valid because there are no entries
        assert segobj.is_valid() is False
        article = NNTPArticle()

        assert segobj.add(article) is True
        assert len(segobj) == 1

        # Not valid because the entry added is not loaded or retrieved
        assert segobj.is_valid() is False

        # Duplicates are ignored (we can't add the same file twice)
        assert segobj.add(article) is False
        assert len(segobj) == 1

        # We can't add other types
        assert segobj.add(None) is False
        assert segobj.add("bad bad") is False
        assert segobj.add(1) is False
        assert len(segobj) == 1

        # Test iterations
        for a in segobj:
            assert isinstance(a, NNTPArticle)


    def test_split_and_join(self):
        """
        Test the split() and join() functionality of a NNTPSegmentedPost
        """

        tmp_dir = join(
            self.tmp_dir,
            'NNTPSegmentedPost_Test.test_split_and_join',
        )

        assert(isdir(tmp_dir) == False)
        assert(mkdir(tmp_dir))
        assert(isdir(tmp_dir) == True)

        segobj = NNTPSegmentedPost(
            'mytestfile',
            subject='woo-hoo',
            poster='<noreply@newsreap.com>',
            groups='alt.binaries.l2g',
        )

        _files = []
        for i in range(1, 5):
            tmp_file = join(tmp_dir, 'file%.2d.tmp' % i)
            assert self.touch(tmp_file, size='512K', random=True) is True
            segobj.add(tmp_file)
            _files.append(tmp_file)

        assert(segobj.is_valid())
        assert(len(segobj.files()) == len(_files))
        for f in segobj.files():
            assert(f in _files)

