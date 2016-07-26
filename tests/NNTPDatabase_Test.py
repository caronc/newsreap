# -*- encoding: utf-8 -*-
#
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

import sys
if 'threading' in sys.modules:
    #  gevent patching since pytests import
    #  the sys library before we do.
    del sys.modules['threading']

import gevent.monkey
gevent.monkey.patch_all()

from os.path import dirname
from os.path import abspath

try:
    from tests.TestBase import TestBase

except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

from newsreap.NNTPDatabase import NNTPDatabase


class NNTPDatabase_Test(TestBase):
    """
    Tests NNTPDatabase
    """

    def test_database_reset(self):
        """
        Reset the database
        """
        db = NNTPDatabase()

        # Engine
        engine_1 = db.open(reset=True)

        # TODO Insert Something Here

        # Engine 2
        engine_2 = db.open()
        assert id(engine_1) == id(engine_2)

        # TODO: Check that item still exists

        #  Reset the database
        engine_3 = db.open(reset=True)

        # TODO: Check that item is lost

        # You only need to grab the engine the first time
        # subseqent calls will still have the returned object
        # be the same
        assert id(engine_2) == id(engine_3)
        assert id(engine_1) == id(engine_3)


if __name__ == '__main__':
    import unittest
    unittest.main()
