# -*- coding: utf-8 -*-
#
# Test the NNTPSegmentedPost Object
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

        assert(segobj.add(article) is True)
        assert(len(segobj) == 1)

        # Not valid because the entry added is not loaded or retrieved
        assert(segobj.is_valid() is False)

        # Duplicates are ignored (we can't add the same file twice)
        assert(segobj.add(article) is False)
        assert(len(segobj) == 1)

        # We can't add other types
        assert(segobj.add(None) is False)
        assert(segobj.add("bad bad") is False)
        assert(segobj.add(1) is False)
        assert(len(segobj) == 1)

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

        assert(isdir(tmp_dir) is False)
        assert(mkdir(tmp_dir) is True)
        assert(isdir(tmp_dir) is True)

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

        assert(segobj.is_valid() is True)
        assert(len(segobj.files()) == len(_files))
        for f in segobj.files():
            assert(f in _files)

    def test_templating(self):
        """
        Test templating
        """

        tmp_dir = join(
            self.tmp_dir,
            'NNTPSegmentedPost_Test.test_templating',
        )

        assert(isdir(tmp_dir) is False)
        assert(mkdir(tmp_dir) is True)
        assert(isdir(tmp_dir) is True)

        # our templated subject line
        subject = '"My Test {{custom}} %Y%d%m" - {{garbageitem}}' \
            '"{{filename}}" yEnc ({{index}}/{{count}}){{garbage}}'

        segobj = NNTPSegmentedPost(
            'file.rar',
            subject=subject,
            poster='<noreply@{{domain}}>',
            groups='alt.binaries.l2g',
        )

        for i in range(5):
            tmp_file = join(tmp_dir, 'file.r%.2d' % i)
            assert self.touch(tmp_file, size='1K', random=True) is True
            segobj.add(tmp_file)

        # Test our templating with our custom field
        assert(segobj.apply_template({
            '{{custom}}': '-newsreap',
            '{{domain}}': 'newsreap.com',
            }) is True)

        for no, article in enumerate(segobj):
            # eg "My Test -newsreap 20172210" - "file.r00" yEnc (0/5)
            subject_re = re.match(
                r'^"My Test \-newsreap [0-9]{8}" - '
                r'"(?P<fname>file\.r[0-9]{2})" yEnc '
                r'\((?P<index>[0-9])/(?P<count>[0-9])\)$',
                article.subject,
            )

            poster_re = re.match(
                re.escape('<noreply@newsreap.com>'),
                article.poster,
            )

            # We should have translated our content properly
            assert(subject_re is not None)
            assert(poster_re is not None)

            # Check that we translated content okay
            assert(subject_re.group('count') == str(len(segobj)))
            assert(subject_re.group('index') == str(no+1))
            assert(subject_re.group('fname') == article[0].filename)

    def test_deobsfucation(self):
        """
        Test deobsfucation
        """

        tmp_dir = join(
            self.tmp_dir,
            'NNTPSegmentedPost_Test.test_deobsfucation',
        )

        assert(isdir(tmp_dir) is False)
        assert(mkdir(tmp_dir) is True)
        assert(isdir(tmp_dir) is True)

        subject = '"My Test Obsfucation" - ' \
            '"aj3jabk.af" yEnc ({{index}}/{{count}}) {{totalsize}}'

        segobj = NNTPSegmentedPost(
            'file.rar',
            subject=subject,
            poster='<noreply@newsreap.com>',
            groups='alt.binaries.l2g',
        )

        # We'll build a segmented post
        for i in range(5):
            tmp_file = join(tmp_dir, 'file.r%.2d' % i)
            assert self.touch(tmp_file, size='1K', random=True) is True
            segobj.add(tmp_file)

        # Apply templating
        assert(segobj.apply_template() is True)

        segobj.deobsfucate()
