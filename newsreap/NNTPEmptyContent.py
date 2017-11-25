# -*- coding: utf-8 -*-
#
# A NNTP Empty File Representation
#
# Copyright (C) 2015-2017 Chris Caron <lead2gold@gmail.com>
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

from .NNTPContent import NNTPContent
from .Mime import Mime
from .Mime import MimeResponse
from .Utils import bytes_to_strsize


class NNTPEmptyContent(NNTPContent):
    """
    A Empty file representation; this is mostly used as a place holder that
    helps with sorting and/or validation by holding information that may have
    been extract from an nzbfile or other source.
    """
    def __init__(self, filepath=None, part=None, total_parts=None,
                 begin=None, end=None, total_size=None,
                 work_dir=None, *args, **kwargs):
        """ Intitialize NNTPEmptyContent
        """
        super(NNTPEmptyContent, self).__init__(
            filepath=filepath,
            part=part, total_parts=total_parts,
            begin=begin, end=end, total_size=total_size,
            work_dir=work_dir,
            sort_no=5000, *args, **kwargs)

    def getvalue(self):
        """
        Return an empty string
        """
        return ''

    def open(self, *args, **kwargs):
        """
        You can't open an empty file, so we over-ride it
        """
        return False

    def encode(self, *args, **kwargs):
        """
        Disable some commonly used functions when dealing with an EmptyContent
        object.
        """
        return False

    def load(self, *args, **kwargs):
        """
        Disable some commonly used functions when dealing with an EmptyContent
        object.
        """
        return False

    def copy(self, *args, **kwargs):
        """
        Disable some commonly used functions when dealing with an EmptyContent
        object.
        """
        return False

    def save(self, *args, **kwargs):
        """
        Disable some commonly used functions when dealing with an EmptyContent
        object.
        """
        return False

    def split(self, *args, **kwargs):
        """
        Disable some commonly used functions when dealing with an EmptyContent
        object.
        """
        return False

    def write(self, *args, **kwargs):
        """
        Disable some commonly used functions when dealing with an EmptyContent
        object.
        """
        return False

    def read(self, *args, **kwargs):
        """
        Disable some commonly used functions when dealing with an EmptyContent
        object.
        """
        return False

    def close(self, *args, **kwargs):
        """
        Disable some commonly used functions when dealing with an EmptyContent
        object.
        """
        return

    def append(self, *args, **kwargs):
        """
        Disable some commonly used functions when dealing with an EmptyContent
        object.
        """
        return False

    def next(self):
        """
        Python 2 support
        Support stream type functions and iterations
        Do nothing in an EmptyContent object
        """
        raise StopIteration()

    def mime(self):
        """
        Returns the mime of the object
            Source: https://github.com/ahupp/python-magic
        """

        # Initialize our Mime object
        m = Mime()

        # Try to detect by our filename
        mr = m.from_filename(
            self.filename if self.filename else self.filepath)

        if mr is None:
            # Return our type
            return MimeResponse()
        return mr

    def md5(self):
        """
        No MD5SUM Associated with EmptyContent
        """
        return None

    def sha1(self):
        """
        No SHA1 Associated with EmptyContent
        """
        return None

    def sha256(self):
        """
        No SHA256 Associated with EmptyContent
        """
        return None

    def tell(self):
        """
        always 0L
        """
        return 0L

    def readline(self, *args, **kwargs):
        """
        Never any data to return in an EmptyCoontent object
        """
        return ''

    def __next__(self):
        """
        Python 3 support
        Support stream type functions and iterations
        Do nothing in an EmptyContent object
        """
        raise StopIteration()

    def __iter__(self):
        """
        Grants usage of the next()
        """

        # Ensure our stream is open with read
        return self

    def __str__(self):
        """
        Return a printable version of the file being read
        """
        if self.part is not None:
            return '%s.%.5d' % (self.filename, self.part)

        return self.filename

    def __len__(self):
        """
        Returns the length of the content
        """
        return self._total_size

    def __repr__(self):
        """
        Return a printable version of this content object
        """
        if self.part is not None:
            return \
                '<NNTPEmptyContent sort=%d filename="%s" '\
                'part=%d/%d len=%s />' % (
                    self.sort_no,
                    self.filename,
                    self.part,
                    self.total_parts,
                    bytes_to_strsize(len(self))
                )
        else:
            return '<NNTPEmptyContent sort=%d filename="%s" len=%s />' % (
                self.sort_no,
                self.filename,
                bytes_to_strsize(len(self)),
            )
