# Test replies bigger than what is read at a time, and odd status lines
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

import os

import pytest

from newsreap.NNTPConnection import NNTPConnection
from newsreap.codecs.CodecYenc import CodecYenc
from tests.FakeNNTPServer import FakeNNTPServer
from tests.FakeNNTPServer import Reply

# A listing far bigger than what we read at a time (in our tests)
LINES = ['%05d-%s' % (no, os.urandom(27).hex()) for no in range(5000)]
LISTING = ''.join('%s\r\n' % line for line in LINES)


@pytest.fixture
def connect(tmp_path):
    """
    Yields (server, connect) where connect() gives a connection to it
    """
    with FakeNNTPServer(join_group=False) as server:
        made = []

        def connect():
            connection = NNTPConnection(
                host=server.host, port=server.port, username='valid',
                password='valid', secure=False, join_group=False,
                work_dir=str(tmp_path), retries=0)
            assert connection.connect(timeout=5.0)
            made.append(connection)
            return connection

        yield server, connect
        for connection in made:
            connection.close()


@pytest.mark.parametrize('compress', [False, True])
def test_replies_bigger_than_a_read_come_back_whole(connect, compress):
    """
    A reply many times what is read at a time comes back exactly as sent,
    compressed or not (nothing lost or repeated where the reads meet)
    """
    server, connect = connect
    server.on(r'^LIST BIG', lambda s, m: Reply(
        '215 big list', body=LISTING, compress=compress and s.compress))

    connection = connect()
    connection.MAX_BUFFER_SIZE = 65536
    response = connection.send('LIST BIG')
    assert response.code == 215
    assert response.body.getvalue().decode().split() == LINES


def test_a_short_read_doesnt_shrink_the_next_ones(connect):
    """
    Peeking at the start of an article (max_bytes) only reads less for that
    reply; the connection reads as much as ever afterwards
    """
    server, connect = connect
    server.on(r'^LIST BIG', Reply('215 big list', body=LISTING))

    connection = connect()
    before = connection.MAX_BUFFER_SIZE
    connection.send(
        'LIST BIG', decoders=[CodecYenc(work_dir=connection.work_dir,
                                        max_bytes=100)])
    assert connection.MAX_BUFFER_SIZE == before


def test_a_status_line_split_over_reads_is_read_whole(connect):
    """
    A status line that arrives a little at a time is read once it is all
    there, and a code without any text is fine too
    """
    server, connect = connect
    server.on(r'^LIST SLOW', Reply(
        '215 list follows', body='a.group 2 1 y\r\n', chunk_size=5,
        delay=0.05))
    server.on(r'^DATE', Reply(raw=b'111\r\n'))

    connection = connect()
    response = connection.send('LIST SLOW')
    assert (response.code, response.code_str) == (215, 'list follows')
    assert response.body.getvalue().strip() == b'a.group 2 1 y'

    response = connection.send('DATE')
    assert (response.code, response.code_str) == (111, '')
