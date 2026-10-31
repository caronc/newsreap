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

import re

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


def config_for(server, path, **processing):
    """
    Writes a configuration (like `nr --init` does) that points at a
    FakeNNTPServer and returns its path.

    - path: the configuration file to write
    - processing: any processing settings to change (ie: threads=1)
    """
    from newsreap.NNTPSettings import create_config

    path = create_config(str(path))
    with open(path) as fp:
        content = fp.read()

    # Point our first server at the test server
    for old, new in (
            ('host: my.server.here', 'host: %s' % server.host),
            ('port: 563', 'port: %d' % server.port),
            ('username: myusername', 'username: valid'),
            ('password: mypassword', 'password: valid'),
            ('secure: True', 'secure: %s' % server.secure)):
        content = content.replace(old, new, 1)

    for key, value in processing.items():
        # Change a processing setting (or add it if it isn't there)
        setting = re.compile(r'^(\s+)%s:.*$' % key, re.MULTILINE)
        if setting.search(content):
            content = setting.sub(
                r'\g<1>%s: %s' % (key, value), content, count=1)

        else:
            content = content.replace(
                'processing:\n', 'processing:\n   %s: %s\n' % (key, value),
                1)

    with open(path, 'w') as fp:
        fp.write(content)

    return path
