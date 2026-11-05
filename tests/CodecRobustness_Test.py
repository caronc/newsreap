# Test that the file decoders cope with what servers really send
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

import os
from binascii import b2a_uu
from io import BytesIO

import pytest

from newsreap.codecs.CodecUU import CodecUU
from newsreap.codecs.CodecYenc import CodecYenc
from newsreap.codecs.CodecYenc import yenc_encode


def yenc(name, data=b'EVIL'):
    """
    Returns a one part yEnc article body naming the file given
    """
    encoded, _ = yenc_encode(data)
    return b'=ybegin line=128 size=%d name=%s\r\n%s\r\n=yend size=%d\r\n' % (
        len(data), name, encoded.rstrip(b'\r\n'), len(data))


@pytest.mark.parametrize('codec, body', [
    (CodecYenc, yenc(b'victim.txt')),
    (CodecUU, b'begin 644 victim.txt\r\n' + b2a_uu(b'EVIL') + b'`\r\nend\r\n'),
])
def test_a_file_already_there_is_never_written_to(
        tmp_path, monkeypatch, codec, body):
    """
    The name an article gives is only a name: what it holds is written in
    our work directory, never into a file of that name that is already in
    the directory we run from
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'victim.txt').write_bytes(b'IMPORTANT USER DATA\n')

    found = codec(work_dir=str(tmp_path / 'work')).decode(BytesIO(body))
    assert found.filename == 'victim.txt'
    assert found.getvalue() == b'EVIL'
    assert os.path.dirname(found.path()) != str(tmp_path)
    assert (tmp_path / 'victim.txt').read_bytes() == b'IMPORTANT USER DATA\n'


@pytest.mark.parametrize('name', [b'..', b'.', b'../../etc/passwd', b'a\\b'])
def test_names_that_are_folders_still_decode(tmp_path, name):
    """
    A name that is (or ends in) a folder doesn't stop the file decoding
    """
    found = CodecYenc(work_dir=str(tmp_path)).decode(BytesIO(yenc(name)))
    assert found.getvalue() == b'EVIL'
    assert '/' not in found.filename and found.filename not in ('.', '..')


@pytest.mark.parametrize('body', [
    b'=ypart begin=1 end=3\r\nabc\r\n=yend size=3\r\n',
    b'=ybegin line=128 size=3\r\nabc\r\n=yend size=3\r\n',
    b'=ybegin line=128 size=3\r\n=yend size=3 crc32=1\r\n',
    b'=yend size=3\r\n',
])
def test_broken_yenc_markers_dont_crash(tmp_path, body):
    """
    Markers out of order, or a begin without a name, are coped with
    """
    CodecYenc(work_dir=str(tmp_path)).decode(BytesIO(body))


@pytest.mark.parametrize('body', [
    b'`\r\nend\r\n',
    b'end\r\n',
    b'Begin the upload tomorrow, folks\r\nend of story\r\n',
])
def test_broken_uuencode_markers_dont_crash(tmp_path, body):
    """
    An end (or `) before any begin, and prose, are coped with
    """
    assert CodecUU(work_dir=str(tmp_path)).decode(BytesIO(body)) is not None


def test_yenc_attributes_can_come_in_any_order(tmp_path):
    """
    Posters write the attributes in any order, quote names, and use any
    case for the keywords
    """
    codec = CodecYenc(work_dir=str(tmp_path))
    found = codec.detect(
        b'=ybegin line=128 size=1000 part=1 total=5 name=x.bin')
    assert (found['key'], found['part'], found['total'], found['size'],
            found['name']) == ('begin', 1, 5, 1000, 'x.bin')

    assert codec.detect(b'=ybegin size=5 name="x y.bin"')['name'] == 'x y.bin'
    assert codec.detect(b'=yBEGIN size=5 name=x')['key'] == 'begin'
    assert codec.detect(b'=yend crc32=abcd1234 size=5 part=2',
                        relative=False) == {
        'key': 'end', 'crc32': 'abcd1234', 'size': 5, 'part': 2}


def test_uuencoding_with_a_big_buffer_works(tmp_path):
    """
    Asking for more than uuencode can take at a time is turned down to
    what it can, without failing
    """
    path = tmp_path / 'data.bin'
    path.write_bytes(b'x' * 200)
    encoded = CodecUU(work_dir=str(tmp_path)).encode(str(path), mem_buf=100)
    assert encoded is not None
