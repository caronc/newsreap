# -*- encoding: utf-8 -*-
#
# A base testing class/library to test the workings of an NNTPArticle
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

from newsreap.NNTPArticle import NNTPArticle
from newsreap.NNTPBinaryContent import NNTPBinaryContent
from newsreap.NNTPHeader import NNTPHeader
from newsreap.NNTPArticle import NNTPArticle
from newsreap.NNTPResponse import NNTPResponse


class NNTPArticle_Test(TestBase):

    def test_loading_response(self):
        """
        Tests the load_response() function of the article
        """

        # Prepare a Response
        response = NNTPResponse(200, 'Great Data')
        response.decoded.add(NNTPBinaryContent())

        # Preare Article
        article = NNTPArticle(id='random-id')

        # Load and Check
        assert article.load_response(response) is True
        assert article.header is None
        assert len(article.decoded) == 1

        # Prepare a Response (with a Header)
        response = NNTPResponse(200, 'Great Data')
        response.decoded.add(NNTPHeader())
        response.decoded.add(NNTPBinaryContent())

        # Preare Article
        article = NNTPArticle(id='random-id')

        # Load and Check
        assert article.load_response(response) is True
        assert isinstance(article.header, NNTPHeader)
        assert len(article.decoded) == 1
