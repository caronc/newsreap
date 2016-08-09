# -*- coding: utf-8 -*-
#
# A NNTP Binary File Representation
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

from newsreap.NNTPContent import NNTPContent

class NNTPBinaryContent(NNTPContent):
    """
    A Binary file representation
    """
    def __init__(self, filename=None, part=1, tmp_dir=None, *args, **kwargs):
        super(NNTPBinaryContent, self).__init__(
            filename=filename,
            part=part, tmp_dir=tmp_dir, sort_no=10000, *args, **kwargs)
