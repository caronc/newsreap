# -*- encoding: utf-8 -*-
#
# Test the NNTPResponse Object
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

from os.path import dirname
from os.path import abspath

try:
    from tests.TestBase import TestBase

except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

from newsreap.NNTPResponse import NNTPResponse


class NNTPResponse_Test(TestBase):
    """
    A Class for testing NNTPResponse

    A Response is the type returned by the _recv() and
    send() functions within the NNTPConnection() class.

    """

    def test_initialization(self):
        """
        Test that our initializaation works correct.
        """

        response = NNTPResponse()
        assert response.code == 0
        assert response.code_str == ''
        assert isinstance(response.decoded, sortedset)
        assert str(response) ==  ''

        response = NNTPResponse(200)
        assert response.code == 200
        assert response.code_str == ''
        assert isinstance(response.decoded, sortedset)
        assert str(response) ==  '200'

        response = NNTPResponse(400 ,'test response')
        assert response.code == 400
        assert response.code_str == 'test response'
        assert isinstance(response.decoded, sortedset)
        assert str(response) ==  '400: test response'
