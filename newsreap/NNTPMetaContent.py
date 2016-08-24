# -*- coding: utf-8 -*-
#
# A NNTP Meta Information Representation
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

from newsreap.NNTPContent import NNTPContent


class NNTPMetaContent(NNTPContent):
    """
    An NNTP Meta representation
    """
    def __init__(self, filepath=None, part=0, work_dir=None, sort_no=2500,
                 *args, **kwargs):

        super(NNTPMetaContent, self).__init__(
            filepath=filepath,
            part=part,
            work_dir=work_dir, sort_no=sort_no, *args, **kwargs)

        self.content = list()
        self._iter = None

    def key(self):
        """
        Returns a key that can be used for sorting with:
            lambda x : x.key()
        """
        return '%.5d/MetaContent/%d' % (self.sort_no, id(self))

    def next(self):
        """
        Python 2 support
        Support stream type functions and iterations
        """
        if not self._iter:
            self._iter = iter(self.content)

        return next(self._iter)

    def __next__(self):
        """
        Python 3 support
        Support iterating through list
        """
        if not self._iter:
            self._iter = iter(self.content)

        return next(self._iter)

    def __iter__(self):
        """
        Mimic iter()
        """
        return iter(self.content)

    def __delitem__(self, key):
        """
        Mimic del action
        """
        del self.content[key]

    def __contains__(self, item):
        """
        Mimic 'in' directive
        """
        return item in self.content

    def __len__(self):
        """
        Support len() call
        """
        return len(self.content)

    def __repr__(self):
        """
        Keep it only possible to track 1 header
        """
        return '<NNTPMetaContent sort=%d content="%s" />' % (
            self.sort_no, repr(self.content),
        )
