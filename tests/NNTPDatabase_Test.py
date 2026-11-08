# A base testing class/library to test the NNTP Server and Connection class
#
# Copyright (C) 2015-2016 Chris Caron <lead2gold@gmail.com>
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
import sys
from os.path import dirname
from os.path import abspath

try:
    from tests.TestBase import TestBase

except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

import pytest

from newsreap.Database import DatabaseUnavailable
from newsreap.NNTPDatabase import NNTPDatabase
from newsreap.NNTPDatabase import UnknownAlias
from newsreap.NNTPDatabase import UnknownGroups
from newsreap.NNTPDatabase import UnknownServer
from newsreap.objects.nntp.Group import Group
from newsreap.objects.nntp.GroupTrack import GroupTrack
from newsreap.objects.nntp.Server import Server


class NNTPDatabase_Test(TestBase):
    """
    Tests NNTPDatabase
    """

    def test_database_reset(self):
        """
        Reset the database
        """
        db = NNTPDatabase()

        # Engine
        engine_1 = db.open(reset=True)

        # TODO Insert Something Here

        # Engine 2
        engine_2 = db.open()
        assert id(engine_1) == id(engine_2)

        # TODO: Check that item still exists

        #  Reset the database
        engine_3 = db.open(reset=True)

        # TODO: Check that item is lost

        # You only need to grab the engine the first time
        # subseqent calls will still have the returned object
        # be the same
        assert id(engine_2) == id(engine_3)
        assert id(engine_1) == id(engine_3)


if __name__ == '__main__':
    import unittest
    unittest.main()


TEST = 'alt.binaries.test'
OTHER = 'alt.binaries.other'


# Database servers these are tested on too (besides SQLite); see
# INDEX_SERVERS in NNTPIndexer_Test.py
SERVERS = [
    name for name in ('postgresql', 'mysql')
    if os.environ.get('NEWSREAP_TEST_%s' % name.upper())]


@pytest.fixture(params=['sqlite', *SERVERS])
def db(request, tmp_path):
    """
    A main database (a file, or an emptied database server) that knows
    two groups
    """
    db = NNTPDatabase(engine='sqlite:///%s' % (tmp_path / 'nntp.db')
                      if request.param == 'sqlite' else
                      os.environ['NEWSREAP_TEST_%s' % request.param.upper()])
    db.open(reset=True)
    with db.transaction() as session:
        session.add_all([Group(name=TEST, count=5, flags='y'),
                         Group(name=OTHER, count=7, flags='y')])

    yield db
    db.close()


def test_groups_are_found_by_name_or_alias(db):
    """
    - a group is found by its name or an alias of it
    - a name that matches nothing is refused and named (or left out)
    """
    db.alias_add('t', [TEST])
    assert set(db.groups([TEST, 't'])) == {TEST}
    assert db.groups(watched=True) == {}

    with pytest.raises(UnknownGroups) as error:
        db.groups([TEST, 'alt.binaries.typo'])
    assert error.value.names == ['alt.binaries.typo']

    # Or just left out
    assert set(db.groups([TEST, 'alt.binaries.typo'], strict=False)) == {
        TEST}
    assert db.groups(['alt.binaries.typo'], strict=False) == {}
    assert db.group_names() == [OTHER, TEST]


def test_watching_and_unwatching_groups(db):
    """
    - only groups that changed are returned
    - a typo stops a watch, but not an unwatch (the typo is returned)
    - watching never changes a group's settings
    """
    db.configure([TEST], nzb=False)
    assert set(db.watch([TEST, OTHER])) == {TEST, OTHER}
    assert db.watch([TEST]) == {}
    assert db.group_options([TEST])[TEST]['nzb'] is False

    with pytest.raises(UnknownGroups):
        db.watch([TEST, 'alt.binaries.typo'])

    result = db.unwatch([TEST, 'alt.binaries.typo'])
    assert set(result.removed) == {TEST}
    assert result.unknown == ['alt.binaries.typo']
    assert db.group_list() == [{
        'name': OTHER, 'id': db.groups(OTHER)[OTHER], 'count': 7,
        'flags': 'y', 'watch': True, 'content': None, 'nzb': None,
        'retention': None}]
    assert len(db.group_list(all=True)) == 2

    with pytest.raises(ValueError):
        db.watch([])


def test_groups_have_settings_of_their_own(db):
    """
    - each setting can be set, or put back to the configuration (None)
    - a setting that doesn't exist, or a value that doesn't fit, is
      refused and nothing changes
    """
    found = db.configure([TEST], content=True, retention=30)
    assert found == {TEST: {'content': True, 'nzb': None, 'retention': 30}}

    found = db.configure(TEST, retention=None, nzb=False)
    assert found == {TEST: {'content': True, 'nzb': False, 'retention': None}}
    assert db.configure(TEST) == found

    for bad in ({'retention': -1}, {'retention': '7'}, {'retention': True},
                {'content': 'yes'}, {'colour': True}):
        with pytest.raises(ValueError):
            db.configure(TEST, **bad)

    with pytest.raises(UnknownGroups):
        db.configure([TEST, 'alt.binaries.typo'], content=False)
    assert db.group_options(TEST)[TEST]['content'] is True


def test_settings_work_on_a_database_made_before_they_existed(db):
    """
    The table (or a column) missing is added the first time a setting is
    changed
    """
    from sqlalchemy import text
    with db.transaction() as session:
        session.execute(text('DROP TABLE group_option'))
        session.execute(text(
            'CREATE TABLE group_option (group_id INTEGER PRIMARY KEY, '
            'content BOOLEAN, nzb BOOLEAN)'))

    assert db.group_options(TEST)[TEST]['retention'] is None
    assert db.configure(TEST, retention=7)[TEST]['retention'] == 7


def test_fetched_groups_are_listed_even_when_not_watched(db):
    """
    A group with something tracked (it was fetched) is listed; forgetting
    it takes it off again
    """
    with db.transaction() as session:
        session.add(Server(name='news', host='news.example.com'))

    assert db.group_list() == []
    group = db.groups(TEST)[TEST]
    db.track(group, db.server_id('news.example.com'), 1, 2)
    assert [(g['name'], g['watch']) for g in db.group_list()] == [
        (TEST, False)]

    db.forget_tracking([group])
    assert db.group_list() == []


def test_aliases_can_be_added_and_removed(db):
    """
    - removing a group from one alias leaves the others alone
    - names that match nothing, or groups outside the alias, are skipped
      and returned; the rest are still removed
    - no groups removes the whole alias
    """
    db.alias_add('both', [TEST, OTHER])
    db.alias_add('one', [TEST])
    assert db.aliases() == {'both': [OTHER, TEST], 'one': [TEST]}

    result = db.alias_remove('one', [OTHER])
    assert (result.removed, result.outside) == ({}, [OTHER])
    assert db.aliases()['one'] == [TEST]

    with pytest.raises(UnknownAlias):
        db.alias_remove('nosuch')

    result = db.alias_remove('both', [TEST, 'alt.binaries.typo'])
    assert set(result.removed) == {TEST}
    assert result.unknown == ['alt.binaries.typo']
    assert db.aliases() == {'both': [OTHER], 'one': [TEST]}

    db.alias_remove(' ONE ')
    assert db.aliases() == {'both': [OTHER]}

    with pytest.raises(ValueError):
        db.alias_add('  ', [TEST])


def test_a_database_that_was_never_set_up_is_refused(tmp_path):
    """
    Every method says the database can't be used (nothing is made)
    """
    db = NNTPDatabase(engine='sqlite:///%s' % (tmp_path / 'nntp.db'))
    for call in (db.aliases, db.group_list, lambda: db.watch([TEST])):
        with pytest.raises(DatabaseUnavailable):
            call()
    db.close()


def test_a_group_listing_is_kept(db):
    """
    - new groups are added, known ones refreshed (a repeat counted once)
    - a name too long to keep is skipped, and long flags are trimmed
    - the main database can be made on every database (MySQL needs a
      length on every text column)
    - what is tracked for that server gets the new first and last article
    - a server that isn't in the database is refused
    """
    with db.transaction() as session:
        session.add(Server(name='news', host='news.example.com'))
    server = db.server_id('news.example.com')

    db.track(db.groups(TEST)[TEST], server, 1, 2)
    listing = [
        {'group': TEST, 'count': 50, 'flags': ['y'], 'low': 10, 'high': 60},
        {'group': 'alt.new', 'count': 3, 'flags': ['m'], 'low': 1,
         'high': 3},
        {'group': 'alt.new', 'count': 3, 'flags': ['m'], 'low': 1,
         'high': 3},
        # Garbage: too long to keep, so it is skipped (or trimmed)
        {'group': 'alt.' + 'x' * 200, 'count': 1, 'flags': [],
         'low': 1, 'high': 1},
        {'group': 'alt.long', 'count': 1, 'flags': ['='] + ['y'] * 400,
         'low': 1, 'high': 1},
    ]
    assert db.save_groups('news.example.com', listing) == 2

    found = {g['name']: g for g in db.group_list(all=True)}
    assert (found[TEST]['count'], found['alt.new']['flags']) == (50, 'm')
    assert len(found['alt.long']['flags']) == 256
    assert len(found) == 4
    with db.transaction() as session:
        track = session.query(GroupTrack).one()
    assert (track.low, track.high) == (10, 60)

    with pytest.raises(UnknownServer):
        db.save_groups('other.example.com', listing)
    with pytest.raises(UnknownServer):
        db.server_id('other.example.com')


def test_how_far_a_group_was_fetched_is_tracked(db):
    """
    - a scan notes the first and last article, and when it fetched
    - indexing is noted too
    - forgetting the group removes it all
    """
    with db.transaction() as session:
        session.add(Server(name='news', host='news.example.com'))
    server = db.server_id('news.example.com')
    group = db.groups(TEST)[TEST]

    db.track(group, server, 5, 9)
    with db.transaction() as session:
        track = session.query(GroupTrack).one()
    assert (track.low, track.high, track.scan_pointer) == (5, 9, 5)
    first_scan, first_index = track.last_scan, track.last_index

    db.track(group, server, 6, 20, scanned=True)
    db.indexed([group])
    with db.transaction() as session:
        track = session.query(GroupTrack).one()
    assert (track.low, track.high) == (6, 20)
    assert track.last_scan > first_scan
    assert track.last_index > first_index

    db.forget_tracking([group])
    with db.transaction() as session:
        assert session.query(GroupTrack).count() == 0


def test_servers_and_group_counts(db):
    """
    - only servers not there yet are added (each once)
    - groups are counted, watched ones too
    - the latest scan and index of any server is what's tracked
    """
    assert db.add_servers(['a.example.com', 'b.example.com',
                           'a.example.com']) == ['a.example.com',
                                                 'b.example.com']
    assert db.add_servers(['a.example.com']) == []

    db.watch(TEST)
    assert db.group_counts() == (2, 1)

    group = db.groups(TEST)[TEST]
    first, second = (db.server_id('a.example.com'),
                     db.server_id('b.example.com'))
    db.track(group, first, 1, 5, scanned=True)
    db.track(group, second, 1, 5)
    db.indexed([group])
    scanned, indexed = db.tracked()[TEST]
    with db.transaction() as session:
        latest = max(t.last_scan for t in session.query(GroupTrack))
    assert scanned == latest
    assert indexed is not None
    assert OTHER not in db.tracked()


def test_transfer_statistics_are_kept_per_server_and_day(db):
    """
    - what each command did adds to the day's row (workers share it)
    - the fastest action seen is kept, not added
    - servers that did nothing, or aren't in the database, are skipped
    - a database made before statistics were kept gets the table the next
      time it is opened
    - old days are pruned (a dry run only counts them)
    """
    from datetime import timedelta
    from sqlalchemy import text
    from newsreap.NNTPStatistics import NNTPStatistics
    from newsreap.Utils import utcnow

    with db.transaction() as session:
        session.execute(text('DROP TABLE server_stats'))
    engine = db.engine_str()
    db.close()
    assert db.open(engine=engine, reset=None) is not None
    assert db.server_stats() == {}
    assert db.prune_stats(31) == 0

    db.add_servers(['a.example.com'])
    today = utcnow().date()

    def run(rx, peak, **kwargs):
        return NNTPStatistics(rx_bytes=rx, tx_bytes=10, commands=5,
                              peak_rate=peak, started=0.0, finished=2.0,
                              **kwargs)

    assert db.record_stats({
        'a.example.com': run(100, 50.0, fetched=3),
        'b.example.com': run(1, 1.0),
        'idle.example.com': NNTPStatistics()}) == ['a.example.com']
    db.record_stats({'a.example.com': run(300, 20.0, fetched=2)})

    found = db.server_stats()['a.example.com'][today]
    assert (found.rx_bytes, found.tx_bytes, found.fetched) == (400, 20, 5)
    assert (found.peak_rate, found.elapsed) == (50.0, 4.0)

    # Another day starts a row of its own
    db.record_stats({'a.example.com': run(1, 1.0)},
                    day=today - timedelta(days=40))
    assert len(db.server_stats()['a.example.com']) == 2
    assert len(db.server_stats(since=today)['a.example.com']) == 1

    assert db.prune_stats(31, dry_run=True) == 1
    assert db.prune_stats(31) == 1
    assert list(db.server_stats()['a.example.com']) == [today]


def test_today_is_compared_with_a_usual_day(db):
    """
    - each period adds its days up (their run time too, so speeds are
      right)
    - with enough days before today, a slow day or one with many failures
      is warned about; an ordinary one isn't
    """
    from datetime import date
    from datetime import timedelta
    from newsreap.NNTPDatabase import USUAL_DAYS
    from newsreap.NNTPStatistics import NNTPStatistics

    db.add_servers(['a.example.com'])
    today = date(2026, 3, 10)
    mb = 1024 * 1024

    def day(back, rx, seconds, commands=100, dropped=0):
        db.record_stats({'a.example.com': NNTPStatistics(
            rx_bytes=rx, commands=commands, dropped=dropped,
            started=0.0, finished=seconds)},
            day=today - timedelta(days=back))

    for back in range(1, USUAL_DAYS):
        day(back, 100 * mb, 10)

    # Not enough days yet to say what is usual
    day(0, 20 * mb, 10, dropped=50)
    found, = db.transfers(today=today)
    assert (found.usual, found.warnings) == (None, [])

    day(USUAL_DAYS, 100 * mb, 10)
    found, = db.transfers(today=today)
    assert found.days == USUAL_DAYS + 1
    assert found.yesterday.rx_bytes == 100 * mb
    assert found.week.rx_bytes == 320 * mb
    assert found.usual.rate == 10 * mb
    assert found.today.rate == 2 * mb
    assert [what for what, _, _ in found.warnings] == ['slow', 'failures']

    # Busier but just as fast, with few failures: nothing to say
    db.prune_stats(0)
    db.add_servers(['b.example.com'])
    for back in range(USUAL_DAYS):
        db.record_stats({'b.example.com': NNTPStatistics(
            rx_bytes=50 * mb, commands=100, dropped=1, started=0.0,
            finished=5.0)}, day=today - timedelta(days=back + 1))
    db.record_stats({'b.example.com': NNTPStatistics(
        rx_bytes=200 * mb, commands=400, dropped=5, started=0.0,
        finished=20.0)}, day=today)
    found = {t.host: t for t in db.transfers(today=today)}
    assert found['b.example.com'].warnings == []


def test_claims_go_by_the_database_servers_clock(db, monkeypatch):
    """
    On a database server, when a claim runs out is worked out by the
    server's clock, so a worker whose clock is wrong can't take what
    another has (SQLite is on one machine, so its clock is ours)
    """
    from datetime import timedelta
    from newsreap import NNTPDatabase as module
    from newsreap.Utils import utcnow

    assert db.claim(['group:x'], 'a', 120) == ['group:x']

    # Our clock is now an hour ahead
    ahead = utcnow() + timedelta(hours=1)
    monkeypatch.setattr(module, 'utcnow', lambda: ahead)
    with db.transaction() as session:
        dialect = session.get_bind().dialect.name
        now = db._now(session)

    if dialect == 'sqlite':
        assert now == ahead
        assert db.claim(['group:x'], 'b', 120) == ['group:x']

    else:
        assert abs(now - (ahead - timedelta(hours=1))) < timedelta(minutes=5)
        assert db.claim(['group:x'], 'b', 120) == []
        assert set(db.claims()) == {'group:x'}
        assert db.renew_claims('a', 120) == 1


def test_only_one_worker_can_claim_something(db):
    """
    - what another worker has is skipped; our own is renewed
    - a limit stops once that many are ours
    - letting go (or the time running out, or the worker having stopped)
      frees it for the others
    """
    assert db.claims() == {}
    assert db.release_claims('a') == 0

    assert db.claim(['group:x', 'group:y'], 'a', 60) == ['group:x', 'group:y']
    assert db.claim(['group:y', 'group:z'], 'b', 60) == ['group:z']
    assert db.claim(['group:x'], 'a', 60) == ['group:x']
    assert set(db.claims(prefix='group:')) == {'group:x', 'group:y',
                                               'group:z'}
    assert db.claims(prefix='settle') == {}

    slots = ['slot:%d' % no for no in range(4)]
    assert db.claim(slots, 'a', 60, limit=3) == slots[:3]
    assert db.claim(slots, 'b', 60, limit=3) == slots[3:]

    assert db.release_claims('a', ['group:x']) == 1
    assert db.claim(['group:x'], 'b', 60) == ['group:x']

    # Run out (a worker that stopped without letting go)
    assert db.claim(['settle'], 'a', -1) == ['settle']
    assert db.claim(['settle'], 'b', 60) == ['settle']

    # Known to have stopped
    assert db.claim(['group:y'], 'c', 60) == []
    assert db.claim(['group:y'], 'c', 60, free=['a']) == ['group:y']

    assert db.renew_claims('b', 60) == 4
    assert db.release_claims('b') == 4
    assert {owner for owner, _ in db.claims().values()} == {'a', 'c'}


def test_claims_of_a_stopped_worker_are_taken_over(db):
    """
    - a worker on this machine whose process is gone loses its claims at
      once; one still running keeps them
    - closing lets go of everything
    """
    import subprocess
    import sys
    from newsreap.NNTPClaims import NNTPClaims

    gone = subprocess.Popen([sys.executable, '-c', 'pass'])
    gone.wait()

    ours = NNTPClaims(db)
    dead = NNTPClaims(db, owner='%s:%d:x' % (ours.host, gone.pid))
    alive = NNTPClaims(db, owner='%s:%d:y' % (ours.host, os.getpid()))
    away = NNTPClaims(db, owner='elsewhere:%d:z' % gone.pid)

    assert dead.claim(['group:a']) == ['group:a']
    assert alive.claim(['group:b']) == ['group:b']
    assert away.claim(['group:c']) == ['group:c']

    assert ours.stopped() == [dead.owner]
    assert ours.claim(['group:a', 'group:b', 'group:c']) == ['group:a']

    ours.close()
    assert 'group:a' not in db.claims()
    for claims in (dead, alive, away):
        claims.close()
    assert db.claims() == {}


def test_closing_doesnt_wait_long_on_a_stuck_renewal(monkeypatch):
    """
    close() waits only a little while for a renewal that is stuck on the
    database (ie: it is locked), then lets go of our claims anyway
    """
    import threading
    import time
    from newsreap import NNTPClaims as module

    stuck = threading.Event()
    released = []

    class Database:
        def claim(self, names, owner, seconds, limit=None, free=()):
            return list(names)

        def renew_claims(self, owner, seconds):
            stuck.wait(30)

        def claims(self, prefix=None):
            return {}

        def release_claims(self, owner, names=None):
            released.append(owner)
            return 1

    monkeypatch.setattr(module, 'CLOSE_WAIT', 0.2)
    claims = module.NNTPClaims(Database(), lease=0.15)
    claims.claim(['group:x'])

    # Its first renewal is under way (and stuck)
    time.sleep(0.2)
    started = time.monotonic()
    claims.close()
    assert time.monotonic() - started < 5
    assert released == [claims.owner]
    stuck.set()


def test_two_accounts_on_one_server_are_kept_apart(db):
    """
    Each account (or port) of a server is a server of its own: its id,
    group listing and statistics; a plain one is still known by its host
    """
    from newsreap.NNTPStatistics import NNTPStatistics

    servers = [{'host': 'news.example.com', 'username': 'first'},
               {'host': 'news.example.com', 'username': 'second'},
               {'host': 'news.example.com', 'port': 8119},
               'plain.example.com']
    assert db.add_servers(servers) == [
        'first@news.example.com', 'second@news.example.com',
        'news.example.com:8119', 'plain.example.com']
    assert db.add_servers(servers) == []

    ids = {db.server_id(key) for key in (
        'first@news.example.com', 'second@news.example.com',
        'news.example.com:8119', 'plain.example.com')}
    assert len(ids) == 4

    with pytest.raises(UnknownServer):
        db.server_id('news.example.com')

    db.record_stats({
        'first@news.example.com': NNTPStatistics(rx_bytes=10, commands=1),
        'second@news.example.com': NNTPStatistics(rx_bytes=20, commands=1)})
    found = db.server_stats()
    assert {key: sum(day.rx_bytes for day in days.values())
            for key, days in found.items()} == {
        'first@news.example.com': 10, 'second@news.example.com': 20}


def test_server_keys_are_kept_the_same_way(db):
    """
    A server's host is kept in lower case without spaces (its user as it
    was given), however it is handed to us
    """
    from newsreap.Utils import normal_server_key

    assert normal_server_key(' Me@News.Example.COM ') == 'Me@news.example.com'
    assert normal_server_key('News.Example.com:8119') == \
        'news.example.com:8119'

    assert db.add_servers([' News.Example.com ', 'Me@NEWS.example.com']) \
        == ['news.example.com', 'Me@news.example.com']
    assert db.add_servers(['news.example.COM']) == []
    assert db.server_id('NEWS.example.com ') == \
        db.server_id('news.example.com')
    assert db.server_id(' Me@News.Example.com') == \
        db.server_id('Me@news.example.com')

    with db.transaction() as session:
        hosts = sorted(host for host, in session.query(Server.host))
    assert hosts == ['news.example.com', 'news.example.com']


def test_group_counts_past_two_billion_are_kept(db):
    """
    A group's article count (and so its article numbers) can pass 2
    billion on every database
    """
    with db.transaction() as session:
        session.query(Group).filter(Group.name == TEST).update(
            {Group.count: 5000000000})

    with db.transaction() as session:
        assert session.query(Group.count).filter(
            Group.name == TEST).scalar() == 5000000000
