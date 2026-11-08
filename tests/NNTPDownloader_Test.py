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


import fcntl
import io
import os
import zipfile
from shutil import which
from zlib import crc32

import re

import pytest
from click.testing import CliRunner

from newsreap.NNTPDownloader import NNTPDownloader
from newsreap.NNTPManager import NNTPManager
from newsreap.NNTPRetry import NNTPRetry
from newsreap.NNTPSettings import NNTPSettings
from newsreap.codecs.CodecYenc import yenc_encode

from tests.FakeNNTPServer import FakeNNTPServer
from tests.FakeNNTPServer import Reply
from tests.conftest import config_for
from tests.conftest import run_program

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


def write_nzb(path, files, meta=(), date=1577836800):
    """
    Writes an NZB file listing the files given (see post()); returns its
    path

    - date: when they were posted (seconds since 1970; in 2020 by default)
    """
    head = ''.join('  <meta type="%s">%s</meta>\n' % entry for entry in meta)
    body = ''.join(
        ' <file poster="me@example.com" date="%d" '
        'subject="&quot;%s&quot; yEnc (1/%d)">\n'
        '  <groups><group>%s</group></groups>\n  <segments>\n%s'
        '  </segments>\n </file>\n' % (
            date, name, len(segments), GROUP, ''.join(
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
    return asked_since(server, 0)


def asked_since(server, start):
    """
    Returns the articles asked of the server since its `start`th command
    (each once)
    """
    return {c.split('<', 1)[1].rstrip('>') for c in server.commands()[start:]
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
    assert get.folder == str(tmp_path / 'Downloads' / 'release.2')


def test_a_stopped_download_carries_on_where_it_left_off(tmp_path, usenet):
    """
    Running it again only fetches what it doesn't have yet: a part that
    failed is fetched again; the rest is kept.  A part that came damaged
    (of an old post) is only asked for again once a server is added.
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

    # What could be put together is there to see (with what is wrong with
    # it beside it), and nothing was removed
    folder = get.folder
    assert os.path.isfile(os.path.join(folder, 'notes.txt'))
    with open(os.path.join(folder, 'notes.txt.ERRORS')) as fp:
        errors = fp.read()
    assert '<notes.txt.2@example.com>: it couldn\'t be fetched' in errors
    assert '<notes.txt.3@example.com>: it is damaged' in errors
    assert 'Part 1 ' not in errors
    assert os.path.isdir(get.staging)

    # Again: only the part that failed is fetched; the damaged one would
    # come the same from the same servers
    before = len(server.commands())
    assert get.download(nzb) is False
    assert asked_since(server, before) == {'notes.txt.2@example.com'}
    assert (get.resumed, get.held) == (2, 1)
    with open(os.path.join(folder, 'notes.txt.ERRORS')) as fp:
        errors = fp.read()
    assert 'Part 2 ' not in errors
    assert '<notes.txt.3@example.com>: it is damaged (its crc32 doesn\'t ' \
        'match what was posted) (asked for again only once you add a ' \
        'server)' in errors

    # A server added since might have it whole: it is asked for, it is
    # finished in the same folder, and the .ERRORS file goes
    get.retry = NNTPRetry([*(tuple(h.rsplit(':', 1)) for h in get.retry.hosts),
                           ('News.New.Example', 563)])
    before = len(server.commands())
    assert get.download(nzb) is True, get.problem
    assert asked_since(server, before) == {'notes.txt.3@example.com'}
    assert get.resumed == 3
    assert get.folder == folder
    assert os.listdir(folder) == ['notes.txt']
    assert (tmp_path / 'Downloads' / 'release' / 'notes.txt'
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
    assert (tmp_path / 'Downloads' / 'release' / 'notes.txt'
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
    run_program(
        [which('7z'), 'a', '-p%s' % password, '-mhe=on', str(target),
         str(source / 'secret.txt')], check=True)
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
    # 9 blocks; half as many again as recovery blocks, in 3 volumes (how
    # they are shared out differs between par2 versions)
    run_program(
        [which('par2'), 'create', '-r50', '-n3', '-s1024', '-q',
         str(source / 'notes.par2'), str(source / 'notes.txt')], check=True)

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

    # Only the recovery blocks the missing part needs (it covers 3 blocks)
    # were fetched, not all of them
    def blocks(name):
        return int(re.search(r'\+([0-9]+)\.par2$', name).group(1))

    fetched = asked(server)
    total = sum(blocks(name) for name, _ in volumes)
    got = sum(blocks(name) for name, segments in volumes
              if {m for _, m in segments} & fetched)
    assert 3 <= got < total


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


@pytest.mark.parametrize('name, tidied', [
    ('  My   Holiday!! $$ (2019)  ', 'My.Holiday.2019'),
    ('Ubuntu-16.04.1 [1/50] "x.rar"', 'Ubuntu-16.04.1.1.50.x.rar'),
    ('a . . b', 'a.b'),
    ('tab\there', 'tab.here'),
    ('...hidden', 'hidden'),
    ('../../etc/passwd', 'etc.passwd'),
    ('!!!', 'download'),
])
def test_names_we_make_are_easy_to_use(name, tidied):
    """
    Names made for what is downloaded have nothing a shell treats
    specially, a period for each run of whitespace, no runs of periods and
    nothing hidden or outside the download directory
    """
    from newsreap.NNTPDownloader import tidy_name
    assert tidy_name(name) == tidied


def test_posted_files_keep_their_names(tmp_path, usenet):
    """
    The directory is named tidily, but the files keep the names they were
    posted with (par2 looks for them by those names)
    """
    server, downloader = usenet
    nzb = write_nzb(tmp_path / 'My Release!.nzb', [
        post(server, tmp_path, 'my notes (1).txt', NOTES)])

    get = downloader()
    assert get.download(nzb) is True, get.problem
    assert get.folder == str(tmp_path / 'Downloads' / 'My.Release')
    assert os.listdir(get.folder) == ['my notes (1).txt']


def odd_nzb(path, segments, subject='&quot;secret.txt&quot; yEnc (1/1)'):
    """
    Writes an NZB file of one file with the segments given (as XML)
    """
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">'
        '<file poster="x" date="1" subject="%s"><groups><group>%s</group>'
        '</groups><segments>%s</segments></file></nzb>' % (
            subject, GROUP, segments))
    return str(path)


def test_a_file_an_nzb_names_is_never_read(tmp_path, monkeypatch):
    """
    The file name a subject gives is only a name: a file of that name in
    the directory we run from is never read in as part of the download
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'secret.txt').write_text('my secret')
    nzb = odd_nzb(tmp_path / 'x.nzb',
                  '<segment bytes="10" number="1">a@b</segment>')

    files, _, _ = NNTPDownloader.read(nzb)
    assert [(f['name'], f['segments']) for f in files] == [
        ('secret.txt', [(1, 'a@b')])]


def test_odd_segments_dont_stop_an_nzb_being_read(tmp_path):
    """
    A segment number that isn't one follows the one before, and a segment
    without a Message-ID is left out
    """
    nzb = odd_nzb(tmp_path / 'x.nzb', ''.join((
        '<segment bytes="10" number="1">a@b</segment>',
        '<segment bytes="10" number="x">c@d</segment>',
        '<segment bytes="10" number="3"></segment>',
        '<segment bytes="zz">e@f</segment>')))

    files, _, _ = NNTPDownloader.read(nzb)
    assert files[0]['segments'] == [(1, 'a@b'), (2, 'c@d'), (3, 'e@f')]


def test_a_file_no_server_has_makes_the_download_fail(tmp_path, usenet):
    """
    A file none of whose articles can be had is missing from the
    download, so it isn't done (and nothing is removed)
    """
    server, downloader = usenet
    readme = post(server, tmp_path, 'readme.nfo', README)
    server.on(r'^ARTICLE <readme.nfo.1@example.com>$',
              Reply('430 No such article'))
    nzb = write_nzb(tmp_path / 'release.nzb', [
        post(server, tmp_path, 'notes.txt', NOTES, parts=2), readme])

    get = downloader()
    assert get.download(nzb) is False
    assert 'missing' in get.problem
    assert os.path.isdir(get.staging)

    # Nothing of it came; what is wrong with it is still said
    assert sorted(os.listdir(get.folder)) == [
        'notes.txt', 'readme.nfo.ERRORS']
    with open(os.path.join(get.folder, 'readme.nfo.ERRORS')) as fp:
        assert '<readme.nfo.1@example.com>: no server has it' in fp.read()

    # It is an old post: asking our servers again can't help, so they
    # aren't asked
    server._rules.clear()
    before = len(server.commands())
    assert get.download(nzb) is False
    assert asked_since(server, before) == set()
    with open(os.path.join(get.folder, 'readme.nfo.ERRORS')) as fp:
        assert 'only once you add a server' in fp.read()

    # Asked to anyway (and a server has it now), it is finished in the
    # same folder; the .ERRORS file goes
    get = downloader(retry_missing=True)
    assert get.download(nzb) is True, get.problem
    assert asked_since(server, before) == {'readme.nfo.1@example.com'}
    assert sorted(os.listdir(get.folder)) == ['notes.txt', 'readme.nfo']


def test_new_posts_are_asked_for_again_for_a_while(
        tmp_path, usenet, monkeypatch):
    """
    A part of a new post no server had yet is asked for again by the next
    run (a post takes a while to reach every server), but not straight
    after the last time
    """
    import time

    server, downloader = usenet
    readme = post(server, tmp_path, 'readme.nfo', README)
    server.on(r'^ARTICLE <readme.nfo.1@example.com>$',
              Reply('430 No such article'), once=True)
    nzb = write_nzb(tmp_path / 'release.nzb', [readme], date=int(time.time()))

    get = downloader()
    assert get.download(nzb) is False
    with open(os.path.join(get.folder, 'readme.nfo.ERRORS')) as fp:
        assert 'no server has it (asked for again next time)' in fp.read()

    # Moments later: not yet
    before = len(server.commands())
    assert get.download(nzb) is False
    assert asked_since(server, before) == set() and get.held == 1

    # A while later: it arrived
    monkeypatch.setattr('newsreap.NNTPRetry.RETRY_GAP',
                        __import__('datetime').timedelta(0))
    assert get.download(nzb) is True, get.problem
    assert asked_since(server, before) == {'readme.nfo.1@example.com'}
    assert (tmp_path / 'Downloads' / 'release' / 'readme.nfo').read_bytes() \
        == README


def test_files_of_the_same_name_dont_overwrite_each_other(tmp_path, usenet):
    """
    Files posted under the same name are all kept (the second one gets
    ' (2)', the third ' (3)'), with the same names each run
    """
    server, downloader = usenet
    first = post(server, tmp_path, 'readme.nfo', README)
    second_id = 'readme.nfo.again@example.com'
    path = tmp_path / 'again.msg'
    path.write_bytes(yenc_article('readme.nfo', NOTES[:300], 1, 1, 0, 300,
                                  second_id))
    server.add_article(second_id, str(path), GROUP)
    nzb = write_nzb(tmp_path / 'release.nzb', [
        first, ('readme.nfo', [(1, second_id)])])

    third_id = 'readme.nfo.third@example.com'
    path = tmp_path / 'third.msg'
    path.write_bytes(yenc_article('readme.nfo', NOTES[:200], 1, 1, 0, 200,
                                  third_id))
    server.add_article(third_id, str(path), GROUP)
    nzb = write_nzb(tmp_path / 'release.nzb', [
        first, ('readme.nfo', [(1, second_id)]),
        ('readme.nfo', [(1, third_id)])])

    names = ['readme (2).nfo', 'readme (3).nfo', 'readme.nfo']
    get = downloader()
    assert get.download(nzb) is True, get.problem
    assert sorted(os.listdir(get.folder)) == names
    assert {(tmp_path / 'Downloads' / 'release' / name).read_bytes()
            for name in names} == {README, NOTES[:300], NOTES[:200]}

    # Run again, each one keeps its name
    assert get.download(nzb) is True, get.problem
    assert sorted(os.listdir(get.folder)) == names
    assert (tmp_path / 'Downloads' / 'release' / 'readme (3).nfo')\
        .read_bytes() == NOTES[:200]


def staged(tmp_path, parts):
    """
    Returns a downloader with parts already fetched into its staging
    directory, and its staging database session

    - parts: [(message_id, number, begin, end, data)] each
    """
    from newsreap.NNTPGetDatabase import NNTPGetDatabase
    from newsreap.objects.get.DownloadedSegment import DownloadedSegment

    get = NNTPDownloader(None, str(tmp_path), str(tmp_path / 'out'))
    get.staging = str(tmp_path / 'staging')
    for folder in ('parts', 'files', 'tmp'):
        os.makedirs(os.path.join(get.staging, folder))

    db = NNTPGetDatabase(engine='sqlite:///%s' % (tmp_path / 'x.db'),
                         reset=False)
    session = db.session()
    for message_id, number, begin, end, data in parts:
        path = os.path.join(get.staging, 'parts', message_id)
        with open(path, 'wb') as fp:
            fp.write(data)
        session.merge(DownloadedSegment(
            message_id=message_id, filename='x.bin', number=number,
            begin=begin, end=end, path=path, valid=True))
    session.commit()
    return get, db, session


def test_parts_that_dont_say_where_they_go_follow_each_other(tmp_path):
    """
    Parts that don't say where they go (uuencoded ones) are put one after
    another in part order; one missing leaves the rest unplaced, so the
    file isn't complete
    """
    get, db, session = staged(tmp_path, [
        ('b', 2, 0, 4, b'BBBB'), ('a', 1, 0, 4, b'AAAA')])
    try:
        found = get._assemble(session, [
            {'name': 'x.bin', 'segments': [(1, 'a'), (2, 'b')]}])
        path, complete = found['x.bin']
        assert complete is True
        with open(path, 'rb') as fp:
            assert fp.read() == b'AAAABBBB'

        found = get._assemble(session, [
            {'name': 'x.bin', 'segments': [(1, 'a'), (2, 'b'), (3, 'c')]}])
        assert found['x.bin'][1] is False

    finally:
        db.close()


def test_parts_that_dont_say_where_they_go_keep_their_place(tmp_path):
    """
    With a part of them missing, the rest still go where they belong: every
    part but the last is posted the same size, so part 3 goes after the
    space part 2 leaves (as zeros)
    """
    get, db, session = staged(tmp_path, [
        ('a', 1, 0, 4, b'AAAA'), ('c', 3, 0, 2, b'CC')])
    try:
        path, complete = get._assemble(session, [
            {'name': 'x.bin', 'segments': [(1, 'a'), (2, 'b'), (3, 'c')]}
        ])['x.bin']
        assert complete is False
        with open(path, 'rb') as fp:
            assert fp.read() == b'AAAA\0\0\0\0CC'
        assert not any("don't say where" in line
                       for line in get.errors[path])

    finally:
        db.close()


def test_a_part_that_cant_be_read_back_is_left_out(tmp_path, caplog):
    """
    A part whose file can't be read is warned about and left as zeros (the
    rest is still put together), and is fetched again next time
    """
    import logging
    from newsreap.Logging import NEWSREAP_ENGINE
    from newsreap.objects.get.DownloadedSegment import DownloadedSegment

    caplog.set_level(logging.WARNING, logger=NEWSREAP_ENGINE)
    get, db, session = staged(tmp_path, [
        ('a', 1, 0, 4, b'AAAA'), ('b', 2, 4, 8, b'BBBB')])
    try:
        unreadable = os.path.join(get.staging, 'parts', 'a')
        os.chmod(unreadable, 0)
        if os.access(unreadable, os.R_OK):
            pytest.skip('files can always be read here (ie: as root)')

        path, complete = get._assemble(session, [
            {'name': 'x.bin', 'segments': [(1, 'a'), (2, 'b')]}])['x.bin']
        assert complete is False
        with open(path, 'rb') as fp:
            assert fp.read() == b'\0\0\0\0BBBB'
        assert any("<a>: it couldn't be read back" in line
                   for line in get.errors[path])
        assert any('Could not read' in r.getMessage()
                   for r in caplog.records)
        assert session.get(DownloadedSegment, 'a').path == ''

    finally:
        db.close()


def test_a_part_placed_beyond_any_file_is_damaged(tmp_path):
    """
    A part claiming to sit beyond 1 TiB isn't written (no huge file is
    made), and the file isn't complete
    """
    get, db, session = staged(tmp_path, [
        ('a', 1, 0, 4, b'AAAA'), ('b', 2, 10 ** 13, 10 ** 13 + 4, b'BBBB')])
    try:
        path, complete = get._assemble(session, [
            {'name': 'x.bin', 'segments': [(1, 'a'), (2, 'b')]}])['x.bin']
        assert complete is False
        assert os.path.getsize(path) == 4

    finally:
        db.close()


def test_names_too_long_for_a_file_system_are_cut_down():
    """
    A name longer than a file system allows keeps its extension and as
    much of the rest as fits (counted in bytes, never cutting a character
    in half)
    """
    from newsreap.NNTPDownloader import safe_name
    from newsreap.NNTPDownloader import tidy_name

    for made in (safe_name('a' * 300 + '.part01.rar'),
                 tidy_name('é' * 300 + '.mkv'),
                 safe_name('x' * 400)):
        assert len(made.encode('utf-8')) <= 230
    assert safe_name('a' * 300 + '.part01.rar').endswith('.rar')
    assert tidy_name('é' * 300 + '.mkv').endswith('é.mkv')


def test_errors_files_that_cant_be_written_or_removed_only_warn(
        tmp_path, caplog):
    """
    A .ERRORS file that can't be written (or removed) is warned about, and
    nothing else is held up
    """
    import logging
    from newsreap.Logging import NEWSREAP_ENGINE
    from newsreap.NNTPDownloader import clear_errors
    from newsreap.NNTPDownloader import write_errors

    caplog.set_level(logging.WARNING, logger=NEWSREAP_ENGINE)
    target = tmp_path / 'notes.txt'
    (tmp_path / 'notes.txt.ERRORS').mkdir()

    assert write_errors(str(target), ['Part 2 <a@b>: gone']) is False
    clear_errors(str(target))
    assert [r.getMessage().split()[:3] for r in caplog.records] == [
        ['Could', 'not', 'write'], ['Could', 'not', 'remove']]
    assert not (tmp_path / 'notes.txt.ERRORS.tmp').exists()

    # And when all is well, it is written and then cleared
    (tmp_path / 'notes.txt.ERRORS').rmdir()
    assert write_errors(str(target), ['Part 2 <a@b>: gone']) is True
    assert 'Part 2 <a@b>: gone' in (tmp_path / 'notes.txt.ERRORS').read_text()
    clear_errors(str(target))
    assert not (tmp_path / 'notes.txt.ERRORS').exists()
    clear_errors(str(target))


def test_an_unfinished_file_never_looks_whole(tmp_path, usenet, monkeypatch):
    """
    An unfinished file has its .ERRORS file written before it is moved
    in; a whole one is moved in before its .ERRORS file goes, so a stop at
    any moment never leaves an unfinished file without one
    """
    import newsreap.NNTPDownloader as module

    server, downloader = usenet
    nzb = write_nzb(tmp_path / 'release.nzb', [
        post(server, tmp_path, 'notes.txt', NOTES, parts=3)])
    server.on(r'^ARTICLE <notes.txt.2@example.com>$',
              Reply('430 No such article'), once=True)

    seen = []
    place_file, clear_errors = module.place_file, module.clear_errors

    def placing(source, target):
        seen.append(('placed', os.path.exists(target + '.ERRORS')))
        return place_file(source, target)

    def clearing(path):
        seen.append(('cleared', os.path.exists(path)))
        return clear_errors(path)

    monkeypatch.setattr(module, 'place_file', placing)
    monkeypatch.setattr(module, 'clear_errors', clearing)

    get = downloader()
    assert get.download(nzb) is False
    assert seen == [('placed', True)]

    seen.clear()
    get = downloader(retry_missing=True)
    assert get.download(nzb) is True, get.problem
    assert seen == [('placed', True), ('cleared', True)]
    assert os.listdir(get.folder) == ['notes.txt']


def test_an_unfinished_file_stays_in_staging_without_its_errors_file(
        tmp_path, usenet, monkeypatch):
    """
    When its .ERRORS file can't be written, an unfinished file isn't
    placed (it would look whole); it stays in the staging directory
    """
    from newsreap.NNTPErrorsFile import NNTPErrorsFile

    server, downloader = usenet
    nzb = write_nzb(tmp_path / 'release.nzb', [
        post(server, tmp_path, 'notes.txt', NOTES, parts=3),
        post(server, tmp_path, 'readme.nfo', README)])
    server.on(r'^ARTICLE <notes.txt.2@example.com>$',
              Reply('430 No such article'), once=True)
    monkeypatch.setattr(NNTPErrorsFile, 'save', lambda self, *a, **k: False)

    get = downloader()
    assert get.download(nzb) is False
    assert os.listdir(get.folder) == ['readme.nfo']
    assert os.path.isfile(os.path.join(get.staging, 'files', 'notes.txt'))


def test_an_errors_file_that_cant_be_read_means_asking_again(
        tmp_path, usenet):
    """
    A .ERRORS file that can't be read back remembers nothing, so what no
    server had is asked for again
    """
    server, downloader = usenet
    nzb = write_nzb(tmp_path / 'release.nzb', [
        post(server, tmp_path, 'notes.txt', NOTES, parts=3)])
    server.on(r'^ARTICLE <notes.txt.2@example.com>$',
              Reply('430 No such article'), once=True)

    get = downloader()
    assert get.download(nzb) is False
    errors = os.path.join(get.folder, 'notes.txt.ERRORS')
    with open(errors, 'a') as fp:
        fp.write('\n  : [ broken\n')

    before = len(server.commands())
    assert get.download(nzb) is True, get.problem
    assert asked_since(server, before) == {'notes.txt.2@example.com'}
    assert os.listdir(get.folder) == ['notes.txt']


def test_ctrl_c_keeps_what_is_on_its_way_and_stops(
        tmp_path, usenet, monkeypatch):
    """
    Ctrl-C stops asking for more; the articles already asked for are
    still kept, nothing is placed, and running it again carries on
    """
    from newsreap import NNTPArchiver as module

    server, downloader = usenet
    nzb = write_nzb(tmp_path / 'release.nzb', [
        post(server, tmp_path, 'notes.txt', NOTES, parts=8)])

    # Ctrl-C is pressed while waiting for the articles to come back
    wait = module.wait
    calls = []

    def pressed(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return wait(*args, **kwargs)

    monkeypatch.setattr(module, 'wait', pressed)
    get = downloader()
    with pytest.raises(KeyboardInterrupt):
        get.download(nzb)

    # Everything asked for was kept (none of it is asked for again)
    first = asked(server)
    assert 1 < len(first) < 8 and get.fetched == len(first)
    assert not os.path.exists(str(tmp_path / 'Downloads'))

    monkeypatch.undo()
    before = len(server.commands())
    get = downloader()
    assert get.download(nzb) is True, get.problem
    assert asked_since(server, before) == {
        'notes.txt.%d@example.com' % no for no in range(1, 9)} - first
    assert (tmp_path / 'Downloads' / 'release' / 'notes.txt').read_bytes() \
        == NOTES


def test_ctrl_c_on_the_command_line_says_it_stopped(tmp_path, monkeypatch):
    """
    nr get stopped with Ctrl-C says so and exits with 130
    """
    from newsreap.cli import cli

    def stopped(self, *args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(NNTPDownloader, 'download', stopped)
    with FakeNNTPServer(join_group=False) as server:
        nzb = write_nzb(tmp_path / 'release.nzb', [
            post(server, tmp_path, 'notes.txt', NOTES)])
        config = config_for(server, tmp_path / 'config.yaml')
        result = CliRunner().invoke(cli, ['--config', config, 'get', nzb],
                                    obj={})
        assert result.exit_code == 130
        assert 'Stopped; run it again to carry on.' in result.output


def test_a_stop_while_placing_finishes_in_the_same_folder(
        tmp_path, usenet, monkeypatch):
    """
    Stopped while its files are being placed, running it again finishes
    in the same folder (no second folder, no '(2)' copies)
    """
    import newsreap.NNTPDownloader as module

    server, downloader = usenet
    nzb = write_nzb(tmp_path / 'release.nzb', [
        post(server, tmp_path, 'notes.txt', NOTES, parts=2),
        post(server, tmp_path, 'readme.nfo', README)])

    place_file = module.place_file
    calls = []

    def pressed(source, target):
        calls.append(target)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return place_file(source, target)

    monkeypatch.setattr(module, 'place_file', pressed)
    with pytest.raises(KeyboardInterrupt):
        downloader().download(nzb)

    monkeypatch.undo()
    get = downloader()
    assert get.download(nzb) is True, get.problem
    assert os.listdir(str(tmp_path / 'Downloads')) == ['release']
    assert sorted(os.listdir(get.folder)) == ['notes.txt', 'readme.nfo']


def test_a_copy_cut_short_leaves_nothing_behind(tmp_path, monkeypatch):
    """
    A file copied from another drive that can't be finished (ie: the drive
    is full) leaves no part written copy in the download directory
    """
    import newsreap.NNTPDownloader as module

    source, target = tmp_path / 'from.bin', tmp_path / 'to.bin'
    source.write_bytes(b'data')

    def other_drive(a, b):
        if a == str(source):
            raise OSError(18, 'Invalid cross-device link')
        return os.replace(a, b)

    def full(a, b):
        with open(b, 'wb') as fp:
            fp.write(b'da')
        raise OSError(28, 'No space left on device')

    monkeypatch.setattr(module, 'replace', other_drive)
    monkeypatch.setattr(module, 'copyfile', full)
    with pytest.raises(OSError):
        module.place_file(str(source), str(target))

    assert sorted(os.listdir(str(tmp_path))) == ['from.bin']


@pytest.mark.parametrize('needed, chosen', [
    # Nothing needed; nothing fetched
    (0, []),
    # The fewest blocks that will do, then the fewest volumes
    (2, ['n.vol1+2.par2']),
    (3, ['n.vol0+1.par2', 'n.vol1+2.par2']),
    (4, ['n.vol3+4.par2']),
    (6, ['n.vol1+2.par2', 'n.vol3+4.par2']),
    # par2 didn't say (or wants more than there is): every volume that
    # holds anything
    (float('inf'), ['n.vol0+1.par2', 'n.vol1+2.par2', 'n.vol3+4.par2']),
    (99, ['n.vol0+1.par2', 'n.vol1+2.par2', 'n.vol3+4.par2']),
])
def test_just_enough_recovery_volumes_are_chosen(needed, chosen):
    """
    Recovery volumes are chosen to fetch as few blocks as will do; one
    holding no blocks is never fetched
    """
    volumes = [{'name': name} for name in (
        'n.vol7+0.par2', 'n.vol0+1.par2', 'n.vol1+2.par2', 'n.vol3+4.par2')]
    assert [f['name'] for f in NNTPDownloader.recovery_volumes(
        volumes, needed)] == chosen


def test_only_nzb_files_and_collections_are_downloaded(tmp_path):
    """
    nr get refuses what is neither an NZB file nor a collection's id (an
    article is fetched with nr fetch)
    """
    from newsreap.cli import cli

    with FakeNNTPServer(join_group=False) as server:
        config = config_for(server, tmp_path / 'config.yaml')
        result = CliRunner().invoke(
            cli, ['--config', config, 'get', '<one@example.com>'], obj={})
        assert result.exit_code == 1
        assert 'nr fetch' in result.output


def test_extracted_files_only_take_their_name_once_whole(tmp_path,
                                                         monkeypatch):
    """
    What was extracted is moved in keeping its layout; from another drive
    each file is copied under a temporary name, so a stop part way leaves
    nothing that looks whole
    """
    import newsreap.NNTPDownloader as downloader

    source = tmp_path / 'extracted'
    (source / 'Season.1').mkdir(parents=True)
    (source / 'Season.1' / 'e1.mkv').write_bytes(b'one')
    (source / 'notes.txt').write_bytes(b'notes')
    target = tmp_path / 'placed'
    (target / 'Season.1').mkdir(parents=True)
    (target / 'notes.txt').write_bytes(b'older')

    def other_drive(source, target):
        raise OSError(18, 'Invalid cross-device link')

    real_replace = downloader.replace
    monkeypatch.setattr(downloader, 'replace', lambda s, t: real_replace(
        s, t) if t.endswith('.tmp') or s.endswith('.tmp') else other_drive(
        s, t))

    def stopped(source, target):
        with open(target, 'wb') as fp:
            fp.write(b'o')
        raise KeyboardInterrupt

    monkeypatch.setattr(downloader, 'copyfile', stopped)
    with pytest.raises(KeyboardInterrupt):
        NNTPDownloader._move_into(str(source), str(target))
    assert not (target / 'notes (2).txt').exists()
    assert sorted(p.name for p in (target / 'Season.1').iterdir()) == []

    monkeypatch.undo()
    monkeypatch.setattr(downloader, 'replace', lambda s, t: real_replace(
        s, t) if t.endswith('.tmp') or s.endswith('.tmp') else other_drive(
        s, t))
    NNTPDownloader._move_into(str(source), str(target))
    assert (target / 'Season.1' / 'e1.mkv').read_bytes() == b'one'
    assert (target / 'notes (2).txt').read_bytes() == b'notes'
    assert (target / 'notes.txt').read_bytes() == b'older'
    assert not list(target.rglob('*.tmp'))


@pytest.mark.skipif(not which('par2'), reason='par2 is not installed')
def test_a_file_no_server_has_is_rebuilt_by_par2(tmp_path, usenet):
    """
    A file no server has any of is rebuilt by par2 and placed with the
    others
    """
    server, downloader = usenet
    source = tmp_path / 'made'
    source.mkdir()
    more = b'more notes, all of them lost\n' * 40
    (source / 'notes.txt').write_bytes(NOTES)
    (source / 'more.txt').write_bytes(more)
    run_program(
        [which('par2'), 'create', '-r100', '-n1', '-s1024', '-q',
         str(source / 'notes.par2'), str(source / 'notes.txt'),
         str(source / 'more.txt')], check=True)

    files = [post(server, tmp_path, 'notes.txt', NOTES, parts=2),
             post(server, tmp_path, 'more.txt', more, parts=2)]
    for name in sorted(os.listdir(source)):
        if name.endswith('.par2'):
            files.append(post(server, tmp_path, name,
                              (source / name).read_bytes()))

    # Every part of it is gone
    server.on(r'^ARTICLE <more.txt\.[0-9]+@example.com>$',
              Reply('430 No such article'))
    nzb = write_nzb(tmp_path / 'release.nzb', files)

    get = downloader()
    assert get.download(nzb) is True, get.problem
    assert get.repaired
    placed = tmp_path / 'Downloads' / 'release'
    assert (placed / 'notes.txt').read_bytes() == NOTES
    assert (placed / 'more.txt').read_bytes() == more


@pytest.mark.skipif(not which('par2'), reason='par2 is not installed')
def test_every_par2_set_is_checked(tmp_path, usenet):
    """
    Each par2 set checks its own files: damage in one that isn't the first
    is still found and repaired
    """
    server, downloader = usenet
    source = tmp_path / 'made'
    source.mkdir()
    more = b'more notes in a set of their own\n' * 40
    (source / 'notes.txt').write_bytes(NOTES)
    (source / 'more.txt').write_bytes(more)
    for name in ('notes', 'more'):
        run_program(
            [which('par2'), 'create', '-r100', '-n1', '-s1024', '-q',
             str(source / ('%s.par2' % name)),
             str(source / ('%s.txt' % name))], check=True)

    files = [post(server, tmp_path, 'notes.txt', NOTES, parts=4),
             post(server, tmp_path, 'more.txt', more, parts=2)]
    for name in sorted(os.listdir(source)):
        if name.endswith('.par2'):
            files.append(post(server, tmp_path, name,
                              (source / name).read_bytes()))

    # A part of notes.txt (its set is checked second) is gone
    server.on(r'^ARTICLE <notes.txt.2@example.com>$',
              Reply('430 No such article'))
    nzb = write_nzb(tmp_path / 'release.nzb', files)

    get = downloader()
    assert get.download(nzb) is True, get.problem
    assert get.repaired
    placed = tmp_path / 'Downloads' / 'release'
    assert (placed / 'notes.txt').read_bytes() == NOTES
    assert (placed / 'more.txt').read_bytes() == more
