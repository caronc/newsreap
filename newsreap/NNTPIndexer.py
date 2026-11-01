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
  never show up).  Once formed it gets a lasting id (its GID), worked
  out the way newznab does it (see gid_order()) so an NZB file of the
  same post gets the same one.
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
from datetime import UTC
from hashlib import md5
from os.path import basename
from os.path import isfile
from os.path import splitext

from sqlalchemy import DateTime
from sqlalchemy import bindparam
from sqlalchemy import func
from sqlalchemy import insert
from sqlalchemy import literal_column
from sqlalchemy import and_
from sqlalchemy import select
from sqlalchemy import union
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import text

from .NNTPArticle import NNTPArticle
from .NNTPEmptyContent import NNTPEmptyContent
from .NNTPGroupDatabase import NNTPGroupDatabase
from .NNTPIndexDatabase import NNTPIndexDatabase
from .NNTPIndexDatabase import SEARCH_TABLE
from .NNTPSegmentedPost import NNTPSegmentedPost
from .NNTPnzb import NNTPnzb
from .NNTPnzb import NZBSegmentNumber
from .NNTPnzb import NZB_META_REPEATS
from .Utils import bytes_to_strsize
from .codecs.CodecYenc import CodecYenc
from .objects.group.Article import Article
from .objects.index.Collection import Collection
from .objects.index.Collection import CollectionSource
from .objects.index.Collection import CollectionState
from .objects.index.Collection import COLLECTION_FORMED
from .objects.index.ArchiveFile import ArchiveFile
from .objects.index.CollectionAlias import CollectionAlias
from .objects.index.CollectionFile import CollectionFile
from .objects.index.CollectionMeta import CollectionMeta
from .objects.index.FileGroup import FileGroup
from .objects.index.GroupClock import GroupClock
from .objects.index.Segment import Segment

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

# The segment counter newznab takes out of a subject to name a file
NEWZNAB_COUNTER_RE = re.compile(r'\((\d+)/(\d+)\)', re.ASCII)

# A search keyword: %s (the default) looks in names and subjects, %p in
# posters; a leading - leaves out what has it (a leading + is dropped)
KEYWORD_RE = re.compile(
    r'^(?P<column>%[sp])?(?P<op>[+-])?(?P<text>.+)$', re.ASCII | re.DOTALL)

# The search index only finds keywords at least this long; shorter ones are
# looked for in every entry
SEARCH_MIN_LENGTH = 3

# How many rows go into each statement when adding them on PostgreSQL (it
# allows 65535 values per statement)
PG_INSERT_ROWS = 5000

# The ways a file is put in an article whose file name tells us the real
# name of the file it belongs to (see learn_filenames())
FILENAME_CODECS = ('yenc', 'uuencode')

# MySQL's FULLTEXT (ngram) index finds pieces this long; shorter keywords
# are looked for in every entry (its ngram_token_size, which is 2 unless
# changed)
MYSQL_NGRAM_SIZE = 2

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


def gid_order(subject):
    """
    Returns what a file is sorted by when working out a GID

    This matches the order newznab writes the files of an NZB in (and so
    the GID it reads back from it):
    - the subject without its last (a/b) counter (every copy of that
      counter is removed, as newznab does), with spaces trimmed
    - case doesn't matter
    """
    counters = NEWZNAB_COUNTER_RE.findall(subject)
    if counters:
        subject = subject.replace('(%s/%s)' % counters[-1], '')

    return subject.strip().casefold()


def parse_keywords(keywords):
    """
    Returns (exclude, column, text) for each search keyword given (see
    KEYWORD_RE); the column is 'subject' or 'poster'
    """
    parsed = []
    for keyword in keywords:
        result = KEYWORD_RE.match(keyword)
        if not result:
            # Nothing to look for
            continue

        parsed.append((
            result.group('op') == '-',
            'poster' if result.group('column') == '%p' else 'subject',
            result.group('text')))

    return parsed


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

        - db_file: the index database; an SQLite file or a database URL
          (see NNTPSettings.index_database())
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
            if not create and not NNTPIndexDatabase.exists(self.db_file):
                # Nothing was ever indexed
                return None

            self._db = NNTPIndexDatabase.from_engine(self.db_file)

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

                # Real file names the archive found in these articles
                self._archived_filenames(
                    session, [row.message_id for row in rows])
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

            # Remember how far we have seen the group (see settle())
            seen = cache.clock()
            clock = session.get(GroupClock, group)
            if seen is not None and (clock is None or seen > clock.seen):
                session.merge(GroupClock(name=group, seen=seen))
                session.commit()

        finally:
            cache.close()

        return True

    def _index_chunk(self, session, group, rows, collections, files):
        """
        Indexes one chunk of articles found in `group`
        """
        # The articles we already have: a scanned file found in another
        # group is tagged with this one too; one an NZB file lists is left
        # just as the NZB file has it
        owned = {}
        for ids in chunked([row.message_id for row in rows]):
            owned.update(
                (message_id, (file_id, source)) for message_id, file_id, source
                in session.query(
                    Segment.message_id, Segment.file_id, Collection.source)
                .join(CollectionFile, CollectionFile.id == Segment.file_id)
                .join(Collection, Collection.id == CollectionFile.collection_id)
                .filter(Segment.message_id.in_(ids)))

        tagged = {file_id for file_id, source in owned.values()
                  if source == CollectionSource.Scan}
        if tagged:
            self._insert_ignore(
                session, FileGroup,
                [{'file_id': file_id, 'name': group} for file_id in tagged])

        # Only the rest needs working out
        rows = [row for row in rows if row.message_id not in owned]

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
            # Nothing new is left (or it all belonged to collections that
            # were removed)
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
        self._insert_ignore(
            session, Segment,
            [{'message_id': row.message_id,
              'file_id': files[info['file_key']],
              'part': info['part'],
              'size': row.size or 0} for row, _, info, _ in kept])

        # Tag every file with the group it was found in
        file_ids = {files[info['file_key']] for _, _, info, _ in kept}
        self._insert_ignore(
            session, FileGroup,
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

    @staticmethod
    def _insert_ignore(session, table, rows):
        """
        Adds the rows given to a table, skipping the ones it already has
        (the same primary or unique key)
        """
        if not rows:
            return

        dialect = session.get_bind().dialect.name
        if dialect == 'postgresql':
            # Sent as a few large statements; one row at a time is very
            # slow (and each statement can only hold so many values)
            for chunk in chunked(rows, PG_INSERT_ROWS):
                session.execute(
                    pg_insert(table).values(chunk).on_conflict_do_nothing())
            return

        if dialect == 'mysql':
            statement = insert(table).prefix_with('IGNORE')

        else:
            statement = insert(table).prefix_with('OR IGNORE')

        session.execute(statement, rows)

    @staticmethod
    def _days(dialect, value):
        """
        Returns SQL that turns a date and time into a number of days (with
        fractions), so two of them can be compared
        """
        if dialect == 'postgresql':
            return '(EXTRACT(EPOCH FROM %s) / 86400.0)' % value

        if dialect == 'mysql':
            # Our connections use UTC (see Database.open())
            return '(UNIX_TIMESTAMP(%s) / 86400.0)' % value

        return 'julianday(%s)' % value

    @staticmethod
    def learn_filenames(session, names):
        """
        Gives the files of the articles given their real names (ie: from
        their yEnc header, which obfuscated subjects don't have); not
        committed

        - names: Message-ID -> the name of the file it holds

        Returns how many files were renamed.
        """
        renamed = 0
        for ids in chunked(names):
            for message_id, file_id in session.query(
                    Segment.message_id, Segment.file_id)\
                    .filter(Segment.message_id.in_(ids)):

                name = (names[message_id] or '').strip()[:256]
                if not name:
                    continue

                renamed += session.query(CollectionFile)\
                    .filter(CollectionFile.id == file_id)\
                    .filter(CollectionFile.filename != name)\
                    .update({CollectionFile.filename: name},
                            synchronize_session=False)

        return renamed

    def _archived_filenames(self, session, message_ids):
        """
        Uses the real file names the archive (see NNTPArchiver) already
        found in the articles given (not committed)
        """
        names = {}
        for ids in chunked(
                message_id.strip().strip('<>') for message_id in message_ids):
            for message_id, filename in session.query(
                    ArchiveFile.message_id, ArchiveFile.filename)\
                    .filter(ArchiveFile.message_id.in_(ids))\
                    .filter(ArchiveFile.codec.in_(FILENAME_CODECS))\
                    .filter(ArchiveFile.filename != '')\
                    .order_by(ArchiveFile.id):
                # The first file of an article is the one it holds
                names.setdefault(message_id, filename)

        if names:
            self.learn_filenames(session, names)

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
        Works out which scanned collections have formed: the complete ones,
        and the ones missing parts that nothing new arrived for in a while
        (they are settled).  Each newly formed collection gets its GID.

        "A while" is measured by how far we have seen the groups it was
        found in (see GroupClock), not by the time now: a collection only
        settles once every one of its groups has been seen at least
        `settle_hours` past its last article.  Collections are never marked
        incomplete just because we haven't scanned in a while.

        - now: count every group as seen up to this time (UTC) when its
          clock is behind it; use it to settle what is left of groups you
          no longer scan

        Call it once every group is indexed.  Returns how many collections
        formed (also kept in `formed`).
        """
        self.formed = 0

        session = self.session()
        if session is None:
            # Nothing was ever indexed
            return 0

        dialect = session.get_bind().dialect.name
        days = lambda value: self._days(dialect, value)  # noqa: E731

        # The least seen of its groups decides
        seen = \
            '(SELECT %s FROM group_clock ' \
            ' WHERE group_clock.name IN (' \
            '  SELECT file_group.name FROM file_group ' \
            '  JOIN collection_file ' \
            '  ON collection_file.id = file_group.file_id ' \
            '  WHERE collection_file.collection_id = collection.id))' \
            % days('MIN(group_clock.seen)')

        values = {
            'complete': CollectionState.Complete,
            'settled': CollectionState.Settled,
            'forming': CollectionState.Forming,
            'scan': CollectionSource.Scan,
            'days': self.settle_after.total_seconds() / 86400.0,
        }

        params = []
        if now is not None:
            if now.tzinfo is not None:
                now = now.astimezone(UTC).replace(tzinfo=None)

            # A group is seen up to now at least (even with no clock)
            seen = '%s(COALESCE(%s, %s), %s)' % (
                'MAX' if dialect == 'sqlite' else 'GREATEST',
                seen, days(':now'), days(':now'))
            values['now'] = now
            params.append(bindparam('now', type_=DateTime()))

        # Update the scanned collections still forming (and the settled
        # ones; late parts can still complete them, but once settled they
        # stay that way)
        session.execute(text(
            'UPDATE collection SET state = CASE '
            'WHEN segments >= segments_expected AND '
            '  (files_expected = 0 OR files >= files_expected) '
            '  THEN :complete '
            'WHEN state = :settled OR last_posted IS NULL THEN :settled '
            'WHEN ' + days('last_posted') + ' < ' + seen + ' - :days '
            '  THEN :settled '
            'ELSE :forming END '
            'WHERE source = :scan AND state IN (:forming, :settled)')
            .bindparams(*params), values)

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
    def _files(session, collection_id):
        """
        Returns a collection's files in the order an NZB file lists them;
        the first one is what its GID comes from

        - Files read from an NZB file keep the order it had them in.
        - Scanned files are sorted the way newznab sorts them (see
          gid_order()).
        """
        files = session.query(CollectionFile)\
            .filter(CollectionFile.collection_id == collection_id)\
            .order_by(CollectionFile.id).all()

        return sorted(files, key=lambda entry: (
            entry.position is None, entry.position or 0,
            gid_order(entry.subject)))

    @classmethod
    def _gid(cls, session, collection_id):
        """
        Works out a collection's GID from the first segment (lowest part)
        of its first file; None if it has no segments
        """
        files = cls._files(session, collection_id)
        if not files:
            return None

        segment = session.query(Segment.message_id)\
            .filter(Segment.file_id == files[0].id)\
            .order_by(Segment.part, Segment.message_id).first()

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
            # Another NZB file's GID finds the collection we kept for it
            alias = session.get(CollectionAlias, value.lower())
            return query.filter(
                Collection.id == alias.collection_id if alias
                else Collection.gid == value.lower()).first()

        try:
            return query.filter(Collection.id == int(value)).first()

        except ValueError:
            # Neither an id nor a GID
            return None

    def find(self, keywords=(), groups=None, complete=None, forming=False,
             limit=None, case_sensitive=False):
        """
        Returns collections (newest first) with every keyword in their name
        or the subject or name of one of their files (see
        parse_keywords()):
        - a keyword starting with '-' must not be in any of them
        - a keyword starting with '%p' is looked for in the posters instead

        - groups: only collections with a file found in one of these groups
        - complete: True for only complete ones, False for only incomplete
          ones, None for both
        - forming: include collections still forming
        - case_sensitive: only match keywords with the same case (otherwise
          case, and accents, don't matter)
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
        for no, (exclude, column, value) in enumerate(
                parse_keywords(keywords)):
            found = Collection.id.in_(self._search(
                session, no, column, value, case_sensitive))
            query = query.filter(~found if exclude else found)

        if groups:
            # Found in (at least) one of the groups
            query = query.filter(Collection.id.in_(
                session.query(CollectionFile.collection_id)
                .join(FileGroup, FileGroup.file_id == CollectionFile.id)
                .filter(FileGroup.name.in_(list(groups)))))

        query = query.order_by(
            Collection.first_posted.desc(), Collection.id.desc())

        return query.limit(limit).all() if limit else query.all()

    @staticmethod
    def summary(entry):
        """
        Returns the line that describes a collection in a list: its id, how
        much of it was found, its size, files and name
        """
        percent = 100.0 * entry.segments / entry.segments_expected \
            if entry.segments_expected else 100.0

        files = '%d/%d' % (entry.files, entry.files_expected) \
            if entry.files_expected else '%d' % entry.files

        return '[%6d] %5.1f%% %10s %7s file(s)  %s' % (
            entry.id, min(percent, 100.0), bytes_to_strsize(entry.size),
            files, entry.name)

    @staticmethod
    def _search(session, no, column, value, case_sensitive=False):
        """
        Returns a query of the collections with `value` in their name or
        a file subject (column 'subject'), or in a poster (column 'poster')

        - no: makes its parameters unique (one per keyword)
        - case_sensitive: only the same case counts (otherwise case and
          accents don't matter)

        See NNTPIndexDatabase.search_index() for how each database finds
        them quickly.
        """
        dialect = session.get_bind().dialect.name
        if dialect != 'sqlite':
            # Look in the collection names (or posters) and file subjects
            # (or posters) themselves
            columns = (
                (Collection.id, getattr(
                    Collection, 'name' if column == 'subject' else column)),
                (CollectionFile.collection_id, getattr(
                    CollectionFile, column)),
            ) + ((
                # The (real) names of the files too
                (CollectionFile.collection_id, CollectionFile.filename),
            ) if column == 'subject' else ())

            # Searched once as a table of its own; MySQL would otherwise
            # search again for every collection
            found = union(*(
                select(collection_id.label('id')).where(NNTPIndexer._contains(
                    dialect, searched, value, case_sensitive))
                for collection_id, searched in columns)).subquery()

            return select(found.c.id)

        params = {}
        if len(value) >= SEARCH_MIN_LENGTH:
            # The search index finds it (whatever its case)
            where = '%s MATCH :match%d' % (column, no)
            params['match%d' % no] = '"%s"' % value.replace('"', '""')

        else:
            # Too short for the search index; look through every entry
            where = 'instr(lower(%s), :lower%d) > 0' % (column, no)
            params['lower%d' % no] = value.lower()

        if case_sensitive:
            # Only the ones with the same case
            where += ' AND instr(%s, :exact%d) > 0' % (column, no)
            params['exact%d' % no] = value

        return select(literal_column('collection_id'))\
            .select_from(text(SEARCH_TABLE))\
            .where(text(where).bindparams(**params))

    @staticmethod
    def _contains(dialect, column, value, case_sensitive=False):
        """
        Returns the condition (on a database server) for `value` being
        anywhere in `column` (see _search())
        """
        # Keep % and _ as they are
        pattern = '%%%s%%' % value.replace('/', '//')\
            .replace('%', '/%').replace('_', '/_')

        if dialect == 'postgresql':
            # Found with the (accent ignoring) search index first
            found = func.newsreap_unaccent(column).ilike(
                func.newsreap_unaccent(pattern), escape='/')

            return and_(found, column.like(pattern, escape='/')) \
                if case_sensitive else found

        if dialect == 'mysql':
            # The usual (_ci) collations ignore case and accents
            found = column.like(pattern, escape='/')
            if len(value) >= MYSQL_NGRAM_SIZE and '"' not in value:
                # Narrowed down with the search index first
                found = and_(column.match('"%s"' % value), found)

            return and_(found, column.collate('utf8mb4_bin').like(
                pattern, escape='/')) if case_sensitive else found

        found = column.ilike(pattern, escape='/')
        return and_(found, column.like(pattern, escape='/')) \
            if case_sensitive else found

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

        # Every detail we have about it (just as an NZB file gave them)
        nzb.meta = {}
        for key, entry in session.query(
                CollectionMeta.type, CollectionMeta.value)\
                .filter(CollectionMeta.collection_id == collection.id)\
                .order_by(CollectionMeta.id):
            # A type given more than once keeps every value
            nzb.meta.setdefault(key, []).append(entry)

        nzb.meta = {
            key: values[0] if len(values) == 1 else values
            for key, values in nzb.meta.items()}

        if not nzb.title():
            nzb.meta['title'] = collection.name

        # The files go in the order our GID came from, so the GID read back
        # from the NZB file (ie: by newznab) is the same as ours
        for sort_no, entry in enumerate(self._files(session, collection.id)):
            groups = sorted(
                name for name, in session.query(FileGroup.name)
                .filter(FileGroup.file_id == entry.id))

            post = NNTPSegmentedPost(
                None, subject=entry.subject, poster=entry.poster,
                groups=groups, utc=entry.first_posted, work_dir=work_dir,
                sort_no=sort_no)
            post.filename = entry.filename

            # One article per part; a part posted twice uses the lowest
            # Message-ID (so every database picks the same one)
            seen = set()
            for part, message_id, size in session.query(
                    Segment.part, Segment.message_id, Segment.size)\
                    .filter(Segment.file_id == entry.id)\
                    .order_by(Segment.part, Segment.message_id):

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

    @classmethod
    def _remove(cls, session, collection_id):
        """
        Removes a collection's files and marks it as deleted (not committed)
        """
        values = {'id': collection_id}
        cls._clear(session, collection_id)
        session.execute(text(
            'DELETE FROM collection_meta WHERE collection_id = :id'), values)

        # Its posting time is kept so what belongs to it is still recognised
        session.execute(text(
            'UPDATE collection SET state = :deleted, files = 0, '
            'segments = 0, size = 0 WHERE id = :id'),
            dict(values, deleted=CollectionState.Deleted))

    @staticmethod
    def _clear(session, collection_id):
        """
        Removes a collection's files (and their articles and groups), not
        committed
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

    def import_nzb(self, path, name=None, merge=False):
        """
        Adds the post an NZB file describes as a collection

        An NZB file is the real post, not our best guess, so:
        - its articles are taken from any scanned collection that had them
          (what those have left is counted again and re-formed by settle())
        - a scanned collection it covers completely becomes it (and keeps
          its id)
        - scanning never changes it afterwards
        - every <meta> detail it has is kept (see CollectionMeta)

        An article only belongs to one NZB file's collection, and the first
        one imported keeps it.  Another NZB file with its articles:
        - has the same articles (or fewer): only the details it has that we
          don't are added (see _merge_meta())
        - has every one of them and more (ie: extra par2 files, or bloat):
          its details are added too, but its extra files are left out
          (listed in `left_out`)
        - has only some of them: it is refused
        With merge set, it replaces those collections instead (see
        _replace()).
        Its GID then finds our collection (see CollectionAlias).

        - name: what to call it (defaults to the name the NZB file gives,
          or else its file name)
        - merge: replace the collections that share its articles with it
          (see _replace())

        Returns (collection, added):
        - collection: the collection (None if the NZB file couldn't be read,
          the post was removed before, or it only shares some articles with
          a collection we have)
        - added: True if files were added (False if we already had them)

        Also sets:
        - details: how many details were added
        - left_out: the subjects of the files not added (without merge)
        - merging: True if it replaced collections we had (with merge)
        - merged / dropped: how many files that collection gained / lost
        - replaced: the ids of the other collections it replaced (removed)
        """
        self.details = 0
        self.merging = False
        self.merged = 0
        self.dropped = 0
        self.replaced = []
        self.left_out = []

        nzb = NNTPnzb(path)
        if not nzb.is_valid():
            logger.warning("'%s' is not a valid NZB file." % path)
            return None, False

        # Every file with its articles (lowest part first), in the order
        # the NZB file lists them
        posts = [(post, list(post)) for post in nzb]
        posts = [(post, articles) for post, articles in posts if articles]
        if not posts:
            logger.warning("'%s' lists no articles." % path)
            return None, False

        # Its GID is the one newznab reads from it: the first segment of the
        # first file, as the NZB file lists them
        gid = nzb.gid() or make_gid(posts[0][1][0].id)

        session = self.session(create=True)
        message_ids = [
            article.id.strip().strip('<>')
            for _, articles in posts for article in articles]

        # Another NZB file of a collection we have uses its GID
        alias = session.get(CollectionAlias, gid)
        existing = session.get(Collection, alias.collection_id) if alias \
            else session.query(Collection)\
            .filter(Collection.gid == gid).first()

        if existing is not None and existing.state == CollectionState.Deleted:
            # It was removed (nr delete); it stays that way
            logger.warning(
                "'%s' is collection #%d, which was removed." % (
                    path, existing.id))
            return None, False

        if alias is not None and not merge:
            # It was merged into (or replaced by) a collection before
            return existing, False

        # The collections (from other NZB files) that have its articles
        owners = self._owners(session, message_ids)
        if existing is not None and existing.source == CollectionSource.Nzb:
            owners.setdefault(existing.id, set())

        if owners and merge:
            # It replaces them
            return self._replace(
                session, owners, existing, nzb, posts, gid, message_ids)

        if len(owners) > 1:
            logger.warning(
                "'%s' shares articles with collections %s; it was not "
                "imported (use --merge to replace them with it)." % (
                    path, ', '.join('#%d' % i for i in sorted(owners))))
            return None, False

        if owners:
            (collection_id, owned), = owners.items()
            return self._overlap(
                session, session.get(Collection, collection_id), owned,
                nzb, posts, gid, path)

        # Take its articles from the scanned collections that have them
        emptied = self._claim(session, message_ids)

        # A scanned collection it covers completely becomes it
        if existing is not None and existing.id in emptied:
            collection = existing

        elif emptied:
            collection = session.get(Collection, emptied[0])

        else:
            collection = None

        if existing is not None and existing is not collection:
            # It keeps what the NZB file doesn't list, and gets a GID of its
            # own once it forms again
            existing.gid = None
            existing.state = CollectionState.Forming
            session.flush()

        for collection_id in emptied:
            if collection is None or collection_id != collection.id:
                # Nothing is left of it; it was all a part of this post
                session.query(Collection)\
                    .filter(Collection.id == collection_id).delete()

        if collection is None:
            collection = Collection()
            session.add(collection)

        collection.key = 'nzb\n%s' % gid
        collection.gid = gid
        collection.source = CollectionSource.Nzb
        collection.poster = (posts[0][0].poster or '')[:128]
        collection.name = (
            name or nzb.title() or splitext(basename(path))[0])[:256]
        collection.files_expected = len(posts)
        session.flush()

        # What the NZB file says about it
        session.query(CollectionMeta)\
            .filter(CollectionMeta.collection_id == collection.id).delete()
        session.add_all(
            CollectionMeta(collection_id=collection.id, type=key, value=value)
            for key, value in self._meta(nzb))

        self._add_files(session, collection, posts)
        self._formed(session, collection)

        return collection, True

    @staticmethod
    def _meta(nzb):
        """
        Returns the (type, value) of every detail an NZB file gives (in its
        order, without repeats)
        """
        meta = []
        for key, values in (nzb.meta or {}).items():
            for value in (values if isinstance(values, list) else [values]):
                entry = (str(key)[:64], str(value)[:1024])
                if entry not in meta:
                    meta.append(entry)

        return meta

    @staticmethod
    def _owners(session, message_ids):
        """
        Returns the collections (from NZB files) that have any of the
        articles given: collection id -> the message ids it has
        """
        owners = {}
        for ids in chunked(dict.fromkeys(message_ids)):
            for message_id, collection_id in session.query(
                    Segment.message_id, CollectionFile.collection_id)\
                    .join(CollectionFile, CollectionFile.id == Segment.file_id)\
                    .join(Collection,
                          Collection.id == CollectionFile.collection_id)\
                    .filter(Collection.source == CollectionSource.Nzb)\
                    .filter(Segment.message_id.in_(ids)):
                owners.setdefault(collection_id, set()).add(message_id)

        return owners

    def _overlap(self, session, collection, owned, nzb, posts, gid, path):
        """
        Handles an NZB file sharing articles with a collection we have from
        another one, when it isn't merged (see import_nzb())

        - owned: the message ids of the NZB file the collection has
        """
        wanted = {
            article.id.strip().strip('<>')
            for _, articles in posts for article in articles}

        # Every article the collection has
        total = session.query(Segment.message_id)\
            .join(CollectionFile, CollectionFile.id == Segment.file_id)\
            .filter(CollectionFile.collection_id == collection.id).count()

        if len(owned) < len(wanted) and len(owned) < total:
            # Neither one has everything the other has
            logger.warning(
                "'%s' only shares some of its articles with collection #%d "
                "(%s); it was not imported (use --merge to replace it with "
                "it)." % (path, collection.id, collection.name))
            return None, False

        if gid != collection.gid and session.get(CollectionAlias, gid) is None:
            # Its GID finds our collection from now on
            session.add(CollectionAlias(gid=gid, collection_id=collection.id))

        self._merge_meta(session, collection.id, self._meta(nzb))

        # Fixes and bloat look the same; the files we don't have are only
        # added when merging
        self.left_out = [
            post.subject or '' for post, articles in posts
            if any(article.id.strip().strip('<>') not in owned
                   for article in articles)]

        session.commit()
        return collection, False

    def _replace(self, session, owners, existing, nzb, posts, gid,
                 message_ids):
        """
        Replaces the collections an NZB file shares articles with by it
        (see import_nzb() with merge set; not undone if it fails part way)

        - the one sharing the most (or the one with its GID) keeps its id
          and becomes the NZB file's collection
        - the others are removed (their GIDs find it from now on)
        - every detail they had is kept, unless the NZB file gives another
          value for it (passwords and tags are added to, never replaced)
        - their articles the NZB file doesn't list are let go of; nothing
          points to the old NZB files any more

        - owners: collection id -> its articles the NZB file lists
        """
        if existing is not None and existing.id in owners:
            # The one with its GID
            target = existing.id

        else:
            target = sorted(
                owners, key=lambda i: (-len(owners[i]), i))[0]

        others = [i for i in sorted(owners) if i != target]
        self.merging = True

        # The files it had (to report what changed)
        before = {key for key, in session.query(CollectionFile.key)
                  .filter(CollectionFile.collection_id == target)}

        # Every detail: ours, then what the others had, then the NZB file's
        for other in others:
            self._merge_meta(session, target, [
                (key, value) for key, value in session.query(
                    CollectionMeta.type, CollectionMeta.value)
                .filter(CollectionMeta.collection_id == other)
                .order_by(CollectionMeta.id)])

        self._merge_meta(session, target, self._meta(nzb), replace=True)

        # What they had is let go of; the NZB file lists what it is now
        self._clear(session, target)
        for other in others:
            self._remove(session, other)

        old_gids = {
            gid_ for gid_, in session.query(Collection.gid)
            .filter(Collection.id.in_([target, *others]))
            .filter(Collection.gid.isnot(None))}

        if others:
            # Anything that found the others finds ours now
            session.query(CollectionAlias)\
                .filter(CollectionAlias.collection_id.in_(others))\
                .update({CollectionAlias.collection_id: target},
                        synchronize_session=False)

            session.query(Collection)\
                .filter(Collection.id.in_(others))\
                .delete(synchronize_session=False)

        # Our old GIDs (and the others') find it from now on; its own GID
        # is its own
        session.query(CollectionAlias)\
            .filter(CollectionAlias.gid == gid)\
            .delete(synchronize_session=False)

        known = {alias for alias, in session.query(CollectionAlias.gid)
                 .filter(CollectionAlias.gid.in_(list(old_gids)))}
        session.add_all(
            CollectionAlias(gid=old, collection_id=target)
            for old in sorted(old_gids - known - {gid}))
        session.flush()

        # Take its articles from the scanned collections that have them
        for collection_id in self._claim(session, message_ids):
            # Nothing is left of it; it was all a part of this post
            session.query(Collection)\
                .filter(Collection.id == collection_id).delete()

        collection = session.get(Collection, target)
        collection.key = 'nzb\n%s' % gid
        collection.gid = gid
        collection.source = CollectionSource.Nzb
        collection.poster = (posts[0][0].poster or '')[:128]
        if nzb.title():
            collection.name = nzb.title()[:256]
        collection.files_expected = len(posts)
        session.flush()

        self._add_files(session, collection, posts)

        after = {key for key, in session.query(CollectionFile.key)
                 .filter(CollectionFile.collection_id == target)}
        self.merged = len(after - before)
        self.dropped = len(before - after)
        self.replaced = others

        self._formed(session, collection)
        return collection, True

    def _merge_meta(self, session, collection_id, meta, replace=False):
        """
        Adds the details given (type, value) that a collection doesn't have
        (not committed)

        - a type it doesn't have (ie: a password) is added
        - a type expected to repeat (see NZB_META_REPEATS) gets the new
          values added to the ones it has
        - any other type keeps its value, unless replace is set
        """
        have = {}
        for key, value in session.query(
                CollectionMeta.type, CollectionMeta.value)\
                .filter(CollectionMeta.collection_id == collection_id)\
                .order_by(CollectionMeta.id):
            have.setdefault(key, []).append(value)

        # The types we have replaced so far
        replaced = set()

        for key, value in meta:
            if value in have.get(key, ()):
                # We have it already
                continue

            if key in have and key not in NZB_META_REPEATS \
                    and key not in replaced:
                if not replace:
                    logger.warning(
                        "Collection #%d keeps its %s '%s' (not '%s')." % (
                            collection_id, key, have[key][0], value))
                    continue

                session.query(CollectionMeta)\
                    .filter(CollectionMeta.collection_id == collection_id)\
                    .filter(CollectionMeta.type == key).delete()
                have[key] = []
                replaced.add(key)

            session.add(CollectionMeta(
                collection_id=collection_id, type=key, value=value))
            have.setdefault(key, []).append(value)
            self.details += 1

        session.flush()

    def _add_files(self, session, collection, posts, position=0):
        """
        Adds the files (and their articles) from an NZB file to a collection
        (not committed)

        - position: where the first one goes in the collection

        Returns how many new files were added (the others already belonged
        to it, and only got more articles)
        """
        # Files whose subjects only differ by their counter are one file
        files = {}
        dates = {}
        segments = []
        groups = []
        added = 0
        for position, (post, articles) in enumerate(posts, start=position):
            subject = post.subject or ''
            info = parse_subject(subject)
            key = '%d\n%s' % (collection.id, info['base'])
            expected = max(info['parts'], len(articles))
            if key not in files:
                entry = session.query(CollectionFile)\
                    .filter(CollectionFile.key == key).first()

                if entry is None:
                    entry = CollectionFile(
                        key=key, collection_id=collection.id,
                        position=position, subject=subject[:256],
                        poster=(post.poster or '')[:128],
                        filename=(info['filename'] or release_name(
                            info['desc']) or subject)[:256],
                        file_no=info['file_no'], segments_expected=expected)
                    session.add(entry)
                    session.flush()
                    added += 1

                files[key] = entry

            entry = files[key]
            entry.segments_expected = max(entry.segments_expected, expected)
            dates[entry.id] = (post.utc, post.utc)
            groups.extend(
                {'file_id': entry.id, 'name': str(group)}
                for group in post.groups)

            segments.extend(
                {'message_id': article.id.strip().strip('<>'),
                 'file_id': entry.id, 'part': article.no,
                 'size': sum(len(content) for content in article)}
                for article in articles)

        session.flush()

        # An article another NZB file listed stays with that one
        self._insert_ignore(session, Segment, segments)
        self._insert_ignore(session, FileGroup, groups)

        self._recount(
            session, [entry.id for entry in files.values()], [collection.id],
            dates)

        return added

    @staticmethod
    def _formed(session, collection):
        """
        Saves a collection from an NZB file; it is as complete as the post
        is going to get
        """
        session.commit()
        session.refresh(collection)
        collection.state = CollectionState.Complete if collection.complete \
            else CollectionState.Settled
        session.commit()

    def _claim(self, session, message_ids):
        """
        Takes the articles given away from the scanned collections that
        have them (not committed)

        What those collections have left is counted again; their GID is
        worked out again once they form (see settle()).  Returns the ids of
        the collections left with nothing, the one that lost the most
        articles first.
        """
        # Who has them (articles an NZB file listed stay where they are)
        files = {}
        lost = {}
        taken = []
        for ids in chunked(dict.fromkeys(message_ids)):
            for message_id, file_id, collection_id in session.query(
                    Segment.message_id, Segment.file_id,
                    CollectionFile.collection_id)\
                    .join(CollectionFile, CollectionFile.id == Segment.file_id)\
                    .join(Collection,
                          Collection.id == CollectionFile.collection_id)\
                    .filter(Collection.source == CollectionSource.Scan)\
                    .filter(Segment.message_id.in_(ids)):

                files[file_id] = collection_id
                lost[collection_id] = lost.get(collection_id, 0) + 1
                taken.append(message_id)

        if not taken:
            # Nobody had them
            return []

        for ids in chunked(taken):
            session.query(Segment).filter(Segment.message_id.in_(ids))\
                .delete(synchronize_session=False)

        # Count what is left, and remove the files left with nothing
        self._recount(session, files, lost)
        for ids in chunked(files):
            empty = [file_id for file_id, in session.query(CollectionFile.id)
                     .filter(CollectionFile.id.in_(ids))
                     .filter(CollectionFile.segments == 0)]

            if empty:
                session.query(FileGroup)\
                    .filter(FileGroup.file_id.in_(empty))\
                    .delete(synchronize_session=False)
                session.query(CollectionFile)\
                    .filter(CollectionFile.id.in_(empty))\
                    .delete(synchronize_session=False)

        self._recount(session, [], lost)

        # They have to form again (with a GID of their own)
        session.query(Collection)\
            .filter(Collection.id.in_(list(lost)))\
            .update({Collection.gid: None,
                     Collection.state: CollectionState.Forming},
                    synchronize_session=False)

        # Anything read before now is out of date
        session.expire_all()

        emptied = [collection_id for collection_id, in session.query(
            Collection.id).filter(Collection.id.in_(list(lost)))
            .filter(Collection.files == 0)]

        return sorted(emptied, key=lambda c: (-lost[c], c))

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
