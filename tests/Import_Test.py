# Using NewsReap as a library
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

import json
import socket
import sys
from os.path import abspath
from os.path import dirname

from newsreap.SocketBase import SocketBase
from newsreap.SocketBase import SOCKET_TIMEOUT
from tests.conftest import run_program

# Imports every module NewsReap has (the nr command and its plugins too),
# then says what was changed for the program as a whole
EVERYTHING = '''
import importlib, json, pkgutil, socket, sys
sys.path.insert(0, %r)
import gevent.monkey
import newsreap
for found in pkgutil.walk_packages(newsreap.__path__, 'newsreap.'):
    importlib.import_module(found.name)
import newsreap.cli
print(json.dumps({
    'patched': sorted(gevent.monkey.saved),
    'timeout': socket.getdefaulttimeout(),
}))
'''


def test_importing_newsreap_leaves_the_program_alone():
    """
    Importing NewsReap changes nothing for the program using it:

    - gevent never patches the standard library
    - the default timeout of every socket is left alone
    """
    root = dirname(dirname(abspath(__file__)))
    result = run_program(
        [sys.executable, '-I', '-c', EVERYTHING % root], check=True)
    assert json.loads(result.stdout) == {'patched': [], 'timeout': None}


def test_our_sockets_get_their_own_timeout():
    """
    Our sockets wait SOCKET_TIMEOUT seconds unless told otherwise, without
    it being the default for the whole program
    """
    sock = SocketBase(host='localhost', port=119)
    try:
        assert sock.bind() is True
        assert sock.socket.gettimeout() == SOCKET_TIMEOUT
        assert socket.getdefaulttimeout() is None

        sock.close()
        assert sock.bind(timeout=3.0) is True
        assert sock.socket.gettimeout() == 3.0

    finally:
        sock.close()
