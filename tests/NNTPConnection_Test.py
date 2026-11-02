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

import unittest
import os

from os.path import join
from os.path import dirname
from os.path import abspath

try:
    from tests.TestBase import TestBase

except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

from tests.FakeNNTPServer import FakeNNTPServer

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
        super().setUp()

        # Secure NNTP Server
        self.nntps = FakeNNTPServer(secure=True)

        # Insecure NNTP Server
        self.nntp = FakeNNTPServer(secure=False)

        # Start our servers
        self.nntps.start()
        self.nntp.start()

        # Acquire our configuration
        self.nttp_ipaddr, self.nntp_portno = self.nntp.host, self.nntp.port
        self.nttps_ipaddr, self.nntps_portno = \
            self.nntps.host, self.nntps.port

    def tearDown(self):
        # Shutdown our test servers
        self.nntps.stop()
        self.nntp.stop()

        super().tearDown()

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

    @unittest.skipIf(os.environ.get("TRAVIS") == "true",
                     "Skipping this test on Travis CI.")
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
            secure=True,
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

        result = NNTP_XOVER_RESPONSE_RE.match((
            '100\t' + 'A Package [001/001] "file.rar" yEnc (001/001)\t' +\
            'Magnum Opus <sir@john.doe>\t' +\
            '11 Aug 2014 08:33:07 GMT\t' +\
            '<foeVWs8FHWoKYXByTLD8_78o259@JBinUp.local>\t\t' +\
            '1061463\t' +\
            '8160\t' +\
            'Xref: news-big.astraweb.com alt.binaries.boneless:12929673602 ' +\
            'alt.binaries.multimedia:100'
            # The expression scans the raw data received from the server
            ).encode('ascii'),
        )

        assert result is not None
        # ID (Unique)
        assert result.group('subject') == \
            b'A Package [001/001] "file.rar" yEnc (001/001)'
        # Message-ID (Unique)
        assert result.group('id') == b'foeVWs8FHWoKYXByTLD8_78o259@JBinUp.local'
        # Article ID
        assert int(result.group('article_no')) == 100
        # Poster
        assert result.group('poster') == b'Magnum Opus <sir@john.doe>'
        # Date
        assert parse(result.group('date').decode('ascii')) == \
            datetime(2014, 8, 11, 8, 33, 0o7, tzinfo=pytz.UTC)
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
            assert NNTP_XOVER_RESPONSE_RE.match(
                entry.encode('ascii')) is not None

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
        groups = sock.groups(filters=re.compile('.*\\.l2g\\..*', re.ASCII))
        # Now we should hit the 2 l2g groups defined
        assert len(groups) == 2

        # You can use lists of expressions too
        groups = sock.groups(filters=re.compile('.*\\.l2g\\..*', re.ASCII))
        # Now we should hit the 2 l2g groups defined
        assert len(groups) == 2

        # Now fetch the content from the server with the lazy flag set; we
        # should retrieve a much larger list
        groups = sock.groups(filters=(
                re.compile('^alt\\..*\\.awesome', re.ASCII),
                re.compile('^alt\\..*\\.crap', re.ASCII),
        ))
        assert len(groups) == 2

        groups = sock.groups(filters='alt.binaries', lazy=False)
        # In var/grouplist, this is how many entries we have after
        # applying the filter
        assert len(groups) == 5270

        # Again without the lazy flag set
        groups = sock.groups(filters='alt.binaries')
        assert len(groups) == 5270

    def test_posting(self):
        sock = NNTPConnection(
            host=self.nttp_ipaddr,
            port=self.nntp_portno,
            username='valid',
            password='valid',
            secure=False,
            join_group=False,
        )
        assert sock.connect(timeout=5.0) is True

        # TODO

    def test_dot_stuffing_across_chunks(self):
        """
        Lines starting with a dot are stuffed and unstuffed even when a
        line starts at the edge of a chunk
        """
        sock = NNTPConnection()

        stuffed = b''.join(sock._stuff([
            '.header\r\n', b'body\r\n.', b'dot\r\n', b'\n.', b'x\r\n.\r\n']))
        assert stuffed == \
            b'..header\r\nbody\r\n..dot\r\n\n..x\r\n..\r\n'

        # Text is encoded with our character set
        assert b''.join(sock._stuff(['café'])) == b'caf\xe9'

        sock._soft_reset()
        restored = b''.join(sock._unstuff(chunk) for chunk in (
            b'..header\r\nbody\r\n', b'..dot\r\n..', b'.\r\n'))
        assert restored == b'.header\r\nbody\r\n.dot\r\n..\r\n'

        # The 2 dots of a stuffed line can arrive in separate pieces
        sock._soft_reset()
        restored = b''.join(sock._unstuff(chunk) for chunk in (
            b'first\r\n.', b'.second\r\n', b'.', b'.', b'.third\r\n'))
        assert restored == b'first\r\n.second\r\n..third\r\n'

        # Every way of splitting stuffed content gives the same result
        wire = b'..a\r\nb\r\n...\r\n..\r\nc.d\r\n'
        for cut in range(len(wire) + 1):
            for cut2 in range(cut, len(wire) + 1):
                sock._soft_reset()
                restored = b''.join(sock._unstuff(chunk) for chunk in (
                    wire[:cut], wire[cut:cut2], wire[cut2:]))
                assert restored == b'.a\r\nb\r\n..\r\n.\r\nc.d\r\n', \
                    (cut, cut2)

    def test_end_of_data_must_be_on_its_own_line(self):
        """
        Only a line holding a single dot marks the end of the data
        """
        sock = NNTPConnection()
        for data, expected in (
                (b'220 ok\r\nbody\r\n.\r\n', True),
                (b'220 ok\r\n.\r\n', True),
                (b'220 ok\r\nbody\n.\n', True),
                (b'220 ok\r\nends with a dot.\r\n', False),
                (b'220 ok\r\nbody\r\n', False),
                (b'.\r', False)):
            sock._soft_reset()
            sock._buffer.write(data)
            assert sock._has_eod(len(data)) is expected, data


def test_replies_never_carry_the_last_one_along(nntp_server):
    """
    A reply read straight after another (without a decoder) starts clean;
    the buffer used to keep its old position, so the next reply followed
    a run of NUL bytes and an XOVER lost its first line
    """
    from datetime import datetime
    from tests.conftest import connect_to

    for no in range(1, 4):
        nntp_server.add_overview(
            'alt.test', no, 'Subject %d' % no, 'p@x', datetime(2020, 1, 1),
            'id%d@x' % no)

    sock = connect_to(nntp_server, join_group=True)
    sock.group('alt.test')

    first = sock.send('LIST OVERVIEW.FMT').body.getvalue()
    assert sock.send('LIST OVERVIEW.FMT').body.getvalue() == first
    assert b'\x00' not in first

    assert len(sock.xover('alt.test', 1, 3)) == 3
    sock.close()


def test_the_overview_format_is_asked_for_once(tmp_path):
    """
    LIST OVERVIEW.FMT is asked for before the first XOVER (once per
    connection); a server that can't answer is taken to send an Xref
    """
    from datetime import datetime
    from tests.FakeNNTPServer import FakeNNTPServer
    from tests.conftest import connect_to
    from newsreap.codecs.CodecArticleIndex import OVERVIEW_EXTRAS

    with FakeNNTPServer(overview_fmt=(
            'Subject:', 'From:', 'Date:', 'Message-ID:', 'References:',
            ':bytes', ':lines', 'X-Extra:', 'Xref:')) as server:
        server.add_overview('alt.test', 1, 'One', 'p@x', datetime(2020, 1, 1),
                            'id1@x', extras=['junk', 'news alt.test:1 a.b:9'])

        sock = connect_to(server, join_group=True)
        sock.group('alt.test')
        found = sock.xover('alt.test', 1, 1)
        sock.xover('alt.test', 1, 1)
        assert next(iter(found.values()))['xgroups'] == {
            'alt.test': 1, 'a.b': 9}
        assert sock.overview_format() == ('X-Extra:', 'Xref:')
        assert [c for c in server.commands()
                if c.startswith('LIST')] == ['LIST OVERVIEW.FMT']
        sock.close()

    with FakeNNTPServer(overview_fmt=None) as server:
        server.add_overview('alt.test', 1, 'One', 'p@x', datetime(2020, 1, 1),
                            'id1@x')
        sock = connect_to(server, join_group=True)
        sock.group('alt.test')
        assert len(sock.xover('alt.test', 1, 1)) == 1
        assert sock.overview_format() == OVERVIEW_EXTRAS
        sock.close()
