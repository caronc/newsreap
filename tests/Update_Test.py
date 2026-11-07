# Test fetching groups with nr update (headers, content, NZB files, scores)
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


from datetime import timedelta
from zlib import crc32

from click.testing import CliRunner

from newsreap.NNTPNzbReader import NNTPNzbReader
from newsreap.codecs.CodecYenc import yenc_encode

from tests.FakeNNTPServer import FakeNNTPServer
from tests.NNTPArchiver_Test import article
from tests.NNTPIndexer_Test import GROUP
from tests.NNTPIndexer_Test import OTHER
from tests.NNTPIndexer_Test import POSTED
from tests.NNTPIndexer_Test import POSTER
from tests.NNTPIndexer_Test import nzb_file
from tests.NNTPIndexer_Test import release
from tests.conftest import config_for


def yenc_article(name, data, **headers):
    """
    Returns an article holding a file (yEnc encoded, in one part)
    """
    encoded, _ = yenc_encode(data)
    return article(
        b'=ybegin line=128 size=%d name=%s\r\n%s\r\n'
        b'=yend size=%d crc32=%08x\r\n' % (
            len(data), name.encode(), encoded.rstrip(b'\r\n'), len(data),
            crc32(data)), **headers)


def fetched(server):
    """
    Returns how many articles the server was asked for
    """
    return len([c for c in server.commands() if c.startswith('ARTICLE')])


def test_nzb_files_are_read_from_what_they_hold(tmp_path):
    """
    An NZB file posted yEnc encoded (or as it is) is turned back into the
    file; anything else isn't one
    """
    nzb = nzb_file(tmp_path / 'a.nzb', 'Holiday', (1,), total=1)
    with open(nzb, 'rb') as fp:
        data = fp.read()

    work_dir = str(tmp_path)
    assert NNTPNzbReader.decode(
        [yenc_article('a.nzb', data)], work_dir) == data
    assert NNTPNzbReader.decode([article(data)], work_dir).replace(
        b'\r\n', b'\n') == data.rstrip()
    assert NNTPNzbReader.decode([article('just words')], work_dir) is None
    assert NNTPNzbReader.decode([None], work_dir) is None


def test_one_update_fetches_headers_content_and_nzb_files(tmp_path):
    """
    nr update fetches the headers, indexes them, keeps the small articles'
    text (--content) and reads the NZB files posted (--nzb); running it
    again fetches nothing it already has
    """
    from newsreap.cli import cli

    group_list = tmp_path / 'group.list'
    group_list.write_text('%s 0000000003 0000000001 y\n' % GROUP)

    nzb = nzb_file(tmp_path / 'holiday.nzb', 'Holiday', (1, 2), total=2)
    with open(nzb, 'rb') as fp:
        nzb_data = fp.read()

    articles = {
        'talk@x': (article('Has anyone been to the café?',
                           Subject='Coffee'), 'Coffee'),
        'nzb@x': (yenc_article('holiday.nzb', nzb_data,
                               Subject='holiday.nzb'),
                  'Holiday [1/1] - "holiday.nzb" yEnc (1/1)'),
        'junk@x': (article('not an nzb file at all'),
                   'Junk [1/1] - "junk.nzb" yEnc (1/1)'),
    }

    with FakeNNTPServer(group_list=str(group_list),
                        join_group=False) as server:
        server.add_group(GROUP, 3, 1, 3)
        for no, (message_id, (raw, subject)) in enumerate(
                articles.items(), 1):
            path = tmp_path / ('%d.msg' % no)
            path.write_bytes(raw)
            server.add_article(message_id, str(path), GROUP)
            server.add_overview(
                GROUP, no, subject, POSTER, POSTED + timedelta(minutes=no),
                message_id, size=len(raw))

        config = config_for(server, tmp_path / 'config.yaml')
        runner = CliRunner()

        def nr(*args, **kwargs):
            return runner.invoke(cli, ['--config', config, *args], obj={},
                                 **kwargs)

        assert nr('db', 'init').exit_code == 0
        assert nr('update', 'groups').exit_code == 0

        # Every switch at once
        result = nr('update', GROUP, '--content', '--nzb')
        assert result.exit_code == 0, result.output
        assert '%s: 3 article(s) cached' % GROUP in result.output
        assert '%s: 3 article(s) indexed' % GROUP in result.output
        assert '%s: 1 NZB file(s) read (1 new collection(s)), 1 unusable' \
            % GROUP in result.output

        # The NZB file's post is in the index, the text can be searched
        assert '2/2 file(s)  holiday' in nr('search', 'holiday').output
        assert '<talk@x>' in nr('search', '--posts', 'cafe').output

        # Written straight out when asked
        result = nr('search', '--no-pager', 'holiday')
        assert result.exit_code == 0, result.output
        assert '2/2 file(s)  holiday' in result.output

        # At most as many as asked for, of every kind
        def rows(*args):
            result = nr('search', *args)
            assert result.exit_code == 0, result.output
            return [line for line in result.output.splitlines()
                    if line.startswith(('[', '  ['))]

        assert len(rows('--raw', '-g', GROUP)) == 3
        assert len(rows('--raw', '-g', GROUP, '--limit', '1')) == 1
        assert len(rows('--limit', '1')) == 1
        assert len(rows('--raw', '--collections', '-g', GROUP, '-l', '1')) \
            == 2

        # Nothing is fetched twice
        count = fetched(server)
        result = nr('update', GROUP, '--content', '--nzb')
        assert result.exit_code == 0, result.output
        assert 'no new NZB files (2 read before)' in result.output
        assert fetched(server) == count

        # Unless asked to
        result = nr('update', 'nzb', GROUP, '--force')
        assert result.exit_code == 0, result.output
        assert fetched(server) == count + 2

        # Scores are worked out again without fetching anything
        (tmp_path / 'filters.yaml').write_text(
            'filters:\n  - name: talk\n    subject: Coffee\n    score: 5\n')
        commands = len(server.commands())
        result = nr('update', GROUP, '--rescore')
        assert result.exit_code == 0, result.output
        assert '1 rescored' in result.output
        assert len(server.commands()) == commands
        assert '{+5}' in nr('search', '--posts', 'cafe').output

        # --rescore never fetches headers, so it can't be told which
        result = nr('update', GROUP, '--rescore', '--newest', '10')
        assert result.exit_code == 1


def test_groups_can_be_told_to_always_keep_content_or_read_nzb_files(
        tmp_path):
    """
    A group can be told what nr update always does for it ('nr group watch
    --content --no-nzb'); the configuration file decides for the rest, and
    the command line still wins for one run
    """
    import sqlite3
    from newsreap.cli import cli

    group_list = tmp_path / 'group.list'
    group_list.write_text('%s 0000000002 0000000001 y\n' % GROUP)

    nzb = nzb_file(tmp_path / 'holiday.nzb', 'Holiday', (1, 2), total=2)
    with open(nzb, 'rb') as fp:
        nzb_data = fp.read()

    articles = {
        'talk@x': (article('Has anyone been to the café?',
                           Subject='Coffee'), 'Coffee'),
        'nzb@x': (yenc_article('holiday.nzb', nzb_data,
                               Subject='holiday.nzb'),
                  'Holiday [1/1] - "holiday.nzb" yEnc (1/1)'),
    }

    with FakeNNTPServer(group_list=str(group_list),
                        join_group=False) as server:
        server.add_group(GROUP, 2, 1, 2)
        for no, (message_id, (raw, subject)) in enumerate(
                articles.items(), 1):
            path = tmp_path / ('%d.msg' % no)
            path.write_bytes(raw)
            server.add_article(message_id, str(path), GROUP)
            server.add_overview(
                GROUP, no, subject, POSTER, POSTED + timedelta(minutes=no),
                message_id, size=len(raw))

        # Every group reads its NZB files unless it says otherwise
        config = config_for(server, tmp_path / 'config.yaml', nzb='yes')
        runner = CliRunner()

        def nr(*args, **kwargs):
            return runner.invoke(cli, ['--config', config, *args], obj={},
                                 **kwargs)

        assert nr('db', 'init').exit_code == 0
        assert nr('update', 'groups').exit_code == 0

        # A database made before group options existed still works
        db = sqlite3.connect(tmp_path / 'newsreap.db')
        db.execute('DROP TABLE group_option')
        db.commit()
        db.close()
        assert nr('group', 'list').exit_code == 0

        result = nr('group', 'watch', GROUP, '--content', '--no-nzb')
        assert result.exit_code == 0, result.output
        assert '%s: content, no nzb' % GROUP in result.output
        assert '(content, no nzb)' in nr('group', 'list').output
        assert 'no content, nzb' in nr('status').output

        # Left out for one run
        result = nr('update', '--watched', '--no-content')
        assert result.exit_code == 0, result.output
        assert 'article(s) kept' not in result.output
        assert 'NZB file(s)' not in result.output
        assert fetched(server) == 0

        # As the group says
        result = nr('update', '--watched')
        assert result.exit_code == 0, result.output
        assert 'article(s) kept' in result.output
        assert 'NZB file(s)' not in result.output

        # --rescore only does what it is asked
        result = nr('update', GROUP, '--rescore')
        assert result.exit_code == 0, result.output
        assert 'NZB file(s)' not in result.output

        # Back to what the configuration file says
        result = nr('group', 'watch', GROUP, '--default')
        assert '%s: as the configuration file says' % GROUP in result.output
        result = nr('update', GROUP, '--cached')
        assert result.exit_code == 0, result.output
        assert '1 NZB file(s) read' in result.output
        assert 'article(s) kept' not in result.output

        assert nr('group', 'watch', GROUP, '--default', '--nzb')\
            .exit_code == 1


def test_the_update_steps_can_still_be_run_on_their_own(tmp_path):
    """
    nr update groups/search/index/archive/nzb are still there, and
    'nr update' explains itself
    """
    from newsreap.cli import cli

    with FakeNNTPServer() as server:
        config = config_for(server, tmp_path / 'config.yaml')
        runner = CliRunner()

        def nr(*args, **kwargs):
            return runner.invoke(cli, ['--config', config, *args], obj={},
                                 **kwargs)

        result = nr('update', '--help')
        assert result.exit_code == 0
        assert ' update [OPTIONS] [GROUPS]...' in result.output.split('\n')[0]
        for name in ('--content', '--nzb', '--rescore', 'archive', 'groups',
                     'index', 'nzb', 'search'):
            assert name in result.output

        assert nr('db', 'init').exit_code == 0

        # A group we don't know of
        result = nr('update', 'alt.nothing.here')
        assert result.exit_code == 1


def test_articles_can_be_shown_and_fetched(tmp_path):
    """
    nr show tells everything known of articles (a small one is fetched
    once and kept; only the start of a big one is fetched); nr fetch saves
    what they hold
    """
    from newsreap.cli import cli

    group_list = tmp_path / 'group.list'
    group_list.write_text('%s 0000000003 0000000001 y\n' % GROUP)

    data = bytes(range(256)) * 400
    articles = {
        'talk@x': (article('Has anyone been to the café?\nIt is new.',
                           Subject='Coffee'), 'Coffee'),
        'big@x': (yenc_article('big.bin', data, Subject='big'),
                  'Big [1/1] - "big.bin" yEnc (1/1)'),
    }

    with FakeNNTPServer(group_list=str(group_list),
                        join_group=False) as server:
        server.add_group(GROUP, 3, 1, 3)
        for no, (message_id, (raw, subject)) in enumerate(
                articles.items(), 1):
            path = tmp_path / ('%d.msg' % no)
            path.write_bytes(raw)
            server.add_article(message_id, str(path), GROUP)
            server.add_overview(
                GROUP, no, subject, POSTER, POSTED + timedelta(minutes=no),
                message_id, size=len(raw))

        # Listed, but no server has it
        server.add_overview(
            GROUP, 3, 'Gone', POSTER, POSTED, 'gone@x', size=100)

        config = config_for(server, tmp_path / 'config.yaml')
        runner = CliRunner()

        def nr(*args, **kwargs):
            return runner.invoke(cli, ['--config', config, *args], obj={},
                                 **kwargs)

        assert nr('db', 'init').exit_code == 0
        assert nr('update', 'groups').exit_code == 0
        assert nr('update', GROUP).exit_code == 0

        def logins():
            return len([c for c in server.commands()
                        if c.startswith('AUTHINFO USER')])

        # Two at once (both fetched whole, over one connection) and kept
        before = logins()
        result = nr('show', '<talk@x>', 'big@x')
        assert logins() == before + 1
        assert result.exit_code == 0, result.output
        assert 'Message-ID: <talk@x>\nSubject:    Coffee\n' in result.output
        assert 'Group:      %s (article 1)' % GROUP in result.output
        assert 'Kind:       text' in result.output
        assert 'It is new.' in result.output

        # The big one: what it holds
        assert 'Message-ID: <big@x>' in result.output
        assert 'Holds:      big.bin (yenc, 100.00KB' in result.output
        assert 'Indexed:    [' in result.output
        assert 'part 1 of big.bin' in result.output

        # Both kept: not fetched again
        count = fetched(server)
        result = nr('show', '--local', 'talk@x', 'big@x')
        assert result.exit_code == 0
        assert 'It is new.' in result.output
        assert 'Holds:      big.bin' in result.output
        assert nr('show', 'talk@x', 'big@x').exit_code == 0
        assert fetched(server) == count

        # Only the start of a big one's text is shown
        result = nr('show', 'talk@x', '--max-size', '10B', '--lines', '1')
        assert 'Has anyone been' in result.output
        assert 'It is new.' not in result.output
        assert '... (1 more line(s))' in result.output

        # Nothing known of it here; missing on the server
        assert nr('show', '--local', 'nothing@x').exit_code == 1
        result = nr('show', 'gone@x')
        assert result.exit_code == 0
        assert 'Subject:    Gone' in result.output
        assert nr('show').exit_code == 1

        # Save what they hold (named as they were posted)
        out = tmp_path / 'out'
        result = nr('fetch', 'big@x', 'talk@x', '-o', str(out))
        assert result.exit_code == 0, result.output
        assert result.output.splitlines() == [
            str(out / 'big.bin'), str(out / 'talk@x.msg')]
        assert (out / 'big.bin').read_bytes() == data
        assert b'Has anyone been' in (out / 'talk@x.msg').read_bytes()

        # Nothing is replaced unless asked
        result = nr('fetch', 'big@x', '-o', str(out))
        assert result.output.strip() == str(out / 'big.bin.1')
        result = nr('fetch', 'big@x', '-o', str(out), '--force')
        assert result.output.strip() == str(out / 'big.bin')

        # As the server sent it
        result = nr('fetch', '--raw', 'big@x', '-o', str(out))
        assert result.output.strip() == str(out / 'big@x.msg')
        assert (out / 'big@x.msg').read_bytes().startswith(b'Subject: big')

        assert nr('fetch', 'gone@x', '-o', str(out)).exit_code == 1


def test_groups_can_be_reset_and_worked_on_offline(tmp_path):
    """
    --reset removes what a group alone brought in (crossposted files stay)
    and fetches it again; --cached fetches no headers; search lists
    everything, newest or oldest first
    """
    from newsreap.cli import cli

    group_list = tmp_path / 'group.list'
    group_list.write_text('%s 0000000008 0000000001 y\n'
                          '%s 0000000004 0000000001 y\n' % (GROUP, OTHER))

    with FakeNNTPServer(group_list=str(group_list)) as server:
        both = list(release('Shared', files=2, segments=2))
        mine = list(release('Mine', files=2, segments=2))
        for no, (subject, poster, message_id) in enumerate(
                [*mine, *both], 1):
            server.add_overview(
                GROUP, no, subject, poster,
                POSTED + timedelta(days=no), message_id, size=1000)
        for no, (subject, poster, message_id) in enumerate(both, 1):
            server.add_overview(
                OTHER, no, subject, poster,
                POSTED + timedelta(days=4 + no), message_id, size=1000)

        config = config_for(server, tmp_path / 'config.yaml')
        runner = CliRunner()

        def nr(*args, **kwargs):
            return runner.invoke(cli, ['--config', config, *args], obj={},
                                 **kwargs)

        assert nr('db', 'init').exit_code == 0
        assert nr('update', 'groups').exit_code == 0
        assert nr('update', GROUP, OTHER).exit_code == 0

        # Everything, newest first (or oldest first)
        names = lambda *a: [  # noqa: E731
            line.split()[-1] for line in nr('search', *a).output.split('\n')
            if line]
        assert names() == ['Shared', 'Mine']
        assert names('--oldest-first') == ['Mine', 'Shared']
        assert names('-O') == ['Mine', 'Shared']

        # No headers are asked for
        xovers = len([c for c in server.commands() if 'OVER' in c])
        result = nr('update', GROUP, '--cached', '--nzb')
        assert result.exit_code == 0, result.output
        assert len([c for c in server.commands() if 'OVER' in c]) == xovers
        assert nr('update', GROUP, '--cached', '--days', '2').exit_code == 1

        # Start the group over; what the other group has too stays
        result = nr('update', GROUP, '--reset', '--no-index', input='n\n')
        assert result.exit_code == 1
        assert 'Nothing was reset.' in result.output
        result = nr('-y', 'update', GROUP, '--reset', '--no-index')
        assert result.exit_code == 0, result.output
        assert '%s: reset (2 file(s), 1 collection(s) and 0 post(s) ' \
            'removed)' % GROUP in result.output
        assert '%s: 8 article(s) cached' % GROUP in result.output
        assert names() == ['Shared']

        assert nr('update', 'index', GROUP).exit_code == 0
        assert names() == ['Shared', 'Mine']


def test_status_shows_everything_kept(tmp_path):
    """
    nr status gives an overview (setup, storage, groups, aliases and what
    is indexed); search can look at collections, posts and headers at once
    """
    from newsreap.cli import cli

    group_list = tmp_path / 'group.list'
    group_list.write_text('%s 0000000004 0000000001 y\n' % GROUP)

    with FakeNNTPServer(group_list=str(group_list),
                        join_group=False) as server:
        raw = article('Holiday photos are up', Subject='Holiday talk')
        path = tmp_path / 'talk.msg'
        path.write_bytes(raw)
        server.add_article('talk@x', str(path), GROUP)

        for no, (subject, poster, message_id) in enumerate(
                [*release('Holiday', files=1, segments=2),
                 ('Holiday talk', POSTER, 'talk@x')], 1):
            server.add_overview(
                GROUP, no, subject, poster, POSTED + timedelta(minutes=no),
                message_id, size=len(raw))

        config = config_for(server, tmp_path / 'config.yaml')
        runner = CliRunner()

        def nr(*args, **kwargs):
            return runner.invoke(cli, ['--config', config, *args], obj={},
                                 **kwargs)

        assert nr('db', 'init').exit_code == 0
        assert nr('update', 'groups').exit_code == 0

        # A name that isn't a group (or an alias)
        result = nr('update', 'update')
        assert result.exit_code == 1
        assert "There is no group (or alias) named 'update'" in result.output

        assert nr('update', GROUP, '--content').exit_code == 0
        assert nr('alias', 'add', 'test', GROUP).exit_code == 0

        result = nr('status')
        assert result.exit_code == 0, result.output
        for expected in (
                'Setup', 'Servers', 'Storage', 'Groups', 'Aliases',
                'Collections', 'Articles', 'Header caches:',
                'test               %s' % GROUP,
                'Kept (--content):  3 (1 text, 0 binary'):
            assert expected in result.output, expected

        line = next(row for row in result.output.splitlines()
                    if row.strip().startswith(GROUP))
        assert line.split()[1] == '3'

        # Each kind under its own heading
        result = nr('search', 'holiday', '--collections', '--posts',
                    '--raw', '-g', GROUP)
        assert result.exit_code == 0, result.output
        output = result.output
        assert output.index('Collections:') < output.index('Posts:') < \
            output.index('Articles:')
        assert '<talk@x>' in output

        # Only collections (the same as no switch at all)
        assert nr('search', '--collections', 'holiday').output == \
            nr('search', 'holiday').output

        # A condition only posts have searches posts on its own
        result = nr('search', '--ibody', '*photos*')
        assert result.exit_code == 0, result.output
        assert '<talk@x>' in result.output

        # Asked for collections, it can't be used (and says so)
        result = nr('search', '--collections', '--ibody', '*photos*')
        assert result.exit_code == 1
        assert "--ibody can't be used to search collections" in result.output

        # --articles is what --raw is now called
        assert nr('search', '--articles', '-g', GROUP).output == \
            nr('search', '--raw', '-g', GROUP).output
        assert 'Searching --articles needs a --group' in nr(
            'search', '--articles').output

        # A condition one of them can't look at
        assert nr('search', '--collections', '--posts', '--ibody',
                  '*photos*').exit_code == 1


def test_what_a_search_finds_can_be_listed_shown_or_removed(tmp_path):
    """
    A search can hand on just what it found (-q), say what each is as JSON,
    or remove it (--delete; shown first and asked, unless -y is given)
    """
    import json

    from newsreap.cli import cli

    group_list = tmp_path / 'group.list'
    group_list.write_text('%s 0000000004 0000000001 y\n' % GROUP)

    with FakeNNTPServer(group_list=str(group_list),
                        join_group=False) as server:
        for message_id, text in (('talk@x', 'Holiday photos are up'),
                                 ('more@x', 'More holiday photos')):
            path = tmp_path / ('%s.msg' % message_id)
            path.write_bytes(article(text, Subject='Holiday talk'))
            server.add_article(message_id, str(path), GROUP)

        for no, (subject, poster, message_id) in enumerate(
                [*release('Holiday', files=1, segments=2),
                 ('Holiday talk', POSTER, 'talk@x'),
                 ('Holiday talk', POSTER, 'more@x')], 1):
            server.add_overview(
                GROUP, no, subject, poster, POSTED + timedelta(minutes=no),
                message_id, size=100)

        config = config_for(server, tmp_path / 'config.yaml')
        runner = CliRunner()

        def nr(*args, **kwargs):
            return runner.invoke(cli, ['--config', config, *args], obj={},
                                 **kwargs)

        assert nr('db', 'init').exit_code == 0
        assert nr('update', 'groups').exit_code == 0
        assert nr('update', GROUP, '--content').exit_code == 0

        # Only what each is called (to hand to another command)
        result = nr('search', 'holiday', '-q')
        assert result.exit_code == 0, result.output
        collections = result.output.split()
        assert collections and all(c.isdigit() for c in collections)
        assert sorted(nr('search', '--posts', 'photos', '-q').output.split()) \
            == ['more@x', 'talk@x']

        # What each is, one JSON object per line
        rows = [json.loads(line) for line in nr(
            'search', '--articles', '-g', GROUP, '--json').output.split('\n')
            if line]
        assert {row['kind'] for row in rows} == {'article'}
        assert {'talk@x', 'more@x'} <= {row['message_id'] for row in rows}

        # A limit counts posts (not the lines that show them)
        result = nr('search', '--posts', 'photos', '-l', '1', '--no-pager')
        assert len([line for line in result.output.splitlines()
                    if line.startswith('    <')]) == 1

        # Asked first; nothing goes without a yes
        result = nr('search', '--posts', 'more', '--delete', input='n\n')
        assert 'Remove 1 post(s)?' in result.output
        assert 'Nothing was removed.' in result.output
        assert 'more@x' in nr('search', '--posts', 'photos', '-q').output

        result = nr('search', '--posts', 'more', '--delete', input='y\n')
        assert 'Removed 1 post(s).' in result.output
        assert nr('search', '--posts', 'photos', '-q').output.split() == [
            'talk@x']

        # -y answers yes; collections and articles go the same way
        result = nr('-y', 'search', 'holiday', '--delete')
        assert result.exit_code == 0, result.output
        assert 'Removed %d collection(s).' % len(collections) in \
            result.output
        assert nr('search', 'holiday', '-q').output.strip() == ''

        result = nr('-y', 'search', '--articles', '-g', GROUP,
                    '--message-id', 'talk@x', '--delete')
        assert 'Removed 1 article(s).' in result.output
        assert 'talk@x' not in nr(
            'search', '--articles', '-g', GROUP, '-q').output
        assert 'more@x' in nr(
            'search', '--articles', '-g', GROUP, '-q').output
