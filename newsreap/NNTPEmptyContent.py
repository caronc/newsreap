# -*- coding: utf-8 -*-
#
# A NNTP Empty File Representation
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

class NNTPEmptyContent(NNTPContent):
    """
    A Empty file representation; this is mostly used as a place holder that
    helps with sorting and/or validation by holding information that may have
    been extract from an nzbfile or other source.
    """
    def __init__(self, filepath=None, part=None, tmp_dir=None, size=0, *args, **kwargs):
        super(NNTPEmptyContent, self).__init__(
            filepath=filepath,
            part=part, tmp_dir=tmp_dir, sort_no=5000, *args, **kwargs)

        # Store size
        try:
            self.size = int(size)
            if self.size < 0:
                self.size = 0

        except (ValueError, TypeError):
            self.size = 0

    def open(self, *args, **kwargs):
        """
        You can't open an empty file, so we over-ride it
        """
        return False

    def __len__(self):
        """
        Returns the length of the content
        """
        return self.size
