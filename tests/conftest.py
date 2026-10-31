# Shared pytest fixtures
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

# gevent must patch the standard library before anything else uses it
import gevent.monkey
gevent.monkey.patch_all()

import pytest

from tests.FakeNNTPServer import FakeNNTPServer


def connect_to(server, **kwargs):
    """
    Returns an NNTPConnection pointed at a FakeNNTPServer.

    - Logs in with the server's (valid) test account unless told otherwise.
    - Retries happen right away so tests don't sit waiting.
    """
    from newsreap.NNTPConnection import NNTPConnection

    options = {
        'host': server.host,
        'port': server.port,
        'username': 'valid',
        'password': 'valid',
        'secure': server.secure,
        'join_group': server.join_group,
        'retry_wait': 0,
    }
    options.update(kwargs)
    return NNTPConnection(**options)


@pytest.fixture
def nntp_server():
    """
    A running (plain text) FakeNNTPServer
    """
    with FakeNNTPServer(secure=False) as server:
        yield server


@pytest.fixture
def nntps_server():
    """
    A running (secure) FakeNNTPServer
    """
    with FakeNNTPServer(secure=True) as server:
        yield server
