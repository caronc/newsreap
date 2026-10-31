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
if 'threading' in sys.modules:
    #  gevent patching since pytests import
    #  the sys library before we do.
    del sys.modules['threading']

import gevent.monkey
gevent.monkey.patch_all()

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
