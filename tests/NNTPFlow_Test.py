# Tests the conversations (flow) between NewsReap and an NNTP Server
#
# Copyright (C) 2026 Chris Caron <lead2gold@gmail.com>
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

import gevent.monkey
gevent.monkey.patch_all()

import time
from os.path import join

import pytest

from newsreap.NNTPArticle import NNTPArticle
from newsreap.NNTPIOStream import NNTPIOStream

from tests.FakeNNTPServer import FakeNNTPServer
from tests.FakeNNTPServer import Reply
from tests.FakeNNTPServer import VAR_PATH
from tests.conftest import connect_to

GROUP = 'alt.binaries.test'

LOGIN = ['AUTHINFO USER valid', 'AUTHINFO PASS ****', 'XFEATURE COMPRESS GZIP']


def text_article(path, body):
    """
    Writes a small plain text article and returns its path
    """
    with open(path, 'wb') as fp:
        fp.write(
            b'From: tester@example.com\r\n'
            b'Subject: A text article\r\n'
            b'Message-ID: <text@example.com>\r\n'
            b'\r\n' + body)
    return str(path)


#
# Logging in
#
@pytest.mark.parametrize('secure', [False, True])
def test_login_sends_user_password_then_compression(secure):
    """
    A login sends the user, then the password, then asks for compression
    """
    with FakeNNTPServer(secure=secure) as server:
        sock = connect_to(server)
        assert sock.connect(timeout=5.0) is True
        assert sock._iostream == NNTPIOStream.RFC3977_GZIP
        assert sock.can_post is True
        sock.close()

        assert server.wait_for('QUIT')
        assert server.commands() == LOGIN + ['QUIT']


def test_bad_user_stops_the_login(nntp_server):
    """
    The password is never sent once the user is turned down
    """
    sock = connect_to(nntp_server, username='nobody')
    assert sock.connect(timeout=5.0) is False
    assert nntp_server.commands() == ['AUTHINFO USER nobody']


def test_bad_password_stops_the_login(nntp_server):
    """
    Nothing more is sent once the password is turned down
    """
    sock = connect_to(nntp_server, password='wrong')
    assert sock.connect(timeout=5.0) is False
    assert nntp_server.commands() == LOGIN[:2]


def test_server_without_compression():
    """
    Compression is turned off when the server doesn't support it
    """
    with FakeNNTPServer(compression=False) as server:
        sock = connect_to(server)
        assert sock.connect(timeout=5.0) is True
        assert sock._iostream == NNTPIOStream.RFC3977

        # Group listings still work (just not compressed)
        assert len(sock.groups(lazy=False)) == 38570
        sock.close()


def test_server_that_does_not_allow_posting():
    """
    The welcome message tells us whether we can post
    """
    with FakeNNTPServer(welcome='200 news server ready') as server:
        sock = connect_to(server)
        assert sock.connect(timeout=5.0) is True
        assert sock.can_post is False
        sock.close()


@pytest.mark.parametrize('welcome', [
    '502 Access denied',
    Reply('400 Too many connections', close=True),
    Reply(close=True),
    Reply(raw=b'garbage that is not a status line\r\n'),
])
def test_unwelcome_servers_are_not_logged_into(welcome):
    """
    No login is attempted when the server doesn't welcome us
    """
    with FakeNNTPServer(welcome=welcome) as server:
        sock = connect_to(server)
        assert sock.connect(timeout=5.0) is False
        assert server.commands() == []


def test_group_is_selected_again_after_reconnecting(nntp_server, tmp_path):
    """
    Closing forgets the group; the next fetch selects it again
    """
    nntp_server.add_article('5', join(VAR_PATH, '00000005.ntx'), GROUP)

    sock = connect_to(nntp_server, join_group=True)
    assert sock.connect(timeout=5.0) is True
    assert sock.group(GROUP) == (1, 0, 1, GROUP)

    sock.close()
    assert sock.group_name is None

    # We connect again on our own when needed
    assert sock.get('5', work_dir=str(tmp_path), group=GROUP)
    assert sock.group_name == GROUP
    sock.close()

    assert nntp_server.wait_for('QUIT', count=2)
    assert nntp_server.commands() == \
        LOGIN + ['GROUP %s' % GROUP, 'QUIT'] + \
        LOGIN + ['GROUP %s' % GROUP, 'ARTICLE <5>', 'QUIT']


#
# Fetching articles
#
def test_article_fetch_flow(nntp_server, tmp_path):
    """
    Fetching an article selects its group and then asks for it
    """
    nntp_server.add_article('5', join(VAR_PATH, '00000005.ntx'), GROUP)

    sock = connect_to(nntp_server, join_group=True)
    assert sock.connect(timeout=5.0) is True

    article = sock.get('5', work_dir=str(tmp_path), group=GROUP)
    assert isinstance(article, NNTPArticle)

    content = next(iter(article.decoded))
    assert content.filename == 'testfile.txt'
    assert content.is_valid() is True

    with open(join(VAR_PATH, 'testfile.txt'), 'rb') as fp:
        assert content.getvalue() == fp.read()

    sock.close()
    assert nntp_server.wait_for('QUIT')
    assert nntp_server.commands() == \
        LOGIN + ['GROUP %s' % GROUP, 'ARTICLE <5>', 'QUIT']


def test_responses_do_not_wait_for_more_data(nntp_server, tmp_path):
    """
    A complete response is handled right away
    """
    nntp_server.add_article('5', join(VAR_PATH, '00000005.ntx'), GROUP)
    sock = connect_to(nntp_server, join_group=True)
    assert sock.connect(timeout=5.0) is True

    start = time.monotonic()
    for _ in range(5):
        assert sock.get('5', work_dir=str(tmp_path), group=GROUP)
    assert sock.groups(lazy=False)
    assert time.monotonic() - start < 2.0
    sock.close()


@pytest.mark.parametrize('chunk_size, delay', [
    # Many small pieces
    (64, 0.01),
    # A server that stalls part way through
    (8192, 1.5),
])
def test_slow_servers(nntp_server, tmp_path, chunk_size, delay):
    """
    Articles sent a little at a time (with pauses) still arrive intact
    """
    path = join(VAR_PATH, '00000020.ntx')
    with open(path, 'rb') as fp:
        content = fp.read()

    nntp_server.add_article('20', path, GROUP)
    nntp_server.on(
        r'^ARTICLE <20>$',
        Reply('220 0 <20>', body=content, chunk_size=chunk_size, delay=delay))

    sock = connect_to(nntp_server, join_group=True)
    assert sock.connect(timeout=5.0) is True
    article = sock.get('20', work_dir=str(tmp_path), group=GROUP)

    decoded = next(iter(article.decoded))
    assert decoded.part == 1
    assert decoded.is_valid() is True
    assert len(decoded) == 11250
    sock.close()


def test_garbage_response(nntp_server, tmp_path):
    """
    A response that isn't NNTP is treated as an error
    """
    nntp_server.add_group(GROUP)
    nntp_server.on(r'^ARTICLE', Reply(raw=b'\x00\x01 garbage\r\n'))

    sock = connect_to(nntp_server, join_group=True, retries=2)
    assert sock.connect(timeout=5.0) is True
    assert sock.get('5', work_dir=str(tmp_path), group=GROUP) is None

    # We tried again (twice) before giving up
    assert nntp_server.commands().count('ARTICLE <5>') == 3


def test_missing_article(nntp_server, tmp_path):
    """
    Asking for an article the server doesn't have returns nothing
    """
    nntp_server.add_group(GROUP)
    sock = connect_to(nntp_server, join_group=True)
    assert sock.connect(timeout=5.0) is True
    assert sock.get('missing', work_dir=str(tmp_path), group=GROUP) is None
    sock.close()


def test_dropped_connection_is_retried(nntp_server, tmp_path):
    """
    A response cut short is fetched again on a new connection
    """
    path = join(VAR_PATH, '00000005.ntx')
    with open(path, 'rb') as fp:
        content = fp.read()

    nntp_server.add_article('5', path, GROUP)
    # The first attempt is cut off half way through
    nntp_server.on(
        r'^ARTICLE <5>$',
        Reply('220 0 <5>', raw=content[:len(content) // 2], close=True),
        once=True)

    sock = connect_to(nntp_server, join_group=True)
    assert sock.connect(timeout=5.0) is True
    article = sock.get('5', work_dir=str(tmp_path), group=GROUP)
    assert isinstance(article, NNTPArticle)
    assert next(iter(article.decoded)).is_valid() is True

    # We logged in again and selected our group before asking again
    assert nntp_server.commands()[4:] == [
        'ARTICLE <5>', *LOGIN, 'GROUP ' + GROUP, 'ARTICLE <5>']
    sock.close()


def test_retrying_gives_up_eventually(nntp_server, tmp_path):
    """
    A server that keeps dropping us is given up on after our retries
    """
    nntp_server.add_group(GROUP)
    nntp_server.on(r'^ARTICLE', Reply('220 0 <5>', raw=b'cut', close=True))

    sock = connect_to(nntp_server, join_group=True, retries=3)
    assert sock.connect(timeout=5.0) is True
    assert sock.get('5', work_dir=str(tmp_path), group=GROUP) is None
    assert nntp_server.commands().count('ARTICLE <5>') == 4

    # Nothing is retried when told not to
    sock = connect_to(nntp_server, join_group=True, retries=0)
    assert sock.connect(timeout=5.0) is True
    assert sock.get('5', work_dir=str(tmp_path), group=GROUP) is None
    assert nntp_server.commands().count('ARTICLE <5>') == 5


def test_stalled_connection_is_retried(nntp_server, tmp_path):
    """
    A server that stops sending part way through is treated as dead
    """
    path = join(VAR_PATH, '00000005.ntx')
    with open(path, 'rb') as fp:
        content = fp.read()

    nntp_server.add_article('5', path, GROUP)
    # Half the article arrives and then nothing more (but the connection
    # stays open)
    nntp_server.on(
        r'^ARTICLE <5>$',
        Reply('220 0 <5>', raw=content[:len(content) // 2]), once=True)

    sock = connect_to(nntp_server, join_group=True, stall_timeout=0.5)
    assert sock.connect(timeout=5.0) is True

    started = time.monotonic()
    article = sock.get('5', work_dir=str(tmp_path), group=GROUP)
    assert time.monotonic() - started < 5.0
    assert next(iter(article.decoded)).is_valid() is True
    assert nntp_server.commands().count('ARTICLE <5>') == 2
    sock.close()


def test_crawling_connection_is_retried(nntp_server, tmp_path):
    """
    A transfer that stays far below our minimum speed is treated as dead
    """
    path = join(VAR_PATH, '00000005.ntx')
    with open(path, 'rb') as fp:
        content = fp.read()

    nntp_server.add_article('5', path, GROUP)
    # Roughly 640 bytes a second; the article would take minutes
    nntp_server.on(
        r'^ARTICLE <5>$',
        Reply('220 0 <5>', body=content, chunk_size=64, delay=0.1),
        once=True)

    sock = connect_to(
        nntp_server, join_group=True, min_rate=4096, slow_window=1.0)
    assert sock.connect(timeout=5.0) is True

    started = time.monotonic()
    article = sock.get('5', work_dir=str(tmp_path), group=GROUP)
    assert time.monotonic() - started < 5.0
    assert next(iter(article.decoded)).is_valid() is True
    assert nntp_server.commands().count('ARTICLE <5>') == 2
    sock.close()


def test_short_slow_down_is_tolerated(nntp_server, tmp_path):
    """
    A brief pause or slow down (shorter than our limits) is waited out
    """
    path = join(VAR_PATH, '00000005.ntx')
    with open(path, 'rb') as fp:
        content = fp.read()

    nntp_server.add_article('5', path, GROUP)
    nntp_server.on(
        r'^ARTICLE <5>$',
        Reply('220 0 <5>', body=content,
              chunk_size=len(content) // 3, delay=0.6))

    sock = connect_to(
        nntp_server, join_group=True, stall_timeout=2.0, min_rate=1024,
        slow_window=5.0)
    assert sock.connect(timeout=5.0) is True
    article = sock.get('5', work_dir=str(tmp_path), group=GROUP)
    assert next(iter(article.decoded)).is_valid() is True
    assert nntp_server.commands().count('ARTICLE <5>') == 1
    sock.close()


def test_dot_stuffed_lines_are_restored(nntp_server, tmp_path):
    """
    Lines the server dot stuffed (RFC 3977) are given back as they were
    """
    path = text_article(
        tmp_path / 'text.msg', b'.hidden line\r\n..\r\nlast line\r\n')
    nntp_server.add_article('text@example.com', path, GROUP)

    sock = connect_to(nntp_server, join_group=True)
    assert sock.connect(timeout=5.0) is True
    article = sock.get(
        'text@example.com', work_dir=str(tmp_path / 'work'), group=GROUP)

    assert isinstance(article, NNTPArticle)
    body = article.body.getvalue()
    assert b'.hidden line\r\n' in body
    assert b'\r\n..\r\n' in body
    assert b'last line' in body
    sock.close()


#
# Posting
#
def test_posting_flow(nntp_server, tmp_path):
    """
    Posting sends POST, then the article, then the end of data marker
    """
    article = NNTPArticle(
        subject='Test Post',
        poster='tester <tester@example.com>',
        groups=GROUP,
        body='first line\r\n.\r\n.leading dot\r\nlast line\r\n',
        work_dir=str(tmp_path),
    )

    sock = connect_to(nntp_server, join_group=False)
    assert sock.connect(timeout=5.0) is True
    results = sock.post(article)
    sock.close()

    assert [r.code for r in results] == [240]
    assert nntp_server.wait_for('QUIT')
    assert nntp_server.commands() == LOGIN + ['POST', 'QUIT']

    # The article arrived whole; lines starting with a dot were stuffed
    # on the way over and were not mistaken for the end of the article
    assert len(nntp_server.posted) == 1
    posted = nntp_server.posted[0]
    assert b'Subject: Test Post\r\n' in posted
    assert b'Newsgroups: %s\r\n' % GROUP.encode() in posted
    assert b'\r\nfirst line\r\n.\r\n.leading dot\r\nlast line\r\n' in posted


def test_posting_binary_content(nntp_server, tmp_path):
    """
    yEnc encoded attachments are posted and come out the other end intact
    """
    from newsreap.codecs.CodecYenc import CodecYenc

    article = NNTPArticle(
        subject='Binary Post',
        poster='tester <tester@example.com>',
        groups=GROUP,
        work_dir=str(tmp_path),
    )
    article.add(CodecYenc(work_dir=str(tmp_path)).encode(
        join(VAR_PATH, 'joystick.jpg')))

    sock = connect_to(nntp_server, join_group=False)
    assert sock.connect(timeout=5.0) is True
    assert [r.code for r in sock.post(article)] == [240]
    sock.close()

    # Decode what the server received
    from io import BytesIO
    posted = BytesIO(nntp_server.posted[0])
    decoded = CodecYenc(work_dir=str(tmp_path)).decode(posted)
    with open(join(VAR_PATH, 'joystick.jpg'), 'rb') as fp:
        assert decoded.getvalue() == fp.read()


def test_posting_refused():
    """
    A server that won't take posts returns its refusal
    """
    with FakeNNTPServer(posting=False) as server:
        article = NNTPArticle(
            subject='Test Post', poster='tester@example.com',
            groups=GROUP, body='hello')

        sock = connect_to(server, join_group=False)
        assert sock.connect(timeout=5.0) is True
        results = sock.post(article)
        sock.close()

        assert [r.code for r in results] == [440]
        assert server.posted == []


#
# Searching by date
#
def test_seeking_by_date(nntp_server):
    """
    seek_by_date() finds the first article posted at (or after) a date,
    even when the group has gaps in its article numbers
    """
    import random
    from datetime import datetime
    from datetime import timedelta

    base = datetime(2020, 1, 1)
    group = 'alt.test.seek'

    # Article numbers with a big gap (expired articles) and random holes
    random.seed(1)
    numbers = sorted(n for n in random.sample(range(1, 20001), 3000)
                     if not 8000 <= n <= 11000)
    for n in numbers:
        nntp_server.add_overview(
            group, n, 'Subject %d' % n, 'poster@example.com',
            base + timedelta(minutes=n), '%d@example.com' % n)

    sock = connect_to(nntp_server, join_group=True)
    assert sock.connect(timeout=5.0) is True

    targets = [1, numbers[0], 8500, 11000, numbers[-1]] + \
        random.sample(range(1, numbers[-1] + 1), 20)
    for target in targets:
        expected = next(n for n in numbers if n >= target)
        found = sock.seek_by_date(
            base + timedelta(minutes=target, seconds=-30), group=group)
        assert found == expected, target
        assert sock.group_index == expected

    # A date after every article leaves us at the start of the group
    # Nothing posted that late points just past the last article
    assert sock.seek_by_date(
        base + timedelta(days=365), group=group) == numbers[-1] + 1

    # Each search only takes a handful of (small) requests
    xovers = [c for c in nntp_server.commands() if c.startswith('XOVER')]
    assert len(xovers) / (len(targets) + 1) < 25
    sock.close()


def test_overview_listing(nntp_server):
    """
    xover() returns the (date sorted) overview entries of a range
    """
    from datetime import datetime
    from datetime import timedelta
    from newsreap.NNTPConnection import XoverGrouping

    group = 'alt.test.xover'
    base = datetime(2020, 1, 1)
    for n in range(1, 31):
        nntp_server.add_overview(
            group, n, '"file.%02d" yEnc (1/1)' % n, 'poster@example.com',
            # Posted out of order (by number)
            base - timedelta(minutes=n), '%d@example.com' % n)

    # Some garbage that a server might send our way
    nntp_server.overview[group].append((30, '30\tbroken\tentry'))

    sock = connect_to(nntp_server, join_group=True)
    assert sock.connect(timeout=5.0) is True
    assert sock.group(group)[0] == 30

    entries = sock.xover(start=1, end=30, sort=XoverGrouping.BY_TIME)
    assert len(entries) == 30
    numbers = [e['article_no'] for e in entries.values()]
    assert numbers == list(range(30, 0, -1))

    entry = entries.values()[0]
    assert entry['id'] == '30@example.com'
    assert entry['subject'] == '"file.30" yEnc (1/1)'
    assert entry['size'] == 1000
    assert entry['lines'] == 10
    sock.close()


def test_empty_overview_range(nntp_server):
    """
    A range with no articles (423) is empty rather than an error
    """
    nntp_server.add_group(GROUP, 10, 1, 10)
    nntp_server.on(r'^XOVER', '423 No articles in that range')

    sock = connect_to(nntp_server, join_group=True)
    assert sock.connect(timeout=5.0) is True
    entries = sock.xover(group=GROUP, start=1, end=10)
    assert entries is not None
    assert len(entries) == 0
    sock.close()
