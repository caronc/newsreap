# -*- coding: utf-8 -*-
#
# A NNTP Binary File Representation
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
from newsreap.Utils import bytes_to_strsize

class NNTPBinaryContent(NNTPContent):
    """
    A Binary file representation
    """
    def __init__(self, filepath=None, part=None, total_parts=None,
                 begin=None, end=None, total_size=None,
                 work_dir=None, *args, **kwargs):
        """ Intitialize NNTPBinaryContent
        """

        super(NNTPBinaryContent, self).__init__(
            filepath=filepath,
            part=part, total_parts=total_parts,
            begin=begin, end=end, total_size=total_size,
            work_dir=work_dir,
            sort_no=10000, *args, **kwargs)

    def __repr__(self):
        """
        Return a printable version of the file being read
        """
        if self.part is not None:
            return '<NNTPBinaryContent sort=%d filename="%s" part=%d/%d len=%s />' % (
                self.sort_no,
                self.filename,
                self.part,
                self.total_parts,
                bytes_to_strsize(len(self)),
            )
        else:
            return '<NNTPBinaryContent sort=%d filename="%s" len=%s />' % (
                self.sort_no,
                self.filename,
                bytes_to_strsize(len(self)),
            )
