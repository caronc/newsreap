# The database every collection found (across all groups) is kept in
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

from os import makedirs
from os.path import dirname
from os.path import isfile
from sqlalchemy import create_engine
from sqlalchemy import inspect
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import OperationalError

# Importing these libraries forces them associate themselves
# with the ObjectBase
from .objects.index.Collection import Collection
from .objects.index.CollectionAlias import CollectionAlias
from .objects.index.CollectionFile import CollectionFile
from .objects.index.CollectionMeta import CollectionMeta
from .objects.index.FileGroup import FileGroup
from .objects.index.GroupClock import GroupClock
from .objects.index.Segment import Segment

# The ObjectBase which contains all of the data required to
# access our table.
from .objects.index.ObjectBase import ObjectBase
from .objects.index.Vsp import Vsp

from .Database import Database

# SQLite: the full text search index of collection names, file subjects
# and posters (one entry per collection and per file; see
# NNTPIndexer.find())
SEARCH_TABLE = 'collection_search'

# The columns searched (on PostgreSQL and MySQL they get an index each)
SEARCH_COLUMNS = (
    ('collection', 'name'),
    ('collection', 'poster'),
    ('collection_file', 'subject'),
    ('collection_file', 'poster'),
)

# PostgreSQL: ignores the accents of text; it has to be IMMUTABLE so it can
# be indexed (unaccent() itself isn't)
PG_UNACCENT = 'newsreap_unaccent'

# The search index looks for any piece of text (at least 3 characters
# long), whatever its case or accents
SEARCH_TOKENIZERS = (
    'trigram remove_diacritics 1',
    # Older versions of SQLite can't ignore accents
    'trigram',
)

# Keeps the search index up to date whenever a collection or file changes;
# a collection's name uses the negative of its id, its files their own id
SEARCH_TRIGGERS = (
    'CREATE TRIGGER IF NOT EXISTS collection_search_add '
    'AFTER INSERT ON collection BEGIN '
    '  INSERT INTO collection_search(rowid, subject, poster, collection_id) '
    "  VALUES (-new.id, COALESCE(new.name, ''), COALESCE(new.poster, ''), "
    '    new.id); '
    'END',

    'CREATE TRIGGER IF NOT EXISTS collection_search_change '
    'AFTER UPDATE OF name, poster ON collection BEGIN '
    '  DELETE FROM collection_search WHERE rowid = -old.id; '
    '  INSERT INTO collection_search(rowid, subject, poster, collection_id) '
    "  VALUES (-new.id, COALESCE(new.name, ''), COALESCE(new.poster, ''), "
    '    new.id); '
    'END',

    'CREATE TRIGGER IF NOT EXISTS collection_search_remove '
    'AFTER DELETE ON collection BEGIN '
    '  DELETE FROM collection_search WHERE rowid = -old.id; '
    'END',

    'CREATE TRIGGER IF NOT EXISTS collection_search_file_add '
    'AFTER INSERT ON collection_file BEGIN '
    '  INSERT INTO collection_search(rowid, subject, poster, collection_id) '
    "  VALUES (new.id, COALESCE(new.subject, ''), "
    "    COALESCE(new.poster, ''), new.collection_id); "
    'END',

    'CREATE TRIGGER IF NOT EXISTS collection_search_file_change '
    'AFTER UPDATE OF subject, poster, collection_id ON collection_file '
    'BEGIN '
    '  DELETE FROM collection_search WHERE rowid = old.id; '
    '  INSERT INTO collection_search(rowid, subject, poster, collection_id) '
    "  VALUES (new.id, COALESCE(new.subject, ''), "
    "    COALESCE(new.poster, ''), new.collection_id); "
    'END',

    'CREATE TRIGGER IF NOT EXISTS collection_search_file_remove '
    'AFTER DELETE ON collection_file BEGIN '
    '  DELETE FROM collection_search WHERE rowid = old.id; '
    'END',
)


class NNTPIndexDatabase(Database):
    """
    A managment class to handle the Index Database (the collections,
    files and segments found in every group)
    """

    def __init__(self, engine=None, reset=None):
        """
        Initialize NNTP Index Database
        """
        super().__init__(
            base=ObjectBase,
            vsp=Vsp,
            engine=engine,
            reset=reset,
        )

    @classmethod
    def from_engine(cls, engine):
        """
        Opens the index database (creating its tables if they don't exist
        yet):
        - engine: a database URL (ie: postgresql://..., mysql+pymysql://...)
          or the path of an SQLite file
        """
        url = make_url(engine) if '://' in engine else None
        if url is None or url.get_backend_name() == 'sqlite':
            return cls.from_file(url.database if url else engine)

        db = cls(engine=engine, reset=False)

        # Older databases may not have every table yet
        ObjectBase.metadata.create_all(db._engine)

        with db._engine.begin() as conn:
            cls.search_index(conn)

        return db

    @staticmethod
    def exists(engine):
        """
        Returns True if the index database given was ever created (see
        from_engine())
        """
        if '://' not in engine:
            return isfile(engine)

        url = make_url(engine)
        if url.get_backend_name() == 'sqlite':
            return bool(url.database) and isfile(url.database)

        handle = create_engine(engine)
        try:
            return inspect(handle).has_table('collection')

        finally:
            handle.dispose()

    @classmethod
    def from_file(cls, db_file):
        """
        Opens the (SQLite) database stored in db_file, creating it (and its
        directory) if it doesn't exist yet
        """
        makedirs(dirname(db_file), exist_ok=True)
        db = cls(engine='sqlite:///%s' % db_file, reset=False)

        # Older databases may not have every table yet
        ObjectBase.metadata.create_all(db._engine)

        with db._engine.begin() as conn:
            cls.search_index(conn)

        return db

    @classmethod
    def search_index(cls, conn):
        """
        Sets up searching (if the database doesn't have it yet); every
        search ignores case and accents unless asked not to, and finds any
        piece of text:
        - SQLite: an FTS5 (trigram) search table, kept up to date by
          triggers
        - PostgreSQL: pg_trgm indexes (that ignore accents)
        - MySQL: ngram FULLTEXT indexes (its usual _ci collations ignore
          case and accents)
        Any other database works too, but has to look through everything.
        """
        dialect = conn.dialect.name
        if dialect == 'sqlite':
            cls._sqlite_search(conn)

        elif dialect == 'postgresql':
            cls._postgresql_search(conn)

        elif dialect == 'mysql':
            cls._mysql_search(conn)

    @staticmethod
    def _postgresql_search(conn):
        """
        Sets up searching on PostgreSQL (see search_index())
        """
        try:
            # Both come with PostgreSQL; the owner of a database can add
            # them (PostgreSQL 13 and newer)
            conn.exec_driver_sql('CREATE EXTENSION IF NOT EXISTS pg_trgm')
            conn.exec_driver_sql('CREATE EXTENSION IF NOT EXISTS unaccent')

        except DBAPIError as e:
            raise RuntimeError(
                'Searching the index on PostgreSQL needs the pg_trgm and '
                'unaccent extensions; ask your database administrator to '
                'run CREATE EXTENSION pg_trgm; and CREATE EXTENSION '
                'unaccent; in it (%s)' % e.orig) from e

        # The schema unaccent was added to
        schema = conn.exec_driver_sql(
            "SELECT n.nspname FROM pg_extension e JOIN pg_namespace n "
            "ON n.oid = e.extnamespace WHERE e.extname = 'unaccent'").scalar()

        conn.exec_driver_sql(
            'CREATE OR REPLACE FUNCTION %s(text) RETURNS text '
            'LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT AS '
            "$$ SELECT %s.unaccent('%s.unaccent'::regdictionary, $1) $$" % (
                PG_UNACCENT, schema, schema))

        for table, column in SEARCH_COLUMNS:
            conn.exec_driver_sql(
                'CREATE INDEX IF NOT EXISTS %s_%s_search ON %s '
                'USING gin (%s(%s) gin_trgm_ops)' % (
                    table, column, table, PG_UNACCENT, column))

    @staticmethod
    def _mysql_search(conn):
        """
        Sets up searching on MySQL (see search_index())
        """
        # Without this, ngram pieces holding a stop word (ie: 'a' or 'i')
        # are never indexed, so 'cafe' (ca, af, fe) could never be found
        conn.exec_driver_sql('SET SESSION innodb_ft_enable_stopword = OFF')

        for table, column in SEARCH_COLUMNS:
            name = '%s_%s_search' % (table, column)
            if conn.exec_driver_sql(
                    'SELECT 1 FROM information_schema.statistics '
                    'WHERE table_schema = DATABASE() AND table_name = %s '
                    'AND index_name = %s LIMIT 1', (table, name)).first():
                continue

            conn.exec_driver_sql(
                'ALTER TABLE %s ADD FULLTEXT INDEX %s (%s) WITH PARSER ngram'
                % (table, name, column))

    @staticmethod
    def _sqlite_search(conn):
        """
        Sets up searching on SQLite (see search_index())
        """
        if conn.exec_driver_sql(
                "SELECT 1 FROM sqlite_master WHERE name = '%s'"
                % SEARCH_TABLE).first() is None:

            for tokenizer in SEARCH_TOKENIZERS:
                try:
                    conn.exec_driver_sql(
                        "CREATE VIRTUAL TABLE %s USING fts5(subject, poster, "
                        "collection_id UNINDEXED, tokenize='%s')" % (
                            SEARCH_TABLE, tokenizer))
                    break

                except OperationalError:
                    # This version of SQLite doesn't support it; try the
                    # next one
                    continue

            # What is indexed already
            conn.exec_driver_sql(
                'INSERT INTO %s(rowid, subject, poster, collection_id) '
                "SELECT -id, COALESCE(name, ''), COALESCE(poster, ''), id "
                'FROM collection' % SEARCH_TABLE)
            conn.exec_driver_sql(
                'INSERT INTO %s(rowid, subject, poster, collection_id) '
                "SELECT id, COALESCE(subject, ''), COALESCE(poster, ''), "
                'collection_id FROM collection_file' % SEARCH_TABLE)

        for trigger in SEARCH_TRIGGERS:
            conn.exec_driver_sql(trigger)
