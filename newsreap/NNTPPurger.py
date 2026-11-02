# Applies filters to everything already stored: drops what they remove
# and works scores and tags out again
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
Filters (see NNTPFilter) are checked as articles arrive, but rules added
later only reach what is already stored through a purge.  A purge works
through every place articles are kept, a chunk at a time:

- the header caches (one per group): dropped articles are deleted (the
  article numbers stay marked as fetched, so a scan doesn't fetch them
  again)
- the index: dropped files and segments are deleted, totals recounted,
  and collections left with nothing are removed (collections from NZB
  files are left alone unless asked)
- the archive: dropped posts are deleted (with their files and groups)

With `rescore` the scores and tags are worked out again (from 0) and
saved where every article's are kept (see NNTPArticleRanks); otherwise the
ones stored are what the rules see.  An article is judged in every group
it was posted to, with everything known about it (its header, the names
of its files, and its text once archived), so it scores the same wherever
it is found.  Collections then take on the best score and every tag of
their articles.  Articles only the index still has (their headers were let
go of) keep the scores they had.
A dry run changes nothing; it only counts (and shows a few examples).
"""

from fnmatch import fnmatchcase
from os.path import isfile

from sqlalchemy import literal

from .NNTPArticleRanks import NNTPArticleRanks
from .NNTPFilterRule import FilterItem
from .NNTPFilterRule import tags_to_text
from .NNTPGroupDatabase import NNTPGroupDatabase
from .NNTPIndexDatabase import NNTPIndexDatabase
from .NNTPIndexer import NNTPIndexer
from .NNTPIndexer import chunked
from .objects.group.Article import Article
from .objects.index.ArchiveFile import ArchiveFile
from .objects.index.ArchiveGroup import ArchiveGroup
from .objects.index.ArchivedPost import ArchivedPost
from .objects.index.Collection import Collection
from .objects.index.Collection import CollectionSource
from .objects.index.Collection import CollectionState
from .objects.index.CollectionAlias import CollectionAlias
from .objects.index.CollectionFile import CollectionFile
from .objects.index.CollectionMeta import CollectionMeta
from .objects.index.FileGroup import FileGroup
from .objects.index.Segment import Segment

# Logging
import logging
from newsreap.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)

# How many articles (or archived posts) are looked at a time
DEFAULT_PURGE_CHUNK_SIZE = 10000

# How many collections are looked at a time (with all of their files)
DEFAULT_PURGE_COLLECTIONS = 200

# How many examples of what is dropped are kept (per place) for showing
DEFAULT_PURGE_SAMPLES = 10


class PurgeCount:
    """
    What a purge found in one place (the header caches, the index or the
    archive)
    """

    def __init__(self):
        # How many items were looked at, dropped, and given a new score
        # or tags
        self.checked = 0
        self.dropped = 0
        self.rescored = 0

        # Index only: the files and collections removed
        self.files = 0
        self.collections = 0

        # A few of the items dropped: (group(s), subject, poster, rule)
        self.samples = []

    def __str__(self):
        return '%d checked, %d dropped, %d rescored' % (
            self.checked, self.dropped, self.rescored)


class NNTPPurger:
    """
    Applies filters to what is already stored; see the module description
    """

    def __init__(self, filters, index_db=None, rescore=False, drop=True,
                 dry_run=False, nzb=False, groups=None,
                 chunk_size=DEFAULT_PURGE_CHUNK_SIZE,
                 samples=DEFAULT_PURGE_SAMPLES):
        """
        Prepares a purge

        - filters: the NNTPFilter to apply
        - index_db: the index database (where the scores and tags of
          articles are kept; see NNTPArticleRanks)
        - rescore: work scores and tags out again (from 0) and save them
        - drop: delete what the drop rules match (otherwise only scores and
          tags change)
        - dry_run: change nothing; only count
        - nzb: include collections imported from NZB files
        - groups: only what was found in one of these groups (wildcards
          like alt.binaries.* work); everything when None
        - chunk_size: how many items are looked at a time
        - samples: how many examples of what is dropped are kept
        """
        self.filters = filters
        self.index_db = index_db
        self.rescore = bool(rescore)
        self.drop = bool(drop)
        self.dry_run = bool(dry_run)
        self.nzb = bool(nzb)
        self.groups = None if groups is None \
            else tuple(g.strip().lower() for g in groups)
        self.chunk_size = max(1, int(chunk_size))
        self.max_samples = max(0, int(samples))

        # What we found, per place
        self.headers = PurgeCount()
        self.index = PurgeCount()
        self.archive = PurgeCount()

        # The index database (opened when first needed)
        self._index = None

    def close(self):
        """
        Lets go of the index database
        """
        if self._index is not None:
            self._index.close()
            self._index = None

    def _index_session(self, db_file=None):
        """
        Returns a session to the index database; None if nothing was ever
        indexed
        """
        db_file = db_file or self.index_db
        if self._index is None:
            if not db_file or not NNTPIndexDatabase.exists(db_file):
                return None

            self._index = NNTPIndexDatabase.from_engine(db_file)

        return self._index.session()

    def _in_scope(self, groups):
        """
        Returns True if an item found in these groups is ours to purge
        """
        if self.groups is None:
            return True

        return any(fnmatchcase(group.lower(), pattern)
                   for group in groups for pattern in self.groups)

    def _sample(self, count, groups, subject, poster, rule):
        """
        Keeps an example of what was dropped
        """
        if len(count.samples) < self.max_samples:
            count.samples.append((
                ', '.join(sorted(groups)), subject, poster, rule.name))

    def _judge(self, groups, item, stored):
        """
        Returns what our filters make of an item (see NNTPFilter.check());
        stored is the (score, tags) it has now
        """
        score, tags = stored
        return self.filters.check_item(
            groups, item, score=score or 0, tags=frozenset(tags),
            rescore=self.rescore)

    def purge_headers(self, group, group_db_file):
        """
        Applies our filters to the headers cached for a group

        Every article is judged in all the groups it was posted to (its
        Xref), with what the index knows about it too (its file names and
        text, when a rule needs them), so it is judged the same way
        wherever it is found.

        Returns True if it ran (False if nothing was ever cached for it).
        """
        if not isfile(group_db_file):
            # Nothing was ever cached for this group
            return False

        count = self.headers
        cache = NNTPGroupDatabase.from_file(group_db_file)
        session = cache.session()
        index = self._index_session()

        # Where we are (by article number)
        last = None

        try:
            while True:
                query = session.query(
                    Article.message_id, Article.article_no, Article.subject,
                    Article.poster, Article.size, Article.posted_date,
                    Article.xref)

                if last is not None:
                    query = query.filter(Article.article_no > last)

                rows = query.order_by(Article.article_no)\
                    .limit(self.chunk_size).all()

                if not rows:
                    # We're done
                    break

                last = rows[-1].article_no
                ids = [row.message_id for row in rows]

                # What they score now, and what else is known about them
                stored = NNTPArticleRanks.load(index, ids) if index else {}
                known = NNTPArticleRanks.known(
                    index, ids, bodies=self.filters.uses('body'),
                    filenames=self.filters.uses('filename')) \
                    if index else {}

                dropped = []
                changed = {}
                for row in rows:
                    count.checked += 1
                    groups = [group, *(row.xref or '').split()]
                    extra = known.get(row.message_id, {})
                    item = FilterItem(
                        subject=row.subject, poster=row.poster,
                        message_id=row.message_id, posted=row.posted_date,
                        size=row.size, body=extra.get('body'),
                        filenames=extra.get('filenames'))

                    now = stored.get(row.message_id, (0, frozenset()))
                    verdict = self._judge(groups, item, now)
                    if self.drop and verdict.drop is not None:
                        dropped.append(row.message_id)
                        self._sample(
                            count, groups, row.subject, row.poster,
                            verdict.drop)
                        continue

                    if self.rescore and (verdict.score, verdict.tags) != (
                            now[0], frozenset(now[1])):
                        changed[row.message_id] = (
                            verdict.score, verdict.tags)

                count.dropped += len(dropped)
                count.rescored += len(changed)

                if self.dry_run:
                    continue

                for ids in chunked(dropped):
                    session.query(Article)\
                        .filter(Article.message_id.in_(ids))\
                        .delete(synchronize_session=False)
                session.commit()

                if index is not None and (changed or dropped):
                    NNTPArticleRanks.save(index, changed)
                    NNTPArticleRanks.forget(index, dropped)
                    index.commit()

        finally:
            cache.close()

        return True

    def purge_archive(self, db_file=None):
        """
        Applies our filters to the archived posts (see NNTPArchiver); they
        are judged knowing their text and file names

        Returns True if it ran (False if nothing was ever indexed).
        """
        session = self._index_session(db_file)
        if session is None:
            # Nothing was ever indexed (or archived)
            return False

        count = self.archive

        # Their text (and file names) are only read when a rule needs them
        bodies = self.filters.uses('body')
        names = self.filters.uses('filename')

        # Where we are (by post id)
        last = 0

        while True:
            posts = session.query(
                ArchivedPost.id, ArchivedPost.message_id,
                ArchivedPost.subject, ArchivedPost.poster,
                ArchivedPost.posted, ArchivedPost.size,
                ArchivedPost.text if bodies else literal(None))\
                .filter(ArchivedPost.id > last)\
                .order_by(ArchivedPost.id)\
                .limit(self.chunk_size).all()

            if not posts:
                # We're done
                break

            last = posts[-1].id
            ids = [p.message_id for p in posts]

            # The groups each one was found in, what they score now, and
            # their file names (from the archive and the index)
            groups = {}
            for chunk in chunked(ids):
                for message_id, name in session.query(
                        ArchiveGroup.message_id, ArchiveGroup.name)\
                        .filter(ArchiveGroup.message_id.in_(chunk)):
                    groups.setdefault(message_id, set()).add(name)

            stored = NNTPArticleRanks.load(session, ids)
            known = NNTPArticleRanks.known(
                session, ids, filenames=True) if names else {}

            dropped = []
            changed = {}
            for post in posts:
                post_groups = groups.get(post.message_id, set())
                if not self._in_scope(post_groups):
                    continue

                count.checked += 1
                item = FilterItem(
                    subject=post.subject, poster=post.poster,
                    message_id=post.message_id, posted=post.posted,
                    size=post.size, body=post[-1] if bodies else None,
                    filenames=known.get(post.message_id, {}).get(
                        'filenames', []) if names else None)

                now = stored.get(post.message_id, (0, frozenset()))
                verdict = self._judge(post_groups, item, now)

                if self.drop and verdict.drop is not None:
                    dropped.append(post.message_id)
                    self._sample(
                        count, post_groups, post.subject, post.poster,
                        verdict.drop)
                    continue

                if self.rescore and (verdict.score, verdict.tags) != (
                        now[0], frozenset(now[1])):
                    changed[post.message_id] = (verdict.score, verdict.tags)

            count.dropped += len(dropped)
            count.rescored += len(changed)

            if self.dry_run or not (dropped or changed):
                continue

            for chunk in chunked(dropped):
                session.query(ArchiveFile)\
                    .filter(ArchiveFile.message_id.in_(chunk))\
                    .delete(synchronize_session=False)
                session.query(ArchiveGroup)\
                    .filter(ArchiveGroup.message_id.in_(chunk))\
                    .delete(synchronize_session=False)
                session.query(ArchivedPost)\
                    .filter(ArchivedPost.message_id.in_(chunk))\
                    .delete(synchronize_session=False)

            NNTPArticleRanks.save(session, changed)
            NNTPArticleRanks.forget(session, dropped)
            session.commit()

        return True

    def purge_index(self, db_file=None):
        """
        Applies our filters to the collections indexed (see NNTPIndexer):
        their files (and articles) are dropped the way the rules say, and
        when rescoring, each collection takes on its articles' scores and
        tags again (see NNTPArticleRanks)

        Run it after purge_headers() and purge_archive(), so the scores it
        reads are the new ones.

        Returns True if it ran (False if nothing was ever indexed).
        """
        session = self._index_session(db_file)
        if session is None:
            # Nothing was ever indexed
            return False

        indexer = NNTPIndexer(db_file or self.index_db)
        count = self.index

        # Articles only differ from the rest of their file by their
        # Message-ID and size; only look at each of them when a rule does
        each_segment = self.filters.uses('message_id') or \
            self.filters.uses('size')

        # Where we are (by collection id)
        last = 0

        try:
            while True:
                query = session.query(
                    Collection.id, Collection.score, Collection.tags)\
                    .filter(Collection.id > last)\
                    .filter(Collection.state != CollectionState.Deleted)

                if not self.nzb:
                    # NZB files are the truth; left alone unless asked
                    query = query.filter(
                        Collection.source == CollectionSource.Scan)

                collections = query.order_by(Collection.id)\
                    .limit(DEFAULT_PURGE_COLLECTIONS).all()

                if not collections:
                    # We're done
                    break

                last = collections[-1].id
                self._purge_collections(
                    indexer, session, collections, each_segment, count)

        finally:
            indexer.close()

        return True

    def _purge_collections(self, indexer, session, collections, each_segment,
                           count):
        """
        Applies our filters to a chunk of collections (with all of their
        files)
        """
        stored = {c.id: c for c in collections}

        # Their files, and the groups each one was found in
        files = session.query(
            CollectionFile.id, CollectionFile.collection_id,
            CollectionFile.subject, CollectionFile.poster,
            CollectionFile.first_posted, CollectionFile.filename)\
            .filter(CollectionFile.collection_id.in_(list(stored)))\
            .order_by(CollectionFile.id).all()

        groups = {}
        for ids in chunked([f.id for f in files]):
            for file_id, name in session.query(
                    FileGroup.file_id, FileGroup.name)\
                    .filter(FileGroup.file_id.in_(ids)):
                groups.setdefault(file_id, set()).add(name)

        # Only collections with a file in our groups are looked at
        wanted = {f.collection_id for f in files
                  if self._in_scope(groups.get(f.id, ()))}

        # Their articles (and what they score)
        segments = {}
        for ids in chunked([f.id for f in files
                            if f.collection_id in wanted]):
            for message_id, file_id, size in session.query(
                    Segment.message_id, Segment.file_id, Segment.size)\
                    .filter(Segment.file_id.in_(ids)):
                segments.setdefault(file_id, []).append((message_id, size))

        ranks = NNTPArticleRanks.load(session, [
            message_id for found in segments.values()
            for message_id, _ in found])

        def best(file_id):
            # The best score (and every tag) of a file's articles
            found = [ranks.get(m, (0, frozenset()))
                     for m, _ in segments.get(file_id, ())]
            if not found:
                return 0, frozenset()
            return max(s for s, _ in found), frozenset().union(
                *(t for _, t in found))

        # What we decide:
        #  - lost_files: files dropped as a whole
        #  - lost_segments: articles dropped on their own
        #  - kept: the collections something is left of
        lost_files = []
        lost_segments = []
        kept = set()

        for entry in files:
            if entry.collection_id not in wanted:
                continue

            file_groups = groups.get(entry.id, set())
            scoped = self._in_scope(file_groups)

            if not each_segment:
                # The whole file at once (with its best article's score)
                count.checked += 1
                item = FilterItem(
                    subject=entry.subject, poster=entry.poster,
                    posted=entry.first_posted, filenames=[entry.filename])

                verdict = self.filters.check_item(
                    file_groups, item, *best(entry.id), rescore=False)

                if scoped and self.drop and verdict.drop is not None:
                    lost_files.append(entry.id)
                    self._sample(
                        count, file_groups, entry.subject, entry.poster,
                        verdict.drop)
                    continue

                kept.add(entry.collection_id)
                continue

            # Article by article
            left = 0
            for message_id, size in segments.get(entry.id, ()):
                count.checked += 1
                item = FilterItem(
                    subject=entry.subject, poster=entry.poster,
                    message_id=message_id, posted=entry.first_posted,
                    size=size, filenames=[entry.filename])

                score, tags = ranks.get(message_id, (0, frozenset()))
                verdict = self.filters.check_item(
                    file_groups, item, score, tags, rescore=False)

                if scoped and self.drop and verdict.drop is not None:
                    lost_segments.append(message_id)
                    if left == 0 and len(count.samples) < self.max_samples:
                        self._sample(
                            count, file_groups, entry.subject, entry.poster,
                            verdict.drop)
                    continue

                left += 1

            if left:
                kept.add(entry.collection_id)

            else:
                # Nothing is left of it
                lost_files.append(entry.id)

        # What we found
        lost_set = set(lost_files)
        count.dropped += len(lost_segments) + sum(
            1 for f in files if f.id in lost_set and not each_segment)
        count.files += len(lost_files)

        # Collections with nothing left are removed
        emptied = sorted(wanted - kept)
        count.collections += len(emptied)

        if not self.dry_run and (lost_files or lost_segments):
            # Let go of the articles and files dropped (and their ranks)
            gone = list(lost_segments)
            for file_id in lost_files:
                gone.extend(m for m, _ in segments.get(file_id, ()))

            for ids in chunked(lost_segments):
                session.query(Segment)\
                    .filter(Segment.message_id.in_(ids))\
                    .delete(synchronize_session=False)

            for ids in chunked(lost_files):
                session.query(Segment)\
                    .filter(Segment.file_id.in_(ids))\
                    .delete(synchronize_session=False)
                session.query(FileGroup)\
                    .filter(FileGroup.file_id.in_(ids))\
                    .delete(synchronize_session=False)
                session.query(CollectionFile)\
                    .filter(CollectionFile.id.in_(ids))\
                    .delete(synchronize_session=False)

            NNTPArticleRanks.forget(session, gone)

            # Bring the totals of what is left up to date
            touched = {f.id for f in files if f.collection_id in wanted} \
                - lost_set
            indexer._recount(session, touched, wanted)

            # Remove the collections nothing is left of (children first;
            # the database servers enforce their links)
            for ids in chunked(emptied):
                session.query(CollectionMeta)\
                    .filter(CollectionMeta.collection_id.in_(ids))\
                    .delete(synchronize_session=False)
                session.query(CollectionAlias)\
                    .filter(CollectionAlias.collection_id.in_(ids))\
                    .delete(synchronize_session=False)
                session.query(Collection)\
                    .filter(Collection.id.in_(ids))\
                    .delete(synchronize_session=False)

        if self.rescore:
            # Each collection takes on its articles' scores and tags (the
            # ones what is dropped leaves behind)
            gone = set(lost_segments)
            for file_id in lost_files:
                gone.update(m for m, _ in segments.get(file_id, ()))

            for collection_id in sorted(kept):
                files_of = [f.id for f in files
                            if f.collection_id == collection_id and
                            f.id not in lost_set]
                found = [ranks.get(m, (0, frozenset()))
                         for file_id in files_of
                         for m, _ in segments.get(file_id, ())
                         if m not in gone]
                score = max((s for s, _ in found), default=0)
                tags = frozenset().union(*(t for _, t in found))

                collection = stored[collection_id]
                if score != collection.score or \
                        tags_to_text(tags) != collection.tags:
                    count.rescored += 1
                    if not self.dry_run:
                        indexer.score_collections(
                            session, {collection_id: (score, tags)},
                            replace=True)

        if not self.dry_run:
            session.commit()

            # Our totals were changed directly in the database
            session.expunge_all()

    def __str__(self):
        """
        A short summary of what was found
        """
        verb = 'would be' if self.dry_run else 'were'
        return 'headers: %d dropped, %d rescored; index: %d file(s) and ' \
            '%d collection(s) removed, %d rescored; archive: %d dropped, ' \
            '%d rescored (%s)' % (
                self.headers.dropped, self.headers.rescored,
                self.index.files, self.index.collections,
                self.index.rescored, self.archive.dropped,
                self.archive.rescored, verb)

    def __repr__(self):
        return '<NNTPPurger rescore=%s drop=%s dry_run=%s />' % (
            self.rescore, self.drop, self.dry_run)
