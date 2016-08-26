# -*- encoding: utf-8 -*-
#
# Test the NNTP Groups Codec
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

try:
    from tests.TestBase import TestBase
except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

from newsreap.codecs.CodecGroups import CodecGroups
from newsreap.NNTPMetaContent  import NNTPMetaContent

class CodecGroups_Test(TestBase):

    def test_bad_groups(self):
        """
        Make sure we fail on bad groups
        """
        # Initialize Codec
        ch = CodecGroups()
        assert ch.detect("alt.binaries.l2g") is None
        assert ch.detect("alt.binaries.l2g 0 bad y") is None
        assert ch.detect("alt.binaries.l2g bad 0 y") is None
        assert ch.detect("alt.binaries.l2g bad 0 y also bad") is None
        assert ch.detect("alt.binaries.l2g character bad y") is None
        assert ch.detect("alt.binaries.l2g -1 0 y") is None
        assert ch.detect("alt.binaries.l2g 0 -1 y") is None

        # Empty lines are not valid
        assert ch.detect("") is None
        # white space
        assert ch.detect("    ") is None


    def test_good_groups(self):
        """
        Make sure we are successful with good groups
        """

        # Initialize Codec
        ch = CodecGroups()

        # A normal entry might look like this
        assert ch.detect('alt.binaries.l2g 0 0 y') == {
            'group': 'alt.binaries.l2g',
            'low': 0,
            'high': 0,
            'count': 0,
            'flags': ['y', ],
        }

        # A normal entry might look like this
        assert ch.detect('alt.binaries.l2g 0 0 y') == {
            'group': 'alt.binaries.l2g',
            'low': 0,
            'high': 0,
            'count': 0,
            'flags': ['y', ],
        }

        # some spaces are allowed
        assert ch.detect('     alt.binaries.l2g   0     0     y') == {
            'group': 'alt.binaries.l2g',
            'low': 0,
            'high': 0,
            'count': 0,
            'flags': ['y', ],
        }

        # Populate the high and low watermarks
        assert ch.detect('alt.binaries.l2g 400 12 y') == {
            'group': 'alt.binaries.l2g',
            'low': 400,
            'high': 12,
            'count': 388,
            'flags': ['y', ],
        }


        # No flag is acceptable
        assert ch.detect('alt.binaries.l2g 400 40') == {
            'group': 'alt.binaries.l2g',
            'low': 400,
            'high': 40,
            'count': 360,
            'flags': [],
        }


        # Multiple supported flags
        assert ch.detect('alt.binaries.l2g 400 40 ym') == {
            'group': 'alt.binaries.l2g',
            'low': 400,
            'high': 40,
            'count': 360,
            'flags': ['y', 'm', ],
        }

        # Multiple unsupported flags are still kept too
        assert ch.detect('alt.binaries.l2g 400 40 ymabcd') == {
            'group': 'alt.binaries.l2g',
            'low': 400,
            'high': 40,
            'count': 360,
            'flags': ['y', 'm', 'a', 'b', 'c', 'd' ],
        }


    def test_empty_groups(self):
        """
        If the group is empty, one of the following three situations will
        occur.  Clients MUST accept all three cases; servers MUST NOT
        represent an empty group in any other way.

        o  The high water mark will be one less than the low water mark, and
           the estimated article count will be zero.  Servers SHOULD use this
           method to show an empty group.  This is the only time that the
           high water mark can be less than the low water mark.

        o  All three numbers will be zero.

        o  The high water mark is greater than or equal to the low water
           mark.  The estimated article count might be zero or non-zero; if
           it is non-zero, the same requirements apply as for a non-empty
           group.
        """

        # Initialize Codec
        ch = CodecGroups()

        # low and high are both 0
        assert ch.detect('alt.binaries.l2g 0 0 y') == {
            'group': 'alt.binaries.l2g',
            'low': 0,
            'high': 0,
            'count': 0,
            'flags': ['y', ],
        }

        # high is higher than low
        assert ch.detect('alt.binaries.l2g 499 500 y') == {
            'group': 'alt.binaries.l2g',
            'low': 499,
            'high': 500,
            'count': 0,
            'flags': ['y', ],
        }

        # high is 1 less than low
        assert ch.detect('alt.binaries.l2g 500 499 y') == {
            'group': 'alt.binaries.l2g',
            'low': 500,
            'high': 499,
            'count': 0,
            'flags': ['y', ],
        }


    def test_decoding_01(self):
        """
        Open a stream to a file we can read for decoding; This test
        specifically focuses on var/group.list
        """

        # Initialize Codec
        ch_py = CodecGroups()

        encoded_filepath = join(self.var_dir, 'group.list')
        assert isfile(encoded_filepath)

        # Read data and decode it
        with open(encoded_filepath, 'r') as fd_in:
            # This module always returns 'True' expecting more
            # but content can be retrieved at any time
            assert ch_py.decode(fd_in) is True

        # This is where the value is stored
        assert isinstance(ch_py.decoded, NNTPMetaContent)
        assert isinstance(ch_py.decoded.content, list)

        # The number of lines in group.list parsed should all be valid
        assert len(ch_py.decoded.content) == ch_py._total_lines

        # Test our reset
        ch_py.reset()

        assert isinstance(ch_py.decoded, NNTPMetaContent)
        assert isinstance(ch_py.decoded.content, list)
        assert len(ch_py.decoded.content) == 0
        assert len(ch_py.decoded.content) == ch_py._total_lines
