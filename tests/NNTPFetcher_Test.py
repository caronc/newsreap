# Test downloading what a search found as it was posted (nr search -d)
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

import pytest
from click.testing import CliRunner
from sqlalchemy.exc import OperationalError

from newsreap.NNTPFetcher import NNTPFetcher
from newsreap.NNTPFetcher import file_parts
from newsreap.NNTPIndexer import NNTPIndexer

from tests.FakeNNTPServer import FakeNNTPServer
from tests.FakeNNTPServer import Reply
from tests.NNTPDownloader_Test import GROUP
from tests.NNTPDownloader_Test import NOTES
from tests.NNTPDownloader_Test import README
from tests.NNTPDownloader_Test import asked
from tests.NNTPDownloader_Test import post
from tests.NNTPDownloader_Test import write_nzb
from tests.conftest import config_for

# An article that is only text
CHATTER = b'Just a few words about the release.\r\n'
CHATTER_ID = 'chatter.1@example.com'


def chatter(server, tmp_path):
    """
    Puts an article that is only text on the server; returns what an NZB
    file lists of it
    """
    path = tmp_path / 'chatter.msg'
    path.write_bytes(
        b'Subject: about the release\r\nFrom: me@example.com\r\n'
        b'Message-ID: <%s>\r\n\r\n%s' % (CHATTER_ID.encode(), CHATTER))
    server.add_article(CHATTER_ID, str(path), GROUP)
    return 'chatter', [(1, CHATTER_ID)]


@pytest.fixture
def indexed(tmp_path):
    """
    A test server holding a release (two files and a text article),
    imported into the index; yields (server, nr) where nr runs a command
    """
    from newsreap.cli import cli

    with FakeNNTPServer(join_group=False) as server:
        nzb = write_nzb(tmp_path / 'release.nzb', [
            post(server, tmp_path, 'notes.txt', NOTES, parts=3),
            post(server, tmp_path, 'readme.nfo', README),
            chatter(server, tmp_path)])
        config = config_for(server, tmp_path / 'config.yaml')
        runner = CliRunner()

        def nr(*args, **kwargs):
            return runner.invoke(cli, ['--config', config, *args], obj={},
                                 **kwargs)

        assert nr('db', 'init').exit_code == 0
        assert nr('nzb', 'import', nzb).exit_code == 0
        yield server, nr


def test_found_files_are_downloaded_as_posted(tmp_path, indexed):
    """
    -d puts the files together (nothing more) in a directory named after
    what was found; running it again fetches nothing (a file without a
    .ERRORS file is whole), and a file that is gone, or whose .ERRORS file
    can't be read, is fetched again
    """
    server, nr = indexed
    out = tmp_path / 'out' / 'release'

    result = nr('-y', 'search', 'release', '-d', '-o', str(tmp_path / 'out'))
    assert result.exit_code == 0, result.output
    assert (out / 'notes.txt').read_bytes() == NOTES
    assert (out / 'readme.nfo').read_bytes() == README

    # The text article isn't a file, so nothing is written of it
    assert sorted(os.listdir(str(out))) == ['notes.txt', 'readme.nfo']
    assert 'Saved in %s' % out in result.output

    # Again: everything is there, so nothing is fetched
    before = len(server.commands())
    result = nr('-y', 'search', 'release', '-d', '-o', str(tmp_path / 'out'))
    assert result.exit_code == 0, result.output
    assert not [c for c in server.commands()[before:]
                if c.startswith('ARTICLE')]
    assert '2 already there' in result.output

    # A file that is gone, or whose .ERRORS file can't be read, is fetched
    # again (only its articles); the .ERRORS file goes once it is whole
    for spoil in (lambda: (out / 'notes.txt').unlink(),
                  lambda: (out / 'notes.txt.ERRORS').write_text('{ broken')):
        spoil()
        before = len(server.commands())
        result = nr('-y', 'search', 'release', '-d', '-o',
                    str(tmp_path / 'out'))
        assert result.exit_code == 0, result.output
        assert {c.split('<', 1)[1].rstrip('>')
                for c in server.commands()[before:]
                if c.startswith('ARTICLE')} == {
            'notes.txt.%d@example.com' % no for no in (1, 2, 3)}
        assert (out / 'notes.txt').read_bytes() == NOTES
        assert sorted(os.listdir(str(out))) == ['notes.txt', 'readme.nfo']


def test_text_and_articles_are_saved_and_each_fetched_once(
        tmp_path, indexed):
    """
    -x saves the text article (with a few headers), -a every article as
    it was sent; asked together, each article is fetched once, and what
    is known to carry a file isn't fetched for its text
    """
    server, nr = indexed
    out = tmp_path / 'Downloads' / 'release'

    result = nr('-y', 'search', 'release', '-x', '-a', '-d', '-o',
                str(tmp_path / 'Downloads'))
    assert result.exit_code == 0, result.output
    commands = [c for c in server.commands() if c.startswith('ARTICLE')]
    assert len(commands) == len(set(commands)) == 5

    text = (out / 'text' / ('%s.txt' % CHATTER_ID)).read_text()
    assert text.startswith('Subject: about the release\nFrom: me@example.com')
    assert text.rstrip().endswith('Just a few words about the release.')
    assert sorted(os.listdir(str(out / 'articles'))) == sorted(
        '%s.eml' % m for m in asked(server))
    assert (out / 'articles' / ('%s.eml' % CHATTER_ID)).read_bytes() == \
        (tmp_path / 'chatter.msg').read_bytes()
    assert sorted(os.listdir(str(out))) == [
        'articles', 'notes.txt', 'readme.nfo', 'text']

    # Again with only -x: nothing is fetched (it's all there and right)
    before = len(server.commands())
    assert nr('-y', 'search', 'release', '-x', '-o',
              str(tmp_path / 'Downloads')).exit_code == 0
    assert not [c for c in server.commands()[before:]
                if c.startswith('ARTICLE')]

    # A text file that is gone is written again from the archive (the
    # text article was kept there when it was fetched); nothing is fetched
    (out / 'text' / ('%s.txt' % CHATTER_ID)).unlink()
    before = len(server.commands())
    assert nr('-y', 'search', 'release', '-x', '-o',
              str(tmp_path / 'Downloads')).exit_code == 0
    assert not [c for c in server.commands()[before:]
                if c.startswith('ARTICLE')]
    assert 'Just a few words' in (
        out / 'text' / ('%s.txt' % CHATTER_ID)).read_text()

    # It can be searched as a post now too
    assert nr('search', '--posts', 'words', '-q').output.split() == [
        CHATTER_ID]


def test_only_text_is_fetched_once_kinds_are_known(tmp_path, indexed):
    """
    -x on its own fetches every article the first time (to tell text from
    files); after that nothing is fetched (the text article was kept in
    the archive, and the others are known to carry files)
    """
    server, nr = indexed
    out = tmp_path / 'Downloads' / 'release'
    assert nr('-y', 'search', 'release', '-x', '-o',
              str(tmp_path / 'Downloads')).exit_code == 0
    assert len(asked(server)) == 5
    assert os.listdir(str(out)) == ['text']

    os.remove(str(out / 'text' / ('%s.txt' % CHATTER_ID)))
    before = len(server.commands())
    assert nr('-y', 'search', 'release', '-x', '-o',
              str(tmp_path / 'Downloads')).exit_code == 0
    assert not [c for c in server.commands()[before:]
                if c.startswith('ARTICLE')]
    assert (out / 'text' / ('%s.txt' % CHATTER_ID)).exists()


def test_nothing_is_removed_until_it_is_all_saved(tmp_path, indexed):
    """
    An article no server has makes the download fail (exit 1) and keeps
    the collection (--delete removes nothing); what arrived is placed.
    It is an old post, so running it again doesn't ask for it again (its
    .ERRORS file remembers); --retry-missing does, and once it is all
    there --delete removes the collection.
    """
    server, nr = indexed
    out = tmp_path / 'out' / 'release'
    server.on(r'^ARTICLE <notes.txt.2@example.com>$',
              Reply('430 No such article'), once=True)

    result = nr('-y', 'search', 'release', '-d', '--delete', '-o',
                str(tmp_path / 'out'))
    assert result.exit_code == 1, result.output
    assert '1 of them could not all be saved' in result.output
    assert 'Removed' not in result.output
    assert (out / 'notes.txt').stat().st_size == len(NOTES)
    assert (out / 'notes.txt').read_bytes() != NOTES
    assert '<notes.txt.2@example.com>: no server has it (asked for again ' \
        'only once you add a server)' in (
            out / 'notes.txt.ERRORS').read_text()
    assert nr('search', 'release', '-q').output.strip()

    # Asking again can't help, so it isn't asked for; even with the staging
    # directory gone (and the parts that came with it, fetched again), the
    # .ERRORS file remembers
    for staging in (tmp_path / 'cache' / 'downloads').iterdir():
        NNTPFetcher.remove_staging(str(staging))
    before = len(server.commands())
    result = nr('-y', 'search', 'release', '-d', '--delete', '-o',
                str(tmp_path / 'out'))
    assert result.exit_code == 1, result.output
    assert '1 not asked for again' in result.output
    assert 'ARTICLE <notes.txt.2@example.com>' not in server.commands()[
        before:]
    assert (out / 'notes.txt.ERRORS').exists()

    before = len(server.commands())
    result = nr('-y', 'search', 'release', '-d', '--delete', '-o',
                str(tmp_path / 'out'), '--retry-missing')
    assert result.exit_code == 0, result.output
    assert [c for c in server.commands()[before:]
            if c.startswith('ARTICLE')] == [
        'ARTICLE <notes.txt.2@example.com>']
    assert (out / 'notes.txt').read_bytes() == NOTES
    assert not (out / 'notes.txt.ERRORS').exists()
    assert 'Removed 1 collection(s).' in result.output
    assert nr('search', 'release', '-q').output.strip() == ''


def test_a_damaged_part_is_fetched_again_once_staging_is_gone(
        tmp_path, indexed):
    """
    A part that came damaged is kept in the staging directory; once that
    is removed, the part is asked for again (rather than left as zeros)
    """
    from tests.NNTPDownloader_Test import yenc_article

    server, nr = indexed
    out = tmp_path / 'out' / 'release'
    step = -(-len(NOTES) // 3)
    damaged = tmp_path / 'damaged.msg'
    damaged.write_bytes(yenc_article(
        'notes.txt', NOTES[2 * step:], 3, 3, 2 * step, len(NOTES),
        'notes.txt.3@example.com', damaged=True))
    server.on(r'^ARTICLE <notes.txt.3@example.com>$', lambda s, m: Reply(
        '220 0 <notes.txt.3@example.com>', body=damaged.read_bytes()),
        once=True)

    result = nr('-y', 'search', 'release', '-d', '-o', str(tmp_path / 'out'))
    assert result.exit_code == 1, result.output
    assert (out / 'notes.txt.ERRORS').exists()

    for staging in (tmp_path / 'cache' / 'downloads').iterdir():
        NNTPFetcher.remove_staging(str(staging))
    before = len(server.commands())
    result = nr('-y', 'search', 'release', '-d', '-o', str(tmp_path / 'out'))
    assert 'ARTICLE <notes.txt.3@example.com>' in server.commands()[before:]
    assert result.exit_code == 0, result.output
    assert (out / 'notes.txt').read_bytes() == NOTES
    assert not (out / 'notes.txt.ERRORS').exists()


def test_an_nzb_file_is_saved_and_only_replaced_when_it_changes(
        tmp_path, indexed):
    """
    -z saves an NZB file of each collection found beside its directory;
    one already holding the same is left as it is
    """
    server, nr = indexed
    path = tmp_path / 'out' / 'release.nzb'
    assert nr('-y', 'search', 'release', '-z', '-o',
              str(tmp_path / 'out')).exit_code == 0
    assert 'notes.txt.3@example.com' in path.read_text()
    assert not [c for c in server.commands() if c.startswith('ARTICLE')]

    os.utime(str(path), (1000, 1000))
    assert nr('-y', 'search', 'release', '-z', '-o',
              str(tmp_path / 'out')).exit_code == 0
    assert path.stat().st_mtime == 1000
    assert sorted(os.listdir(str(tmp_path / 'out'))) == ['release.nzb']


def test_download_options_that_dont_fit_are_refused(tmp_path, indexed):
    """
    --download-dir needs something to download, downloads can't be JSON,
    and nothing is downloaded when the answer is no
    """
    server, nr = indexed
    assert nr('search', 'release', '-o', str(tmp_path)).exit_code == 1
    assert nr('search', 'release', '-d', '--json').exit_code == 1

    result = nr('search', 'release', '-d', '-o', str(tmp_path / 'out'),
                input='n\n')
    assert 'Nothing was downloaded.' in result.output
    assert not (tmp_path / 'out').exists()


def test_a_download_carries_on_when_the_index_cant_be_written(
        tmp_path, indexed, monkeypatch):
    """
    Not being able to note what was learned only warns; the download
    still works, and the next run fetches the text article again to find
    out what it holds (the files are there, so they aren't)
    """
    from sqlalchemy.orm import Session
    from sqlalchemy.sql.dml import Update

    server, nr = indexed
    execute = Session.execute

    def broken(self, statement, *args, **kwargs):
        if isinstance(statement, Update) and statement.table.name in (
                'segment', 'collection_file'):
            # What a download learned can't be kept in the index
            raise OperationalError('UPDATE', {}, Exception('disk I/O error'))
        return execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(Session, 'execute', broken)
    result = nr('-y', 'search', 'release', '-d', '-o', str(tmp_path / 'out'))
    assert result.exit_code == 0, result.output
    assert (tmp_path / 'out' / 'release' / 'notes.txt').read_bytes() == NOTES
    assert "couldn't be noted in the index" in result.output

    # Nothing was noted, so the text article is fetched again
    monkeypatch.undo()
    before = len(server.commands())
    assert nr('-y', 'search', 'release', '-d', '-o',
              str(tmp_path / 'out')).exit_code == 0
    assert [c for c in server.commands()[before:]
            if c.startswith('ARTICLE')] == ['ARTICLE <%s>' % CHATTER_ID]


def test_what_a_download_learns_is_kept_in_the_index(tmp_path, indexed):
    """
    Whether each article carries a file or is only text is kept with the
    index
    """
    server, nr = indexed
    assert nr('-y', 'search', 'release', '-d', '-o',
              str(tmp_path / 'out')).exit_code == 0

    indexer = NNTPIndexer(str(tmp_path / 'index.db'))
    try:
        _, known = indexer.fetch_files(1)
        assert known[CHATTER_ID].kind == 0
        assert known['notes.txt.1@example.com'].kind == 1
        assert len(known) == 5

    finally:
        indexer.close()


def test_loose_articles_become_files_by_their_subjects():
    """
    Articles that aren't indexed are put together by the file their
    subjects name (in their group); any other is a file of its own
    """
    files = NNTPFetcher.files_of([
        {'message_id': '<a@x>', 'subject': '"a.rar" yEnc (1/2)',
         'group': 'g'},
        {'message_id': 'b@x', 'subject': 'hello', 'group': 'g'},
        {'message_id': 'c@x', 'subject': '"a.rar" yEnc (2/2)',
         'group': 'g'},
        {'message_id': 'd@x', 'subject': '"a.rar" yEnc (1/1)',
         'group': 'other'}])

    assert [(f['name'], f['group'], f['segments']) for f in files] == [
        ('a.rar', 'g', [(1, 'a@x'), (2, 'c@x')]),
        ('', 'g', [(1, 'b@x')]),
        ('a.rar', 'other', [(1, 'd@x')])]


def test_file_parts_are_read_from_an_article():
    """
    The yEnc and uuencoded parts an article's body holds are decoded
    """
    with open(os.path.join(os.path.dirname(__file__), 'var',
                           '00000005.ntx'), 'rb') as fp:
        body = fp.read().split(b'\r\n\r\n', 1)[1]

    found = list(file_parts(body))
    assert [(p.filename, p.is_valid()) for p in found] == [
        ('testfile.txt', True)]
    assert list(file_parts(b'just text\r\n')) == []


def test_articles_and_posts_found_are_downloaded(tmp_path):
    """
    Articles found that aren't indexed are put together by the file their
    subjects name (--articles -d), and the text 'nr update --content'
    kept is written without fetching anything (--posts -x); --delete
    then removes what was saved
    """
    from datetime import datetime
    from newsreap.cli import cli

    group_list = tmp_path / 'group.list'
    group_list.write_text('%s 0000000004 0000000001 y\n' % GROUP)
    posted = datetime(2026, 1, 1, 12, 0)

    with FakeNNTPServer(join_group=False,
                        group_list=str(group_list)) as server:
        _, segments = post(server, tmp_path, 'notes.txt', NOTES, parts=3)
        for no, message_id in segments:
            server.add_overview(
                GROUP, no, '"notes.txt" yEnc (%d/3)' % no, 'me@example.com',
                posted, message_id, size=500)
        chatter(server, tmp_path)
        server.add_overview(GROUP, 4, 'about the release', 'me@example.com',
                            posted, CHATTER_ID, size=100)

        config = config_for(server, tmp_path / 'config.yaml')
        runner = CliRunner()

        def nr(*args, **kwargs):
            return runner.invoke(cli, ['--config', config, *args], obj={},
                                 **kwargs)

        assert nr('db', 'init').exit_code == 0
        assert nr('update', 'groups').exit_code == 0
        assert nr('update', GROUP, '--no-index').exit_code == 0

        # Either word finds the articles (and - in front leaves them out)
        assert len(nr('search', '--articles', '-g', GROUP, 'notes|about',
                      '-q').output.split()) == 4
        assert nr('search', '--articles', '-g', GROUP, '-q', '--',
                  '-notes|x').output.split() == [CHATTER_ID]

        out = tmp_path / 'out'
        result = nr('-y', 'search', '--articles', '-g', GROUP, 'notes',
                    '-d', '--delete', '-o', str(out))
        assert result.exit_code == 0, result.output
        assert (out / 'notes' / 'notes.txt').read_bytes() == NOTES
        assert 'Removed 3 article(s).' in result.output
        assert nr('search', '--articles', '-g', GROUP, 'notes',
                  '-q').output == ''

        # The text article, kept offline, is written without fetching it
        assert nr('update', GROUP, '--content').exit_code == 0
        before = len(server.commands())
        result = nr('-y', 'search', '--posts', 'release', '-x', '-o',
                    str(out))
        assert result.exit_code == 0, result.output
        assert not [c for c in server.commands()[before:]
                    if c.startswith('ARTICLE')]
        text = (out / 'release' / 'text' / ('%s.txt' % CHATTER_ID)
                ).read_text()
        assert 'Subject: about the release' in text
        assert 'Just a few words about the release.' in text

        # Both kinds at once: an NZB file of each, under names of their own
        result = nr('-y', 'search', '--articles', '--posts', '-g', GROUP,
                    'release', '-z', '-o', str(tmp_path / 'nzbs'))
        assert result.exit_code == 0, result.output
        assert sorted(os.listdir(str(tmp_path / 'nzbs'))) == [
            'release.articles.nzb', 'release.posts.nzb']


def test_what_is_learned_of_articles_not_indexed_is_skipped(
        tmp_path, indexed):
    """
    Noting what was learned skips the articles the index doesn't have (ie:
    only cached ones), and still notes the rest
    """
    from newsreap.NNTPIndexer import SegmentNote

    indexer = NNTPIndexer(str(tmp_path / 'index.db'))
    try:
        assert indexer.note_fetched(segments={
            CHATTER_ID: SegmentNote(0),
            'not.indexed@example.com': SegmentNote(1)}) is True
        _, known = indexer.fetch_files(1)
        assert known == {CHATTER_ID: SegmentNote(0)}

    finally:
        indexer.close()


def test_what_the_archive_knows_saves_fetching(tmp_path):
    """
    After 'nr update --content' kept the small articles, -x fetches
    nothing: the text is written from the archive, and the articles it
    knows carry files are left alone; each article's kind is noted with
    the index too
    """
    from datetime import datetime
    from newsreap.cli import cli

    group_list = tmp_path / 'group.list'
    group_list.write_text('%s 0000000004 0000000001 y\n' % GROUP)
    posted = datetime(2026, 1, 1, 12, 0)

    with FakeNNTPServer(join_group=False,
                        group_list=str(group_list)) as server:
        _, segments = post(server, tmp_path, 'notes.txt', NOTES, parts=3)
        for no, message_id in segments:
            server.add_overview(
                GROUP, no, '"notes.txt" yEnc (%d/3)' % no, 'me@example.com',
                posted, message_id, size=500)
        chatter(server, tmp_path)
        server.add_overview(GROUP, 4, 'about the release', 'me@example.com',
                            posted, CHATTER_ID, size=100)

        config = config_for(server, tmp_path / 'config.yaml')
        runner = CliRunner()

        def nr(*args, **kwargs):
            return runner.invoke(cli, ['--config', config, *args], obj={},
                                 **kwargs)

        assert nr('db', 'init').exit_code == 0
        assert nr('update', 'groups').exit_code == 0
        assert nr('update', GROUP, '--content').exit_code == 0
        assert len(asked(server)) == 4

        before = len(server.commands())
        result = nr('-y', 'search', '-x', '-o', str(tmp_path / 'out'))
        assert result.exit_code == 0, result.output
        assert not [c for c in server.commands()[before:]
                    if c.startswith('ARTICLE')]
        found = [name for _, _, names in os.walk(str(tmp_path / 'out'))
                 for name in names]
        assert found == ['%s.txt' % CHATTER_ID]

        indexer = NNTPIndexer(str(tmp_path / 'index.db'))
        try:
            known = {}
            for collection in indexer.find():
                known.update(indexer.fetch_files(collection.id)[1])
            assert known[CHATTER_ID].kind == 0
            assert known[segments[0][1]].kind == 1

        finally:
            indexer.close()


def test_some_of_a_files_articles_are_marked_partial(tmp_path, indexed):
    """
    Asking for only some of a file's articles marks it partial
    """
    indexer = NNTPIndexer(str(tmp_path / 'index.db'))
    try:
        files, _ = indexer.fetch_files(
            message_ids=['notes.txt.1@example.com'])
        assert [(f['name'], f['segments'], f['partial']) for f in files] == [
            ('notes.txt', [(1, 'notes.txt.1@example.com')], True)]

        files, _ = indexer.fetch_files(1)
        assert not any(f['partial'] for f in files)

    finally:
        indexer.close()


def test_a_copy_already_there_is_left_alone(tmp_path, indexed):
    """
    A file already in place without a .ERRORS file beside it is whole, so
    nothing is fetched for it and it is left as it is
    """
    server, nr = indexed
    out = tmp_path / 'out' / 'release'
    out.mkdir(parents=True)
    (out / 'notes.txt').write_bytes(b'my own copy')

    result = nr('-y', 'search', 'release', '-d', '-o', str(tmp_path / 'out'))
    assert result.exit_code == 0, result.output
    assert (out / 'notes.txt').read_bytes() == b'my own copy'
    assert not [c for c in server.commands() if 'notes.txt' in c]
    assert sorted(os.listdir(str(out))) == ['notes.txt', 'readme.nfo']


def test_text_no_server_has_is_listed_in_text_errors(tmp_path, indexed):
    """
    -x: an article no server has is listed in text.ERRORS; it is an old
    post, so the next run doesn't ask for it again; once it comes
    (--retry-missing), text.ERRORS goes
    """
    server, nr = indexed
    out = tmp_path / 'out' / 'release'
    server.on(r'^ARTICLE <%s>$' % CHATTER_ID, Reply('430 No such article'),
              once=True)

    result = nr('-y', 'search', 'release', '-x', '-o', str(tmp_path / 'out'))
    assert result.exit_code == 1, result.output
    assert '<%s>: no server has it (asked for again only once you add a ' \
        'server)' % CHATTER_ID in (out / 'text.ERRORS').read_text()

    before = len(server.commands())
    result = nr('-y', 'search', 'release', '-x', '-o', str(tmp_path / 'out'))
    assert result.exit_code == 1, result.output
    assert not [c for c in server.commands()[before:]
                if c.startswith('ARTICLE')]
    assert (out / 'text.ERRORS').exists()

    result = nr('-y', 'search', 'release', '-x', '-o', str(tmp_path / 'out'),
                '--retry-missing')
    assert result.exit_code == 0, result.output
    assert sorted(os.listdir(str(out))) == ['text']


def test_message_ids_never_share_a_file_name():
    """
    Message-IDs that tidy up the same still get names of their own; one
    that needs no tidying keeps its name
    """
    names = {NNTPFetcher._id_name(m) for m in (
        'part1/abc@host', 'part1.abc@host', 'part1$abc@host')}
    assert len(names) == 3
    assert NNTPFetcher._id_name('part1.abc@host') == 'part1.abc@host'
