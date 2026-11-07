# Test that work which grows with the database stays quick
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


from sqlalchemy import event

from newsreap.NNTPGroupDatabase import NNTPGroupDatabase
from newsreap.NNTPIndexer import NNTPIndexer
from tests.NNTPIndexer_Test import cache
from tests.NNTPIndexer_Test import caught_up
from tests.NNTPIndexer_Test import release


def test_settling_again_leaves_settled_collections_alone(tmp_path):
    """
    Once collections have settled, settling again doesn't rewrite them
    (there can be millions); only ones late parts completed would change
    """
    group_db = str(tmp_path / 'group.db')
    articles = []
    for no in range(30):
        articles.extend(release('R%d' % no, files=2, segments=3,
                                skip=[(1, 2)]))
    cache(group_db, articles)
    caught_up(group_db)

    indexer = NNTPIndexer(str(tmp_path / 'index.db'))
    try:
        indexer.index('alt.test', group_db)
        indexer.settle()

        rewritten = []

        def counted(conn, cursor, statement, *args):
            if statement.startswith('UPDATE collection SET state'):
                rewritten.append(cursor.rowcount)

        event.listen(indexer.session().get_bind(), 'after_cursor_execute',
                     counted)
        indexer.settle()
        assert rewritten == [0]

    finally:
        indexer.close()


def test_removing_cached_headers_uses_the_index(tmp_path):
    """
    Headers are removed by looking their Message-IDs up (not by reading
    the whole cache for every few hundred of them)
    """
    group_db = str(tmp_path / 'group.db')
    cache(group_db, release('Holiday', files=1, segments=3))

    db = NNTPGroupDatabase.from_file(group_db)
    try:
        engine = db.session().get_bind()
        deletes = []

        def captured(conn, cursor, statement, parameters, *args):
            if statement.lstrip().upper().startswith('DELETE'):
                deletes.append((statement, parameters))

        event.listen(engine, 'before_cursor_execute', captured)
        assert db.forget(['<holiday.1.1@example.com>',
                          'holiday.1.2@example.com']) == 2
        event.remove(engine, 'before_cursor_execute', captured)

        with engine.connect() as conn:
            plan = ' '.join(
                str(row[-1]) for statement, parameters in deletes
                for row in conn.exec_driver_sql(
                    'EXPLAIN QUERY PLAN ' + statement, parameters))
        assert 'SCAN' not in plan, plan

    finally:
        db.close()


def test_a_group_is_forgotten_a_chunk_at_a_time(tmp_path, monkeypatch):
    """
    Forgetting a group works through it in chunks (so a huge group never
    has to fit in memory) and still removes all of it
    """
    from sqlalchemy import text

    monkeypatch.setattr('newsreap.NNTPIndexer.IN_LIST_SIZE', 1)
    group_db = str(tmp_path / 'group.db')
    cache(group_db, release('Holiday', files=3, segments=2))
    caught_up(group_db)

    indexer = NNTPIndexer(str(tmp_path / 'index.db'))
    try:
        indexer.index('alt.test', group_db)
        assert indexer.forget_group('alt.test') == (3, 1, 0)
        session = indexer.session()
        for table in ('segment', 'collection_file', 'collection'):
            assert session.execute(text(
                'SELECT COUNT(*) FROM %s' % table)).scalar() == 0

    finally:
        indexer.close()
