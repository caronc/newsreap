# Test the SocketBase Object
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

import sys
import pytest
from os.path import dirname
from os.path import abspath
from os.path import join

try:
    from tests.TestBase import TestBase

except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

from newsreap.SocketBase import SocketBase
from newsreap.HookManager import HookManager
from newsreap.decorators import hook


@hook(name='socket_read')
def read_hook(**kwargs):
    return 'read'


@hook(name='socket_write')
def write_hook(**kwargs):
    return 'write'


class SocketBase_Test(TestBase):
    """
    Tests the SocketBase object (without connecting anywhere)
    """

    def test_hooks_can_be_passed_in(self):
        """
        Hooks given when the socket is created are loaded
        """
        hooks = HookManager()
        hooks.add(read_hook)

        sock = SocketBase(hooks=hooks)
        results = sock.hooks.call('socket_read')
        assert [r['result'] for r in results] == ['read']

        # A plain function works too
        sock = SocketBase(hooks=write_hook)
        results = sock.hooks.call('socket_write')
        assert [r['result'] for r in results] == ['write']

    def test_hooks_can_be_replaced(self):
        """
        set_hooks() replaces (or adds to) the hooks already loaded
        """
        sock = SocketBase(hooks=read_hook)

        sock.set_hooks(write_hook)
        assert len(sock.hooks.call('socket_read')) == 0
        assert len(sock.hooks.call('socket_write')) == 1

        sock.set_hooks(read_hook, reset=False)
        assert len(sock.hooks.call('socket_read')) == 1
        assert len(sock.hooks.call('socket_write')) == 1

        # Handing a socket its own hooks leaves them alone
        sock.set_hooks(sock.hooks)
        assert len(sock.hooks.call('socket_read')) == 1
        assert len(sock.hooks.call('socket_write')) == 1

    def test_reads_and_writes_need_a_connection(self):
        """
        Nothing is read or sent without a connection
        """
        sock = SocketBase()
        assert sock.read() == b''
        assert sock.send('hello') == 0
        assert sock.close() == b''

    def test_printable_without_a_connection(self):
        """
        str() shows the host and port we were configured with
        """
        assert str(SocketBase(host='localhost', port=119)) == \
            'tcp://localhost:119'
        assert str(SocketBase(host='localhost', port=563, secure=True)) == \
            'tcps://localhost:563'


def test_system_certificates_are_used_by_default(monkeypatch, caplog):
    """
    Without a CA file, secure connections trust the certificates the system
    does (and certifi's); a server they don't vouch for is refused, saying
    why
    """
    import ssl
    from tests.FakeNNTPServer import FakeNNTPServer
    from newsreap.NNTPConnection import NNTPConnection

    loaded = []
    original = ssl.SSLContext.load_default_certs
    monkeypatch.setattr(
        ssl.SSLContext, 'load_default_certs',
        lambda self, *args: loaded.append('system') or original(self, *args))

    with FakeNNTPServer(secure=True) as server:
        sock = NNTPConnection(
            host=server.host, port=server.port, username='valid',
            password='valid', secure=True, join_group=False)
        assert sock.connect(timeout=5.0) is False
        assert loaded == ['system']
        assert 'certificate of 127.0.0.1 could not be verified' in caplog.text
        assert 'set ca_certs' in caplog.text


def test_certificates_can_be_trusted_or_not_checked():
    """
    A server's own (self signed) certificate is trusted when ca_certs points
    at it; verify_cert=False accepts any certificate; a CA file that doesn't
    exist is refused
    """
    from tests.FakeNNTPServer import CERT_FILE
    from tests.FakeNNTPServer import FakeNNTPServer
    from newsreap.NNTPConnection import NNTPConnection

    with FakeNNTPServer(secure=True) as server:
        def connection(**kwargs):
            return NNTPConnection(
                host=server.host, port=server.port, username='valid',
                password='valid', secure=True, join_group=False, **kwargs)

        sock = connection(ca_certs=CERT_FILE)
        assert sock.connect(timeout=5.0) is True
        sock.close()

        sock = connection(verify_cert=False)
        assert sock.connect(timeout=5.0) is True
        sock.close()

        sock = connection(ca_certs='/no/such/ca-bundle.crt')
        with pytest.raises(ValueError):
            sock.connect(timeout=5.0)
        sock.close()


def test_secure_connections_use_modern_tls():
    """
    TLS 1.2 or newer is used (never anything older), without compression
    """
    import ssl
    from tests.FakeNNTPServer import CERT_FILE
    from tests.FakeNNTPServer import FakeNNTPServer
    from newsreap.NNTPConnection import NNTPConnection

    with FakeNNTPServer(secure=True) as server:
        sock = NNTPConnection(
            host=server.host, port=server.port, username='valid',
            password='valid', secure=True, join_group=False,
            ca_certs=CERT_FILE)
        assert sock.connect(timeout=5.0) is True
        assert sock.socket.version() in ('TLSv1.2', 'TLSv1.3')
        assert sock.socket.compression() is None
        assert sock.socket.context.minimum_version == ssl.TLSVersion.TLSv1_2
        sock.close()


def test_every_address_of_a_host_is_tried():
    """
    A host name is looked up for all of its addresses (IPv4 and IPv6); each
    is tried until one answers, and an IPv6 address may be in brackets
    """
    from tests.FakeNNTPServer import CERT_FILE
    from tests.FakeNNTPServer import FakeNNTPServer
    from newsreap.NNTPConnection import NNTPConnection

    assert SocketBase(host='[::1]', port=119).host == '::1'

    with FakeNNTPServer(secure=True) as server:
        # localhost may answer on ::1 first, where nothing is listening
        sock = NNTPConnection(
            host='localhost', port=server.port, username='valid',
            password='valid', secure=True, join_group=False,
            ca_certs=CERT_FILE)
        assert sock.connect(timeout=5.0) is True
        assert sock.remote_connection_info() == ('127.0.0.1', server.port)
        sock.close()

    # A host that can't be found is refused (not retried)
    sock = NNTPConnection(
        host='no-such-host.invalid', port=119, username='valid',
        password='valid', secure=False, join_group=False)
    assert sock.connect(timeout=5.0) is False
