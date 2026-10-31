# Test the sortedset Object
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

from newsreap.SortedSet import sortedset


class SortedSet_Test(TestBase):
    """
    Tests the sortedset which keeps entries ordered by an optional key
    """

    def test_entries_are_kept_in_order(self):
        """
        Entries are sorted by value, or by key() when one is given
        """
        s = sortedset([3, 1, 2])
        assert list(s) == [1, 2, 3]
        assert s[0] == 1
        assert s[-1] == 3

        s = sortedset(['bb', 'a', 'ccc'], key=lambda x: -len(x))
        assert list(s) == ['ccc', 'bb', 'a']

    def test_duplicates_are_ignored(self):
        """
        An entry is a duplicate when its key matches and it compares equal
        """
        s = sortedset([1, 1, 2, 2])
        assert len(s) == 2

        # Same key but different values are both kept
        s = sortedset(key=len)
        s.add('ab')
        s.add('cd')
        s.add('ab')
        assert len(s) == 2
        assert 'ab' in s
        assert 'cd' in s
        assert 'ef' not in s

    def test_unhashable_entries_are_supported(self):
        """
        Dictionaries can be stored even though they can't be hashed
        """
        s = sortedset(key=lambda x: x['key'])
        s.add({'key': 2, 'value': 'b'})
        s.add({'key': 1, 'value': 'a'})
        s.add({'key': 1, 'value': 'a'})

        assert len(s) == 2
        assert [x['value'] for x in s] == ['a', 'b']
        assert {'key': 1, 'value': 'a'} in s

    def test_entries_can_be_removed(self):
        """
        remove(), discard(), pop() and del all take entries away
        """
        s = sortedset([1, 2, 3, 4])

        s.remove(2)
        assert list(s) == [1, 3, 4]

        try:
            s.remove(2)
            assert False

        except KeyError:
            pass

        # discard() never complains
        s.discard(2)
        s.discard(3)
        assert list(s) == [1, 4]

        assert s.pop() == 4
        assert s.pop(0) == 1
        assert len(s) == 0

        try:
            s.pop()
            assert False

        except KeyError:
            pass

        s = sortedset([1, 2, 3])
        del s[0]
        assert list(s) == [2, 3]
        assert s.index(3) == 1

        s.clear()
        assert len(s) == 0

    def test_set_operations_keep_the_sort_key(self):
        """
        Unions, differences and slices still sort using the same key
        """
        key = lambda x: -x
        a = sortedset([1, 2, 3], key=key)
        b = sortedset([3, 4], key=key)

        for result in (a | b, a.union(b), a[0:2], a - b, a & b, a.copy()):
            assert isinstance(result, sortedset)
            assert result.key is key

        assert list(a | b) == [4, 3, 2, 1]
        assert list(a - b) == [2, 1]
        assert list(a & b) == [3]
        assert list(a.difference(b)) == [2, 1]
        assert list(a.intersection(b)) == [3]
        assert list(a[0:2]) == [3, 2]

        # In place updates work with any iterable
        a |= set([9])
        a.update([0], (5, ))
        assert list(a) == [9, 5, 3, 2, 1, 0]

    def test_equality_with_other_sets(self):
        """
        A sortedset equals any set holding the same entries
        """
        assert sortedset([1, 2]) == set([2, 1])
        assert sortedset([1, 2]) == sortedset([2, 1])
        assert sortedset([1, 2]) != set([1])
        assert sortedset([1]) != [1]
        assert repr(sortedset()) == 'sortedset()'
        assert repr(sortedset([1])) == 'sortedset([1])'

    def test_bad_key_is_rejected(self):
        """
        The key must be callable
        """
        try:
            sortedset(key='not callable')
            assert False

        except TypeError:
            pass
