# Puts loose articles into batches of whole files for downloading
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
"""
Puts loose articles (ones a search found on their own rather than as a
collection) into batches of whole files, so downloading millions of them
only ever holds one batch in memory:

- each article is noted with the file it is part of: the indexed file it
  belongs to, else the file its subject names (see NNTPFetcher.files_of());
  a part seen again belongs to another copy of that file (ie: a repost),
  which is a file of its own
- they come back a file at a time, in the order each file was first
  found, so a file is never split between two batches (a part posted again
  much later still joins the rest)
- a batch holds whole files until it reaches `size` articles (one file
  bigger than that is a batch of its own)

They are kept in a temporary SQLite database, which stays in memory while
it fits in its cache and only uses the disk once it outgrows it.
"""

import json
import sqlite3

from .NNTPFetcher import NNTPFetcher
from .NNTPIndexer import chunked
from .objects.index.Segment import Segment

# How many articles a batch holds (whole files are never split)
DEFAULT_BATCH_SIZE = 10000

# How much memory the articles waiting to be batched may use before the
# rest of them go to disk
CACHE_SIZE = 256 * 1048576

# How many articles are looked up in the index at a time
LOOKUP_SIZE = 1000


class NNTPArticleBatches:
    """
    Loose articles put into batches of whole files; see the module
    description.  Iterating gives each batch (a list of the articles
    added), len() how many batches there are.
    """

    def __init__(self, indexer=None, size=DEFAULT_BATCH_SIZE,
                 cache_size=CACHE_SIZE):
        """
        - indexer: the NNTPIndexer that knows which files the articles are
          part of (only their subjects are used without one)
        - size: how many articles a batch holds
        - cache_size: how many bytes are kept in memory before using the
          disk
        """
        self.indexer = indexer
        self.size = max(1, int(size or DEFAULT_BATCH_SIZE))

        # How many articles were added
        self.articles = 0

        # How many batches there are (worked out when first asked)
        self._batches = None

        # An empty name is a temporary database: it is in memory until it
        # outgrows its cache, and is gone once it is closed
        self._db = sqlite3.connect('')
        self._db.execute('PRAGMA cache_size = -%d' % max(
            1, int(cache_size) // 1024))
        self._db.executescript(
            'CREATE TABLE files ('
            ' id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE);'
            'CREATE TABLE articles ('
            ' seq INTEGER PRIMARY KEY, file INTEGER NOT NULL,'
            ' data TEXT NOT NULL);'
            'CREATE INDEX articles_file ON articles (file);'
            # A file (by its subject) and part -> how many copies have it
            'CREATE TABLE seen ('
            ' key TEXT PRIMARY KEY, copies INTEGER NOT NULL) WITHOUT ROWID;')

    def add(self, articles):
        """
        Adds articles (an iterable of any length; each a dict with at
        least its 'message_id', and its 'subject' and 'group' when known)
        """
        session = self.indexer.session() if self.indexer is not None \
            else None

        for chunk in chunked(articles, LOOKUP_SIZE):
            # The files the index has them under
            ids = [article['message_id'] for article in chunk]
            indexed = dict(session.query(
                Segment.message_id, Segment.file_id)
                .filter(Segment.message_id.in_(ids))) \
                if session is not None else {}

            # The file (by its subject) and part of each article that
            # isn't indexed, and how many copies we have of each so far
            parts = [None] * len(chunk)
            for no, article in enumerate(chunk):
                if article['message_id'] not in indexed:
                    key, parsed = NNTPFetcher.loose_key(article)
                    if key is not None:
                        parts[no] = '%s\0%d' % (key, parsed['part'])

            wanted = list({part for part in parts if part is not None})
            seen = dict(self._db.execute(
                'SELECT key, copies FROM seen WHERE key IN (%s)' % ','.join(
                    '?' * len(wanted)), wanted)) if wanted else {}

            keys = []
            for no, article in enumerate(chunk):
                file_id = indexed.get(article['message_id'])
                if file_id is not None:
                    keys.append('i:%d' % file_id)

                elif parts[no] is None:
                    # Its subject names no file; it is a file of its own
                    keys.append('u:%d' % (self.articles + no))

                else:
                    # Every part of a file is kept together, so a batch
                    # holds each file whole; a part seen again goes to the
                    # next copy (see NNTPFetcher.files_of())
                    copy = seen.get(parts[no], 0)
                    seen[parts[no]] = copy + 1
                    keys.append('n:%s\0%d' % (
                        parts[no].rsplit('\0', 1)[0], copy))

            self._db.executemany(
                'INSERT INTO seen (key, copies) VALUES (?, ?) '
                'ON CONFLICT (key) DO UPDATE SET copies = excluded.copies',
                ((part, seen[part]) for part in wanted))

            # Each file is numbered the first time it is seen, so they come
            # back in that order
            self._db.executemany(
                'INSERT OR IGNORE INTO files (key) VALUES (?)',
                ((key, ) for key in keys))
            numbers = dict(self._db.execute(
                'SELECT key, id FROM files WHERE key IN (%s)' % ','.join(
                    '?' * len(set(keys))), tuple(set(keys))))

            self._db.executemany(
                'INSERT INTO articles (file, data) VALUES (?, ?)',
                ((numbers[key], json.dumps(article))
                 for key, article in zip(keys, chunk)))
            self.articles += len(chunk)

        self._db.commit()
        self._batches = None

    def files(self, batch):
        """
        Returns what downloading a batch needs, as
        NNTPIndexer.fetch_files() does: (files, known); the articles that
        aren't indexed are put together by their subjects
        """
        ids = [article['message_id'] for article in batch]
        files, known = self.indexer.fetch_files(message_ids=ids) \
            if self.indexer is not None and \
            self.indexer.session() is not None else ([], {})

        indexed = {m for f in files for _, m in f['segments']}
        files.extend(NNTPFetcher.files_of(
            article for article in batch
            if article['message_id'] not in indexed))
        return files, known

    def close(self):
        """
        Lets go of the articles (the temporary database goes with them)
        """
        self._db.close()

    def __iter__(self):
        """
        Yields each batch: a list of the articles added, a file at a time
        """
        batch, last = [], None
        for number, data in self._db.execute(
                'SELECT file, data FROM articles ORDER BY file, seq'):
            if number != last and len(batch) >= self.size:
                yield batch
                batch = []

            last = number
            batch.append(json.loads(data))

        if batch:
            yield batch

    def __len__(self):
        """
        How many batches there are
        """
        if self._batches is None:
            # Counted the way __iter__() splits them
            self._batches = held = 0
            for count, in self._db.execute(
                    'SELECT COUNT(*) FROM articles GROUP BY file '
                    'ORDER BY file'):
                if held >= self.size:
                    self._batches += 1
                    held = 0
                held += count

            if held:
                self._batches += 1

        return self._batches

    def __bool__(self):
        return self.articles > 0

    def __str__(self):
        return '%d article(s) in %d batch(es)' % (self.articles, len(self))

    def __repr__(self):
        return '<NNTPArticleBatches articles=%d size=%d />' % (
            self.articles, self.size)
