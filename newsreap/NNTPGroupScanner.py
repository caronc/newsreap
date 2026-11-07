# Caches the article headers of a Usenet group for offline searching
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
Fetches the article headers (XOVER) of a group and caches them in the
group's own database so they can be searched offline.

- The article numbers already fetched are remembered (see ScannedRange),
  so each scan only fetches what is new or was missed before.  Stopping
  part way (Ctrl-C, a lost connection) loses nothing already saved.
- Headers are fetched in batches across all of the manager's
  connections, but only a few batches are held in memory at a time.
"""


from collections import deque
from datetime import timedelta
from itertools import islice
from os import makedirs
from os import replace
from os import unlink
from os.path import basename
from os.path import dirname
from os.path import isfile
from os.path import join
from shutil import copyfile

from sqlalchemy import func
from sqlalchemy.exc import OperationalError

from .NNTPArticleRanks import NNTPArticleRanks
from .NNTPFilterRule import NNTPFilterRule
from .NNTPGroupDatabase import NNTPGroupDatabase
from .NNTPIndexDatabase import NNTPIndexDatabase
from .codecs.CodecArticleIndex import XoverGrouping
from .objects.group.Article import Article
from .objects.group.ScannedRange import ScannedRange
from .Utils import utcnow

# Logging
import logging
from newsreap.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)

# The default number of article headers fetched in one request
DEFAULT_BATCH_SIZE = 25000


# A group fetched for the first time without saying how far back to go is
# only fetched whole if it has no more articles than this
DEFAULT_FIRST_FETCH = 100000


class NNTPGroupScanner:
    """
    Caches the article headers of one group; see the module description
    """

    def __init__(self, manager, group, db_file, server,
                 batch_size=DEFAULT_BATCH_SIZE, window=None, ramdisk=None,
                 filters=None, index_db=None,
                 first_fetch=DEFAULT_FIRST_FETCH):
        """
        Prepares a scanner

        - manager: the NNTPManager() (or anything with the same group(),
          seek_by_date() and xover() calls) used to talk to the server
        - group: the group name (ie: alt.binaries.test)
        - db_file: the database file the group's articles are kept in
        - server: the host the article numbers belong to
        - batch_size: how many headers are fetched per request
        - window: how many requests may be outstanding at once (defaults to
          two per connection the manager may open)
        - ramdisk: a directory in memory to work in (optional)
        - filters: the NNTPFilter that scores, tags and drops articles
          before they are saved (optional)
        - index_db: the index database the scores and tags filters give
          are kept in (see NNTPArticleRanks); needed for them to be kept
        - first_fetch: the most articles a group can have to be fetched
          whole the first time without saying how far back to go (see
          plan()); 0 for any number
        """
        # What we're scanning and where it's kept
        self.manager = manager
        self.group = group
        self.db_file = db_file
        self.server = server

        # How we fetch it
        self.batch_size = max(1, int(batch_size))

        if not window:
            # Keep every connection busy, with a little to spare
            window = manager.window()

        self.window = window
        self.ramdisk = ramdisk
        self.filters = filters or None
        self.index_db = index_db
        self.first_fetch = max(0, int(first_fetch or 0))

        # Set when the group was too big to fetch whole the first time (how
        # many articles it has)
        self.too_big = None

        # The index database (opened when first needed) and the ranks of
        # the batch being saved
        self._index = None
        self._ranks = {}

        # The group's first and last article number (from the server), and
        # when (UTC) we asked for them
        self.low = None
        self.high = None
        self.checked = None

        # What our last scan did:
        #  - articles: headers received and saved
        #  - dropped: headers received that our filters dropped
        #  - batches: requests that completed
        #  - failed: requests that didn't (they are fetched next time)
        #  - pending: article numbers we wanted but didn't get to
        #  - wanted: article numbers that needed fetching
        self.wanted = 0
        self.articles = 0
        self.dropped = 0
        self.batches = 0
        self.failed = 0
        self.pending = 0

    def watermarks(self):
        """
        Asks the server for the group's first and last article number

        Returns (low, high), or None if the group couldn't be selected.
        """
        # Anything posted after this may not be counted in the answer
        checked = utcnow()

        result = self.manager.group(self.group)
        try:
            _, low, high, _ = result

        except (TypeError, ValueError):
            # Not the answer we expected
            return None

        if low is None or high is None:
            # The group could not be selected
            return None

        self.low, self.high = low, high
        self.checked = checked
        return low, high

    def plan(self, date_from=None, date_to=None, newest=None, force=False,
             session=None):
        """
        Works out which article numbers still need to be fetched

        - date_from / date_to: only articles posted between these dates
        - newest: only the newest X articles
        - force: fetch again even what was fetched before
        - session: the group database (to skip what was already fetched)

        With none of the dates or counts given, it carries on from where
        we started fetching before: whatever is missing since then (ie: a
        run stopped part way) and everything newer.  The first time, the
        whole group is only fetched if it isn't too big (see first_fetch);
        otherwise nothing is planned and `too_big` says how many articles
        it has.

        Returns a list of (low, high) ranges (empty when there is nothing
        to do), or None if the server couldn't tell us what it has.
        """
        if self.watermarks() is None:
            return None

        low, high = self.low, self.high

        self.too_big = None
        if session is not None and not (newest or date_from or date_to):
            first = self.first_fetched(session)
            if first is not None:
                low = max(low, first)

            elif self.first_fetch and high - low + 1 > self.first_fetch:
                # Fetching all of it could take days; we need to be told
                # how far back to go
                self.too_big = high - low + 1
                return []

        if newest:
            # Only the newest articles
            low = max(low, high - int(newest) + 1)

        if date_from:
            # Start at the first article posted at (or after) our date
            found = self.manager.seek_by_date(date_from, group=self.group)
            if found is None:
                return None
            low = max(low, found)

        if date_to:
            # Stop just before the first article posted after our date
            found = self.manager.seek_by_date(
                date_to + timedelta(seconds=1), group=self.group)
            if found is None:
                return None
            high = min(high, found - 1)

        if low > high:
            # Nothing falls within what was asked for
            return []

        if force or session is None:
            # Fetch everything asked for
            return [(low, high)]

        return self.missing(self.scanned(session, low, high), low, high)

    def first_fetched(self, session):
        """
        Returns the first article number ever fetched from our server (None
        if nothing was)
        """
        return session.query(func.min(ScannedRange.low))\
            .filter(ScannedRange.server == self.server).scalar()

    def scanned(self, session, low, high):
        """
        Returns the ranges already fetched that touch low to high (sorted)
        """
        return [
            (r.low, r.high) for r in session.query(ScannedRange)
            .filter(ScannedRange.server == self.server)
            .filter(ScannedRange.high >= low)
            .filter(ScannedRange.low <= high)
            .order_by(ScannedRange.low)]

    @staticmethod
    def missing(scanned, low, high):
        """
        Returns the parts of low to high not covered by `scanned`

        - scanned: (low, high) ranges sorted by their low value
        """
        gaps = []
        cur = low
        for s_low, s_high in scanned:
            if s_low > cur:
                # A gap before this range
                gaps.append((cur, min(s_low - 1, high)))

            # Continue after this range
            cur = max(cur, s_high + 1)
            if cur > high:
                break

        if cur <= high:
            # Whatever is left after the last range
            gaps.append((cur, high))

        return gaps

    def split(self, ranges):
        """
        Splits ranges into batch sized pieces (a generator)
        """
        for low, high in ranges:
            while low <= high:
                end = min(low + self.batch_size - 1, high)
                yield low, end
                low = end + 1

    def mark(self, session, low, high):
        """
        Remembers that low to high has been fetched

        Ranges that touch or overlap are merged into one.
        """
        # Find everything we touch (or sit right beside)
        touching = session.query(ScannedRange)\
            .filter(ScannedRange.server == self.server)\
            .filter(ScannedRange.high >= low - 1)\
            .filter(ScannedRange.low <= high + 1).all()

        for entry in touching:
            # Grow our range to cover it, then drop it
            low = min(low, entry.low)
            high = max(high, entry.high)
            session.delete(entry)

        session.add(ScannedRange(server=self.server, low=low, high=high))

    def scan(self, date_from=None, date_to=None, newest=None, force=False,
             limit=None):
        """
        Fetches and caches the group's article headers

        - date_from / date_to / newest / force: see plan()
        - limit: only process this many batches

        Returns True if everything asked for was fetched.
        """
        # Reset what our last scan did
        self.articles = self.batches = self.failed = self.pending = 0
        self.wanted = self.dropped = 0

        with self._database() as db:
            session = db.session()

            ranges = self.plan(
                date_from=date_from, date_to=date_to, newest=newest,
                force=force, session=session)

            if ranges is None:
                logger.warning(
                    "Could not access group '%s' on '%s'." % (
                        self.group, self.server))
                return False

            if not ranges:
                if self.too_big:
                    # We were not told how far back to go
                    return False

                # Nothing to do
                self._caught_up(db, session)
                return True

            # What we still need to fetch
            wanted = self.wanted = sum(high - low + 1 for low, high in ranges)
            logger.info('Fetching %d article number(s) from %s.' % (
                wanted, self.group))

            # Fill an empty database without (most of) its indexes; they
            # are much faster to build once at the end
            fresh = session.query(Article.message_id).first() is None
            dropped = self._drop_indexes(db) \
                if fresh and wanted > self.batch_size else []

            try:
                done = self._fetch(
                    db, session, islice(self.split(ranges), limit),
                    force=force)

            finally:
                # Put back anything we dropped
                self._create_indexes(db, dropped)

            self.pending = wanted - done
            if self.failed or self.pending:
                return False

            self._caught_up(db, session)
            return True

    def _caught_up(self, db, session):
        """
        Remembers when we last had everything up to the server's newest
        article (see NNTPGroupDatabase.clock())
        """
        if self.high is None or self.checked is None:
            # We never asked the server
            return

        if self.high < self.low or self.scanned(session, self.high, self.high):
            # The newest article (if there is one) is cached
            db.caught_up(self.server, self.checked)

    def _fetch(self, db, session, batches, force=False):
        """
        Fetches batches (keeping at most `window` of them outstanding),
        saving each one as it arrives

        Returns how many article numbers were covered.
        """
        # Store new articles; existing ones are only replaced when forced
        insert = Article.__table__.insert().prefix_with(
            'OR REPLACE' if force else 'OR IGNORE')

        # Requests that are waiting on (or holding) results
        outstanding = deque()

        def submit():
            # Top our requests back up to our window size
            for low, high in islice(batches, self.window - len(outstanding)):
                outstanding.append((low, high, self.manager.xover(
                    group=self.group, start=low, end=high,
                    sort=XoverGrouping.BY_ARTICLE_NO, block=False)))

        # Track the article numbers we've covered
        done = 0

        try:
            submit()
            while outstanding:
                # Handle our requests in the order we made them
                low, high, request = outstanding.popleft()
                request.wait()

                # Keep our connections busy while we save this batch
                submit()

                result = request.response[0] if len(request) else None
                if result is None:
                    # This batch is fetched again on our next scan
                    logger.warning(
                        'Could not fetch articles %d-%d from %s.' % (
                            low, high, self.group))
                    self.failed += 1
                    continue

                rows = self._rows(result.values())

                if rows:
                    # Save our articles
                    session.execute(insert, rows)

                # Remember this range together with its articles, so a scan
                # that stops part way picks up where it left off
                self.mark(session, low, high)
                session.commit()
                self._save_ranks()

                self.articles += len(rows)
                self.batches += 1
                done += high - low + 1

                logger.debug('Cached %d article(s) from %d-%d (%s).' % (
                    len(rows), low, high, self.group))

        finally:
            # Anything not handled yet (ie: Ctrl-C) is no longer wanted
            for _, _, request in outstanding:
                request.abort()

            # Let go of the index database (see _save_ranks())
            self._ranks = {}
            if self._index is not None:
                self._index.close()
                self._index = None

        return done

    def _rows(self, articles):
        """
        Returns the database rows of the articles fetched, leaving out the
        ones our filters drop (and scoring and tagging the rest)
        """
        rows = []
        for article in articles:
            # The groups it was posted to (its Xref)
            groups = sorted(article.get('xgroups') or ())
            row = {
                'message_id': article['id'],
                'article_no': article['article_no'],
                'subject': article['subject'],
                'poster': article['poster'],
                'size': article['size'],
                'lines': article['lines'],
                'posted_date': article['date'],
                'xref': ' '.join(groups),
            }

            if self.filters is not None:
                # Judged in every group it was posted to, so it is judged
                # the same way whichever group it is found in
                where = [self.group, *groups]
                verdict = self.filters.check(
                    where, subject=row['subject'],
                    poster=row['poster'], message_id=row['message_id'],
                    posted=row['posted_date'], size=row['size'],
                    crossposts=NNTPFilterRule.count_groups(where))

                if verdict.drop is not None:
                    # Never stored
                    self.dropped += 1
                    continue

                self._ranks[row['message_id']] = (verdict.score, verdict.tags)

            rows.append(row)

        return rows

    def _save_ranks(self):
        """
        Keeps the scores and tags of the batch just saved (see
        NNTPArticleRanks); worked out from 0 every time, so a scan of the
        same articles never adds to them
        """
        ranks, self._ranks = self._ranks, {}
        if not ranks or not self.index_db:
            return

        if self._index is None:
            self._index = NNTPIndexDatabase.from_engine(self.index_db)

        session = self._index.session()
        NNTPArticleRanks.save(session, ranks)
        session.commit()

    def _database(self):
        """
        Returns our database (as a context manager); see _GroupDatabase
        """
        return _GroupDatabase(self.db_file, ramdisk=self.ramdisk)

    @staticmethod
    def _drop_indexes(db):
        """
        Drops the (non unique) article indexes and returns them
        """
        dropped = []
        for index in Article.__table__.indexes:
            if index.unique:
                # Keep what stops duplicates from being stored
                continue

            try:
                index.drop(bind=db._engine)
                dropped.append(index)

            except OperationalError:
                # The index was already gone
                pass

        return dropped

    @staticmethod
    def _create_indexes(db, indexes):
        """
        Creates the indexes given
        """
        for index in indexes:
            try:
                index.create(bind=db._engine)

            except OperationalError:
                # The index already exists
                pass

    def __str__(self):
        """
        A short summary of our last scan
        """
        if self.low is None:
            return '%s: could not be accessed' % self.group

        if self.too_big:
            return "%s: %s articles; say how far back to start the first " \
                "time (ie: --newest 200000 or --days 7)" % (
                    self.group, '{:,}'.format(self.too_big))

        if not self.wanted:
            return '%s: already up to date' % self.group

        text = '%s: %d article(s) cached' % (self.group, self.articles)
        if self.dropped:
            text += ', %d dropped by filters' % self.dropped
        if self.failed:
            text += ', %d batch(es) failed' % self.failed

        if self.pending:
            text += ', %d article number(s) left to fetch' % self.pending

        return text

    def __repr__(self):
        """
        Return an unambiguous version of the object
        """
        return '<NNTPGroupScanner group="%s" server="%s" />' % (
            self.group, self.server)


class _GroupDatabase:
    """
    Opens a group's database for a scan (a context manager)

    - The database (and its directory) is created if it doesn't exist.
    - With a ramdisk, the work happens on a copy in memory that is moved
      back once done (even when the scan stops early).
    """

    def __init__(self, db_file, ramdisk=None):
        self.db_file = db_file
        self.ramdisk = ramdisk
        self.work_file = db_file
        self.db = None

    def __enter__(self):
        makedirs(dirname(self.db_file), exist_ok=True)

        if self.ramdisk:
            # Work on a copy in memory
            self.work_file = join(self.ramdisk, basename(self.db_file))
            if isfile(self.db_file):
                # Opening (and closing) it first saves anything a stopped
                # run left in its journal into the file we copy
                NNTPGroupDatabase.from_file(self.db_file).close()
                copyfile(self.db_file, self.work_file)

            elif isfile(self.work_file):
                # Something left behind from an earlier run
                unlink(self.work_file)

        self.db = NNTPGroupDatabase.from_file(self.work_file)
        return self.db

    def __exit__(self, *args):
        # Let go of the database
        self.db.close()

        if self.work_file != self.db_file:
            # Copy our ramdisk version back beside the original first, then
            # swap it in (one step, so the original is never half written)
            staged = '%s.new' % self.db_file
            copyfile(self.work_file, staged)
            replace(staged, self.db_file)
            unlink(self.work_file)
