# Test the ASCII Codec
#
# Copyright (C) 2026 Chris Caron <lead2gold@gmail.com>
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

import sys
if 'threading' in sys.modules:
    #  gevent patching since pytests import
    #  the sys library before we do.
    del sys.modules['threading']

import gevent.monkey
gevent.monkey.patch_all()

from os.path import dirname
from os.path import abspath
from os.path import join

try:
    from tests.TestBase import TestBase

except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

from io import BytesIO

from newsreap.codecs.CodecAscii import CodecAscii
from newsreap.NNTPAsciiContent import NNTPAsciiContent


class CodecAscii_Test(TestBase):
    """
    Tests the codec that stores plain (ASCII) article text
    """

    def test_text_lines_are_detected(self):
        """
        Printable text (bytes or str) is recognised
        """
        codec = CodecAscii(work_dir=self.tmp_dir)
        assert codec.detect(b'Hello World\r\n') == {}
        assert codec.detect('Hello World\n') == {}
        assert codec.detect(b'\ttabbed and \x1b escaped') == {}

    def test_binary_lines_are_ignored(self):
        """
        Lines holding control characters are treated as binary data
        """
        codec = CodecAscii(work_dir=self.tmp_dir)
        assert codec.detect(b'binary \x00 data') is None
        assert codec.detect(b'\x01\x02\x03') is None

    def test_lines_are_stored_with_crlf_endings(self):
        """
        Each decoded line is stored with a CRLF line ending
        """
        codec = CodecAscii(work_dir=self.tmp_dir)
        stream = BytesIO(b'first line\nsecond line  \n')

        assert codec.decode(stream) == len(b'first line\r\n')
        codec.decode(stream)

        # Nothing left to read
        assert codec.decode(stream) == len(b'first line\r\nsecond line\r\n')

        assert isinstance(codec.decoded, NNTPAsciiContent)
        assert codec.decoded.getvalue() == b'first line\r\nsecond line\r\n'
        assert str(codec.decoded) == 'first line\r\nsecond line\r\n'
