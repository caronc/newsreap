# Test the NNTPResponse Object
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
from newsreap.SortedSet import sortedset

from os.path import dirname
from os.path import abspath

try:
    from tests.TestBase import TestBase

except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

from newsreap.NNTPResponse import NNTPResponse
from newsreap.NNTPResponse import NNTPResponseCode


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

    def test_contains(self):
        """
        Test that we can use the keyword 'in' correctly

        """

        response = NNTPResponse(200)
        assert(200 in response)
        assert(NNTPResponseCode.SUCCESS in response)


def test_only_2xx_answers_are_a_success():
    """
    is_success() is True for 2xx answers only; a 3xx one (ie: 340 send the
    article) means the server wants more, and an answer with no code is no
    success either
    """
    from newsreap.NNTPResponse import NNTPResponse

    assert NNTPResponse(200, 'Welcome').is_success() is True
    assert NNTPResponse(240, 'Article received OK').is_success() is True
    assert NNTPResponse(340, 'Send article').is_success() is False
    assert NNTPResponse(381, 'Password required').is_success() is False
    assert NNTPResponse(430, 'No such article').is_success() is False
    assert NNTPResponse(None, '').is_success() is False
