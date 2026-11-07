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


def test_text_is_read_in_whatever_character_set_it_uses():
    """
    UTF-8 is used whenever the text is UTF-8 (whatever the article says),
    otherwise the character set given, then cp1252, then latin-1
    """
    to_text = CodecAscii.to_text

    # Said and true
    assert to_text('caf\xe9'.encode('latin-1'), 'ISO-8859-1') == (
        'café', 'iso-8859-1')
    assert to_text('日本語'.encode('shift_jis'), 'Shift_JIS') == (
        '日本語', 'shift_jis')
    assert to_text('日本語'.encode('iso-2022-jp'), 'iso-2022-jp') == (
        '日本語', 'iso-2022-jp')

    # Said, but it is really UTF-8
    assert to_text('café'.encode(), 'iso-8859-1') == ('café', 'utf-8')
    assert to_text('café'.encode(), b'"latin1"') == ('café', 'utf-8')

    # Not said (or said wrong, or a name that isn't a character set)
    assert to_text(b'smart \x93quotes\x94') == ('smart “quotes”', 'cp1252')
    assert to_text('na\xefve'.encode('latin-1'), 'us-ascii') == (
        'naïve', 'cp1252')
    assert to_text(b'plain', 'x-not-a-charset') == ('plain', 'utf-8')
    assert to_text(b'\x81\x8d', None) == ('\x81\x8d', 'latin-1')
    assert to_text(b'', 'UTF-8') == ('', 'utf-8')
    assert to_text(b'', None) == ('', 'utf-8')


def test_text_mixing_character_sets_is_read_a_line_at_a_time():
    """
    A UTF-8 reply quoting latin-1 (or the other way around) comes out
    right on every line, whatever the article claims
    """
    mixed = 'Café — great\n'.encode() + b'> old caf\xe9 \x93quote\x94\n'
    expected = 'Café — great\n> old café “quote”\n'
    assert CodecAscii.to_text(mixed)[0] == expected
    assert CodecAscii.to_text(mixed, 'iso-8859-1')[0] == \
        'Café — great\n> old café \x93quote\x94\n'
    assert CodecAscii.to_text(mixed, 'us-ascii')[0] == expected


def test_byte_order_marks_and_dos_files_are_understood():
    """
    A byte order mark says which Unicode encoding is used, and .nfo files
    are read as DOS text (unless they are UTF-8)
    """
    to_text = CodecAscii.to_text
    assert to_text(b'\xef\xbb\xbfhello') == ('hello', 'utf-8')
    assert to_text('hello'.encode('utf-16')) == ('hello', 'utf-16')
    assert to_text('hello'.encode('utf-32')) == ('hello', 'utf-32')
    assert to_text('hello'.encode('utf-16-le'), 'UTF-16LE') == (
        'hello', 'utf-16le')

    art = '╔══╗ Release ║ café'
    assert to_text(art.encode('cp437'), filename='INFO.NFO') == (
        art, 'cp437')
    assert to_text(art.encode(), filename='info.nfo') == (art, 'utf-8')
    assert to_text(art.encode('cp437'))[0] != art


def test_text_is_tidied():
    """
    Line endings become '\\n' and NUL characters are removed
    """
    assert CodecAscii.to_text(b'a\x00b\r\nc\rd') == ('ab\nc\nd', 'utf-8')
    assert CodecAscii.charset_name('LATIN1') == 'iso8859-1'
    assert CodecAscii.charset_name(b'utf8') == 'utf-8'
    assert CodecAscii.charset_name(None) is None
    assert CodecAscii.charset_name('  ') is None


def test_decoded_text_is_read_with_its_character_set(tmp_path):
    """
    text() reads what the codec decoded with the character set given
    """
    codec = CodecAscii(work_dir=str(tmp_path), charset='latin-1')
    codec.decode(BytesIO(b'caf\xe9\n'))
    assert codec.text() == ('café\n', 'latin-1')
