# A sorted set that keeps its entries ordered by an optional key function
#
# Copyright (C) 2015-2026 Chris Caron <lead2gold@gmail.com>
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

from collections.abc import MutableSet
from collections.abc import Set

from sortedcontainers import SortedKeyList


def _identity(value):
    return value


class sortedset(MutableSet):
    """
    A set that keeps its entries sorted and can be indexed like a list.

    - Entries are ordered by key(entry), or by the entry itself when no
      key is given.
    - An entry is a duplicate when it has the same sort key as an existing
      entry and compares equal to it (==). Entries do not need to be
      hashable, so dictionaries can be stored too.
    """

    def __init__(self, iterable=(), key=None):
        if key is not None and not callable(key):
            raise TypeError("'%s' object is not callable" % str(type(key)))

        self._key = key
        self._list = SortedKeyList(key=key if key else _identity)
        for value in iterable:
            self.add(value)

    @property
    def key(self):
        return self._key

    def _from_iterable(self, iterable):
        # Results of set operations (|, &, -, ^) keep our sort key
        return type(self)(iterable, key=self._key)

    def _find(self, value):
        """
        Returns the index of value, or -1 if it is not in the set
        """
        try:
            key = self._list.key(value)
            index = self._list.bisect_key_left(key)
            for entry in self._list.irange_key(key, key):
                if entry == value:
                    return index
                index += 1

        except TypeError:
            # The value can not be compared with what we already have
            pass

        return -1

    def __contains__(self, value):
        return self._find(value) >= 0

    def __len__(self):
        return len(self._list)

    def __iter__(self):
        return iter(self._list)

    def __reversed__(self):
        return reversed(self._list)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return self._from_iterable(self._list[index])
        return self._list[index]

    def __delitem__(self, index):
        del self._list[index]

    def add(self, value):
        """
        Adds value to the set; nothing happens if it is already present
        """
        if value not in self:
            self._list.add(value)

    def discard(self, value):
        """
        Removes value from the set if it is present
        """
        index = self._find(value)
        if index >= 0:
            del self._list[index]

    def remove(self, value):
        """
        Removes value from the set; raises KeyError if it is missing
        """
        index = self._find(value)
        if index < 0:
            raise KeyError(value)
        del self._list[index]

    def pop(self, index=-1):
        """
        Removes and returns the entry at index (the last one by default)
        """
        if not self._list:
            raise KeyError('pop from an empty set')
        return self._list.pop(index)

    def clear(self):
        self._list.clear()

    def index(self, value):
        index = self._find(value)
        if index < 0:
            raise ValueError('%r is not in set' % (value, ))
        return index

    def update(self, *iterables):
        for iterable in iterables:
            for value in iterable:
                self.add(value)

    def union(self, *iterables):
        result = self.copy()
        result.update(*iterables)
        return result

    def intersection(self, *iterables):
        result = self.copy()
        for iterable in iterables:
            result &= self._from_iterable(iterable)
        return result

    def difference(self, *iterables):
        result = self.copy()
        for iterable in iterables:
            result -= self._from_iterable(iterable)
        return result

    def copy(self):
        return self._from_iterable(self._list)

    __copy__ = copy

    def __eq__(self, other):
        if not isinstance(other, Set):
            return NotImplemented
        return len(self) == len(other) and all(v in self for v in other)

    def __ne__(self, other):
        result = self.__eq__(other)
        return result if result is NotImplemented else not result

    # Mutable sets can not be hashed
    __hash__ = None

    def __repr__(self):
        if not self:
            return 'sortedset()'
        return 'sortedset(%r)' % (list(self), )
