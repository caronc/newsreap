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

import re
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

        # Prepare Article
        article = NNTPArticle(id='random-id')

        # There is no data so our article can't be valid
        assert article.is_valid() is False

        # Load and Check
        assert article.load_response(response) is True
        assert article.header is None
        assert len(article.decoded) == 1
        assert len(article.decoded) == len(article.files())
        assert str(article) == 'random-id'
        assert unicode(article) == u'random-id'
        assert article.size() == 0

        # Now there is data, but it's an empty Object so it can't be valid
        assert article.is_valid() is False

        result = re.search(' Message-ID=\"(?P<id>[^\"]+)\"', repr(article))
        assert result is not None
        assert result.group('id') == str(article)

        result = re.search(' attachments=\"(?P<no>[^\"]+)\"', repr(article))
        assert result is not None
        assert int(result.group('no')) == len(article)

        # Prepare Article
        article_a = NNTPArticle(id='a')
        article_b = NNTPArticle(id='b')
        assert (article_a < article_b) is True

        # playing with the sort order however alters things
        article_a.no += 1
        assert (article_a < article_b) is False

        # Prepare a Response (with a Header)
        response = NNTPResponse(200, 'Great Data')
        response.decoded.add(NNTPHeader())
        response.decoded.add(NNTPBinaryContent())

        # Prepare Article
        article = NNTPArticle(id='random-id')

        # Load and Check
        assert article.load_response(response) is True
        assert isinstance(article.header, NNTPHeader)
        assert len(article.decoded) == 1

    def test_group(self):
        """
        Tests the group variations
        """

        # Test String
        article = NNTPArticle(
            id='random-id',
        )
        assert(isinstance(article.groups, set))
        assert(len(article.groups) == 0)

        # Test String
        article = NNTPArticle(
            id='random-id',
            groups='convert.lead.2.gold',
        )
        assert(isinstance(article.groups, set))
        assert(len(article.groups) == 1)
        assert('convert.lead.2.gold' in article.groups)

        # Support Tuples
        article = NNTPArticle(
            id='random-id',
            groups=(
                'convert.lead.2.gold',
                'convert.lead.2.gold.again',
            ),
        )

        assert(isinstance(article.groups, set))
        assert(len(article.groups) == 2)
        assert('convert.lead.2.gold' in article.groups)
        assert('convert.lead.2.gold.again' in article.groups)

        # Support Lists
        article = NNTPArticle(
            id='random-id',
            groups=[
                'convert.lead.2.gold',
                'convert.lead.2.gold.again',
            ],
        )
        assert(isinstance(article.groups, set))
        assert(len(article.groups) == 2)
        assert('convert.lead.2.gold' in article.groups)
        assert('convert.lead.2.gold.again' in article.groups)

        # Support Sets
        article = NNTPArticle(
            id='random-id',
            groups=set([
                'convert.lead.2.gold',
                'convert.lead.2.gold.again',
            ]),
        )
        assert(isinstance(article.groups, set))
        assert(len(article.groups) == 2)
        assert('convert.lead.2.gold' in article.groups)
        assert('convert.lead.2.gold.again' in article.groups)

        try:
            # Throw an exception if the group is invalid
            article = NNTPArticle(id='random-id', groups=4)
            assert False

        except Exception, e:
            assert isinstance(e, AttributeError)

        # Duplicates groups are are removed automatically
        article = NNTPArticle(
            id='random-id',
            groups=[
                'convert.lead.2.gold.again',
                'ConVert.lead.2.gold',
                'convert.lead.2.gold',
                'convert.lead.2.gold.again',
            ],
        )
        assert(isinstance(article.groups, set))
        assert(len(article.groups) == 2)
        assert('convert.lead.2.gold' in article.groups)
        assert('convert.lead.2.gold.again' in article.groups)
