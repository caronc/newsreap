# Tests caching the article headers of a group (nr update search)
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

import os
import subprocess
import sys
from datetime import datetime
from datetime import timedelta

import pytest
from click.testing import CliRunner

from newsreap.NNTPConnectionRequest import NNTPConnectionRequest
from newsreap.NNTPGroupDatabase import NNTPGroupDatabase
from newsreap.NNTPGroupScanner import NNTPGroupScanner
from newsreap.NNTPManager import NNTPManager
from newsreap.NNTPSettings import NNTPSettings
from newsreap.objects.group.Article import Article
from newsreap.objects.group.ScannedRange import ScannedRange

from tests.FakeNNTPServer import FakeNNTPServer
from tests.conftest import config_for

GROUP = 'alt.binaries.test'

# When our made up articles were posted (article n is n hours later)
POSTED = datetime(2020, 1, 1)


@pytest.fixture
def usenet(tmp_path):
    """
    A server with 300 articles (101 to 119 have expired) and the settings
    and manager to reach it
    """
    group_list = tmp_path / 'group.list'
    group_list.write_text(
        '%s 0000000300 0000000001 y\n'
        'alt.binaries.other 0000000010 0000000001 y\n' % GROUP)

    with FakeNNTPServer(group_list=str(group_list)) as server:
        server.add_group(GROUP, 281, 1, 300)
        for n in range(1, 301):
            if 100 < n < 120:
                # Expired; the server no longer has these
                continue

            server.add_overview(
                GROUP, n, 'post %d "file%d.rar" yEnc (1/1)' % (n, n),
                'poster@example.com', POSTED + timedelta(hours=n),
                'msg%d@example.com' % n, size=5000, lines=40)

        path = config_for(
            server, tmp_path / 'config.yaml', header_batch_size=100,
            threads=2)
        settings = NNTPSettings(cfg_file=path)
        manager = NNTPManager(settings)

        yield server, settings, manager

        manager.close()
        settings.close()


def scanner_for(settings, manager, **kwargs):
    """
    Returns a scanner for our test group
    """
    return NNTPGroupScanner(
        manager, GROUP, db_file=settings.group_database(GROUP),
        server='test', batch_size=100, **kwargs)


def xovers(server):
    """
    The XOVER commands a server received
    """
    return [c for c in server.commands() if c.startswith('XOVER')]


def cached(settings):
    """
    Returns (article numbers, scanned ranges) saved for our test group
    """
    db = NNTPGroupDatabase(
        engine='sqlite:///%s' % settings.group_database(GROUP), reset=None)
    session = db.session()
    numbers = sorted(n for (n, ) in session.query(Article.article_no))
    ranges = [(r.low, r.high) for r in session.query(ScannedRange)
              .order_by(ScannedRange.low)]
    db.close()
    return numbers, ranges


#
# Working out what to fetch
#
def test_missing_ranges():
    """
    Only what hasn't been fetched yet is asked for
    """
    missing = NNTPGroupScanner.missing
    assert missing([], 1, 100) == [(1, 100)]
    assert missing([(1, 100)], 1, 100) == []
    assert missing([(20, 30)], 1, 100) == [(1, 19), (31, 100)]
    assert missing([(1, 10), (50, 60), (90, 200)], 1, 100) == \
        [(11, 49), (61, 89)]

    # Ranges reaching past what we want don't matter
    assert missing([(0, 5), (95, 105)], 1, 100) == [(6, 94)]


def test_batches_split_ranges():
    """
    Ranges are split into batch sized requests
    """
    scanner = NNTPGroupScanner(None, GROUP, db_file='', server='test',
                               batch_size=100, window=2)
    assert list(scanner.split([(1, 250), (300, 300)])) == [
        (1, 100), (101, 200), (201, 250), (300, 300)]


def test_dropped_articles_are_never_stored():
    """
    An article a drop rule matches (ie: posted to too many groups) isn't
    handed on to be stored or scored
    """
    from newsreap.NNTPFilter import NNTPFilter
    scanner = NNTPGroupScanner(
        None, GROUP, db_file='', server='test',
        filters=NNTPFilter(rules=[{'crossposts': '>2'}]))

    def header(message_id, *groups):
        return {'id': message_id, 'article_no': 1, 'subject': 'test',
                'poster': 'me@example.com', 'size': 10, 'lines': 1,
                'date': POSTED, 'xgroups': {GROUP, *groups}}

    rows = scanner._rows([
        header('one@x'), header('two@x', 'alt.b.two'),
        header('spam@x', 'alt.b.two', 'alt.b.three')])
    assert [row['message_id'] for row in rows] == ['one@x', 'two@x']
    assert scanner.dropped == 1
    assert 'spam@x' not in scanner._ranks


def test_scanned_ranges_merge(usenet):
    """
    Ranges that touch or overlap become one; other servers are kept apart
    """
    _, settings, manager = usenet
    scanner = scanner_for(settings, manager)

    with scanner._database() as db:
        session = db.session()
        for low, high in ((1, 10), (21, 30), (11, 20), (25, 40), (60, 70)):
            scanner.mark(session, low, high)

        scanner.server = 'other'
        scanner.mark(session, 1, 5)
        session.commit()

        ranges = [(r.server, r.low, r.high) for r in session.query(
            ScannedRange).order_by(ScannedRange.server, ScannedRange.low)]

    assert ranges == [('other', 1, 5), ('test', 1, 40), ('test', 60, 70)]


#
# Scanning
#
def test_group_is_cached_once(usenet):
    """
    Everything is fetched the first time and nothing the second
    """
    server, settings, manager = usenet
    scanner = scanner_for(settings, manager)

    assert scanner.scan() is True
    assert (scanner.low, scanner.high) == (1, 300)
    assert scanner.articles == 281
    assert scanner.batches == 3
    assert str(scanner) == '%s: 281 article(s) cached' % GROUP

    numbers, ranges = cached(settings)
    assert len(numbers) == 281
    # The expired articles count as fetched too
    assert ranges == [(1, 300)]

    # Nothing new; nothing fetched
    assert scanner.scan() is True
    assert str(scanner) == '%s: already up to date' % GROUP
    assert len(xovers(server)) == 3

    # The headers are part of our statistics
    assert manager.stats.headers == 281


def test_new_articles_are_picked_up(usenet):
    """
    A later scan only fetches what was posted since
    """
    server, settings, manager = usenet
    scanner = scanner_for(settings, manager)
    assert scanner.scan() is True

    # Ten more articles show up
    for n in range(301, 311):
        server.add_overview(
            GROUP, n, 'post %d' % n, 'poster@example.com',
            POSTED + timedelta(hours=n), 'msg%d@example.com' % n)
    server.add_group(GROUP, 291, 1, 310)

    assert scanner.scan() is True
    assert scanner.articles == 10
    assert xovers(server)[-1] == 'XOVER 301-310'
    assert cached(settings)[1] == [(1, 310)]


def test_stopping_part_way_resumes(usenet):
    """
    A scan that stops early picks up where it left off
    """
    server, settings, manager = usenet
    scanner = scanner_for(settings, manager)

    # Only one batch this time
    assert scanner.scan(limit=1) is False
    assert scanner.pending == 200
    assert str(scanner) == \
        '%s: 100 article(s) cached, 200 article number(s) left to fetch' % (
            GROUP)

    assert scanner.scan() is True
    assert xovers(server) == ['XOVER 1-100', 'XOVER 101-200', 'XOVER 201-300']
    assert cached(settings)[1] == [(1, 300)]


def test_killed_scans_get_their_indexes_back(tmp_path):
    """
    A scan killed while filling a new database (its indexes dropped for
    speed) gets them back the next time the database is opened
    """
    db_file = str(tmp_path / 'cache' / 'search' / 'test.db')
    db = NNTPGroupDatabase.from_file(db_file)
    assert NNTPGroupScanner._drop_indexes(db)
    db.close()

    db = NNTPGroupDatabase.from_file(db_file)
    with db._engine.connect() as conn:
        names = {name for name, in conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'index'")}
    db.close()

    assert {index.name for index in Article.__table__.indexes} <= names


def test_killed_writes_leave_a_working_database(tmp_path):
    """
    A process killed part way through saving articles leaves everything it
    saved before then (and a database that works)
    """
    db_file = str(tmp_path / 'test.db')
    script = (
        'import os, sys\n'
        'from sqlalchemy import event\n'
        'from newsreap import Database\n'
        'from newsreap.NNTPGroupDatabase import NNTPGroupDatabase\n'
        'from newsreap.objects.group.Article import Article\n'
        '# A small cache, so big saves are written out before committing\n'
        'tuned = Database.sqlite_tuned\n'
        'def small(engine):\n'
        '    tuned(engine)\n'
        '    event.listen(engine, "connect", lambda c, _: c.execute(\n'
        '        "PRAGMA cache_size = 100"))\n'
        'Database.sqlite_tuned = small\n'
        'db = NNTPGroupDatabase.from_file(sys.argv[1])\n'
        'session = db.session()\n'
        'def add(start):\n'
        '    session.add_all(Article(\n'
        '        message_id="%d@x" % n, article_no=n, subject="s",\n'
        '        poster="p", size=1, lines=1)\n'
        '        for n in range(start, start + 5000))\n'
        '    session.flush()\n'
        'add(1)\n'
        'session.commit()\n'
        '# Killed while saving the next ones\n'
        'add(5001)\n'
        'os._exit(9)\n')

    result = subprocess.run(
        [sys.executable, '-c', script, db_file],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    assert result.returncode == 9

    db = NNTPGroupDatabase.from_file(db_file)
    session = db.session()
    assert session.query(Article).count() == 5000
    with db._engine.connect() as conn:
        assert conn.exec_driver_sql(
            'PRAGMA integrity_check').scalar() == 'ok'
    db.close()


def test_failed_batch_is_fetched_next_time(usenet):
    """
    A batch the server fails on is left for the next scan
    """
    server, settings, manager = usenet
    server.on(r'^XOVER 101-200$', '503 Timeout', once=True)
    scanner = scanner_for(settings, manager)

    assert scanner.scan() is False
    assert scanner.failed == 1
    assert '1 batch(es) failed' in str(scanner)
    assert cached(settings)[1] == [(1, 100), (201, 300)]

    assert scanner.scan() is True
    assert xovers(server)[-1] == 'XOVER 101-200'
    assert cached(settings)[1] == [(1, 300)]


def clock(settings):
    """
    Returns how far our test group has been seen (see
    NNTPGroupDatabase.clock())
    """
    db = NNTPGroupDatabase.from_file(settings.group_database(GROUP))
    seen = db.clock()
    db.close()
    return seen


def test_caught_up_scans_move_the_clock(usenet):
    """
    A scan that reaches the server's newest article records when it did;
    a group's clock is never behind its newest cached article
    """
    server, settings, manager = usenet
    scanner = scanner_for(settings, manager)

    # Stopped before reaching the newest article; only the newest cached
    # article (number 100) counts
    assert scanner.scan(limit=1) is False
    assert clock(settings) == POSTED + timedelta(hours=100)

    # Caught up: the time we asked the server counts (it's long after our
    # made up articles were posted)
    assert scanner.scan() is True
    first = clock(settings)
    assert first > POSTED + timedelta(hours=300)

    # Nothing new is still caught up (and later)
    assert scanner.scan() is True
    assert clock(settings) >= first


def test_failed_scans_leave_the_clock(usenet):
    """
    A scan that missed something doesn't count as caught up
    """
    server, settings, manager = usenet
    server.on(r'^XOVER 201-300$', '503 Timeout', once=True)
    scanner = scanner_for(settings, manager)

    assert scanner.scan() is False
    assert clock(settings) == POSTED + timedelta(hours=200)


def test_newest_and_dates(usenet):
    """
    The newest X articles, or a range of dates, can be asked for
    """
    server, settings, manager = usenet
    scanner = scanner_for(settings, manager)

    assert scanner.scan(newest=50) is True
    assert cached(settings)[1] == [(251, 300)]

    # Articles 50 to 60 were posted on these dates
    assert scanner.scan(
        date_from=POSTED + timedelta(hours=50),
        date_to=POSTED + timedelta(hours=60)) is True
    assert cached(settings)[1] == [(50, 60), (251, 300)]

    # Nothing was posted this late
    assert scanner.scan(date_from=datetime(2030, 1, 1)) is True
    assert scanner.wanted == 0


def test_force_fetches_again(usenet):
    """
    --force fetches (and replaces) articles cached before
    """
    server, settings, manager = usenet
    scanner = scanner_for(settings, manager)
    assert scanner.scan(newest=10) is True

    # The server's copy of an article changed
    server.overview[GROUP] = [
        (n, line.replace('post 300 ', 'renamed 300 '))
        for n, line in server.overview[GROUP]]

    assert scanner.scan(newest=10) is True
    assert scanner.wanted == 0

    assert scanner.scan(newest=10, force=True) is True
    assert scanner.articles == 10

    db = NNTPGroupDatabase(
        engine='sqlite:///%s' % settings.group_database(GROUP), reset=None)
    subject = db.session().query(Article.subject)\
        .filter(Article.article_no == 300).scalar()
    db.close()
    assert subject.startswith('renamed 300')


def test_ramdisk_copy_is_moved_back(usenet, tmp_path):
    """
    Work done on a ramdisk ends up in the group's database
    """
    _, settings, manager = usenet
    ramdisk = tmp_path / 'ramdisk'
    ramdisk.mkdir()

    scanner = scanner_for(settings, manager, ramdisk=str(ramdisk))
    assert scanner.scan(newest=50) is True
    assert list(ramdisk.iterdir()) == []
    assert cached(settings)[1] == [(251, 300)]

    # A second run starts from what was saved (and carries on from it;
    # going further back has to be asked for)
    assert scanner.scan() is True
    assert cached(settings)[1] == [(251, 300)]
    assert scanner.scan(newest=300) is True
    assert cached(settings)[1] == [(1, 300)]
    assert list(ramdisk.iterdir()) == []


def test_a_first_fetch_of_a_big_group_needs_a_start(usenet):
    """
    The first time, a group with more articles than first_fetch has to be
    told how far back to go; afterwards a run carries on from there (what
    is new, and anything missed)
    """
    _, settings, manager = usenet
    scanner = scanner_for(settings, manager)
    scanner.first_fetch = 100

    assert scanner.scan() is False
    assert scanner.too_big == 300
    assert str(scanner) == (
        'alt.binaries.test: 300 articles; say how far back to start the '
        'first time (ie: --newest 200000 or --days 7)')
    assert cached(settings)[1] == []

    assert scanner.scan(newest=20) is True
    assert scanner.too_big is None
    assert cached(settings)[1] == [(281, 300)]

    # Nothing new since
    assert scanner.scan() is True
    assert str(scanner) == 'alt.binaries.test: already up to date'


def test_unknown_group(usenet):
    """
    A group the server doesn't carry fails cleanly
    """
    _, settings, manager = usenet
    scanner = NNTPGroupScanner(
        manager, 'alt.nothing', db_file=settings.group_database('x'),
        server='test')
    assert scanner.scan() is False
    assert str(scanner) == 'alt.nothing: could not be accessed'


def test_group_database_names_are_safe(tmp_path):
    """
    A group name can't place its database outside of our cache directory
    """
    settings = NNTPSettings()
    settings.base_dir = str(tmp_path)
    cache = os.path.join(str(tmp_path), 'cache', 'search')

    assert settings.group_database('Alt.Binaries.Test') == \
        os.path.join(cache, 'alt.binaries.test.db')

    for name in ('../../etc/passwd', '/etc/passwd', '..', 'a\x00b'):
        path = settings.group_database(name)
        assert os.path.dirname(path) == cache, name
        assert not os.path.basename(path).startswith('.'), name


def test_failed_request_still_finishes():
    """
    A request whose action fails is still marked done (nothing waits on it
    forever)
    """
    class Broken:
        def xover(self, **kwargs):
            raise OSError('gone')

    request = NNTPConnectionRequest(actions=[('xover', (), {})])
    request.run(connection=Broken())
    assert request.is_set()
    assert request.response == [None]


#
# The command line
#
def test_update_from_the_command_line(tmp_path):
    """
    nr update groups, then update search, then search finds the articles
    """
    from newsreap.cli import cli

    group_list = tmp_path / 'group.list'
    group_list.write_text('%s 0000000300 0000000001 y\n' % GROUP)

    with FakeNNTPServer(group_list=str(group_list)) as server:
        server.add_group(GROUP, 3, 1, 3)
        for n in (1, 2, 3):
            server.add_overview(
                GROUP, n, 'holiday photos %d' % n, 'poster@example.com',
                POSTED + timedelta(hours=n), 'msg%d@example.com' % n)

        config = config_for(server, tmp_path / 'config.yaml')
        runner = CliRunner()

        def nr(*args):
            return runner.invoke(cli, ['--config', config, *args], obj={})

        # The server isn't in the database yet
        result = nr('update', 'search', GROUP)
        assert result.exit_code == 1

        assert nr('db', 'init').exit_code == 0

        result = nr('update', 'groups')
        assert result.exit_code == 0
        assert '1 group(s) listed, 1 new.' in result.output

        # Listing again updates what we have
        result = nr('update', 'groups')
        assert '1 group(s) listed, 0 new.' in result.output

        assert nr('alias', 'add', 'photos', GROUP).exit_code == 0

        result = nr('--statistics', 'update', 'search', 'photos')
        assert result.exit_code == 0
        assert '%s: 3 article(s) cached' % GROUP in result.output
        assert 'Headers       3 received' in result.output

        result = nr('update', 'search', 'photos')
        assert '%s: already up to date' % GROUP in result.output

        result = nr('search', '--raw', '-g', 'photos', 'photos 2')
        assert result.exit_code == 0
        assert 'holiday photos 2' in result.output
        assert 'holiday photos 1' not in result.output

        # Bad dates are refused
        result = nr('update', 'search', 'photos', '--date-from', '!!!')
        assert result.exit_code == 1
