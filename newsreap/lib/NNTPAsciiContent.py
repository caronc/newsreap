# -*- coding: utf-8 -*-
#
# A NNTP Ascii File Representation
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

from NNTPContent import NNTPContent

class NNTPAsciiContent(NNTPContent):
    """
    An Ascii file representation
    """
    def __init__(self, filename=None, part=0, tmp_dir=None, *args, **kwargs):
        super(NNTPAsciiContent, self).__init__(
            filename=filename,
            part=part, tmp_dir=tmp_dir, sort_no=20000, *args, **kwargs)


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
