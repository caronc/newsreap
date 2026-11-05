# Test that articles with very long Message-IDs or subjects are coped with
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

from sqlalchemy import text

from newsreap.NNTPArticleRanks import NNTPArticleRanks
from newsreap.NNTPIndexer import fit_key
from tests.NNTPIndexer_Test import GROUP
from tests.NNTPIndexer_Test import cache
from tests.NNTPIndexer_Test import index_engine  # noqa: F401
from tests.NNTPIndexer_Test import indexed
from tests.NNTPIndexer_Test import paths  # noqa: F401


def test_long_keys_fit_and_stay_apart():
    """
    A key too long for its column is cut down to fit, and two that start
    the same still differ
    """
    one, two = fit_key('x' * 600 + 'a'), fit_key('x' * 600 + 'b')
    assert len(one) == len(two) == 512
    assert one != two
    assert fit_key('short') == 'short'


def test_long_subjects_and_message_ids_are_coped_with(paths):
    """
    A very long subject still indexes (its keys fit their columns), and an
    article whose Message-ID is longer than any can be is left out
    """
    cache(paths[GROUP], [
        ('%s [1/1] - "%s.rar" yEnc (1/1)' % ('L' * 600, 'x' * 300),
         'p@x', 'ok@example.com'),
        ('Too long [1/1] - "y.rar" yEnc (1/1)', 'p@x', 'm' * 300 + '@x')])

    indexer = indexed(paths)
    try:
        session = indexer.session()
        assert session.execute(text(
            'SELECT MAX(LENGTH(key)) FROM collection')).scalar() <= 512
        assert session.execute(text(
            'SELECT MAX(LENGTH(key)) FROM collection_file')).scalar() <= 512
        assert [m for m, in session.execute(text(
            'SELECT message_id FROM segment'))] == ['ok@example.com']

    finally:
        indexer.close()


def test_ranks_of_long_message_ids_can_be_saved_again(paths):
    """
    The score of an article with a long Message-ID is kept, found, and
    can be saved again
    """
    from newsreap.NNTPIndexDatabase import NNTPIndexDatabase

    long_id = 'r' * 302 + '@x'
    db = NNTPIndexDatabase.from_engine(paths['index'])
    try:
        session = db.session()
        NNTPArticleRanks.save(session, {long_id: (5, frozenset(['a']))})
        session.commit()
        NNTPArticleRanks.save(session, {long_id: (7, frozenset(['b']))})
        session.commit()
        assert NNTPArticleRanks.load(session, [long_id]) == {
            long_id: (7, frozenset(['b']))}

    finally:
        db.close()


def test_sizes_over_2gb_fit_on_database_servers():
    """
    Collection and file sizes are 64 bit numbers on PostgreSQL and MySQL
    (most releases are bigger than 2GB)
    """
    from sqlalchemy.dialects import mysql
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateTable

    from newsreap.objects.index.Collection import Collection
    from newsreap.objects.index.CollectionFile import CollectionFile

    for table in (Collection.__table__, CollectionFile.__table__):
        for dialect in (postgresql.dialect(), mysql.dialect()):
            created = str(CreateTable(table).compile(dialect=dialect))
            line = next(line for line in created.splitlines()
                        if line.strip().startswith('size '))
            assert 'BIGINT' in line, (table.name, dialect.name, line)
