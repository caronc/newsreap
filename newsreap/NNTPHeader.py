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
from .NNTPSettings import NNTP_EOL
from .NNTPMetaContent import NNTPMetaContent

# When printing of the object is required, content is returned as
# it appears in this list (sequentially).  Anything not matched
# here is simply printed at the end.
HEADER_PRINT_SEQUENCE = (
    'Subject',
    'From',
    'Date',
    'Message-ID',
    'Lines',
    'Size',
    'Newsgroups',
    'Content-Type',
    'Mime-Version',
    'Content-Transfer-Encoding',

    # Defined X-Entries
    'X-Newsposter',
)

# By maintaining a list of headers we can set manually that are defined by the
# NNTP Spec, we can automatically stick X- infront of anything else so that
# they are valid as well.
VALID_HEADER_ENTRIES = (
    'Subject',
    'From',
    'Date',
    'Message-ID',
    'Lines',
    'Size',
    'Newsgroups',
    'Content-Type',
    'Mime-Version',
    'Content-Transfer-Encoding',
)

# What we stick infront of any entry that is not marked in the Valid Header
# list above
UNKNOWN_PREFIX = "X-"


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

    def post_iter(self):
        """
        Returns NNTP string as it would be required for posting
        """
        for k in HEADER_PRINT_SEQUENCE:
            if k in self.content:
                yield '%s: %s%s' % (k, self.content[k], NNTP_EOL)

        for k, v in self.content.iteritems():
            if k not in HEADER_PRINT_SEQUENCE:
                yield '%s: %s%s' % (k, v, NNTP_EOL)

        # Last entry
        yield NNTP_EOL

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

    def update(self, *args, **kwargs):
        """
        Mimic Dictionary:  dict.update(key)
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

    def iteritems(self):
        """
        Mimic Dictionary:  dict.items()
        """
        return self.content.iteritems()

    def pop(self, key, d=None):
        """
        Mimic Dictionary:  dict.pop(key, default)
        """
        key = self.__fmt_key(key)
        return self.content.pop(key, d)

    def str(self, delimiter=': ', eol='\r\n', count=0):
        """Returns a nice consistent message header enforcing header sequence.
        Anything not defined in the sequence is printed at the end.

        use count to specfy how may header lines to print; if you set this
        to zero, then all of the matched header lines are printed.

        This function attempts to honour print sequence allowing for a
        consistent output.
        """
        response = []
        for k in HEADER_PRINT_SEQUENCE:
            if k in self.content:
                response.append('%s%s%s' % (k, delimiter, self.content[k]))

        for k, v in self.content.iteritems():
            if k not in HEADER_PRINT_SEQUENCE:
                response.append('%s%s%s' % (k, delimiter, v))

        return eol.join(response)

    def __str__(self):
        """General str() default message format
        """
        return self.str()

    def __fmt_key(self, key):
        """Formats the hash key for more consistent hits; hence fetching the
        'Message-ID' key should still be fetched even if the user indexes
        with 'message-id'.
        """
        def _fmt(_k):
            return _k.group(1) + _k.group(2).upper()

        key = re.sub(
            # Flip -id to ID (short for Identifier)
            # Flip -crc to CRC (short for Cyclic Redundancy Check)
            r'([_-])((id|crc)([^a-z0-9]|$))',
            _fmt,
            re.sub(r'(^|\s|[_-])(\S)', _fmt, key.strip().lower()),
            flags=re.IGNORECASE,
        )
        if key in VALID_HEADER_ENTRIES or key.startswith(UNKNOWN_PREFIX):
            return key
        return UNKNOWN_PREFIX + key

    def __len__(self):
        """
        Returns the number of header entries
        """
        return len(self.content)

    def __contains__(self, key):
        """
        support 'in' keyword
        """
        key = self.__fmt_key(key)
        return key in self.content

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
