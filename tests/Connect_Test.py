# Test connecting to a server that isn't there
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


import socket

import gevent

from newsreap.NNTPConnection import NNTPConnection


def test_a_server_that_is_down_is_given_up_on(tmp_path, monkeypatch):
    """
    Connecting to a port nothing listens on gives up (instead of trying
    forever), and the default timeout of other sockets is left alone
    """
    monkeypatch.setattr('newsreap.SocketBase.DEFAULT_CONNECT_TIMEOUT', 2.0)
    before = socket.getdefaulttimeout()

    # A port nothing listens on
    probe = socket.socket()
    probe.bind(('127.0.0.1', 0))
    port = probe.getsockname()[1]
    probe.close()

    connection = NNTPConnection(
        host='127.0.0.1', port=port, username='u', password='p',
        secure=False, work_dir=str(tmp_path), retries=0)
    attempt = gevent.spawn(connection.send, 'DATE')
    attempt.join(timeout=15)
    assert attempt.ready(), 'still trying after 15 seconds'
    assert socket.getdefaulttimeout() == before
    connection.close()
