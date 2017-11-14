# -*- coding: utf-8 -*-
#
# A NNTP Ascii File Representation
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

from os.path import exists
from newsreap.NNTPContent import NNTPContent
from newsreap.Utils import bytes_to_strsize


class NNTPAsciiContent(NNTPContent):
    """
    An Ascii file representation
    """
    def __init__(self, filepath=None, part=None, total_parts=None,
                 work_dir=None, sort_no=20000, *args, **kwargs):
        super(NNTPAsciiContent, self).__init__(
            filepath=filepath,
            part=part, total_parts=total_parts, work_dir=work_dir,
            sort_no=sort_no, *args, **kwargs)

    def __next__(self):
        """
        Python 3 support
        Support stream type functions and iterations
        """
        data = self.stream.readline()
        if not data:
            self.close()
            raise StopIteration()

        return data

    def can_post(self):
        """
        AsciiContent is postable as long as it exists
        """
        return (self._isdir is False) and exists(self.filepath)

    def next(self):
        """
        Python 2 support
        Support stream type functions and iterations
        """
        data = self.stream.readline()
        if not data:
            self.close()
            raise StopIteration()

        return data

    def __str__(self):
        """ Returns content """
        return self.getvalue()

    def __unicode__(self):
        """ Returns content """
        return unicode(self.getvalue())

    def __repr__(self):
        """
        Return a printable version of the file being read
        """
        if self.part is not None:
            return '<NNTPAsciiContent sort=%d filename="%s" part=%d/%d len=%s />' % (
                self.sort_no,
                self.filename,
                self.part,
                self.total_parts,
                bytes_to_strsize(len(self)),
            )
        else:
            return '<NNTPAsciiContent sort=%d filename="%s" len=%s />' % (
                self.sort_no,
                self.filename,
                bytes_to_strsize(len(self)),
            )
