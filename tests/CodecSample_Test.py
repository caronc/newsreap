# Test the sample codec (the template new codecs start from)
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


from io import BytesIO
from zlib import crc32

from newsreap.codecs.CodecSample import CodecSample


def test_lines_between_begin_and_end_are_decoded(tmp_path):
    """
    Only the text inside the block is kept, under the name it gives
    """
    codec = CodecSample(work_dir=str(tmp_path))
    stream = BytesIO(
        b'Some chatter first\r\n'
        b'=sample end\r\n'
        b'=sample begin notes.txt\r\n'
        b'line one\r\n'
        b'line two\r\n'
        b'=sample end\r\n'
        b'after it\r\n')

    content = codec.decode(stream)
    assert content.filename == 'notes.txt'
    assert content.getvalue() == b'line one\r\nline two\r\n'
    assert content.is_valid()
    assert codec.crc32() == '%08x' % crc32(content.getvalue())

    # What follows the block is left for the next reader
    assert stream.readline() == b'after it\r\n'


def test_a_block_cut_short_waits_for_more(tmp_path):
    """
    decode() returns True when the article ends before the end line
    """
    codec = CodecSample(work_dir=str(tmp_path))
    assert codec.decode(BytesIO(b'=sample begin a.txt\r\nhalf\r\n')) is True


def test_only_its_own_lines_are_detected(tmp_path):
    """
    detect() names begin and end lines, and leaves out-of-place ones alone
    """
    codec = CodecSample(work_dir=str(tmp_path))
    assert codec.detect('=sample begin a.txt') == {
        'key': 'begin', 'name': 'a.txt'}
    assert codec.detect('=sample end', relative=False) == {'key': 'end'}
    assert codec.detect('=sample end') is None
    assert codec.detect('begin 644 a.txt') is None
    assert repr(codec) == '<CodecSample lines_processed=0 />'


def test_encoding_and_decoding_give_the_text_back(tmp_path):
    """
    What encode() writes, decode() reads back the same
    """
    codec = CodecSample(work_dir=str(tmp_path))
    encoded = codec.encode(b'first\nsecond', name='notes.txt')
    assert encoded.getvalue() == (
        b'=sample begin notes.txt\r\nfirst\r\nsecond\r\n=sample end\r\n')

    codec.reset()
    decoded = codec.decode(BytesIO(encoded.getvalue()))
    assert decoded.getvalue() == b'first\r\nsecond\r\n'
    assert str(codec) == 'Undetermined.File'
