# -*- coding: utf-8 -*-
#
# A NNTP Article Header Representation
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
import re
from newsreap.NNTPMetaContent import NNTPMetaContent

class NNTPHeader(NNTPMetaContent):
    """
    A Header representation of an NNTP Article
    """
    def __init__(self, work_dir=None, *args, **kwargs):
        super(NNTPHeader, self).__init__(
            work_dir=work_dir, sort_no=100, *args, **kwargs)

        # Initialize our header
        self.content = dict()

    def __setitem__(self, key, item):
        """
        Mimic Dictionary:  dict[key] = value
        """
        key = self.__fmt_key(key)
        self.content[key] = item

    def __getitem__(self, key):
        """
        Mimic Dictionary:  value = dict[key]
        """
        key = self.__fmt_key(key)
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

    def has_key(self, key):
        """
        Mimic Dictionary:  dict.has_key(key)
        """
        key = self.__fmt_key(key)
        return self.content.has_key(key)

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

    def pop(self, key, d=None):
        """
        Mimic Dictionary:  dict.pop(key, default)
        """
        key = self.__fmt_key(key)
        return self.content.pop(key, d)

    def __fmt_key(self, key):
        """Formats the hash key for more consistent hits; hence fetching the
        'Message-ID' key should still be fetched even if the user indexes
        with 'message-id'.
        """
        def _fmt(_k):
            return _k.group(1) + _k.group(2).upper()

        return re.sub(
            # Flip -id to ID (short for Identifier)
            # Flip -crc to CRC (short for Cyclic Redundancy Check)
            r'([_-])((id|crc)([^a-z0-9]|$))',
            _fmt,
            re.sub(r'(^|\s|[_-])(\S)', _fmt, key.strip().lower()),
            flags=re.IGNORECASE,
        )

    def __len__(self):
        """
        Returns the number of header entries
        """
        return len(self.content)

    def __delitem__(self, key):
        """
        allows the deletion of header keys
        """
        key = self.__fmt_key(key)
        del self.content[key]

    def __repr__(self):
        """
        Keep it only possible to track 1 header
        """
        return '<NNTPHeader sort=%d content="%s" />' % (
            self.sort_no, repr(self.content),
        )
