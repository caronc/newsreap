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

import pytest
import re
import sys
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

from tests.FakeNNTPServer import CERT_FILE
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
            ca_certs=CERT_FILE,
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
            ca_certs=CERT_FILE,
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
            ca_certs=CERT_FILE,
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


def test_server_settings_that_are_not_numbers_use_their_defaults():
    """
    A server setting that should be a number (or an iostream that isn't a
    name) falls back to its default instead of stopping the connection
    from being made
    """
    sock = NNTPConnection(
        host='localhost', port=119, retries='lots', retry_wait=None,
        stall_timeout=[60], min_rate='fast', slow_window=-5, iostream=5)

    assert sock.retries == 3
    assert sock.retry_wait == 2.0
    assert sock.stall_timeout == 60.0
    assert sock.min_rate == 1024
    assert sock.slow_window == 0.0
    assert sock._iostream == NNTPIOStream.RFC3977_GZIP


def test_next_and_prev_walk_through_a_group(nntp_server):
    """
    next() reads forward from where we are (the last article included) and
    prev() reads back to the first article; neither goes past the group
    """
    from datetime import datetime
    from tests.conftest import connect_to

    for no in range(1, 6):
        nntp_server.add_overview(
            'alt.test', no, 'Subject %d' % no, 'p@x', datetime(2020, 1, 1),
            'id%d@x' % no)

    def numbers(found):
        return sorted(entry['article_no'] for entry in found.values())

    sock = connect_to(nntp_server, join_group=True)
    sock.group('alt.test')
    assert (sock.group_head, sock.group_tail) == (1, 5)

    assert sock.seek(1) == 1
    assert numbers(sock.next(2)) == [1, 2]
    assert numbers(sock.next(10)) == [3, 4, 5]
    assert sock.next().code == 423

    assert numbers(sock.prev(2)) == [4, 5]
    assert numbers(sock.prev(10)) == [1, 2, 3]
    assert sock.prev().code == 423
    sock.close()


def test_a_command_can_be_given_its_own_timeout(nntp_server):
    """
    send(timeout=...) is how long that reply may go quiet before it counts
    as stalled; without one the stall_timeout setting is used
    """
    from tests.conftest import connect_to

    sock = connect_to(nntp_server, join_group=True)
    recv = sock._recv
    asked = []

    def spy(*args, **kwargs):
        asked.append(kwargs.get('timeout'))
        return recv(*args, **kwargs)

    sock._recv = spy
    assert sock.send('MODE READER', timeout=7.5).code is not None
    assert sock.send('MODE READER').code is not None
    assert asked[-2:] == [7.5, 0]
    sock.close()


@pytest.mark.parametrize('iostream', [None, False, 0, ''])
def test_an_empty_iostream_turns_compression_off(iostream):
    """
    An iostream of nothing (ie: 'iostream: ~' in the configuration) means
    no compression
    """
    sock = NNTPConnection(host='localhost', port=119, iostream=iostream)
    assert sock._iostream == NNTPIOStream.RFC3977


@pytest.mark.parametrize('join_group', [True, False])
def test_lists_posts_and_nzb_files_are_fetched_directly(tmp_path, join_group):
    """
    A connection fetches a list of Message-IDs, every part of a post (one
    it can't find is kept, unfetched) and every part an NZB file lists,
    with the options given
    """
    from tests.FakeNNTPServer import FakeNNTPServer
    from tests.NNTPDownloader_Test import GROUP
    from tests.NNTPDownloader_Test import write_nzb
    from tests.conftest import connect_to
    from newsreap.NNTPArticle import NNTPArticle
    from newsreap.NNTPSegmentedPost import NNTPSegmentedPost
    from newsreap.NNTPnzb import NNTPnzb

    with FakeNNTPServer() as server:
        for name in ('a', 'b'):
            path = tmp_path / ('%s.eml' % name)
            path.write_bytes(b'Subject: test\r\n\r\nhello %s\r\n' % name.encode())
            server.add_article('%s@example.com' % name, str(path), GROUP)

        # A yEnc encoded one (only decoded when we are asked to)
        server.add_article('y@example.com', join(
            dirname(abspath(__file__)), 'var', '00000005.ntx'), GROUP)

        sock = connect_to(server, join_group=join_group)
        if not join_group:
            # We pick the group ourselves (group= is only used to join one)
            sock.group(GROUP)

        found = sock.get(['a@example.com', 'b@example.com'], str(tmp_path),
                         group=GROUP)
        assert sorted(article.id for article in found) == [
            'a@example.com', 'b@example.com']

        post = NNTPSegmentedPost('file.bin', groups=GROUP)
        for no, message_id in enumerate(
                ('a@example.com', 'gone@example.com', 'b@example.com'), 1):
            post.add(NNTPArticle(id=message_id, no=no))

        # A post knows its groups (they are joined when join_group is set)
        found = sock.get(post, str(tmp_path), decoders=False,
                         group=None if join_group else GROUP)
        assert len(found) == 3
        fetched = {article.id: len(article.body) > 0 for article in found}
        assert fetched == {'a@example.com': True, 'gone@example.com': False,
                           'b@example.com': True}

        nzb = NNTPnzb(write_nzb(tmp_path / 'r.nzb', [
            ('a.bin', [(1, 'a@example.com')]),
            ('y.bin', [(1, 'y@example.com')])]))
        found = sock.get(nzb, str(tmp_path), group=GROUP)
        assert sorted(article.id for article in found) == [
            'a@example.com', 'y@example.com']
        assert [len(a) for a in found if a.id == 'y@example.com'] == [1]

        # As it is (nothing decoded), as asked
        found = sock.get(nzb, str(tmp_path), group=GROUP, decoders=False)
        assert [len(a) for a in found if a.id == 'y@example.com'] == [0]
        sock.close()


def test_stat_answers_none_when_the_server_refuses(nntp_server):
    """
    A STAT the server refuses (ie: 412 no group selected) is answered with
    None (it couldn't be told), not mistaken for the article being there
    """
    from tests.conftest import connect_to

    sock = connect_to(nntp_server, join_group=False)
    assert sock.stat('nothing@example.com') is None
    sock.close()


def test_posting_keeps_the_options_given():
    """
    post() hands update_headers on for every article (lists too), and an
    unexpected error is raised as it is (not hidden by another one)
    """
    from newsreap.NNTPArticle import NNTPArticle
    from newsreap.NNTPResponse import NNTPResponse

    sock = NNTPConnection(host='localhost', port=119)
    asked = []

    def posted(article, update_headers=True):
        asked.append(update_headers)
        return NNTPResponse(240, 'Article received OK')

    sock._post = posted
    articles = [NNTPArticle(id='%d@example.com' % no, no=no)
                for no in (1, 2)]
    assert len(sock.post(articles, update_headers=False)) == 2
    assert asked == [False, False]

    def broken(article, update_headers=True):
        raise ValueError('bad article')

    sock._post = broken
    with pytest.raises(ValueError):
        sock.post(articles[0])


def test_the_answer_to_a_post_keeps_what_was_posted():
    """
    The answer to a post holds the article posted, even once nothing else
    does (ie: it was made just for the post)
    """
    from newsreap.NNTPArticle import NNTPArticle
    from newsreap.NNTPResponse import NNTPResponse

    sock = NNTPConnection(host='localhost', port=119)
    sock._post = lambda article, update_headers=True: NNTPResponse(
        240, 'Article received OK')

    results = sock.post(NNTPArticle(id='lone@example.com'))
    assert [r.body.msgid() for r in results] == ['lone@example.com']


def test_decoders_given_start_clean_for_each_part(tmp_path):
    """
    Decoders handed to a connection for a post are reset before each part
    (one cut short would otherwise carry on into the next)
    """
    from tests.FakeNNTPServer import FakeNNTPServer
    from tests.NNTPDownloader_Test import GROUP
    from tests.conftest import connect_to
    from newsreap.NNTPArticle import NNTPArticle
    from newsreap.NNTPSegmentedPost import NNTPSegmentedPost
    from newsreap.codecs.CodecHeader import CodecHeader

    with FakeNNTPServer() as server:
        for name in ('a', 'b'):
            path = tmp_path / ('%s.eml' % name)
            path.write_bytes(b'Subject: test\r\n\r\nhello\r\n')
            server.add_article('%s@example.com' % name, str(path), GROUP)

        sock = connect_to(server, join_group=True)
        post = NNTPSegmentedPost('file.bin', groups=GROUP)
        for no, name in enumerate(('a', 'b'), 1):
            post.add(NNTPArticle(id='%s@example.com' % name, no=no))

        decoder = CodecHeader(work_dir=str(tmp_path))
        events = []
        reset = decoder.reset
        detect = decoder.detect

        def counted():
            events.append('reset')
            return reset()

        def seen(*args, **kwargs):
            if not events or events[-1] != 'used':
                events.append('used')
            return detect(*args, **kwargs)

        decoder.reset = counted
        decoder.detect = seen
        sock.get(post, str(tmp_path), decoders=[decoder])

        # Each time it is used, it was reset just before
        used = [no for no, event in enumerate(events) if event == 'used']
        assert len(used) == 2
        assert all(events[no - 1] == 'reset' for no in used)
        sock.close()


def test_a_group_that_cant_be_reached_still_answers_a_tuple():
    """
    group() answers (None, None, None, name) when the server can't be
    reached, as it does for a group that doesn't exist
    """
    from newsreap.NNTPConnection import NNTPConnection

    connection = NNTPConnection(
        host='127.0.0.1', port=119, username='valid', password='valid',
        secure=False, join_group=False, retries=0)
    connection.connect = lambda *args, **kwargs: False
    assert connection.group('alt.test') == (None, None, None, 'alt.test')


def test_closing_a_connection_closes_its_backups_too(tmp_path):
    """
    Closing a connection lets go of its socket (the server sees it go) and
    of its backup servers' connections
    """
    import time
    from tests.FakeNNTPServer import FakeNNTPServer
    from tests.conftest import connect_to

    with FakeNNTPServer(join_group=False) as server:
        primary = connect_to(server)
        backup = connect_to(server)
        assert primary.connect() and backup.connect()
        assert primary.append(backup) is True

        raw = primary.socket
        primary.close()
        assert raw.fileno() == -1
        assert not backup.connected and backup.socket is None

        # The server is told (QUIT) both times
        deadline = time.monotonic() + 5
        while server.commands().count('QUIT') < 2 and \
                time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.commands().count('QUIT') == 2


def test_a_group_that_couldnt_be_joined_isnt_taken_as_joined():
    """
    A failed GROUP leaves the connection in the group it was in, so asking
    for the missing group again tries to join it again
    """
    from tests.FakeNNTPServer import FakeNNTPServer
    from tests.conftest import connect_to

    with FakeNNTPServer(join_group=True) as server:
        server.add_group('alt.binaries.test')
        connection = connect_to(server, join_group=True)
        try:
            assert connection.group('alt.binaries.test')[0] is not None
            assert connection.group('alt.no.such') == \
                (None, None, None, 'alt.no.such')
            assert connection.group_name == 'alt.binaries.test'

            assert connection.stat('<a@b>', group='alt.no.such') is None
            assert server.commands().count('GROUP alt.no.such') == 2

        finally:
            connection.close()


def test_what_we_write_is_sent_right_away():
    """
    Connections send each write at once (TCP_NODELAY) rather than holding
    a short one back until the server acknowledges the last; posting an
    article otherwise waits up to 40ms at its end
    """
    import socket
    from tests.FakeNNTPServer import FakeNNTPServer
    from tests.conftest import connect_to

    with FakeNNTPServer(join_group=False) as server:
        connection = connect_to(server)
        try:
            assert connection.connect()
            assert connection.socket.getsockopt(
                socket.IPPROTO_TCP, socket.TCP_NODELAY)

        finally:
            connection.close()
