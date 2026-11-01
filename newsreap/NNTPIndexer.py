# Groups cached articles (from every group) into files and collections
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
Turns the article headers `nr update search` cached into something you can
download.  Usenet is treated as one big pool; the groups an article was
found in are just tags on the file it belongs to:

- Articles are grouped into files: the segments (parts) a poster posted a
  file in share one subject apart from their segment counter, ie:
      My.Release [01/10] - "my.release.part01.rar" yEnc (12/50)
- Files are grouped into collections: the files a poster posted together
  (ie: every .rar, .par2 and .nfo of one release), whatever groups they
  were posted to.
- Everything lives in one index database (not the group caches), and a
  collection keeps everything its NZB file needs.  The cached headers can
  be removed once indexed (see NNTPGroupDatabase.purge()).
- A collection is forming while its articles arrive.  It is formed once
  complete, or settled once nothing new arrived for a while (some parts
  never show up).  Once formed it gets a lasting id (its GID).
- Something posted long after (or before) a collection with the same name
  and poster is a repost and becomes a collection of its own.

Only articles that haven't been indexed before are looked at, so running
it again after `nr update search` only handles what is new.
"""

import gevent.monkey
gevent.monkey.patch_all()

import re
from datetime import datetime
from datetime import timedelta
from hashlib import md5
from os.path import basename
from os.path import isfile
from os.path import splitext

from sqlalchemy import DateTime
from sqlalchemy import bindparam
from sqlalchemy import insert
from sqlalchemy import literal_column
from sqlalchemy import text

from .NNTPArticle import NNTPArticle
from .NNTPEmptyContent import NNTPEmptyContent
from .NNTPGroupDatabase import NNTPGroupDatabase
from .NNTPIndexDatabase import NNTPIndexDatabase
from .NNTPSegmentedPost import NNTPSegmentedPost
from .NNTPnzb import NNTPnzb
from .NNTPnzb import NZBSegmentNumber
from .codecs.CodecYenc import CodecYenc
from .objects.group.Article import Article
from .objects.index.Collection import Collection
from .objects.index.Collection import CollectionState
from .objects.index.Collection import COLLECTION_FORMED
from .objects.index.CollectionFile import CollectionFile
from .objects.index.FileGroup import FileGroup
from .objects.index.Segment import Segment
from .Utils import utcnow

# Logging
import logging
from newsreap.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)

# How many articles are indexed (and saved) at a time
DEFAULT_CHUNK_SIZE = 10000

# A collection missing parts is settled once nothing new arrived for this
# many hours
DEFAULT_SETTLE_HOURS = 4

# Articles posted this many days away from a collection's posting time
# start a new collection (a repost)
DEFAULT_REPOST_DAYS = 3

# A GID is an md5 written out in hex
GID_RE = re.compile(r'^[0-9a-f]{32}$', re.IGNORECASE | re.ASCII)

# A counter such as (3/50) or [03/10]
COUNTER_RE = re.compile(
    r'[(\[]\s*(?P<no>\d+)\s*/\s*(?P<total>\d+)\s*[)\]]', re.ASCII)

# A quoted file name ("my.file.rar")
QUOTED_RE = re.compile(r'"(?P<name>[^"]+)"', re.ASCII)

# Something that looks like a file name (a word with an extension)
FILENAME_RE = re.compile(
    r'(?P<name>[^\s"\[\]()/\\]+\.[a-z0-9]{1,5})(?![a-z0-9])',
    re.IGNORECASE | re.ASCII)

# The endings that set the files of one release apart from each other
# (ie: .part01.rar, .r00, .vol00+01.par2, .nfo); removing them leaves the
# release's name
RELEASE_SUFFIX_RE = re.compile(
    r'(\.part\d+|\.vol\d+[+-]\d+)?'
    r'\.(rar|r\d{2,3}|\d{3}|zip|z\d{2}|7z|par2|par|sfv|nfo|nzb|txt|jpg)$',
    re.IGNORECASE | re.ASCII)

# Where the yEnc marker (and whatever follows it, ie: a size) starts
YENC_MARKER_RE = re.compile(r'\byEnc\b.*$', re.IGNORECASE | re.ASCII)

# Brackets and quotes left empty once a counter or file name is taken out
EMPTY_RE = re.compile(r'\[\s*\]|\(\s*\)|"\s*"', re.ASCII)

# Spaces and separators found around descriptions
TRIM_RE = re.compile(r'^[\s"\'\-]+|[\s"\'\-]+$', re.ASCII)

# Reads the usual yEnc subject layout; made once since every article
# subject goes through it
YENC_SUBJECT = CodecYenc()

# The most variables SQLite takes in one statement is far higher than
# this, but keeping our IN (...) lists small keeps them fast
IN_LIST_SIZE = 500


def parse_subject(subject):
    """
    Works out what an article subject says about the file it carries

    Returns a dictionary with:
      - filename: the file's name ('' if the subject doesn't say)
      - desc: the description posted with it ('' if none)
      - part / parts: which segment this is and how many there are
      - file_no / files: which file of the collection it is and how many
        there are (0 if the subject doesn't say)
      - base: the subject with its segment counter taken out; every
        segment of one file shares it
    """
    # Every segment of a file shares its subject, apart from the counter
    counters = list(COUNTER_RE.finditer(subject))
    if counters:
        last = counters[-1]
        base = subject[:last.start()] + '(*/%s)' % last.group('total') + \
            subject[last.end():]

    else:
        # No counter at all; a file posted in a single article
        base = subject

    # The usual yEnc layout gives us the most
    parsed = YENC_SUBJECT.parse_article(subject)
    if parsed and parsed.get('fname'):
        return {
            'filename': parsed['fname'],
            'desc': TRIM_RE.sub('', parsed.get('desc') or ''),
            'part': parsed.get('yindex') or 1,
            'parts': parsed.get('ycount') or 1,
            'file_no': parsed.get('index') or 0,
            'files': parsed.get('count') or 0,
            'base': base,
        }

    # Otherwise we work with what we can find
    result = {
        'filename': '', 'desc': '', 'part': 1, 'parts': 1,
        'file_no': 0, 'files': 0, 'base': base,
    }

    rest = subject
    if counters:
        # The last counter is the segment counter
        result['part'] = int(counters[-1].group('no'))
        result['parts'] = max(int(counters[-1].group('total')), 1)

        if len(counters) > 1:
            # The one before it says which file of the collection it is
            result['file_no'] = int(counters[-2].group('no'))
            result['files'] = int(counters[-2].group('total'))

        rest = COUNTER_RE.sub(' ', rest)

    # A quoted name, or else anything that looks like a file name
    match = QUOTED_RE.search(rest)
    if not match or not FILENAME_RE.search(match.group('name')):
        match = FILENAME_RE.search(rest)

    if match:
        result['filename'] = match.group('name').strip()
        rest = rest[:match.start()] + rest[match.end():]

    # What is left over describes it (anything from the yEnc marker on,
    # such as a size, changes from file to file so it is left out)
    rest = EMPTY_RE.sub(' ', YENC_MARKER_RE.sub('', rest))
    result['desc'] = TRIM_RE.sub('', re.sub(r'\s+', ' ', rest))
    return result


def release_name(filename):
    """
    Returns a file name without the endings that set the files of one
    release apart (ie: my.release.part01.rar -> my.release)
    """
    name = filename
    for _ in range(2):
        # Twice for endings like .vol00+01.par2
        stripped = RELEASE_SUFFIX_RE.sub('', name)
        if stripped == name:
            break
        name = stripped

    return name or filename




def make_gid(message_id):
    """
    Returns the GID of a post whose first segment is `message_id`; the md5
    of its Message-ID (without its < and >) written out in hex
    """
    return md5(message_id.strip().strip('<>').encode('utf-8')).hexdigest()


def chunked(items, size=IN_LIST_SIZE):
    """
    Splits items into lists of at most `size` (a generator)
    """
    items = list(items)
    for start in range(0, len(items), size):
        yield items[start:start + size]


class NNTPIndexer:
    """
    Indexes cached articles (from any number of groups) into one index;
    see the module description
    """

    def __init__(self, db_file, chunk_size=DEFAULT_CHUNK_SIZE,
                 settle_hours=DEFAULT_SETTLE_HOURS,
                 repost_days=DEFAULT_REPOST_DAYS):
        """
        Prepares an indexer

        - db_file: the index database (see NNTPSettings.index_database())
        - chunk_size: how many articles are indexed (and saved) at a time
        - settle_hours: how long a collection missing parts waits for new
          articles before it is settled
        - repost_days: how far apart (in days) something is posted from a
          collection with the same name and poster before it counts as a
          repost; 0 never splits them
        """
        self.db_file = db_file
        self.chunk_size = max(1, int(chunk_size))
        self.settle_after = timedelta(hours=max(0.0, float(settle_hours)))

        # None never splits a collection
        self.repost_gap = timedelta(days=float(repost_days)) \
            if repost_days and float(repost_days) > 0 else None

        # What our last index() did:
        #  - articles: articles indexed
        #  - files / collections: new ones found
        self.articles = 0
        self.files = 0
        self.collections = 0

        # How many collections our last settle() formed
        self.formed = 0

        # Our database (opened when first needed)
        self._db = None

    def session(self, create=False):
        """
        Returns a session to the index database; None if it doesn't exist
        yet (unless `create` is set)
        """
        if self._db is None:
            if not create and not isfile(self.db_file):
                # Nothing was ever indexed
                return None

            self._db = NNTPIndexDatabase.from_file(self.db_file)

        return self._db.session()

    def close(self):
        """
        Lets go of our database
        """
        if self._db is not None:
            self._db.close()
            self._db = None

    def index(self, group, group_db_file):
        """
        Indexes every article cached for a group that wasn't indexed before

        - group: the group's name (every file found in it is tagged with it)
        - group_db_file: where its articles are cached (see
          NNTPSettings.group_database())

        Returns True if it ran (False if nothing was ever cached for it).
        Call settle() once every group is indexed.
        """
        # Reset what our last run did
        self.articles = self.files = self.collections = 0

        if not isfile(group_db_file):
            # Nothing was ever cached for this group
            return False

        session = self.session(create=True)
        cache = NNTPGroupDatabase.from_file(group_db_file)
        cached = cache.session()

        # What we've already looked up during this run:
        #  - collections: key -> the collections sharing it (see _window())
        #  - files: key -> id
        collections = {}
        files = {}

        try:
            # Work through the articles in order, a chunk at a time; each
            # chunk is saved before the next one is read, so stopping part
            # way loses nothing
            while True:
                rows = cached.query(
                    Article.message_id, Article.article_no,
                    Article.subject, Article.poster, Article.size,
                    Article.posted_date)\
                    .filter(Article.indexed.is_(False))\
                    .order_by(Article.article_no)\
                    .limit(self.chunk_size).all()

                if not rows:
                    # We're done
                    break

                self._index_chunk(session, group, rows, collections, files)
                session.commit()

                # Our totals were updated directly in the database; anything
                # read before now is out of date
                session.expire_all()

                # Mark them as indexed; they are the first articles not yet
                # indexed, so every one up to the last of them is ours
                cached.query(Article)\
                    .filter(Article.indexed.is_(False))\
                    .filter(Article.article_no <= rows[-1].article_no)\
                    .update({Article.indexed: True},
                            synchronize_session=False)
                cached.commit()

                self.articles += len(rows)
                logger.debug('Indexed %d article(s) of %s (up to article %d).'
                             % (len(rows), group, rows[-1].article_no))

        finally:
            cache.close()

        return True

    def _index_chunk(self, session, group, rows, collections, files):
        """
        Indexes one chunk of articles found in `group`
        """
        # Work out what each article is part of
        entries = []
        for row in rows:
            info = parse_subject(row.subject or '')
            poster = row.poster or ''

            # The files of one collection share a poster and a name
            name = info['desc'] or release_name(info['filename']) or \
                info['base']
            info['name'] = name[:256]
            info['collection_key'] = '%s\n%s\n%d' % (
                poster, name, info['files'])

            entries.append((row, poster, info))

        # Load the collections we haven't looked at yet during this run
        missing = {
            info['collection_key'] for _, _, info in entries
            if info['collection_key'] not in collections}

        for keys in chunked(missing):
            for key in keys:
                collections[key] = []

            for entry in session.query(
                    Collection.id, Collection.key, Collection.state,
                    Collection.first_posted, Collection.last_posted)\
                    .filter(Collection.key.in_(keys))\
                    .order_by(Collection.id):
                collections[entry.key].append({
                    'id': entry.id, 'state': entry.state,
                    'first': entry.first_posted, 'last': entry.last_posted})

        # Work out which collection each article belongs to
        added = []
        kept = []
        for row, poster, info in entries:
            windows = collections[info['collection_key']]
            window = self._window(windows, row.posted_date)
            if window is None:
                # The first of its kind (or a repost); a new collection
                window = {
                    'id': None, 'state': CollectionState.Forming,
                    'first': row.posted_date, 'last': row.posted_date,
                    'new': Collection(
                        key=info['collection_key'], poster=poster[:128],
                        name=info['name'], files_expected=info['files'])}
                windows.append(window)
                added.append(window)

            elif window['state'] == CollectionState.Deleted:
                # It was removed (nr delete); leave it out
                continue

            elif row.posted_date is not None:
                # Its posting time now covers this article too
                window['first'] = row.posted_date if window['first'] is None \
                    else min(window['first'], row.posted_date)
                window['last'] = row.posted_date if window['last'] is None \
                    else max(window['last'], row.posted_date)

            kept.append((row, poster, info, window))

        if added:
            # Save the new collections so we know their ids
            session.add_all(window['new'] for window in added)
            session.flush()
            for window in added:
                window['id'] = window.pop('new').id
            self.collections += len(added)

        if not kept:
            # Everything belonged to collections that were removed
            return

        # The segments of one file share a collection and their subject
        # (without its segment counter)
        for _, _, info, window in kept:
            info['file_key'] = '%d\n%s' % (window['id'], info['base'])

        # Look up (or add) the files we need
        self._resolve(
            session, CollectionFile, files,
            {info['file_key']: (poster, info, window)
             for _, poster, info, window in kept},
            lambda key, poster, info, window: CollectionFile(
                key=key,
                collection_id=window['id'],
                # The subject as its first segment would have it
                subject=info['base'].replace('(*/', '(1/', 1)[:256],
                poster=poster[:128],
                filename=(info['filename'] or info['name'])[:256],
                file_no=info['file_no'],
                segments_expected=info['parts']))

        # Tie every article to its file (an article found before, ie: in
        # another group, is only stored once)
        session.execute(
            insert(Segment).prefix_with('OR IGNORE'),
            [{'message_id': row.message_id,
              'file_id': files[info['file_key']],
              'part': info['part'],
              'size': row.size or 0} for row, _, info, _ in kept])

        # Tag every file with the group it was found in
        file_ids = {files[info['file_key']] for _, _, info, _ in kept}
        session.execute(
            insert(FileGroup).prefix_with('OR IGNORE'),
            [{'file_id': file_id, 'name': group} for file_id in file_ids])

        # When each file's articles (in this chunk) were posted
        dates = {}
        for row, _, info, _ in kept:
            if row.posted_date is None:
                continue

            file_id = files[info['file_key']]
            low, high = dates.get(file_id, (row.posted_date, row.posted_date))
            dates[file_id] = (
                min(low, row.posted_date), max(high, row.posted_date))

        self._recount(
            session, file_ids, {window['id'] for _, _, _, window in kept},
            dates)

    def _window(self, windows, posted):
        """
        Returns the collection (of the ones sharing a key) an article posted
        at `posted` belongs to; None if it is a new one (ie: a repost)

        Each entry of `windows` holds a collection's id, state and when its
        first and last articles were posted.
        """
        if not windows:
            # Nothing shares its key yet
            return None

        if posted is None or self.repost_gap is None:
            # We can't (or shouldn't) tell them apart; use the newest
            return windows[-1]

        for window in windows:
            if window['first'] is None or (
                    window['first'] - self.repost_gap <= posted <=
                    window['last'] + self.repost_gap):
                # Posted around the same time
                return window

        # Posted long before or after any of them
        return None

    def _recount(self, session, file_ids, collection_ids, dates=None):
        """
        Brings the totals of the files and collections given up to date

        - dates: file id -> (first, last) posting time of articles just
          added to it
        """
        for ids in chunked(file_ids):
            session.execute(text(
                'UPDATE collection_file SET '
                'segments = (SELECT COUNT(DISTINCT part) FROM segment '
                '  WHERE file_id = collection_file.id), '
                'size = (SELECT COALESCE(SUM(size), 0) FROM segment '
                '  WHERE file_id = collection_file.id) '
                'WHERE id IN (%s)' % ','.join(str(i) for i in ids)))

        if dates:
            # Widen each file's posting time to cover its new articles
            session.execute(text(
                'UPDATE collection_file SET '
                'first_posted = CASE WHEN first_posted IS NULL '
                '  OR first_posted > :low THEN :low ELSE first_posted END, '
                'last_posted = CASE WHEN last_posted IS NULL '
                '  OR last_posted < :high THEN :high ELSE last_posted END '
                'WHERE id = :id').bindparams(
                    bindparam('low', type_=DateTime()),
                    bindparam('high', type_=DateTime())),
                [{'id': file_id, 'low': low, 'high': high}
                 for file_id, (low, high) in dates.items()])

        for ids in chunked(collection_ids):
            session.execute(text(
                'UPDATE collection SET '
                'files = (SELECT COUNT(*) FROM collection_file '
                '  WHERE collection_id = collection.id), '
                'segments = (SELECT COALESCE(SUM(segments), 0) '
                '  FROM collection_file '
                '  WHERE collection_id = collection.id), '
                'segments_expected = (SELECT COALESCE('
                '  SUM(segments_expected), 0) FROM collection_file '
                '  WHERE collection_id = collection.id), '
                'size = (SELECT COALESCE(SUM(size), 0) FROM collection_file '
                '  WHERE collection_id = collection.id), '
                'first_posted = (SELECT MIN(first_posted) '
                '  FROM collection_file '
                '  WHERE collection_id = collection.id), '
                'last_posted = (SELECT MAX(last_posted) '
                '  FROM collection_file '
                '  WHERE collection_id = collection.id) '
                'WHERE id IN (%s)' % ','.join(str(i) for i in ids)))

    def _resolve(self, session, table, known, wanted, create):
        """
        Fills `known` (key -> id) with an entry for every key in `wanted`,
        adding the ones that don't exist yet

        - wanted: key -> the arguments `create` needs to make a new entry
        """
        # Look up the keys we haven't seen during this run
        missing = [key for key in wanted if key not in known]
        for keys in chunked(missing):
            known.update(
                session.query(table.key, table.id)
                .filter(table.key.in_(keys)))

        # Add the ones that don't exist yet
        added = [create(key, *wanted[key]) for key in missing
                 if key not in known]
        if not added:
            return

        session.add_all(added)
        session.flush()
        known.update((entry.key, entry.id) for entry in added)

        # Track what we found
        self.files += len(added)

    def settle(self, now=None):
        """
        Works out which collections have formed: the complete ones, and the
        ones missing parts that nothing new arrived for in a while (they are
        settled).  Each newly formed collection gets its GID.

        Call it once every group is indexed.  Returns how many collections
        formed (also kept in `formed`).
        """
        self.formed = 0

        session = self.session()
        if session is None:
            # Nothing was ever indexed
            return 0

        # Anything last posted before this has had its time
        cutoff = (now or utcnow()) - self.settle_after

        # Update the collections still forming (and the settled ones; late
        # parts can still complete them)
        session.execute(text(
            'UPDATE collection SET state = CASE '
            'WHEN segments >= segments_expected AND '
            '  (files_expected = 0 OR files >= files_expected) '
            '  THEN :complete '
            'WHEN last_posted IS NULL OR last_posted < :cutoff '
            '  THEN :settled '
            'ELSE :forming END '
            'WHERE state IN (:forming, :settled)')
            .bindparams(bindparam('cutoff', type_=DateTime())), {
                'complete': CollectionState.Complete,
                'settled': CollectionState.Settled,
                'forming': CollectionState.Forming,
                'cutoff': cutoff})

        # Give every newly formed collection its GID
        formed = session.query(Collection)\
            .filter(Collection.gid.is_(None))\
            .filter(Collection.state.in_(COLLECTION_FORMED))\
            .all()

        for collection in formed:
            gid = self._gid(session, collection.id)
            if gid is None:
                # It has no segments to work it out from
                continue

            other = session.query(Collection.id, Collection.state)\
                .filter(Collection.gid == gid).first()

            if other is None:
                collection.gid = gid
                self.formed += 1

            elif other.state == CollectionState.Deleted:
                # A copy of something removed; remove it too
                self._remove(session, collection.id)

            else:
                # We already have this post (ie: it was imported from an NZB
                # file); this is what was left over
                logger.debug(
                    'Collection #%d is a part of collection #%d.' % (
                        collection.id, other.id))

        session.commit()

        # The states were updated directly in the database; anything read
        # before now is out of date
        session.expire_all()
        return self.formed

    @staticmethod
    def _gid(session, collection_id):
        """
        Works out a collection's GID from the first segment of its first
        file (files sorted by subject); None if it has no segments
        """
        first = session.query(CollectionFile.id)\
            .filter(CollectionFile.collection_id == collection_id)\
            .order_by(CollectionFile.subject, CollectionFile.id).first()
        if first is None:
            return None

        segment = session.query(Segment.message_id)\
            .filter(Segment.file_id == first.id)\
            .order_by(Segment.part, literal_column('segment.rowid')).first()

        return None if segment is None else make_gid(segment.message_id)

    def get(self, value):
        """
        Returns a collection by its id or GID (None if there is no such
        collection, or it was removed)
        """
        session = self.session()
        if session is None:
            return None

        value = str(value).strip()
        query = session.query(Collection)\
            .filter(Collection.state != CollectionState.Deleted)

        if GID_RE.match(value):
            return query.filter(Collection.gid == value.lower()).first()

        try:
            return query.filter(Collection.id == int(value)).first()

        except ValueError:
            # Neither an id nor a GID
            return None

    def find(self, keywords=(), groups=None, complete=None, forming=False,
             limit=None):
        """
        Returns collections (newest first) whose names contain every
        keyword (case doesn't matter).  A keyword starting with '-' must not
        be in the name.

        - groups: only collections with a file found in one of these groups
        - complete: True for only complete ones, False for only incomplete
          ones, None for both
        - forming: include collections still forming
        """
        session = self.session()
        if session is None:
            return []

        # The states we're after
        states = {
            True: [CollectionState.Complete],
            False: [CollectionState.Settled],
            None: list(COLLECTION_FORMED),
        }[complete]

        if forming and complete is not True:
            states.append(CollectionState.Forming)

        query = session.query(Collection).filter(Collection.state.in_(states))
        for keyword in keywords:
            if keyword.startswith('-') and len(keyword) > 1:
                query = query.filter(
                    ~Collection.name.ilike('%%%s%%' % keyword[1:]))

            else:
                query = query.filter(
                    Collection.name.ilike('%%%s%%' % keyword.lstrip('+')))

        if groups:
            # Found in (at least) one of the groups
            query = query.filter(Collection.id.in_(
                session.query(CollectionFile.collection_id)
                .join(FileGroup, FileGroup.file_id == CollectionFile.id)
                .filter(FileGroup.name.in_(list(groups)))))

        query = query.order_by(
            Collection.first_posted.desc(), Collection.id.desc())

        return query.limit(limit).all() if limit else query.all()

    def groups(self, collection_id):
        """
        Returns the (sorted) names of the groups a collection's files were
        found in
        """
        session = self.session()
        if session is None:
            return []

        return sorted(name for name, in session.query(FileGroup.name)
                      .join(CollectionFile,
                            CollectionFile.id == FileGroup.file_id)
                      .filter(CollectionFile.collection_id == collection_id)
                      .distinct())

    def nzb(self, value, path, work_dir=None):
        """
        Saves a collection (by its id or GID) as an NZB file; each file
        lists the groups it was found in

        Returns True if the NZB file was written.
        """
        collection = self.get(value)
        if collection is None:
            logger.warning('There is no collection %s.' % value)
            return False

        session = self.session()

        nzb = NNTPnzb(work_dir=work_dir)
        nzb.meta = {'name': collection.name}
        if collection.category:
            nzb.meta['category'] = collection.category

        if collection.password:
            nzb.meta['password'] = collection.password

        files = session.query(CollectionFile)\
            .filter(CollectionFile.collection_id == collection.id)\
            .order_by(CollectionFile.file_no, CollectionFile.filename)

        for sort_no, entry in enumerate(files):
            groups = sorted(
                name for name, in session.query(FileGroup.name)
                .filter(FileGroup.file_id == entry.id))

            post = NNTPSegmentedPost(
                None, subject=entry.subject, poster=entry.poster,
                groups=groups, utc=entry.first_posted, work_dir=work_dir,
                sort_no=sort_no)
            post.filename = entry.filename

            # One article per part; a part posted twice uses the first
            seen = set()
            for part, message_id, size in session.query(
                    Segment.part, Segment.message_id, Segment.size)\
                    .filter(Segment.file_id == entry.id)\
                    .order_by(Segment.part, literal_column('segment.rowid')):

                if part in seen:
                    continue
                seen.add(part)

                article = NNTPArticle(
                    id=message_id, no=part, subject=entry.subject,
                    poster=entry.poster, groups=groups, work_dir=work_dir)

                # The article's content isn't downloaded; we only know its
                # size
                article.add(NNTPEmptyContent(
                    filepath=entry.filename, part=part, total_size=size,
                    work_dir=work_dir))
                post.add(article)

            nzb.add(post)

        # Number each segment by its part so a missing part leaves a gap
        return nzb.save(path, numbering=NZBSegmentNumber.Article)

    def delete(self, value):
        """
        Removes a collection (by its id or GID) and its files

        The collection itself is kept (marked as deleted) so articles that
        belong to it, and NZB files of it, aren't indexed again.  Returns
        True if the collection was removed.
        """
        collection = self.get(value)
        if collection is None:
            logger.warning('There is no collection %s.' % value)
            return False

        session = self.session()
        self._remove(session, collection.id)
        session.commit()

        # Forget everything read before now; some of it no longer exists
        session.expunge_all()
        return True

    @staticmethod
    def _remove(session, collection_id):
        """
        Removes a collection's files and marks it as deleted (not committed)
        """
        # The files of the collection
        files = 'SELECT id FROM collection_file WHERE collection_id = :id'
        values = {'id': collection_id}

        session.execute(text(
            'DELETE FROM file_group WHERE file_id IN (%s)' % files), values)
        session.execute(text(
            'DELETE FROM segment WHERE file_id IN (%s)' % files), values)
        session.execute(text(
            'DELETE FROM collection_file WHERE collection_id = :id'), values)

        # Its posting time is kept so what belongs to it is still recognised
        session.execute(text(
            'UPDATE collection SET state = :deleted, files = 0, '
            'segments = 0, size = 0 WHERE id = :id'),
            dict(values, deleted=CollectionState.Deleted))

    def import_nzb(self, path, name=None):
        """
        Adds the post an NZB file describes as a collection

        - name: what to call it (defaults to the name the NZB file gives,
          or else its file name)

        Returns (collection, added):
        - collection: the collection (None if the NZB file couldn't be read,
          or the post was removed before)
        - added: False if we already had it
        """
        nzb = NNTPnzb(path)
        if not nzb.is_valid():
            logger.warning("'%s' is not a valid NZB file." % path)
            return None, False

        # Every file with its articles (lowest part first)
        posts = [(post, list(post)) for post in nzb]
        posts = [(post, articles) for post, articles in posts if articles]
        if not posts:
            logger.warning("'%s' lists no articles." % path)
            return None, False

        # Its GID comes from the first segment of its first file (files
        # sorted by subject)
        first = min(posts, key=lambda entry: entry[0].subject or '')
        gid = make_gid(first[1][0].id)

        session = self.session(create=True)
        existing = session.query(Collection)\
            .filter(Collection.gid == gid).first()

        if existing is not None:
            if existing.state == CollectionState.Deleted:
                # It was removed (nr delete); it stays that way
                logger.warning(
                    "'%s' is collection #%d, which was removed." % (
                        path, existing.id))
                return None, False

            # We already have it
            return existing, False

        meta = nzb.meta or {}
        collection = Collection(
            key='nzb\n%s' % gid, gid=gid,
            poster=(first[0].poster or '')[:128],
            name=(name or meta.get('name') or
                  splitext(basename(path))[0])[:256],
            files_expected=len(posts),
            category=meta.get('category'),
            password=meta.get('password'))
        session.add(collection)
        session.flush()

        # Files whose subjects only differ by their counter are one file
        files = {}
        dates = {}
        segments = []
        groups = []
        for post, articles in posts:
            subject = post.subject or ''
            info = parse_subject(subject)
            key = '%d\n%s' % (collection.id, info['base'])
            if key not in files:
                files[key] = CollectionFile(
                    key=key, collection_id=collection.id,
                    subject=subject[:256], poster=(post.poster or '')[:128],
                    filename=(info['filename'] or release_name(
                        info['desc']) or subject)[:256],
                    file_no=info['file_no'],
                    segments_expected=max(info['parts'], len(articles)))
                session.add(files[key])
                session.flush()

            entry = files[key]
            dates[entry.id] = (post.utc, post.utc)
            groups.extend(
                {'file_id': entry.id, 'name': str(group)}
                for group in post.groups)

            segments.extend(
                {'message_id': article.id.strip().strip('<>'),
                 'file_id': entry.id, 'part': article.no,
                 'size': sum(len(content) for content in article)}
                for article in articles)

        # An article another collection already has stays with it
        session.execute(insert(Segment).prefix_with('OR IGNORE'), segments)
        if groups:
            session.execute(insert(FileGroup).prefix_with('OR IGNORE'), groups)

        self._recount(
            session, [entry.id for entry in files.values()], [collection.id],
            dates)
        session.commit()

        # An NZB file is as complete as the post is going to get
        session.refresh(collection)
        collection.state = CollectionState.Complete if collection.complete \
            else CollectionState.Settled
        session.commit()

        return collection, True

    def __str__(self):
        """
        A short summary of our last index()
        """
        if not self.articles:
            return 'nothing new to index'

        return '%d article(s) indexed; %d new file(s) in %d new ' \
            'collection(s)' % (self.articles, self.files, self.collections)

    def __repr__(self):
        """
        Return an unambiguous version of the object
        """
        return '<NNTPIndexer db_file="%s" />' % self.db_file
