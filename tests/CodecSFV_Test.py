# Test the SFV codec (lists of files and their CRC32 checksums)
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


from importlib import import_module
from zlib import crc32

import pytest

from newsreap.NNTPAsciiContent import NNTPAsciiContent
from newsreap.codecs.CodecSFV import CodecSFV
from newsreap.codecs.CodecSFV import SFVStatus
from newsreap.codecs.CodecSFV import SFV_FILE_RE


def test_sfv_files_are_read():
    """
    Each file named is listed with its checksum; comments, blank lines and
    lines that aren't readable are left out
    """
    data = (
        b'; made by a tool\r\n'
        b'\r\n'
        b'release.part01.rar 1A2B3C4D\r\n'
        b'  "with spaces.rar"   0000ffff  \r\n'
        b'folder\\inner.r00 deadbeef\r\n'
        b'no checksum here\r\n'
        b'12345678\r\n'
        b'caf\xe9.nfo 01234567\r\n')
    assert CodecSFV.read(data) == {
        'release.part01.rar': '1a2b3c4d',
        'with spaces.rar': '0000ffff',
        'folder/inner.r00': 'deadbeef',
        'café.nfo': '01234567',
    }
    assert CodecSFV.read('') == {}
    assert CodecSFV(work_dir=None).decode(b'a.rar 00000001') == {
        'a.rar': '00000001'}

    assert SFV_FILE_RE.match('/path/to/release.SFV')
    assert not SFV_FILE_RE.match('release.sfv.txt')


def test_sfv_files_are_written_and_checked(tmp_path):
    """
    An SFV file written for some files lists their checksums, and checking
    it finds them all good; a damaged or missing file is reported
    """
    folder = tmp_path / 'release'
    (folder / 'sub').mkdir(parents=True)
    files = {
        'a.rar': b'first file' * 1000,
        'b.rar': b'second',
        'sub/c.r00': b'',
    }
    for name, data in files.items():
        (folder / name).write_bytes(data)

    codec = CodecSFV(work_dir=str(tmp_path / 'work'))
    encoded = codec.encode(str(folder), name='release')
    assert isinstance(encoded[0], NNTPAsciiContent)
    assert encoded[0].filename == 'release.sfv'
    assert encoded[0].is_attached()
    assert CodecSFV.read(encoded[0].getvalue()) == {
        name: '%08x' % crc32(data) for name, data in files.items()}

    sfv = folder / 'release.sfv'
    sfv.write_bytes(encoded[0].getvalue())
    assert codec.decode(str(sfv)) == CodecSFV.read(encoded[0].getvalue())
    assert codec.test(str(sfv)) is True
    assert set(codec.results.values()) == {SFVStatus.Ok}

    # Damaged, missing, and a name pointing out of the folder
    (folder / 'b.rar').write_bytes(b'Second')
    (folder / 'sub' / 'c.r00').unlink()
    with open(sfv, 'ab') as fp:
        fp.write(b'../../etc/passwd 00000000\r\n')

    assert codec.test(str(sfv)) is False
    assert codec.results == {
        'a.rar': SFVStatus.Ok,
        'b.rar': SFVStatus.Bad,
        'sub/c.r00': SFVStatus.Missing,
        '../../etc/passwd': SFVStatus.Missing,
    }


def test_unreadable_sfv_files_are_reported(tmp_path, monkeypatch):
    """
    Nothing to work with, a file that isn't there, a file too big to be an
    SFV file, and an SFV file listing nothing all fail
    """
    codec = CodecSFV(work_dir=str(tmp_path))
    assert codec.encode() is None
    assert codec.decode() is None

    with pytest.raises(AttributeError):
        codec.test()

    big = tmp_path / 'big.sfv'
    big.write_bytes(b'a.rar 00000000\n' * 10)
    monkeypatch.setattr(
        import_module('newsreap.codecs.CodecSFV'), 'MAX_SFV_SIZE', 50)
    assert codec.decode(str(big)) is None
    assert codec.test(str(big)) is False

    empty = tmp_path / 'empty.sfv'
    empty.write_bytes(b'; nothing\n')
    assert codec.test(str(empty)) is False

    assert CodecSFV.checksum(str(tmp_path / 'missing')) is None
