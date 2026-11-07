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
    - a database made before statistics were kept gets the table
    - old days are pruned (a dry run only counts them)
    """
    from datetime import timedelta
    from sqlalchemy import text
    from newsreap.NNTPStatistics import NNTPStatistics
    from newsreap.Utils import utcnow

    with db.transaction() as session:
        session.execute(text('DROP TABLE server_stats'))
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
