# -*- coding: utf-8 -*-
#
# A testing class/library for the NNTPGroup Object
#
# Copyright (C) 2017 Chris Caron <lead2gold@gmail.com>
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
# Import threading after monkey patching
# see: http://stackoverflow.com/questions/8774958/\
#        keyerror-in-module-threading-after-a-successful-py-test-run

from os.path import dirname
from os.path import abspath

try:
    from tests.TestBase import TestBase

except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

from newsreap.NNTPGroup import NNTPGroup


class NNTPGroup_Test(TestBase):

    def test_class(self):
        """
        General class testing

        """
        for val in [None, '', '^', '    $', '    ']:
            try:
                # We should throw an exception or this test fails
                NNTPGroup(None)
                assert(False)

            except AttributeError:
                assert(True)

    def test_normalize(self):
        """
        Tests the normalize() function
        """

        assert(NNTPGroup.normalize('alt.binaries.TEST')
               == 'alt.binaries.test')
        assert(NNTPGroup.normalize('  alt.binaries.TEST   ')
               == 'alt.binaries.test')

        assert(NNTPGroup.normalize(None) is None)
        assert(NNTPGroup.normalize('') is None)
        assert(NNTPGroup.normalize('     ') is None)
        assert(NNTPGroup.normalize('% &   ') is None)

    def test_split(self):
        """
        Tests the split() function
        """

        result = NNTPGroup.split('alt.binaries.TEST')
        assert(isinstance(result, set))
        assert(len(result) == 1)
        assert('alt.binaries.test' in result)

        result = result.pop()
        # Normalizing occurs when using the the comparison tool
        assert('   alt.binaries.test   %' == result)

        result = NNTPGroup.split('    alt.binaries.TEST    ')
        assert(isinstance(result, set))
        assert(len(result) == 1)
        assert('alt.binaries.test' in result)

        result = result.pop()
        # Normalizing occurs when using the the comparison tool
        assert('Alt.Binaries.TEST' == result)

        # handle bad cases; in all situations, we should still
        # end up with a set() object
        for val in [None, '', '^', '    $', '    ']:
            result = NNTPGroup.split(val)
            assert(isinstance(result, set))
            assert(len(result) == 0)
