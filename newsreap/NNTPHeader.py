# -*- coding: utf-8 -*-
#
# A NNTP Article Header Representation
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

from newsreap.NNTPMetaContent import NNTPMetaContent

class NNTPHeader(NNTPMetaContent):
    """
    A Header representation of an NNTP Article
    """
    def __init__(self, tmp_dir=None, *args, **kwargs):
        super(NNTPHeader, self).__init__(
            tmp_dir=tmp_dir, sort_no=100, *args, **kwargs)

        # Initialize our header
        self.content = dict()


    def __setitem__(self, key, item):
        """
        Mimic Dictionary:  dict[key] = value
        """
        self.content[key] = item


    def __getitem__(self, key):
        """
        Mimic Dictionary:  value = dict[key]
        """
        return self.content[key]


    def clear(self):
        """
        Mimic Dictionary:  dict.clear()
        """
        return self.content.clear()


    def copy(self):
        """
        Mimic Dictionary:  dict.copy()
        """
        return self.content.copy()


    def has_key(self, k):
        """
        Mimic Dictionary:  dict.has_key(key)
        """
        return self.content.has_key(k)


    def update(self, *args, **kwargs):
        """
        Mimic Dictionary:  dict.has_key(key)
        """
        return self.content.update(*args, **kwargs)


    def keys(self):
        """
        Mimic Dictionary:  dict.keys()
        """
        return self.content.keys()


    def iterkeys(self):
        """
        Mimic Dictionary:  dict.iterkeys()
        """
        return self.content.iterkeys()


    def itervalues(self):
        """
        Mimic Dictionary:  dict.itervalues()
        """
        return self.content.itervalues()


    def key(self):
        """
        Returns a key that can be used for sorting with:
            lambda x : x.key()
        """
        return '%.5d/Header/' % self.sort_no


    def values(self):
        """
        Mimic Dictionary:  dict.values()
        """
        return self.content.values()


    def items(self):
        """
        Mimic Dictionary:  dict.items()
        """
        return self.content.items()


    def pop(self, k, d=None):
        """
        Mimic Dictionary:  dict.pop(key, default)
        """
        return self.content.pop(k, d)


    def __repr__(self):
        """
        Keep it only possible to track 1 header
        """
        return '<NNTPHeader sort=%d content="%s" />' % (
            self.sort_no, repr(self.content),
        )
