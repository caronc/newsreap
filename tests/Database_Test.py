# Test the Database Object
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

import sys
from os.path import dirname
from os.path import abspath
from os.path import join

import pytest

try:
    from tests.TestBase import TestBase

except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase
from newsreap.NNTPGroupDatabase import NNTPGroupDatabase
from tests.NNTPIndexer_Test import index_engine  # noqa: F401


class Database_Test(TestBase):
    """
    Tests the Database base object shared by all of the NewsReap databases
    """

    def test_values_are_stored_and_read_back(self):
        """
        set() saves a value that get() returns
        """
        db = NNTPGroupDatabase(
            engine='sqlite:///%s' % join(self.tmp_dir, 'values.db'),
            reset=True)

        assert db.get('missing', default='nope') == 'nope'
        db.set('colour', 'blue')
        assert db.get('colour') == 'blue'
        db.set('colour', 'green')
        assert db.get('colour') == 'green'

        # Setting a value to None removes it
        db.set('colour', None)
        assert db.get('colour') is None
        db.close()

    def test_closing_one_database_leaves_others_open(self):
        """
        Closing a database only closes its own session
        """
        db1 = NNTPGroupDatabase(
            engine='sqlite:///%s' % join(self.tmp_dir, 'db1.db'),
            reset=True)
        db2 = NNTPGroupDatabase(
            engine='sqlite:///%s' % join(self.tmp_dir, 'db2.db'),
            reset=True)

        db1.set('name', 'one')
        db2.set('name', 'two')

        # Each database keeps its own values
        assert db1.get('name') == 'one'
        assert db2.get('name') == 'two'

        # Load an entry through our second database
        session = db2.session()
        entry = session.query(db2.Vsp).filter_by(key='name').first()
        assert entry in session

        db1.close()

        # Our second database (and what it loaded) is untouched
        assert entry in session
        assert db2.get('name') == 'two'
        db2.close()


def test_a_failed_setting_leaves_the_session_usable(tmp_path, monkeypatch):
    """
    When saving a setting fails, the session is rolled back so the next
    database call still works
    """
    from sqlalchemy.exc import OperationalError

    db = NNTPGroupDatabase(
        engine='sqlite:///%s' % (tmp_path / 'db.db'), reset=True)
    session = db.session()

    def locked(*args, **kwargs):
        raise OperationalError('UPDATE vsp', {}, Exception('locked'))

    rolled_back = []
    monkeypatch.setattr(session, 'query', locked)
    monkeypatch.setattr(
        session, 'rollback', lambda: rolled_back.append(True))

    assert db.set('name', 'value') is False
    assert rolled_back == [True]
    monkeypatch.undo()
    db.close()


def test_database_passwords_are_never_logged(caplog):
    """
    The database URL is logged without its password
    """
    pytest.importorskip('psycopg')
    import logging
    from newsreap.Logging import NEWSREAP_ENGINE

    caplog.set_level(logging.DEBUG, logger=NEWSREAP_ENGINE)
    try:
        NNTPGroupDatabase(
            engine='postgresql+psycopg://user:secret@127.0.0.1:1/nothing'
        ).session()

    except Exception:
        # Nothing listens there; only what was logged matters
        pass

    assert 'Preparing engine' in caplog.text
    assert 'secret' not in caplog.text


def test_sqlite_refuses_to_remove_rows_still_pointed_to(tmp_path):
    """
    SQLite enforces foreign keys as the database servers do: a collection
    can't be removed while its files still point to it
    """
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    from newsreap.NNTPIndexDatabase import NNTPIndexDatabase

    db = NNTPIndexDatabase.from_file(str(tmp_path / 'index.db'))
    session = db.session()
    try:
        session.execute(text(
            "INSERT INTO collection (id, key, name, poster, state, source, "
            "files, files_expected, segments, segments_expected, size, "
            "score, tags) VALUES (1, 'k', 'n', 'p', 1, 1, 1, 1, 0, 0, 0, "
            "0, '')"))
        session.execute(text(
            "INSERT INTO collection_file (id, key, collection_id, subject, "
            "poster, filename, file_no, segments_expected, segments, size) "
            "VALUES (1, 'f', 1, 's', 'p', 'n', 1, 1, 0, 0)"))
        session.commit()

        with pytest.raises(IntegrityError):
            session.execute(text('DELETE FROM collection WHERE id = 1'))
            session.commit()

    finally:
        session.rollback()
        db.close()


#
# Short transactions (ie: one per web request)
#
@pytest.fixture
def index_db(index_engine):
    """
    An empty index database on SQLite and each database server tested on
    """
    from newsreap.NNTPIndexDatabase import NNTPIndexDatabase

    db = NNTPIndexDatabase.from_engine(index_engine)
    yield db
    db.close()


def saved(db, key):
    """
    Returns the value stored under `key`, as a separate connection sees it
    """
    with db.transaction() as session:
        entry = session.query(db.Vsp).filter_by(
            group='test', key=key).first()
        return entry.value if entry else None


def test_a_transaction_saves_what_was_done(index_db):
    """
    What a transaction does is committed once its block ends, and what it
    read can still be used after
    """
    with index_db.transaction() as session:
        session.add(index_db.Vsp(group='test', key='a', value='1'))

    with index_db.transaction() as session:
        entry = session.query(index_db.Vsp).filter_by(
            group='test', key='a').one()

    assert entry.value == '1'
    assert saved(index_db, 'a') == '1'


def test_a_failed_transaction_saves_nothing(index_db):
    """
    An error undoes everything the transaction did and is raised
    """
    with pytest.raises(KeyError):
        with index_db.transaction() as session:
            session.add(index_db.Vsp(group='test', key='a', value='1'))
            session.flush()
            raise KeyError('a')

    assert saved(index_db, 'a') is None


def test_unfinished_work_is_not_seen_by_other_transactions(index_db):
    """
    What one transaction hasn't committed yet is invisible to another
    """
    with index_db.transaction() as session:
        session.add(index_db.Vsp(group='test', key='a', value='1'))
        session.flush()
        assert saved(index_db, 'a') is None

    assert saved(index_db, 'a') == '1'


def test_transactions_can_run_in_several_threads(index_db):
    """
    Each thread's transaction has a session of its own
    """
    from concurrent.futures import ThreadPoolExecutor

    def write(no):
        with index_db.transaction() as session:
            session.add(index_db.Vsp(group='test', key=str(no), value=no))

    with ThreadPoolExecutor(8) as pool:
        list(pool.map(write, range(40)))

    assert [saved(index_db, str(no)) for no in range(40)] == \
        [str(no) for no in range(40)]


def test_a_database_without_its_tables_is_refused(tmp_path):
    """
    A database that was never set up can't be used
    """
    from newsreap.Database import DatabaseUnavailable

    db = NNTPGroupDatabase(engine='sqlite:///%s' % (tmp_path / 'empty.db'))
    for _ in range(2):
        with pytest.raises(DatabaseUnavailable):
            with db.transaction():
                pass
    db.close()


def test_a_database_server_that_cant_be_reached_is_refused():
    """
    A database server that can't be reached is refused without showing
    its password
    """
    pytest.importorskip('pymysql')
    from newsreap.Database import DatabaseUnavailable

    db = NNTPGroupDatabase(
        engine='mysql+pymysql://user:secret@127.0.0.1:1/nothing')
    with pytest.raises(DatabaseUnavailable) as error:
        with db.transaction():
            pass

    assert 'secret' not in str(error.value)
    assert '127.0.0.1:1' in str(error.value)
    db.close()


def test_connections_a_database_server_dropped_are_replaced(index_db):
    """
    A connection the server closed (ie: left idle too long) is replaced
    before it is used, rather than failing the next transaction
    """
    from sqlalchemy import create_engine
    from sqlalchemy import text

    with index_db.transaction() as session:
        dialect = session.get_bind().dialect.name

    if dialect == 'sqlite':
        pytest.skip('SQLite has no server to drop connections')

    # The server drops every connection we left in our pool
    others = {
        'postgresql': 'SELECT pid FROM pg_stat_activity WHERE datname = '
                      'current_database() AND pid <> pg_backend_pid()',
        'mysql': 'SELECT id FROM information_schema.processlist WHERE '
                 'db = DATABASE() AND id <> CONNECTION_ID()',
    }[dialect]
    drop = {
        'postgresql': 'SELECT pg_terminate_backend(%d)',
        'mysql': 'KILL %d',
    }[dialect]

    server = create_engine(index_db.engine_str())
    with server.connect() as connection:
        for (no, ) in connection.execute(text(others)).all():
            connection.execute(text(drop % no))
    server.dispose()

    with index_db.transaction() as session:
        session.add(index_db.Vsp(group='test', key='a', value='1'))

    assert saved(index_db, 'a') == '1'
