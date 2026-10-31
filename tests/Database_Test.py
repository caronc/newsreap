# Test the Database Object
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
from newsreap.NNTPGroupDatabase import NNTPGroupDatabase


class Database_Test(TestBase):
    """
    Tests the Database base object shared by all of the NewsReap databases
    """

    def test_values_are_stored_and_read_back(self):
        """
        set() saves a value that get() returns
        """
        db = NNTPGroupDatabase(
            engine='sqlite:///%s' % join(self.tmp_dir, 'values.db'),
            reset=True)

        assert db.get('missing', default='nope') == 'nope'
        db.set('colour', 'blue')
        assert db.get('colour') == 'blue'
        db.set('colour', 'green')
        assert db.get('colour') == 'green'

        # Setting a value to None removes it
        db.set('colour', None)
        assert db.get('colour') is None
        db.close()

    def test_closing_one_database_leaves_others_open(self):
        """
        Closing a database only closes its own session
        """
        db1 = NNTPGroupDatabase(
            engine='sqlite:///%s' % join(self.tmp_dir, 'db1.db'),
            reset=True)
        db2 = NNTPGroupDatabase(
            engine='sqlite:///%s' % join(self.tmp_dir, 'db2.db'),
            reset=True)

        db1.set('name', 'one')
        db2.set('name', 'two')

        # Each database keeps its own values
        assert db1.get('name') == 'one'
        assert db2.get('name') == 'two'

        # Load an entry through our second database
        session = db2.session()
        entry = session.query(db2.Vsp).filter_by(key='name').first()
        assert entry in session

        db1.close()

        # Our second database (and what it loaded) is untouched
        assert entry in session
        assert db2.get('name') == 'two'
        db2.close()
