# Test downloading what an NZB file lists (nr get)
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

import gevent.monkey
gevent.monkey.patch_all()

import fcntl
import io
import os
import subprocess
import zipfile
from shutil import which
from zlib import crc32

import pytest
from click.testing import CliRunner

from newsreap.NNTPDownloader import NNTPDownloader
from newsreap.NNTPManager import NNTPManager
from newsreap.NNTPSettings import NNTPSettings
from newsreap.codecs.CodecYenc import yenc_encode

from tests.FakeNNTPServer import FakeNNTPServer
from tests.FakeNNTPServer import Reply
from tests.conftest import config_for

GROUP = 'alt.binaries.test'

# What our made up release holds
NOTES = b''.join(b'line %04d of the notes\n' % no for no in range(400))
README = b'Read me first.\n'


def yenc_article(name, data, part, total, begin, size, message_id,
                 damaged=False):
    """
    Returns one part of a file as an article (yEnc encoded)

    - begin: where the part starts in the file (from 0)
    - size: the whole file's size
    - damaged: give it the wrong crc32 (as a transfer that went wrong)
    """
    encoded, _ = yenc_encode(data)
    crc = crc32(data) ^ (1 if damaged else 0)
    return (
        b'Subject: "%s" yEnc (%d/%d)\r\nMessage-ID: <%s>\r\n\r\n'
        b'=ybegin part=%d total=%d line=128 size=%d name=%s\r\n'
        b'=ypart begin=%d end=%d\r\n%s\r\n'
        b'=yend size=%d part=%d pcrc32=%08x\r\n' % (
            name.encode(), part, total, message_id.encode(), part, total,
            size, name.encode(), begin + 1, begin + len(data),
            encoded.rstrip(b'\r\n'), len(data), part, crc))


def post(server, tmp_path, name, data, parts=1):
    """
    Puts a file on the server in `parts` articles; returns what an NZB
    file lists of it: (name, [(number, Message-ID), ...])
    """
    size = len(data)
    step = -(-size // parts) if size else 1
    segments = []
    for no in range(parts):
        piece = data[no * step:(no + 1) * step]
        message_id = '%s.%d@example.com' % (name, no + 1)
        path = tmp_path / ('%s.%d.msg' % (name, no + 1))
        path.write_bytes(yenc_article(
            name, piece, no + 1, parts, no * step, size, message_id))
        server.add_article(message_id, str(path), GROUP)
        segments.append((no + 1, message_id))
    return name, segments


def write_nzb(path, files, meta=()):
    """
    Writes an NZB file listing the files given (see post()); returns its
    path
    """
    head = ''.join('  <meta type="%s">%s</meta>\n' % entry for entry in meta)
    body = ''.join(
        ' <file poster="me@example.com" date="1577836800" '
        'subject="&quot;%s&quot; yEnc (1/%d)">\n'
        '  <groups><group>%s</group></groups>\n  <segments>\n%s'
        '  </segments>\n </file>\n' % (
            name, len(segments), GROUP, ''.join(
                '   <segment bytes="100" number="%d">%s</segment>\n' % entry
                for entry in segments))
        for name, segments in files)
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">\n%s%s</nzb>\n' % (
            '<head>\n%s</head>\n' % head if head else '', body))
    return str(path)


def asked(server):
    """
    Returns the articles asked of the server so far (each once)
    """
    return {c.split('<', 1)[1].rstrip('>') for c in server.commands()
            if c.startswith('ARTICLE <')}


@pytest.fixture
def usenet(tmp_path):
    """
    A test server, and a downloader (with its manager) that uses it
    """
    with FakeNNTPServer(join_group=False) as server:
        settings = NNTPSettings(cfg_file=config_for(
            server, tmp_path / 'config.yaml', connections=2))
        manager = NNTPManager(settings)

        def downloader(**kwargs):
            return NNTPDownloader(
                manager, str(tmp_path / 'staging'),
                str(tmp_path / 'Downloads'), **kwargs)

        yield server, downloader
        manager.close()
        settings.close()


def test_files_are_put_together_and_placed(tmp_path, usenet):
    """
    The files are fetched, put together from their parts and placed in a
    directory of their own; the staging directory goes once it is done
    """
    server, downloader = usenet
    nzb = write_nzb(tmp_path / 'release.nzb', [
        post(server, tmp_path, 'notes.txt', NOTES, parts=3),
        post(server, tmp_path, 'readme.nfo', README)])

    get = downloader()
    assert get.download(nzb) is True, get.problem
    assert get.folder == str(tmp_path / 'Downloads' / 'release')
    assert (tmp_path / 'Downloads' / 'release' / 'notes.txt').read_bytes() \
        == NOTES
    assert (tmp_path / 'Downloads' / 'release' / 'readme.nfo').read_bytes() \
        == README
    assert get.fetched == 4 and str(get) == '4 segment(s) fetched'
    assert not os.path.exists(get.staging)

    # Again: a directory of its own beside the first
    assert get.download(nzb) is True
    assert get.folder == str(tmp_path / 'Downloads' / 'release (2)')


def test_a_stopped_download_carries_on_where_it_left_off(tmp_path, usenet):
    """
    Running it again only fetches what it doesn't have yet: a part that
    failed, was damaged, or was cut short (never written whole) is fetched
    again; the rest is kept
    """
    server, downloader = usenet
    name, segments = post(server, tmp_path, 'notes.txt', NOTES, parts=4)
    nzb = write_nzb(tmp_path / 'release.nzb', [(name, segments)])

    # One fails, and one arrives damaged
    server.on(r'^ARTICLE <notes.txt.2@example.com>$',
              Reply('503 Try again later'), once=True)
    damaged = tmp_path / 'damaged.msg'
    damaged.write_bytes(yenc_article(
        'notes.txt', NOTES[:2500], 3, 4, 2500, len(NOTES),
        'notes.txt.3@example.com', damaged=True))
    server.on(r'^ARTICLE <notes.txt.3@example.com>$', lambda s, m: Reply(
        '220 0 <notes.txt.3@example.com>', body=damaged.read_bytes()),
        once=True)

    get = downloader()
    assert get.download(nzb) is False
    assert (get.fetched, get.failed, get.damaged) == (3, 1, 1)
    assert 'missing or damaged' in get.problem

    # What could be put together is there to see, and nothing was removed
    assert os.path.isfile(os.path.join(get.folder, 'notes.txt'))
    assert os.path.isdir(get.staging)

    before = len(server.commands())
    assert get.download(nzb) is True, get.problem
    again = {c.split('<', 1)[1].rstrip('>')
             for c in server.commands()[before:] if c.startswith('ARTICLE')}
    assert again == {'notes.txt.2@example.com', 'notes.txt.3@example.com'}
    assert get.resumed == 2
    assert (tmp_path / 'Downloads' / 'release (2)' / 'notes.txt'
            ).read_bytes() == NOTES


def test_parts_cut_short_or_lost_are_fetched_again(tmp_path, usenet):
    """
    A part noted but no longer on disk is fetched again, and one written
    part way (still under its temporary name) is never used
    """
    server, downloader = usenet
    nzb = write_nzb(tmp_path / 'release.nzb', [
        post(server, tmp_path, 'notes.txt', NOTES, parts=2)])

    get = downloader(cleanup=False)
    assert get.download(nzb) is True
    parts = sorted(os.listdir(os.path.join(get.staging, 'parts')))
    assert len(parts) == 2 and all(p.endswith('.part') for p in parts)

    # One lost; the other left half written, as a stop would
    os.remove(os.path.join(get.staging, 'parts', parts[0]))
    with open(os.path.join(get.staging, 'parts', parts[1] + '.tmp'),
              'wb') as fp:
        fp.write(b'half')

    before = len(server.commands())
    assert get.download(nzb) is True
    assert len([c for c in server.commands()[before:]
                if c.startswith('ARTICLE')]) == 1
    assert (tmp_path / 'Downloads' / 'release (2)' / 'notes.txt'
            ).read_bytes() == NOTES


def test_one_download_is_never_run_twice_at_once(tmp_path, usenet):
    """
    While one run has a download's staging directory, another is turned
    away (other downloads have staging directories of their own)
    """
    server, downloader = usenet
    nzb = write_nzb(tmp_path / 'release.nzb', [
        post(server, tmp_path, 'readme.nfo', README)])
    other = write_nzb(tmp_path / 'other.nzb', [
        post(server, tmp_path, 'notes.txt', NOTES)])

    first = downloader(cleanup=False)
    assert first.download(nzb) is True

    with open(os.path.join(first.staging, '.lock'), 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        second = downloader()
        assert second.download(nzb) is False
        assert 'already being downloaded' in second.problem

        # Something else entirely is fine
        assert second.download(other) is True
        assert second.staging != first.staging


def zipped(entries):
    """
    Returns a zip archive holding the files given (path -> bytes)
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def test_archives_are_extracted_keeping_their_layout(tmp_path, usenet):
    """
    What an archive holds is placed (in its directories); the archive
    itself isn't, but the files beside it (ie: an .nfo) are
    """
    server, downloader = usenet
    archive = zipped({
        'Show/Season 1/episode.txt': b'one', 'Show/cover.txt': b'two'})
    nzb = write_nzb(tmp_path / 'release.nzb', [
        post(server, tmp_path, 'release.zip', archive, parts=2),
        post(server, tmp_path, 'readme.nfo', README)])

    get = downloader()
    assert get.download(nzb) is True, get.problem
    folder = tmp_path / 'Downloads' / 'release'
    assert (folder / 'Show' / 'Season 1' / 'episode.txt').read_bytes() == \
        b'one'
    assert (folder / 'Show' / 'cover.txt').read_bytes() == b'two'
    assert (folder / 'readme.nfo').read_bytes() == README
    assert not (folder / 'release.zip').exists()


def test_archives_that_cant_be_opened_are_left_for_you(tmp_path, usenet):
    """
    An archive that can't be opened (ie: it is damaged) is placed as it
    is, with everything else, and nothing is removed
    """
    server, downloader = usenet
    nzb = write_nzb(tmp_path / 'release.nzb', [
        post(server, tmp_path, 'release.zip', b'not really a zip file'),
        post(server, tmp_path, 'readme.nfo', README)])

    get = downloader()
    assert get.download(nzb) is False
    assert "release.zip couldn't be opened" in get.problem
    assert sorted(os.listdir(get.folder)) == ['readme.nfo', 'release.zip']
    assert os.path.isdir(get.staging)


def seven_zipped(tmp_path, password):
    """
    Returns a 7-Zip archive (encrypted with a password) holding one file
    """
    source = tmp_path / 'source'
    source.mkdir(exist_ok=True)
    (source / 'secret.txt').write_bytes(b'hidden away')
    target = tmp_path / 'made.7z'
    subprocess.run(
        [which('7z'), 'a', '-p%s' % password, '-mhe=on', str(target),
         str(source / 'secret.txt')], check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return target.read_bytes()


@pytest.mark.skipif(not which('7z'), reason='7-Zip is not installed')
def test_passwords_come_from_us_or_the_nzb_file(tmp_path, usenet):
    """
    A password protected archive is opened with the password given, or
    one its NZB file holds; without one it is left for you as it is
    """
    server, downloader = usenet
    archive = seven_zipped(tmp_path, 'open sesame')
    files = [post(server, tmp_path, 'release.7z', archive, parts=2)]

    # No password known
    nzb = write_nzb(tmp_path / 'release.nzb', files)
    get = downloader()
    assert get.download(nzb) is False
    assert "release.7z couldn't be opened" in get.problem
    assert os.listdir(get.folder) == ['release.7z']

    # The one the NZB file gives (after a wrong one of ours)
    nzb = write_nzb(tmp_path / 'with.nzb', files, meta=[
        ('password', 'open sesame')])
    get = downloader(password='not it')
    assert get.download(nzb) is True, get.problem
    assert (tmp_path / 'Downloads' / 'with' / 'secret.txt').read_bytes() == \
        b'hidden away'


@pytest.mark.skipif(not which('par2'), reason='par2 is not installed')
def test_damaged_files_are_repaired_with_just_enough_recovery_volumes(
        tmp_path, usenet):
    """
    A part no server has is filled in by par2, fetching only the recovery
    volumes the repair needs
    """
    server, downloader = usenet
    source = tmp_path / 'made'
    source.mkdir()
    (source / 'notes.txt').write_bytes(NOTES)
    subprocess.run(
        [which('par2'), 'create', '-r30', '-n3', '-s1024', '-q',
         str(source / 'notes.par2'), str(source / 'notes.txt')],
        check=True, stdout=subprocess.DEVNULL)

    files = [post(server, tmp_path, 'notes.txt', NOTES, parts=4)]
    volumes = []
    for name in sorted(os.listdir(source)):
        if name.endswith('.par2'):
            entry = post(server, tmp_path, name,
                         (source / name).read_bytes())
            (volumes if '.vol' in name else files).append(entry)

    # One part is gone from every server
    server.on(r'^ARTICLE <notes.txt.2@example.com>$',
              Reply('430 No such article'))
    nzb = write_nzb(tmp_path / 'release.nzb', files + volumes)

    get = downloader()
    assert get.download(nzb) is True, get.problem
    assert get.repaired and get.missing == 1
    assert (tmp_path / 'Downloads' / 'release' / 'notes.txt').read_bytes() \
        == NOTES
    assert not (tmp_path / 'Downloads' / 'release' / 'notes.par2').exists()

    # Not every recovery volume was needed
    assert len(asked(server) & {m for _, s in volumes for _, m in s}) < \
        len(volumes)


@pytest.mark.parametrize('answer, needed, fetched', [
    # par2 can repair it with what it has: no recovery volume is fetched
    ('Repairable', 0, set()),
    # It needs 3 more blocks: the smallest volumes that hold them
    ('Unrepairable', 3, {'notes.vol00+01.par2', 'notes.vol01+02.par2'}),
])
def test_recovery_volumes_are_only_fetched_when_needed(
        tmp_path, usenet, monkeypatch, answer, needed, fetched):
    """
    What par2's check says decides which recovery volumes are fetched
    (only enough of the smallest), and the repair is run
    """
    from newsreap.codecs.CodecPar import CodecPar
    from newsreap.codecs.CodecPar import ParReturnCode

    server, downloader = usenet
    files = [post(server, tmp_path, 'notes.txt', NOTES, parts=2),
             post(server, tmp_path, 'notes.par2', b'index')]
    volumes = [post(server, tmp_path, name, b'blocks') for name in (
        'notes.vol03+04.par2', 'notes.vol00+01.par2', 'notes.vol01+02.par2')]
    nzb = write_nzb(tmp_path / 'release.nzb', files + volumes)

    # par2 as though it were installed
    repaired = []

    def test(self, content=None):
        self.needed = needed
        return getattr(ParReturnCode, answer)

    def decode(self, content=None, *args, **kwargs):
        repaired.append(sorted(os.path.basename(p) for p in content))
        return []

    monkeypatch.setattr(CodecPar, 'can_exe', lambda self, path: True)
    monkeypatch.setattr(CodecPar, 'test', test)
    monkeypatch.setattr(CodecPar, 'decode', decode)

    get = downloader()
    assert get.download(nzb) is True, get.problem
    assert get.repaired is True
    assert asked(server) & {
        m for _, segments in volumes for _, m in segments} == {
        '%s.1@example.com' % name for name in fetched}
    assert repaired and 'notes.txt' in repaired[0]


def test_downloading_from_the_command_line(tmp_path):
    """
    nr get downloads an NZB file into the download directory, and says
    where; --no-cleanup keeps the staging directory
    """
    from newsreap.cli import cli

    with FakeNNTPServer(join_group=False) as server:
        nzb = write_nzb(tmp_path / 'release.nzb', [
            post(server, tmp_path, 'notes.txt', NOTES, parts=2)])
        config = config_for(server, tmp_path / 'config.yaml')
        runner = CliRunner()

        def nr(*args):
            return runner.invoke(cli, ['--config', config, *args], obj={})

        assert nr('db', 'init').exit_code == 0
        result = nr('get', nzb, '--output', str(tmp_path / 'out'),
                    '--no-cleanup')
        assert result.exit_code == 0, result.output
        assert 'Saved in %s' % (tmp_path / 'out' / 'release') in \
            result.output
        assert (tmp_path / 'out' / 'release' / 'notes.txt').read_bytes() == \
            NOTES
        assert os.listdir(str(tmp_path / 'cache' / 'downloads'))
