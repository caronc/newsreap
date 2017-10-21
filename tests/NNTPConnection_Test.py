# -*- coding: utf-8 -*-
#
# A base testing class/library to test the NNTP Server and Connection class
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

import re
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
import threading

from os.path import join
from os.path import dirname
from os.path import abspath

try:
    from tests.TestBase import TestBase

except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

from tests.NNTPSocketServer import NNTPSocketServer

from newsreap.NNTPConnection import NNTPConnection
from newsreap.NNTPIOStream import NNTPIOStream

# The directory containing all of the variable data used
# for the NNTPConnection Testing
VAR_PATH = join(dirname(abspath(__file__)), 'var')

# Empty File
EMPTY_FILE = join(VAR_PATH, 'emptyfile.msg')


class NNTPConnection_Test(TestBase):
    def setUp(self):
        """
        Grab a few more things from the config
        """
        super(NNTPConnection_Test, self).setUp()

        # Secure NNTP Server
        self.nntps = NNTPSocketServer(
            secure=True,
        )

        # Insecure NNTP Server
        self.nntp = NNTPSocketServer(
            secure=False,
        )

        # Exit the server thread when the main thread terminates
        self.nntps.daemon = True
        self.nntp.daemon = True

        # Start Our Server Threads
        self.nntps.start()
        self.nntp.start()

        # Acquire our configuration
        self.nttp_ipaddr, self.nntp_portno = \
                self.nntp.local_connection_info()
        self.nttps_ipaddr, self.nntps_portno = \
                self.nntps.local_connection_info()

    def tearDown(self):
        # Shutdown NNTP Dummy Servers Daemons
        self.nntps.shutdown()
        self.nntp.shutdown()

        super(NNTPConnection_Test, self).tearDown()

    def test_authentication(self):
        sock = NNTPConnection(
            host=self.nttp_ipaddr,
            port=self.nntp_portno,
            username='valid',
            password='valid',
            secure=False,
            join_group=False,
        )
        assert sock.connect(timeout=5.0) is True
        assert sock._iostream == NNTPIOStream.RFC3977_GZIP
        sock.close()

        sock = NNTPConnection(
            host=self.nttp_ipaddr,
            port=self.nntp_portno,
            username='invalid',
            password='valid',
            secure=False,
            join_group=False,
        )
        # Invalid Username
        assert sock.connect(timeout=5.0) is False

        sock = NNTPConnection(
            host=self.nttp_ipaddr,
            port=self.nntp_portno,
            username='valid',
            password='invalid',
            secure=False,
            join_group=False,
        )
        # Invalid Password
        assert sock.connect(timeout=5.0) is False

    def test_secure_authentication(self):
        sock = NNTPConnection(
            host=self.nttps_ipaddr,
            port=self.nntps_portno,
            username='valid',
            password='valid',
            secure=True,
            join_group=False,
        )
        assert sock.connect(timeout=5.0) is True
        assert sock._iostream == NNTPIOStream.RFC3977_GZIP
        sock.close()

        sock = NNTPConnection(
            host=self.nttps_ipaddr,
            port=self.nntps_portno,
            username='invalid',
            password='valid',
            secure=True,
            join_group=False,
        )
        # Invalid Username
        assert sock.connect(timeout=5.0) is False

        sock = NNTPConnection(
            host=self.nttps_ipaddr,
            port=self.nntps_portno,
            username='valid',
            password='invalid',
            secure=False,
            join_group=False,
        )
        # Invalid Password
        assert sock.connect(timeout=5.0) is False

    @classmethod
    def test_regular_expressions(cls):
        """
        Tests XOVER Regular Expressions

        These entries were based on ones that have failed in the past during
        testing when scanning against Usenet
        """
        from newsreap.codecs.CodecArticleIndex import NNTP_XOVER_RESPONSE_RE
        from datetime import datetime
        from dateutil.parser import parse
        import pytz

        result = NNTP_XOVER_RESPONSE_RE.match(
            '100\t' + 'A Package [001/001] "file.rar" yEnc (001/001)\t' +\
            'Magnum Opus <sir@john.doe>\t' +\
            '11 Aug 2014 08:33:07 GMT\t' +\
            '<foeVWs8FHWoKYXByTLD8_78o259@JBinUp.local>\t\t' +\
            '1061463\t' +\
            '8160\t' +\
            'Xref: news-big.astraweb.com alt.binaries.boneless:12929673602 ' +\
            'alt.binaries.multimedia:100',
        )

        assert result is not None
        # ID (Unique)
        assert result.group('subject') == \
            'A Package [001/001] "file.rar" yEnc (001/001)'
        # Message-ID (Unique)
        assert result.group('id') == 'foeVWs8FHWoKYXByTLD8_78o259@JBinUp.local'
        # Article ID
        assert int(result.group('article_no')) == 100
        # Poster
        assert result.group('poster') == 'Magnum Opus <sir@john.doe>'
        # Date
        assert parse(result.group('date')) == \
            datetime(2014, 8, 11, 8, 33, 07, tzinfo=pytz.UTC)
        # Size (Bytes)
        assert int(result.group('size')) == 1061463
        # Lines
        assert int(result.group('lines')) == 8160

        entries = (
            # This article has a missing lines field (no content)
            '780331982\t(18/29) "Ma Dent Fait 123.part17.rar" -' +\
            ' 647,91 MB - yEnc (5/79)\tFracas <fracas@get2mail.fr>\t' +\
            'Fri, 31 Jul 2015 22:12:03 -0000\t<O7RJVBU8NlWc81zxmz20_' +\
            '18o29@JBinUp.local>\t\t398150\t\tXref: news-big.astrawe' +\
            'b.com alt.binaries.mp3:780331982',
        )

        for entry in entries:
            # Test Entries
            assert NNTP_XOVER_RESPONSE_RE.match(entry) is not None

    def test_group_searching(self):
        """
        Test Group searching (LIST ACTIVE)

        """
        # Build an artificial list to use first
        grouplist = [
            {
                'group': 'alt.l2g.is.awesome', 'flags': 'y',
                'high': 0, 'low': 0, 'count': 0,
            },
            {
                'group': 'alt.l2g.is.lead2gold', 'flags': 'y',
                'high': 0, 'low': 0, 'count': 0,
            },
            {
                'group': 'alt.binaries.crap', 'flags': 'y',
                'high': 0, 'low': 0, 'count': 0,
            },
        ]
        sock = NNTPConnection(
            host=self.nttp_ipaddr,
            port=self.nntp_portno,
            username='valid',
            password='valid',
            secure=False,
            join_group=False,
        )

        assert sock.connect(timeout=5.0) is True

        # Assign a cached grouplist
        sock._grouplist = grouplist
        sock._grouplist_response = (211, 'dummy success message')

        # Now we should be able to search our results
        groups = sock.groups()

        # We fetched everything (no filters in place)
        assert len(groups) == 3

        # Now we should be able to search our results
        groups = sock.groups(filters='alt.binaries')
        # We fetched anything matching 'alt.binaries' which is just 1
        assert len(groups) == 1

        # You can use Regular expressions too
        groups = sock.groups(filters=re.compile('.*\.l2g\..*'))
        # Now we should hit the 2 l2g groups defined
        assert len(groups) == 2

        # You can use lists of expressions too
        groups = sock.groups(filters=re.compile('.*\.l2g\..*'))
        # Now we should hit the 2 l2g groups defined
        assert len(groups) == 2

        # Now fetch the content from the server with the lazy flag set; we
        # should retrieve a much larger list
        groups = sock.groups(filters=(
                re.compile('^alt\..*\.awesome'),
                re.compile('^alt\..*\.crap'),
        ))
        assert len(groups) == 2

        groups = sock.groups(filters='alt.binaries', lazy=False)
        # In var/grouplist, this is how many entries we have after
        # applying the filter
        assert len(groups) == 5270

        # Again without the lazy flag set
        groups = sock.groups(filters='alt.binaries')
        assert len(groups) == 5270
