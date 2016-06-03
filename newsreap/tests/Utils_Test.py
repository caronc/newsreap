# -*- encoding: utf-8 -*-
#
# A base testing class/library to test the Utils functions
#
# Copyright (C) 2015 Chris Caron <lead2gold@gmail.com>
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

from os.path import abspath
from os.path import dirname

try:
    from tests.TestBase import TestBase

except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

from lib.Utils import strsize_to_bytes
from lib.Utils import bytes_to_strsize


class NNTPConnection_Test(TestBase):

    def test_strsize_n_bytes(self):
        # Garbage Entry
        assert strsize_to_bytes("0J") == 0
        assert strsize_to_bytes("") == 0
        assert strsize_to_bytes("totalgarbage") == 0

        # Good Entries
        assert strsize_to_bytes("0B") == 0
        assert strsize_to_bytes("0") == 0
        assert strsize_to_bytes("10") == 10
        assert strsize_to_bytes("1K") == 1024
        assert strsize_to_bytes("1M") == 1024*1024
        assert strsize_to_bytes("1G") == 1024*1024*1024
        assert strsize_to_bytes("1T") == 1024*1024*1024*1024


        # Garbage Entry
        assert bytes_to_strsize('') == "0.00B"
        assert bytes_to_strsize('GARBAGE') == "0.00B"

        # Good Entries
        assert bytes_to_strsize(0) == "0.00B"
        assert bytes_to_strsize(1) == "1.00B"
        assert bytes_to_strsize(1024) == "1.00KB"
        assert bytes_to_strsize(1024*1024) == "1.00MB"
        assert bytes_to_strsize(1024*1024*1024) == "1.00GB"
        assert bytes_to_strsize(1024*1024*1024*1024) == "1.00TB"
