# -*- coding: utf-8 -*-
#
# Test the NNTP Index Header (XOVER Fetching) Codec
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

from os.path import dirname
from os.path import abspath
from datetime import datetime

try:
    from tests.TestBase import TestBase
except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

from newsreap.codecs.CodecArticleIndex import CodecArticleIndex


class Codec_ArticleIndex(TestBase):

    @classmethod
    def test_bad_groups(cls):
        """
        Make sure we fail on bad groups
        """
        # Initialize Codec
        ch = CodecArticleIndex()

        # Empty lines are not valid
        assert ch.detect("") == None
        # white space
        assert ch.detect("    ") == None

    @classmethod
    def test_strange_formated(cls):
        """
        A List of strange errors that surfaced when indexing
        usenet.  I put them here to try to catch them and bullet
        proof it for next time.
        """

        # Initialize Codec
        ch = CodecArticleIndex()

        # astraweb.us: alt.binaries.zune.videos
        # date failed to parse: 'mercredi, 23 jul 2014 20:01:02 -0600'
        assert ch.detect(
            "24423	Dawn of the Planet of the Apes 2014 480p Webrip XviD " +\
            "AC3-Osiris - Dawn of the Planet of the Apes 2014 480p Webrip" +\
            "XviD AC3.US-Osiris.nzb 241176 bytes (1/1)	Randrup@dcfre.net" +\
            "	mercredi, 23 jul 2014 20:01:02 -0600	" +\
            "<23071420.0102@dcfre.net>		338850	5363	" +\
            "Xref: news-big.astraweb.com alt.binaries.zappateers:169669 " +\
            "alt.binaries.zoogz-rift:160178 alt.binaries.zune.videos:24423 " +\
            "alt.binaries.zygomorphic:27068"
        ) == {
            'id': '23071420.0102@dcfre.net',
            'article_no': 24423,
            'score': 0,
            'group': 'news-big.astraweb.com',
            'poster': u'Randrup@dcfre.net',
            'date': datetime(2014, 7, 24, 2, 1, 2),
            'xgroups': {
                'alt.binaries.zappateers': 169669,
                'alt.binaries.zoogz-rift': 160178,
                'alt.binaries.zune.videos': 24423,
                'alt.binaries.zygomorphic': 27068,
            },
            'size': 338850,
            'lines': 5363,
            'subject':
                u'Dawn of the Planet of the Apes 2014 480p Webrip ' + \
                u'XviD AC3-Osiris - Dawn of the Planet of the Apes ' + \
                u'2014 480p WebripXviD AC3.US-Osiris.nzb 241176 bytes (1/1)',
        }
