# Test the ZIP codec
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
import shutil
import struct
import zipfile

import pytest

from newsreap.NNTPBinaryContent import NNTPBinaryContent
from newsreap.SortedSet import sortedset
from newsreap.codecs.Codec7Zip import DEFAULT_PATH as SEVEN_ZIP
from newsreap.codecs.CodecZip import CodecZip
from newsreap.codecs.CodecZip import ZIP_FILE_RE
from tests.conftest import run_program

# The zip tool (to make password protected ZIP files)
ZIP_TOOL = shutil.which('zip')


def files_in(path):
    """
    Returns {relative path: bytes} for every file under a folder
    """
    found = {}
    for root, _, files in os.walk(path):
        for name in files:
            full = os.path.join(root, name)
            with open(full, 'rb') as fp:
                found[os.path.relpath(full, path)] = fp.read()
    return found


@pytest.fixture
def source(tmp_path):
    """
    A folder holding a few files (one in a folder of its own)
    """
    path = tmp_path / 'release'
    (path / 'extras').mkdir(parents=True)
    (path / 'movie.mkv').write_bytes(os.urandom(200000))
    (path / 'info.nfo').write_bytes(b'Release notes\r\n')
    (path / 'extras' / 'sample.mkv').write_bytes(os.urandom(5000))
    return path


def test_zip_files_are_recognised():
    """
    ZIP files are told apart by their name
    """
    assert ZIP_FILE_RE.match('/path/to/test.zip')
    assert ZIP_FILE_RE.match('TEST.ZIP')
    assert not ZIP_FILE_RE.match('/path/to/test.zip.001')
    assert not ZIP_FILE_RE.match('.zip')


def test_folders_are_zipped_and_extracted(tmp_path, source):
    """
    A folder (and a file on its own) zipped and extracted again comes back
    the same, folders included
    """
    codec = CodecZip(work_dir=str(tmp_path / 'work'))
    loose = tmp_path / 'readme.txt'
    loose.write_bytes(b'read me')

    encoded = codec.encode([str(source), str(loose)], name='release')
    assert isinstance(encoded, sortedset)
    assert len(encoded) == 1
    assert isinstance(encoded[0], NNTPBinaryContent)
    assert encoded[0].is_attached()
    assert encoded[0].filename == 'release.zip'

    # Nothing left to add
    assert len(codec) == 0
    assert codec.test(encoded[0].path()) is True

    decoded = CodecZip(work_dir=str(tmp_path / 'work')).decode(
        encoded[0].path())
    found = files_in(decoded[0].path())
    expected = {
        os.path.join('release', k): v for k, v in files_in(source).items()}
    expected['readme.txt'] = b'read me'
    assert found == expected


def test_nothing_is_done_without_files(tmp_path):
    """
    Encoding or decoding nothing (or a file that isn't there) returns None
    """
    codec = CodecZip(work_dir=str(tmp_path))
    assert codec.encode() is None
    assert codec.decode() is None
    assert codec.add(str(tmp_path / 'missing.zip')) is False

    with pytest.raises(AttributeError):
        codec.test()

    with pytest.raises(AttributeError):
        CodecZip(work_dir=str(tmp_path), max_size='lots')


def test_passwords_can_not_be_used_to_zip(tmp_path, source):
    """
    Python can't write password protected ZIP files; it says so instead
    of making one without the password
    """
    codec = CodecZip(work_dir=str(tmp_path), password='secret')
    assert codec.encode(str(source)) is None


def test_files_never_land_outside_their_folder(tmp_path):
    """
    Names leading out of the folder (or to a drive) and links are left out;
    the rest are extracted
    """
    path = tmp_path / 'evil.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('../escaped.txt', 'x')
        archive.writestr('/absolute.txt', 'x')
        archive.writestr('C:/windows.txt', 'x')
        archive.writestr('a/../../up.txt', 'x')
        archive.writestr('ok/fine.txt', 'fine')

        link = zipfile.ZipInfo('link')
        link.external_attr = 0o120777 << 16
        archive.writestr(link, '/etc/passwd')

    decoded = CodecZip(work_dir=str(tmp_path / 'work')).decode(str(path))
    assert files_in(decoded[0].path()) == {'ok/fine.txt': b'fine'}
    assert not (tmp_path / 'escaped.txt').exists()
    assert not (tmp_path / 'work' / 'escaped.txt').exists()

    assert CodecZip.safe_path('a/./b') == 'a/b'
    assert CodecZip.safe_path('a\\b') == 'a/b'
    assert CodecZip.safe_path('..') is None
    assert CodecZip.safe_path('') is None


def test_damaged_zip_files_are_refused(tmp_path, source):
    """
    A ZIP file cut short, one that isn't a ZIP file, and one whose file
    fails its checksum are all refused
    """
    encoded = CodecZip(work_dir=str(tmp_path)).encode(
        str(source / 'movie.mkv'))
    with open(encoded[0].path(), 'rb') as fp:
        data = fp.read()

    cut = tmp_path / 'cut.zip'
    cut.write_bytes(data[:len(data) // 2])
    assert CodecZip(work_dir=str(tmp_path)).decode(str(cut)) is None
    assert CodecZip(work_dir=str(tmp_path)).test(str(cut)) is False

    junk = tmp_path / 'junk.zip'
    junk.write_bytes(b'not a zip file at all')
    assert CodecZip(work_dir=str(tmp_path)).decode(str(junk)) is None

    # One byte of the compressed file changed
    damaged = bytearray(data)
    damaged[len(damaged) // 3] ^= 0xFF
    bad = tmp_path / 'bad.zip'
    bad.write_bytes(bytes(damaged))
    assert CodecZip(work_dir=str(tmp_path)).decode(str(bad)) is None
    assert CodecZip(work_dir=str(tmp_path)).test(str(bad)) is False


def test_files_bigger_than_they_say_are_refused(tmp_path):
    """
    A file holding more than the ZIP file says it does (a "zip bomb"), or
    more than max_size in all, is refused
    """
    path = tmp_path / 'bomb.zip'
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_STORED) as archive:
        archive.writestr('big.bin', b'A' * 1000)

    # Say the file is only 10 bytes (in both places a ZIP file says it)
    data = bytearray(path.read_bytes())
    local = data.find(b'PK\x03\x04')
    struct.pack_into('<I', data, local + 22, 10)
    central = data.find(b'PK\x01\x02')
    struct.pack_into('<I', data, central + 24, 10)
    path.write_bytes(bytes(data))
    assert CodecZip(work_dir=str(tmp_path)).decode(str(path)) is None

    # Too much in all
    path = tmp_path / 'large.zip'
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('a.bin', b'A' * 4000)
        archive.writestr('b.bin', b'B' * 4000)

    assert CodecZip(work_dir=str(tmp_path), max_size='6KB').decode(
        str(path)) is None
    assert CodecZip(work_dir=str(tmp_path), max_size='10KB').decode(
        str(path)) is not None


def test_files_named_twice_keep_the_first(tmp_path):
    """
    A name in a ZIP file twice keeps the first one (unless overwrite is
    set)
    """
    path = tmp_path / 'twice.zip'
    with zipfile.ZipFile(path, 'w') as archive, \
            pytest.warns(UserWarning, match='Duplicate name'):
        archive.writestr('a.txt', 'first')
        archive.writestr('a.txt', 'second')

    decoded = CodecZip(work_dir=str(tmp_path)).decode(str(path))
    assert files_in(decoded[0].path()) == {'a.txt': b'first'}

    decoded = CodecZip(work_dir=str(tmp_path), overwrite=True).decode(
        str(path))
    assert files_in(decoded[0].path()) == {'a.txt': b'second'}


@pytest.mark.skipif(ZIP_TOOL is None, reason='zip is not installed')
def test_password_protected_zip_files_are_extracted(tmp_path, source):
    """
    A password protected ZIP file is extracted with its password, and
    refused without it (or with the wrong one)
    """
    path = tmp_path / 'locked.zip'
    run_program(
        [ZIP_TOOL, '-q', '-j', '-P', 'secret', str(path),
         str(source / 'info.nfo')], check=True)

    decoded = CodecZip(work_dir=str(tmp_path)).decode(
        str(path), password='secret')
    assert files_in(decoded[0].path()) == {'info.nfo': b'Release notes\r\n'}

    assert CodecZip(work_dir=str(tmp_path), password='secret').test(
        str(path)) is True

    assert CodecZip(work_dir=str(tmp_path)).decode(str(path)) is None
    assert CodecZip(work_dir=str(tmp_path)).decode(
        str(path), password='wrong') is None
    assert CodecZip(work_dir=str(tmp_path), password='wrong').test(
        str(path)) is False


@pytest.mark.skipif(not os.access(SEVEN_ZIP, os.X_OK),
                    reason='7-Zip is not installed')
def test_aes_zip_files_are_handed_to_7zip(tmp_path, source):
    """
    ZIP files encrypted with AES (which Python can't read) are extracted
    with 7-Zip
    """
    path = tmp_path / 'aes.zip'
    run_program(
        [SEVEN_ZIP, 'a', '-tzip', '-mem=AES256', '-psecret', '-y',
         str(path), str(source / 'info.nfo')], check=True)

    decoded = CodecZip(work_dir=str(tmp_path)).decode(
        str(path), password='secret')
    assert files_in(decoded[0].path()) == {'info.nfo': b'Release notes\r\n'}

    # Python can't check these
    assert CodecZip(work_dir=str(tmp_path)).test(
        str(path), password='secret') is False
