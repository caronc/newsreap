# Test applying filters to what is already stored (nr filter)
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

from datetime import timedelta

import pytest
from click.testing import CliRunner

from newsreap.NNTPArchivePost import NNTPArchivePost
from newsreap.NNTPArchiver import NNTPArchiver
from newsreap.NNTPFilter import NNTPFilter
from newsreap.NNTPGroupDatabase import NNTPGroupDatabase
from newsreap.NNTPIndexer import NNTPIndexer
from newsreap.NNTPPurger import NNTPPurger
from newsreap.objects.group.Article import Article
from newsreap.objects.index.ArchiveFile import ArchiveFile
from newsreap.objects.index.ArchiveGroup import ArchiveGroup
from newsreap.objects.index.ArchivedPost import ArchivedPost
from newsreap.objects.index.Collection import Collection
from newsreap.objects.index.Collection import CollectionSource
from newsreap.objects.index.CollectionFile import CollectionFile
from newsreap.objects.index.Segment import Segment

from tests.FakeNNTPServer import FakeNNTPServer
from tests.conftest import config_for
from tests.NNTPIndexer_Test import GROUP
from tests.NNTPIndexer_Test import OTHER
from tests.NNTPIndexer_Test import POSTED
from tests.NNTPIndexer_Test import POSTER
from tests.NNTPIndexer_Test import cache
from tests.NNTPIndexer_Test import index_engine  # noqa: F401
from tests.NNTPIndexer_Test import indexed
from tests.NNTPIndexer_Test import nzb_file
from tests.NNTPIndexer_Test import paths  # noqa: F401
from tests.NNTPIndexer_Test import release

SPAMMER = 'Casino <bot@spam.example>'


def headers(db_file):
    """
    Returns the Message-ID -> (score, tags) of every header cached
    """
    db = NNTPGroupDatabase.from_file(db_file)
    try:
        return {row.message_id: (row.score, row.tags) for row in
                db.session().query(
                    Article.message_id, Article.score, Article.tags)}
    finally:
        db.close()


def collections(indexer):
    """
    Returns the name -> (files, segments, score, tags) of every collection
    """
    session = indexer.session()
    session.expire_all()
    return {c.name: (c.files, c.segments, c.score, c.tags)
            for c in session.query(Collection)}


def archive(index_db, *posts):
    """
    Keeps archived posts: (message_id, subject, poster) found in GROUP
    """
    archiver = NNTPArchiver(index_db, default_hooks=False)
    session = archiver.session(create=True)
    for message_id, subject, poster in posts:
        post = NNTPArchivePost(
            message_id, raw=b'Subject: x\r\n\r\nhello\r\n', subject=subject,
            poster=poster, posted=POSTED, size=100, groups={GROUP})
        post.text = 'hello'
        post.add_file(filename='%s.txt' % message_id, codec='yenc')
        archiver.store(session, post)
    session.commit()
    return archiver


def archived(archiver):
    """
    Returns the Message-ID -> (score, tags) of every archived post
    """
    session = archiver.session()
    session.expire_all()
    return {p.message_id: (p.score, p.tags)
            for p in session.query(ArchivedPost)}


def purge(paths, rules, caches=(GROUP, ), **kwargs):
    """
    Runs a purge (with the rules given) over the header caches of the
    groups given, the index and the archive, and returns it
    """
    purger = NNTPPurger(NNTPFilter(rules=rules), **kwargs)
    for group in caches:
        purger.purge_headers(group, paths[group])
    purger.purge_index(paths['index'])
    purger.purge_archive(paths['index'])
    return purger


def test_purges_remove_matches_from_every_store(paths):
    """
    A purge removes what a rule matches from the header caches, the index
    and the archive (and a dry run only counts it)
    """
    cache(paths[GROUP], [*release('Good'), *release('Spam', poster=SPAMMER)])
    indexer = indexed(paths)
    archiver = archive(
        paths['index'], ('talk@x', 'Hello', POSTER),
        ('ad@x', 'Cheap pills', SPAMMER))

    rules = [{'name': 'spam', 'poster': 'SPAM.example'}]
    before = (headers(paths[GROUP]), collections(indexer), archived(archiver))
    assert len(before[0]) == 24

    # A dry run counts, and changes nothing
    found = purge(paths, rules, dry_run=True)
    assert (found.headers.checked, found.headers.dropped) == (24, 12)
    assert (found.index.files, found.index.collections) == (3, 1)
    assert (found.archive.checked, found.archive.dropped) == (2, 1)
    assert found.headers.samples[0][2:] == (SPAMMER, 'spam')
    assert (headers(paths[GROUP]), collections(indexer),
            archived(archiver)) == before

    # Now for real
    found = purge(paths, rules)
    assert found.headers.dropped == 12
    assert all(not message_id.startswith('spam')
               for message_id in headers(paths[GROUP]))
    assert set(collections(indexer)) == {'Good'}
    assert collections(indexer)['Good'][:2] == (3, 12)
    assert set(archived(archiver)) == {'talk@x'}

    # What belonged to the archived post went with it
    session = archiver.session()
    assert {f.message_id for f in session.query(ArchiveFile)} == {'talk@x'}
    assert {g.message_id for g in session.query(ArchiveGroup)} == {'talk@x'}

    # Running it again finds nothing more
    found = purge(paths, rules)
    assert (found.headers.dropped, found.index.files,
            found.archive.dropped) == (0, 0, 0)

    archiver.close()
    indexer.close()


def test_purges_only_reach_the_groups_given(paths):
    """
    A purge limited to some groups leaves what was only found elsewhere
    alone
    """
    cache(paths[GROUP], [*release('Good'), *release('Spam', poster=SPAMMER)])
    cache(paths[OTHER], release('Junk', poster=SPAMMER))
    indexer = indexed(paths, GROUP, OTHER)

    rules = [{'poster': 'spam.example'}]
    found = purge(paths, rules, caches=(GROUP, OTHER), dry_run=True)
    assert found.index.collections == 2

    # Only the groups given (wildcards work)
    found = purge(paths, rules, caches=(GROUP, ), groups=['alt.binaries.t*'])
    assert found.headers.dropped == 12
    assert found.index.collections == 1
    assert len(headers(paths[GROUP])) == 12
    assert set(collections(indexer)) == {'Good', 'Junk'}

    # The rule itself can be limited to groups too
    found = purge(paths, [{'poster': 'spam.example', 'groups': [OTHER]}],
                  caches=(GROUP, OTHER))
    assert len(headers(paths[OTHER])) == 0
    assert len(headers(paths[GROUP])) == 12
    assert set(collections(indexer)) == {'Good'}
    indexer.close()


def test_purges_by_body_and_file_name_reach_where_they_are_known(paths):
    """
    Bodies are only known for archived posts and file names for indexed
    files and archived posts, so rules on them only reach those
    """
    cache(paths[GROUP], [*release('Good'), *release('Spam', poster=SPAMMER)])
    indexer = indexed(paths)
    archiver = archive(
        paths['index'], ('talk@x', 'Hello', POSTER),
        ('ad@x', 'Cheap pills', SPAMMER))

    # archive() gives every post the text 'hello'
    found = purge(paths, [{'body': {'glob': 'HELLO'}}], dry_run=True)
    assert (found.headers.dropped, found.index.files,
            found.archive.dropped) == (0, 0, 2)

    # Not turned around where the body isn't known
    found = purge(paths, [{'body': {'glob': '*x*', 'not': True}}],
                  dry_run=True)
    assert (found.headers.dropped, found.index.files,
            found.archive.dropped) == (0, 0, 2)

    found = purge(paths, [{'filename': {'glob': '*.part03.rar'}}])
    assert (found.headers.dropped, found.index.files,
            found.archive.dropped) == (0, 2, 0)
    assert collections(indexer)['Good'][:2] == (2, 8)

    found = purge(paths, [{'filename': {'glob': 'ad@x.*'}}])
    assert (found.index.files, found.archive.dropped) == (0, 1)
    assert set(archived(archiver)) == {'talk@x'}

    archiver.close()
    indexer.close()


def test_purges_leave_nzb_collections_alone_unless_asked(paths, tmp_path):
    """
    Collections imported from NZB files are only purged when asked
    """
    indexer = NNTPIndexer(paths['index'])
    collection, _ = indexer.import_nzb(
        nzb_file(tmp_path / 'spam.nzb', 'Spam', files=(1, 2, 3)))
    assert collection.source == CollectionSource.Nzb

    rules = [{'subject': 'spam'}]
    assert purge(paths, rules, groups=()).index.collections == 0
    assert set(collections(indexer)) == {'spam'}

    assert purge(paths, rules, caches=(), nzb=True).index.collections == 1
    assert collections(indexer) == {}
    indexer.close()


def test_purges_can_drop_single_articles(paths):
    """
    A rule on Message-IDs or sizes drops single articles; what is left of
    their files and collections is counted again
    """
    cache(paths[GROUP], release('Good'))
    indexer = indexed(paths)
    assert collections(indexer)['Good'][:2] == (3, 12)

    found = purge(paths, [{'message_id': {'ends': 'good.2.3@example.com'}}])
    assert found.headers.dropped == 1
    assert found.index.dropped == 1
    assert found.index.files == 0

    assert collections(indexer)['Good'][:2] == (3, 11)
    session = indexer.session()
    assert session.query(Segment).filter(
        Segment.message_id == 'good.2.3@example.com').count() == 0

    # Every article of a file gone takes the file with it
    found = purge(paths, [{'message_id': 'good.3.'}])
    assert found.index.files == 1
    assert collections(indexer)['Good'][:2] == (2, 7)
    assert session.query(CollectionFile).count() == 2

    # Sizes are looked at article by article too (cached sizes are
    # 1000 + their article number)
    found = purge(paths, [{'size': {'over': 1004}}])
    assert collections(indexer)['Good'][:2] == (1, 4)
    indexer.close()


def test_rescoring_works_scores_out_again(paths):
    """
    Rescoring gives everything stored the scores and tags of the rules
    (replacing what it had) without removing anything
    """
    cache(paths[GROUP], [*release('Good'), *release('Other', files=1)])
    indexer = indexed(paths)
    archiver = archive(paths['index'], ('good@x', 'Good talk', POSTER))

    rules = [{'subject': 'good', 'score': 10, 'tag': 'good'},
             {'subject': 'part01', 'score': 1},
             {'name': 'drop', 'subject': 'other'}]

    found = purge(paths, rules, rescore=True, drop=False)
    assert found.headers.dropped == 0
    assert found.headers.rescored == 16
    assert found.index.rescored == 2
    assert found.archive.rescored == 1

    scores = headers(paths[GROUP])
    assert scores['good.1.1@example.com'] == (11, 'good')
    assert scores['good.2.1@example.com'] == (10, 'good')
    assert scores['other.1.1@example.com'] == (1, '')

    # A collection takes the best score of its articles
    assert collections(indexer)['Good'][2:] == (11, 'good')
    assert collections(indexer)['Other'][2:] == (1, '')
    assert archived(archiver)['good@x'] == (10, 'good')

    # Lower scores replace higher ones (they aren't added to them)
    rules[0]['score'] = 3
    rules[0]['tag'] = 'fine'
    purge(paths, rules, rescore=True, drop=False)
    assert collections(indexer)['Good'][2:] == (4, 'fine')
    assert headers(paths[GROUP])['good.2.1@example.com'] == (3, 'fine')

    # Nothing changes the second time
    found = purge(paths, rules, rescore=True, drop=False)
    assert (found.headers.rescored, found.index.rescored,
            found.archive.rescored) == (0, 0, 0)

    archiver.close()
    indexer.close()


def test_purges_use_stored_scores_unless_rescoring(paths):
    """
    A purge without rescoring drops by the scores and tags stored; with
    rescoring, by the ones the rules give
    """
    cache(paths[GROUP], release('Good', files=1))
    db = NNTPGroupDatabase.from_file(paths[GROUP])
    db.session().query(Article).filter(
        Article.message_id == 'good.1.1@example.com').update(
        {Article.score: -90, Article.tags: 'junk'})
    db.session().commit()
    db.close()

    found = purge(paths, [{'score': '<-50'}], dry_run=True)
    assert found.headers.dropped == 1

    found = purge(paths, [{'tagged': 'junk'}], dry_run=True)
    assert found.headers.dropped == 1

    found = purge(paths, [{'score': '<-50'}], rescore=True, dry_run=True)
    assert found.headers.dropped == 0


def test_indexing_scores_tags_and_drops(paths):
    """
    Indexing with filters gives collections the best score of their
    articles and all of their tags, and lets go of what the rules drop;
    collections can then be searched by tag and score
    """
    cache(paths[GROUP], [*release('Good'), *release('Spam', poster=SPAMMER),
                         *release('Meh', files=1)])

    rules = NNTPFilter(rules=[
        {'subject': 'good', 'score': 5, 'tag': ['good', 'a_b']},
        {'subject': 'part02', 'score': 2, 'tag': 'second'},
        {'subject': 'meh', 'score': -1},
        {'name': 'spam', 'poster': 'spam.example'}])

    indexer = indexed(paths, filters=rules)
    assert indexer.dropped == 12
    assert 'dropped by filters' in str(indexer)

    found = collections(indexer)
    assert set(found) == {'Good', 'Meh'}
    assert found['Good'][2:] == (7, 'a_b good second')
    assert found['Meh'][2:] == (-1, '')

    # What was dropped is gone from the header cache too
    assert len(headers(paths[GROUP])) == 16

    def names(**kwargs):
        return {c.name for c in indexer.find(**kwargs)}

    assert names() == {'Good', 'Meh'}
    assert names(tags=['good']) == {'Good'}
    assert names(tags=['GOOD, second']) == {'Good'}
    assert names(tags=['good, nope']) == set()
    assert names(tags=['nope', 'good']) == {'Good'}
    assert names(tags=['a_b']) == {'Good'}
    assert names(tags=['axb']) == set()
    assert names(without_tags=['good']) == {'Meh'}
    assert names(without_tags=['good, nope']) == {'Good', 'Meh'}
    assert names(min_score=0) == {'Good'}
    assert names(max_score=0) == {'Meh'}
    assert names(min_score=-1, max_score=6) == {'Meh'}
    assert names(min_score=7, max_score=7) == {'Good'}

    with pytest.raises(ValueError, match="can't be a tag"):
        names(tags=['bad!'])

    # The score and tags show in a search result
    good = next(c for c in indexer.find(tags=['good']))
    assert indexer.summary(good).endswith('  {+7 a_b good second}')
    indexer.close()


def test_scores_from_the_scan_are_used_when_indexing(paths):
    """
    Without filters, indexing uses the scores and tags the articles were
    given when they were cached
    """
    cache(paths[GROUP], release('Good', files=1))
    db = NNTPGroupDatabase.from_file(paths[GROUP])
    db.session().query(Article).update(
        {Article.score: 4, Article.tags: 'old'})
    db.session().commit()
    db.close()

    indexer = indexed(paths)
    assert collections(indexer)['Good'][2:] == (4, 'old')
    indexer.close()


def test_archiving_scores_and_drops(tmp_path):
    """
    Archiving with filters keeps each post's score and tags, and lets go
    of what the rules drop without fetching it
    """
    from newsreap.cli import cli

    with FakeNNTPServer(join_group=False) as server:
        path = tmp_path / 'talk.msg'
        path.write_bytes(b'Subject: Hello there\r\n\r\nHi all\r\n')
        server.add_article('talk@x', str(path), GROUP)
        server.add_overview(GROUP, 1, 'Hello there', POSTER, POSTED,
                            'talk@x', size=200)
        server.add_overview(GROUP, 2, 'Cheap pills', SPAMMER, POSTED,
                            'ad@x', size=200)

        # Spam a header can't tell apart (only its text gives it away)
        path = tmp_path / 'sneaky.msg'
        path.write_bytes(b'Subject: Hi\r\n\r\nBuy NOW, cheap!\r\n')
        server.add_article('sneaky@x', str(path), GROUP)
        server.add_overview(GROUP, 3, 'Hi', POSTER, POSTED, 'sneaky@x',
                            size=200)

        config = config_for(server, tmp_path / 'config.yaml')
        db_file = str(tmp_path / 'cache' / 'search' / ('%s.db' % GROUP))
        runner = CliRunner()

        def nr(*args):
            return runner.invoke(cli, ['--config', config, *args], obj={})

        assert nr('db', 'init').exit_code == 0
        assert nr('update', 'groups').exit_code == 0
        assert nr('update', 'search', GROUP).exit_code == 0

        # The rules arrive after the headers were cached
        (tmp_path / 'filters.yaml').write_text(
            'filters:\n'
            '  - subject: hello\n    score: 3\n    tag: chat\n'
            '  - name: spam\n    poster: spam.example\n'
            '  - name: sneaky\n    body: {glob: "*buy now*"}\n')

        result = nr('update', 'archive', GROUP)
        assert result.exit_code == 0, result.output
        assert '1 article(s) kept (1 text, 0 binary)' in result.output
        assert '2 dropped by filters' in result.output

        # The spam was never asked for; the sneaky one had to be read first
        assert not [c for c in server.commands() if 'ad@x' in c]
        assert [c for c in server.commands() if 'sneaky@x' in c]

        # Posts are found by the words of their subject or text, shown with
        # their subject, and can be narrowed down by tag and score
        def found(*args):
            result = nr('search', '--posts', *args)
            assert result.exit_code == 0, result.output
            return 'Hello there' in result.output

        assert found('there')
        assert found('all')
        assert found('there', '-t', 'chat')
        assert '{+3 chat}' in nr('search', '--posts', 'all').output
        assert not found('there', '-T', 'chat')
        assert not found('there', '-t', 'nope')
        assert found('there', '-A', '3', '-B', '3')
        assert not found('there', '-A', '4')
        assert found('there', '--score', '=3', '-t', '!spam')
        assert not found('there', '--score', '<3')
        assert found('there', '--after', '2019-12-31')
        assert not found('there', '--after', '2020-01-02')

        # The title and the text can be searched on their own
        assert found('--isubject', 'hello*')
        assert not found('--ibody', 'hello*')
        result = nr('search', '--posts', '--ibody', '*all')
        assert 'Hello there' in result.output
        assert 'Hi all' in result.output

    assert set(headers(db_file)) == {'talk@x'}
    archiver = NNTPArchiver(
        str(tmp_path / 'cache' / 'index.db'), default_hooks=False)
    assert archived(archiver) == {'talk@x': (3, 'chat')}
    archiver.close()


def test_filters_from_the_command_line(tmp_path):
    """
    nr filter purge shows (with --dry-run) and removes what a rule
    matches, prints the rule to keep, saves it with --save, and later
    scans drop the same things; scores and tags work the same way
    """
    from newsreap.cli import cli

    group_list = tmp_path / 'group.list'
    group_list.write_text('%s 0000000004 0000000001 y\n' % GROUP)

    with FakeNNTPServer(group_list=str(group_list)) as server:
        overviews = [
            ('Good [1/2] - "good.part1.rar" yEnc (1/1)', POSTER, 'g1@x'),
            ('Good [2/2] - "good.vol00+1.par2" yEnc (1/1)', POSTER, 'g2@x'),
            ('Casino [1/1] - "win.exe" yEnc (1/1)', SPAMMER, 's1@x'),
            ('Pills [1/1] - "buy.exe" yEnc (1/1)', SPAMMER, 's2@x'),
        ]
        for no, (subject, poster, message_id) in enumerate(overviews, 1):
            server.add_overview(
                GROUP, no, subject, poster,
                POSTED + timedelta(minutes=no), message_id)

        config = config_for(server, tmp_path / 'config.yaml')
        filters_file = tmp_path / 'filters.yaml'
        db_file = str(tmp_path / 'cache' / 'search' / ('%s.db' % GROUP))
        runner = CliRunner()

        def nr(*args):
            return runner.invoke(cli, ['--config', config, *args], obj={})

        assert nr('db', 'init').exit_code == 0
        assert nr('update', 'groups').exit_code == 0
        assert nr('update', 'search', GROUP).exit_code == 0
        assert nr('update', 'index', GROUP).exit_code == 0
        assert len(headers(db_file)) == 4

        # Nothing is saved yet
        assert 'There are no saved rules' in nr('filter', 'list').output
        assert 'There are no saved rules' in nr('filter', 'purge').output

        # A score needs a score or tag, and something to match on
        assert nr('filter', 'score', '--isubject', 'x').exit_code == 1
        assert nr('filter', 'score', '--score', '1').exit_code == 1
        assert nr('filter', 'purge', '--iregex-subject', '(').exit_code == 1

        # Groups can be given with wildcards; a group with none of it
        # drops nothing
        result = nr('filter', 'purge', '--iposter', '*@spam.example>', '-n',
                    '-g', 'alt.binaries.*')
        assert result.exit_code == 0, result.output
        assert '%s: 2 of 4 header(s) dropped' % GROUP in result.output
        assert "- alt.binaries.*\n" in result.output

        result = nr('filter', 'purge', '--iposter', '*@spam.example>', '-n',
                    '-g', 'alt.unknown')
        assert result.exit_code == 0, result.output
        assert 'header(s) dropped' not in result.output
        assert 'index: 0 file(s) dropped' in result.output

        # See what would go
        result = nr('filter', 'purge', '--iposter', '*@SPAM.example>', '-n')
        assert result.exit_code == 0, result.output
        assert '%s: 2 of 4 header(s) dropped, 0 rescored' % GROUP \
            in result.output
        assert 'Casino [1/1]' in result.output
        assert 'Dry run: nothing was changed.' in result.output
        assert 'run again with --save' in result.output
        assert "    poster:\n      glob: '*@SPAM.example>'\n" in result.output
        assert len(headers(db_file)) == 4

        # The whole poster has to match (case matters without the i)
        result = nr('filter', 'purge', '--iposter', '*@spam.example', '-n')
        assert 'header(s) dropped' not in result.output
        result = nr('filter', 'purge', '--poster', '*@SPAM.example>', '-n')
        assert 'header(s) dropped' not in result.output

        # Every condition narrows it down; ! leaves out
        result = nr('filter', 'purge', '--iposter', '*@spam.example>',
                    '--isubject', '!casino*', '-n')
        assert '%s: 1 of 4 header(s) dropped' % GROUP in result.output
        assert 'Pills [1/1]' in result.output
        assert "      not: true\n" in result.output

        # Remove it (without keeping the rule)
        result = nr('filter', 'purge', '--iposter', '*@spam.example>')
        assert result.exit_code == 0, result.output
        assert 'index: 2 file(s) dropped, 2 collection(s) removed' \
            in result.output
        assert set(headers(db_file)) == {'g1@x', 'g2@x'}
        assert not filters_file.exists()

        # A rule needs something to match on before it can be saved
        assert nr('filter', 'purge', '--save').exit_code == 1

        # Keep it this time
        result = nr('filter', 'purge', '--iposter', '*@spam.example>',
                    '--name', 'spam', '--save')
        assert result.exit_code == 0, result.output
        assert "Saved the rule 'spam'" in result.output
        assert 'spam' in NNTPFilter(str(filters_file))

        # The same name can't be saved twice
        result = nr('filter', 'purge', '--iposter', 'x', '--name', 'spam',
                    '--save')
        assert result.exit_code == 1

        # New spam is dropped as it arrives
        server.add_overview(
            GROUP, 5, 'More [1/1] - "more.exe" yEnc (1/1)', SPAMMER,
            POSTED + timedelta(minutes=5), 's3@x')
        result = nr('update', 'search', GROUP)
        assert result.exit_code == 0, result.output
        assert '0 article(s) cached, 1 dropped by filters' in result.output
        assert 's3@x' not in headers(db_file)

        # Score and tag (saved), then search by tag
        result = nr('filter', 'score', '--iregex-subject', r'.*\.par2\b.*',
                    '--score', '10', '--tag', 'recovery', '--save')
        assert result.exit_code == 0, result.output
        assert '%s: 0 of 2 header(s) dropped, 1 rescored' % GROUP \
            in result.output
        assert headers(db_file)['g2@x'] == (10, 'recovery')

        # Scanning the same articles again works their scores and tags out
        # from 0 again (they never add up)
        for _ in range(2):
            result = nr('update', 'search', GROUP, '--force')
            assert result.exit_code == 0, result.output
            assert headers(db_file)['g2@x'] == (10, 'recovery')
            assert headers(db_file)['g1@x'] == (0, '')

        for _ in range(2):
            assert nr('filter', 'rescore').exit_code == 0
            assert headers(db_file)['g2@x'] == (10, 'recovery')

        # Indexing them again leaves the collection as it was
        for _ in range(2):
            assert nr('update', 'index', GROUP).exit_code == 0
            result = nr('search', '--tag', 'recovery')
            assert result.output.count('{+10 recovery}') == 1

        result = nr('search', '--tag', 'recovery')
        assert result.exit_code == 0, result.output
        assert '{+10 recovery}' in result.output
        assert nr('search', '--tag', 'nope').output == ''

        # Tags and scores narrow down every kind of search (any of the
        # tags given more than once; all of the ones in 'a, b')
        def found(*args):
            result = nr(*args)
            assert result.exit_code == 0, result.output
            return 'Good' in result.output

        assert found('search', '-t', 'nope', '-t', 'RECOVERY')
        assert not found('search', '-t', 'recovery, nope')
        assert not found('search', '-T', 'recovery')
        assert found('search', '-T', 'recovery, nope')
        assert found('search', '-A', '10', '-B', '10')
        assert not found('search', '-A', '11')
        assert not found('search', '-B', '9')
        assert found('nzb', 'list', '-t', 'recovery', '-A', '5')
        assert not found('nzb', 'list', '-T', 'recovery')
        assert nr('search', '-t', 'bad!').exit_code == 1
        assert nr('nzb', 'list', '-T', 'bad!').exit_code == 1

        result = nr('search', '--raw', '-g', GROUP, '-t', 'recovery')
        assert result.exit_code == 0, result.output
        assert 'good.vol00+1.par2' in result.output
        assert '{+10 recovery}' in result.output
        assert 'good.part1.rar' not in result.output

        result = nr('search', '--raw', '-g', GROUP, '-B', '0')
        assert 'good.part1.rar' in result.output
        assert 'good.vol00+1.par2' not in result.output

        # !tag, score comparisons and posting times work everywhere too
        assert not found('search', '-t', '!recovery')
        assert found('search', '-t', '!nope, recovery')
        assert found('search', '--score', '>=10', '--score', '<11')
        assert not found('search', '--score', '>10')
        assert found('search', '--after', '2019-12-31', '--before', '1d')
        assert not found('search', '--before', '2019-12-31')
        assert found('nzb', 'list', '--score', '=10', '--after', '2019')
        assert not found('nzb', 'list', '--after', '30d')

        result = nr('search', '--raw', '-g', GROUP, '--score', '>0')
        assert 'good.vol00+1.par2' in result.output
        assert 'good.part1.rar' not in result.output
        result = nr('search', '--raw', '-g', GROUP, '--before', '2019')
        assert 'good.' not in result.output

        for command in (('search', '--score', '10'),
                        ('search', '--after', 'whenever'),
                        ('search', '--after', '1d', '--before', '2d'),
                        ('search', '-t', 'a, !a'),
                        ('nzb', 'list', '--score', 'lots')):
            assert nr(*command).exit_code == 1, command

        # find style conditions work on every kind of search
        assert found('search', '--isubject', 'good*')
        assert found('search', '--isubject', 'good')
        assert not found('search', '--isubject', 'goo')
        assert not found('search', '--subject', 'good*')
        assert found('search', '--ifilename', '*.PAR2', '--isubject', '*good*')
        assert not found('search', '--ifilename', '*.par2', '--ifilename', '!*.par2')
        assert found('nzb', 'list', '--iregex-filename', r'good\.part\d\.rar')
        assert found('search', 'go?d')

        result = nr('search', '--raw', '-g', GROUP, '--message-id', 'g2@x')
        assert result.exit_code == 0, result.output
        assert 'good.vol00+1.par2' in result.output
        assert 'good.part1.rar' not in result.output
        assert found('search', '--message-id', 'g1@x')
        assert not found('search', '--message-id', 'nope@x')

        result = nr('search', '--raw', '-g', GROUP, '--isubject', '*.par2*')
        assert result.exit_code == 0, result.output
        assert 'good.vol00+1.par2' in result.output
        assert 'good.part1.rar' not in result.output

        # Each search only takes what it can look at
        for command in (('search', '--ibody', 'x'),
                        ('search', '--raw', '-g', GROUP, '--ifilename', 'x'),
                        ('nzb', 'list', '--body', 'x'),
                        ('search', '--iregex-subject', '(')):
            result = nr(*command)
            assert result.exit_code == 1, command

        # Rules can compare scores and leave out tags too
        result = nr('filter', 'purge', '--score', '<5', '--tagged',
                    '!recovery', '-n')
        assert result.exit_code == 0, result.output
        assert '%s: 1 of 2 header(s) dropped' % GROUP in result.output
        assert "    score: <5\n" in result.output
        assert "    tagged: '!recovery'\n" in result.output
        assert nr('filter', 'purge', '--score', 'lots').exit_code == 1

        # Nothing changes when scores are worked out again
        result = nr('filter', 'rescore', '--dry-run')
        assert result.exit_code == 0, result.output
        assert 'header(s)' not in result.output
        assert 'index: 0 file(s) dropped, 0 collection(s) removed, ' \
            '0 rescored' in result.output

        # Applying the saved rules again finds nothing more to drop
        result = nr('filter', 'purge')
        assert result.exit_code == 0, result.output
        assert 'index: 0 file(s) dropped, 0 collection(s) removed' \
            in result.output

        # List and remove them
        result = nr('filter', 'list')
        assert result.exit_code == 0
        assert '- name: spam\n' in result.output
        assert 'tag: recovery' in result.output

        assert nr('filter', 'remove', 'nope').exit_code == 1
        result = nr('filter', 'remove', 'spam')
        assert result.exit_code == 0, result.output
        assert 'spam' not in NNTPFilter(str(filters_file))
        assert len(NNTPFilter(str(filters_file))) == 1


def test_broken_filters_stop_commands_before_they_start(tmp_path):
    """
    A filters file with a mistake stops nr update (saying where) before
    anything is fetched
    """
    from newsreap.cli import cli

    with FakeNNTPServer() as server:
        server.add_overview(GROUP, 1, 'Hello', POSTER, POSTED, 'a@x')
        config = config_for(server, tmp_path / 'config.yaml')
        (tmp_path / 'filters.yaml').write_text(
            'filters:\n  - name: bad\n    subject: {regex: "("}\n')

        runner = CliRunner()

        def nr(*args):
            return runner.invoke(cli, ['--config', config, *args], obj={})

        assert nr('db', 'init').exit_code == 0
        assert nr('update', 'groups').exit_code == 0

        for command in (('update', 'search', GROUP),
                        ('update', 'index', GROUP),
                        ('update', 'archive', GROUP),
                        ('filter', 'purge'),
                        ('filter', 'list')):
            result = nr(*command)
            assert result.exit_code == 1, command

        assert not [c for c in server.commands() if c.startswith('XOVER')]


def test_the_filters_file_can_be_moved(tmp_path):
    """
    The filters setting says where the filters file is; without it, it is
    filters.yaml next to the configuration file
    """
    from newsreap.NNTPSettings import NNTPSettings

    with FakeNNTPServer() as server:
        config = config_for(server, tmp_path / 'config.yaml')
        settings = NNTPSettings(config)
        assert settings.filters_file() == str(tmp_path / 'filters.yaml')
        assert len(settings.filters()) == 0

        moved = tmp_path / 'elsewhere.yaml'
        moved.write_text('filters:\n  - name: a\n    subject: x\n')
        config = config_for(
            server, tmp_path / 'other.yaml',
            filters="'%{base_dir}/elsewhere.yaml'")
        settings = NNTPSettings(config)
        assert settings.filters_file() == str(moved)
        assert list(settings.filters().keys()) == ['a']

        # Read once, unless asked again
        moved.write_text('filters:\n  - name: b\n    subject: x\n')
        assert list(settings.filters().keys()) == ['a']
        assert list(settings.filters(reload=True).keys()) == ['b']


def test_purge_counts_print_plainly():
    """
    A purge describes what it found in a line
    """
    purger = NNTPPurger(NNTPFilter(rules=[{'subject': 'a'}]))
    assert str(purger).startswith('headers: 0 dropped, 0 rescored; ')
    assert repr(purger) == '<NNTPPurger rescore=False drop=True ' \
        'dry_run=False />'
