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
Turns the article headers `nr update` cached into something you can
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
it again after `nr update` only handles what is new.
"""


import marshal
import re
from collections import deque
from collections import namedtuple
from datetime import timedelta
from datetime import UTC
from hashlib import md5
from heapq import merge
from itertools import chain
from itertools import islice
from shutil import copyfileobj
from shutil import rmtree
from tempfile import NamedTemporaryFile
from tempfile import SpooledTemporaryFile
from tempfile import mkdtemp
from time import sleep
from os import unlink
from os.path import basename
from os.path import isfile
from os.path import join
from os.path import splitext

from sqlalchemy import DateTime
from sqlalchemy import delete
from sqlalchemy import exists
from sqlalchemy import bindparam
from sqlalchemy import func
from sqlalchemy import insert
from sqlalchemy import literal
from sqlalchemy import literal_column
from sqlalchemy import and_
from sqlalchemy import or_
from sqlalchemy import select
from sqlalchemy import union
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import text
from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError

from .Database import Session
from .Database import contended
from .NNTPArticle import NNTPArticle
from .NNTPArticleRanks import NNTPArticleRanks
from .NNTPEmptyContent import NNTPEmptyContent
from .NNTPFilterRule import NNTPFilterRule
from .NNTPFilterRule import parse_time
from .NNTPFilterRule import score_tests
from .NNTPFilterRule import tag_logic
from .NNTPSearchTerm import NNTPSearchTerm
from .NNTPSearchTerm import SEARCH_ANY
from .NNTPFilterRule import tags_from_text
from .NNTPFilterRule import tags_to_text
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
from .objects.index.ArchiveGroup import ArchiveGroup
from .objects.index.ArchivedPost import ArchivedPost
from .objects.index.CollectionAlias import CollectionAlias
from .objects.index.CollectionFile import CollectionFile
from .objects.index.CollectionMeta import CollectionMeta
from .objects.index.FileGroup import FileGroup
from .objects.index.GroupClock import GroupClock
from .objects.index.IndexLock import IndexLock
from .objects.index.PGPPost import PGPPost
from .objects.index.PGPWait import PGPWait
from .objects.index.Segment import Segment

# Logging
import logging
from newsreap.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)

# How many articles are indexed (and saved) at a time
DEFAULT_CHUNK_SIZE = 10000

# How many times a chunk is tried when another worker was in the way (see
# Database.contended()); the wait grows a second each time
INDEX_TRIES = 5

# A collection missing parts is settled once nothing new arrived for this
# many hours
DEFAULT_SETTLE_TIME = 1800

# Articles posted this many days away from a collection's posting time
# start a new collection (a repost)
DEFAULT_REPOST_DAYS = 3

# Obfuscated posts (see signals()) are told apart by when they were posted:
# a pause this long (in minutes) starts a new one
DEFAULT_SESSION_MINUTES = 30

# What a collection put together from posting signals (not its subjects)
# has its key start with
SESSION_KEY = 'session'

# An article of an obfuscated post this much smaller than its biggest one
# is the last article of a file (see _estimate_files())
SHORT_ARTICLE_SHARE = 0.95

# Obfuscated articles that (mostly) all have a different poster have a made
# up one each: only its shape is used then.  This many have to be seen
# together before that is decided.
RANDOM_POSTER_SAMPLE = 20

# A GID is an md5 written out in hex
GID_RE = re.compile(r'^[0-9a-f]{32}$', re.IGNORECASE | re.ASCII)

# The segment counter newznab takes out of a subject to name a file
NEWZNAB_COUNTER_RE = re.compile(r'\((\d+)/(\d+)\)', re.ASCII)

# A search keyword: %s (the default) looks in names and subjects, %p in
# posters; a leading - leaves out what has it (a leading + is dropped)
KEYWORD_RE = re.compile(
    r'^(?P<column>%[sp])?(?P<op>[+-])?(?P<text>.+)$', re.ASCII | re.DOTALL)

# Where a keyword is split into choices ('alert|danger' finds either; \|
# is a | itself)
KEYWORD_OR_RE = re.compile(r'(?<!\\)\|')

# How each score comparison (see score_tests()) is written in SQL
SQL_COMPARISONS = {
    '<': lambda column, value: column < value,
    '<=': lambda column, value: column <= value,
    '>': lambda column, value: column > value,
    '>=': lambda column, value: column >= value,
    '=': lambda column, value: column == value,
}

# The fields collections can be searched by (see NNTPSearchTerm)
COLLECTION_FIELDS = (SEARCH_ANY, 'subject', 'filename', 'poster',
                     'message_id')

# Pieces of one search condition looked up in the search index (each gets
# its own numbered SQL parameters)
TERM_PARAMS = 100

# A * or ? wildcard (not escaped with a backslash)
WILDCARD_RE = re.compile(r'(?<!\\)[*?]')

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

# How many search results are read at a time when they are streamed
STREAM_SIZE = 500

# The most variables SQLite takes in one statement is far higher than
# this, but keeping our IN (...) lists small keeps them fast
IN_LIST_SIZE = 500

# How many rows note_fetched() writes before it commits
NOTE_COMMIT_SIZE = 5000

# About how many articles are held at once when a collection's files are
# saved to (or read from) an NZB file a page at a time
ARTICLE_PAGE_SIZE = 5000

# The most entries sorted in memory at once; more are sorted in runs of
# this many, kept in temporary files (see sorted_in_runs())
SORT_RUN_SIZE = 20000

# The most sorted runs read together; with more, they are first merged
# into bigger runs, so only this many files are ever open at once
SORT_MERGE_SIZE = 64

# How big a list of Message-IDs can get before it is kept on disk
SPOOL_SIZE = 8 * 1048576

# How many of the files an NZB file has that we left out are named
LEFT_OUT_SAMPLES = 10

# How many collection ids an NZB file import names (the ones it replaced, or
# the ones it shares articles with)
ID_SAMPLES = 10

# The longest Message-ID there can be (RFC 5536); a longer one can't be
# kept as it is, so its article is left out
MAX_MESSAGE_ID = 250

# The longest collection and file key kept (see fit_key())
MAX_KEY = 512

# What a download learned about an article (see Segment): whether it
# carries a file or is only text (see ArchiveKind; None when unknown)
SegmentNote = namedtuple('SegmentNote', ('kind', ))

class Purged(namedtuple(
        'Purged', ('headers', 'posts', 'articles', 'files', 'collections'))):
    """
    What removing articles everywhere took away (see NNTPIndexer.purge()):

    - headers: cached headers (in the groups' header caches)
    - posts: posts the archive kept
    - articles, files, collections: from the index (files and collections
      only once nothing was left of them)
    """

    def plus(self, other):
        """
        Returns both added together
        """
        return Purged(*(a + b for a, b in zip(self, other)))

    def __str__(self):
        """
        Says what went, ie: '3 cached header(s) and 1 kept post(s)'; empty
        when nothing did
        """
        said = ['%d %s' % (count, name) for count, name in (
            (self.headers, 'cached header(s)'),
            (self.posts, 'kept post(s)'),
            (self.articles, 'indexed article(s)'),
            (self.files, 'file(s) left empty'),
            (self.collections, 'collection(s) left empty')) if count]
        return ', '.join(said[:-1]) + ' and ' + said[-1] \
            if len(said) > 1 else ''.join(said)


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




# A word or number in a subject, Message-ID or poster
TOKEN_RE = re.compile(r'[A-Za-z0-9]+', re.ASCII)

# A run of digits
DIGITS_RE = re.compile(r'\d+', re.ASCII)

# A run of lower case letters
LOWER_RUN_RE = re.compile(r'[a-z]+', re.ASCII)

# A name made up of nothing but letters, digits and a few symbols (no
# spaces); random looking ones are what obfuscated posts use
RANDOM_NAME_RE = re.compile(r'^[A-Za-z0-9_+=-]{12,}$', re.ASCII)


def looks_random(text):
    """
    Returns True if a word looks made up (ie: the random names obfuscated
    posts use): long, without spaces, and either broken up by digits in
    more than one place or switching case far more often than words joined
    together do (in HolidayPhotos every capital starts a word)
    """
    if not RANDOM_NAME_RE.match(text or ''):
        return False

    letters = ''.join(c for c in text if c.isalpha())
    if len(letters) < 4:
        # Mostly digits (ie: a date or an article number)
        return False

    if len(DIGITS_RE.findall(text)) >= 2:
        return True

    # How long the runs of lower case letters are (on average)
    runs = LOWER_RUN_RE.findall(letters)
    if not runs or not any(c.isupper() for c in letters):
        # One case all the way through (a word, or shouting)
        return False

    return len(runs) >= 4 and sum(map(len, runs)) / len(runs) < 3


def token_shape(text):
    """
    Returns the shape of some text: its random looking words become 'x',
    its numbers '9' (within words too), and the rest is kept (lower case)
    """
    def shape(found):
        word = found.group(0)
        if word.isdigit():
            return '9'

        if len(word) >= 8 and (looks_random(word) or all(
                c in '0123456789abcdefABCDEF' for c in word)):
            return 'x'

        return DIGITS_RE.sub('9', word.lower())

    return TOKEN_RE.sub(shape, text or '')


def message_id_shape(message_id):
    """
    Returns the shape of a Message-ID (see token_shape()); every article a
    posting tool makes has the same one (ie: x@ngpost)
    """
    return token_shape((message_id or '').strip().strip('<>'))[:64]


def subject_template(subject):
    """
    Returns a subject with what changes from article to article (counters,
    numbers, quoted names and random looking words) taken out; the
    articles of one post share it
    """
    if made_up(subject):
        # A made up name for each one
        return 'x' + ('(#/#)' if COUNTER_RE.search(subject) else '')

    template = COUNTER_RE.sub('(#/#)', subject or '')
    template = QUOTED_RE.sub('"*"', template)
    template = TOKEN_RE.sub(
        lambda found: 'x' if looks_random(found.group(0)) else
        found.group(0), template)
    return DIGITS_RE.sub('9', template)[:128]


def xref_key(xref):
    """
    Returns a short key for the groups an article was posted to (its Xref
    as cached); '' if not known
    """
    groups = ' '.join(sorted((xref or '').split()))
    return md5(groups.encode('utf-8')).hexdigest()[:8] if groups else ''


def made_up(subject):
    """
    Returns True if a subject (its counters left out) is one long word: no
    spaces, no file name, mixed case or digits.  Real posts describe
    themselves; obfuscated ones use a made up name for every article (or
    file).
    """
    name = COUNTER_RE.sub('', subject or '').strip()
    return bool(RANDOM_NAME_RE.match(name)) and (
        any(c.isdigit() for c in name) or
        any(c.isupper() for c in name[1:]))


def posting_key(row):
    """
    Returns how a cached article was posted: the shape of its subject and
    Message-ID (the posting tool's) and the groups it went to
    """
    return '%s\n%s\n%s' % (
        subject_template(row.subject), message_id_shape(row.message_id),
        xref_key(row.xref))


def is_obfuscated(info):
    """
    Returns True if what parse_subject() found can't tell which post an
    article is part of: no file name, no file counter, and a made up (or
    no) name (see made_up())
    """
    if info['filename'] or info['files']:
        return False

    name = info['desc'] or info['base']
    return not COUNTER_RE.sub('', name).strip() or made_up(name)


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


def keyword_choices(keywords, case_sensitive=False):
    """
    Returns each search keyword (see parse_keywords()) as (exclude,
    choices): what has any one of its choices is found

    - 'alert|danger' finds either word (\\| is a | itself)
    - a - in front leaves out what has any of them ('-sample|proof')
    - %p in front looks in the posters (for every choice after it too)
    - choices: (column, value, term) each; term is the NNTPSearchTerm of a
      choice holding a * or ? wildcard (its value is None then), else None
    """
    found = []
    for keyword in keywords:
        parsed = [KEYWORD_RE.match(part.replace('\\|', '|'))
                  for part in KEYWORD_OR_RE.split(keyword) if part]
        parsed = [part for part in parsed if part]
        if not parsed:
            # Nothing to look for
            continue

        exclude = parsed[0].group('op') == '-'
        column = 'subject'
        choices = []
        for part in parsed:
            if part.group('column'):
                column = 'poster' if part.group('column') == '%p' \
                    else 'subject'

            value = part.group('text')
            if WILDCARD_RE.search(value):
                choices.append((column, None, NNTPSearchTerm(
                    'poster' if column == 'poster' else SEARCH_ANY,
                    '*%s*' % value, case_sensitive=case_sensitive)))

            else:
                choices.append((column, value, None))

        found.append((exclude, choices))

    return found


def like_escape(value):
    """
    Returns text with LIKE's wildcards (% and _) escaped with '/' (use
    escape='/' with it)
    """
    return value.replace('/', '//').replace('%', '/%').replace('_', '/_')


def tag_condition(column, logic):
    """
    Returns the SQL condition for the tags stored in `column` (space
    separated) satisfying tag logic (see tag_logic()); None when there is
    no logic
    """
    if not logic:
        return None

    padded = literal(' ') + column + literal(' ')

    def has(tag):
        return padded.like('%% %s %%' % like_escape(tag), escape='/')

    return or_(*(
        and_(*(has(tag) for tag in sorted(wanted)),
             *(~has(tag) for tag in sorted(unwanted)))
        for wanted, unwanted in logic))


def rank_query(query, score_column, tags_column, posted_columns=None,
               tags=None, without_tags=None, min_score=None, max_score=None,
               scores=None, after=None, before=None):
    """
    Narrows a query down by score, tags and when it was posted (see
    NNTPFilter)

    - score_column / tags_column: where the score and tags are stored
    - posted_columns: (first, last) of when it was posted (the same column
      twice for a single article)
    - tags: only what has these tags (see tag_logic(); ie:
      ['video, hd', 'music'] is (video and hd) or music, '!junk' is
      anything without junk)
    - without_tags: leave out what has these tags (same logic)
    - min_score / max_score: leave out what scores lower / higher
    - scores: comparisons the score must pass (ie: ['<34, >23']; see
      score_tests()), every one of them
    - after / before: only what was (at least partly) posted at or after /
      before this time (see parse_time(); ie: 2026-01-31 or 90d)

    Raises ValueError for something that can't be used.
    """
    found = tag_condition(tags_column, tag_logic(tags))
    if found is not None:
        query = query.filter(found)

    found = tag_condition(tags_column, tag_logic(without_tags))
    if found is not None:
        query = query.filter(~found)

    if min_score is not None:
        query = query.filter(score_column >= int(min_score))

    if max_score is not None:
        query = query.filter(score_column <= int(max_score))

    for value in scores or ():
        for op, number in score_tests(value, '--score'):
            query = query.filter(SQL_COMPARISONS[op](score_column, number))

    first, last = posted_columns or (None, None)
    after = parse_time(after, '--after')
    if after is not None and last is not None:
        query = query.filter(last >= bindparam(
            'posted_after', after, type_=DateTime()))

    before = parse_time(before, '--before')
    if before is not None and first is not None:
        query = query.filter(first < bindparam(
            'posted_before', before, type_=DateTime()))

    return query


def rank_text(entry):
    """
    Returns how an entry's score and tags (see NNTPFilter) are shown after
    it in a list (ie: '  {+7 hd video}'); empty when it has neither
    """
    score = getattr(entry, 'score', 0) or 0
    tags = getattr(entry, 'tags', '') or ''
    if not (score or tags):
        return ''

    return '  {%s}' % ' '.join(
        (['%+d' % score] if score else []) + tags.split())


def fit_key(key, limit=MAX_KEY):
    """
    Returns a key that fits its column: one too long keeps its start and
    ends with a hash of all of it, so two long keys never become one
    """
    if len(key) <= limit:
        return key

    return '%s#%s' % (key[:limit - 33], md5(key.encode('utf-8')).hexdigest())


def streamed(query):
    """
    Yields what a query finds a few (STREAM_SIZE) at a time, read on a
    connection of its own: other queries can run while it is read (MySQL
    can't run another on a connection still sending results; it quietly
    cut the results short).  Being its own connection, it only sees what
    was committed.
    """
    session = Session(bind=query.session.get_bind())
    try:
        yield from query.with_session(session).yield_per(STREAM_SIZE)

    finally:
        session.close()


def chunked(items, size=IN_LIST_SIZE):
    """
    Splits items into lists of at most `size` (a generator); only one
    chunk is held at a time, so items can be an iterator of any length
    """
    items = iter(items)
    while True:
        chunk = list(islice(items, size))
        if not chunk:
            return
        yield chunk


def id_chunks(message_ids, size=IN_LIST_SIZE):
    """
    Splits Message-IDs into lists of at most `size` (see chunked()), each
    without its <> and without repeats
    """
    for chunk in chunked((m.strip().strip('<>') for m in message_ids), size):
        yield list(dict.fromkeys(chunk))


def sorted_in_runs(items, size=None, work_dir=None, fan_in=None):
    """
    Yields items in sorted order, holding at most `size` of them at once
    (SORT_RUN_SIZE by default)

    - up to `size` items are simply sorted in memory
    - more are sorted `size` at a time; each sorted run is written to a
      file of its own (in a temporary folder in work_dir) and closed
    - the runs are then read back together, at most `fan_in` of them
      (SORT_MERGE_SIZE by default); with more, they are first merged into
      bigger runs, so only that many files are ever open at once
    - every item is read before the first one is given
    - items must be plain values: str, int, bool, None, or tuples of them
    """
    size = size or SORT_RUN_SIZE
    fan_in = max(2, fan_in or SORT_MERGE_SIZE)
    items = iter(items)

    # The folder holding the runs, and the runs open for reading
    folder = None
    reading = set()
    written = 0

    def write(ordered):
        # Saves sorted items as a run; returns where it is
        nonlocal written
        written += 1
        path = join(folder, '%d' % written)
        with open(path, 'wb') as fp:
            for item in ordered:
                marshal.dump(item, fp)
        return path

    def read(path):
        fp = open(path, 'rb')
        reading.add(fp)
        try:
            while True:
                try:
                    item = marshal.load(fp)

                except EOFError:
                    return

                yield item

        finally:
            fp.close()
            reading.discard(fp)

    try:
        chunk = sorted(islice(items, size))
        if len(chunk) < size:
            # They all fit; nothing is written
            yield from chunk
            return

        folder = mkdtemp(prefix='newsreap-sort-', dir=work_dir)
        runs = deque()
        while chunk:
            runs.append(write(chunk))

            # Let go of this run before the next one is read
            chunk = None
            chunk = sorted(islice(items, size))

        # Too many to read together: the oldest ones are merged into one
        # bigger run (and removed) until few enough are left; only as many
        # as that takes are merged, so as little as possible is rewritten
        while len(runs) > fan_in:
            merged = [runs.popleft() for _ in range(
                min(fan_in, len(runs) - fan_in + 1))]
            runs.append(write(merge(*(read(path) for path in merged))))
            for path in merged:
                unlink(path)

        yield from merge(*(read(path) for path in runs))

    finally:
        for fp in list(reading):
            fp.close()

        if folder is not None:
            rmtree(folder, ignore_errors=True)


class NNTPIndexer:
    """
    Indexes cached articles (from any number of groups) into one index;
    see the module description
    """

    def __init__(self, db_file, chunk_size=DEFAULT_CHUNK_SIZE,
                 settle_time=DEFAULT_SETTLE_TIME,
                 repost_days=DEFAULT_REPOST_DAYS, filters=None,
                 session_minutes=DEFAULT_SESSION_MINUTES):
        """
        Prepares an indexer

        - db_file: the index database; an SQLite file or a database URL
          (see NNTPSettings.index_database())
        - chunk_size: how many articles are indexed (and saved) at a time
        - settle_time: how long (in seconds) a collection missing parts
          waits for new articles before it is settled
        - repost_days: how far apart (in days) something is posted from a
          collection with the same name and poster before it counts as a
          repost; 0 never splits them
        - filters: the NNTPFilter that scores and tags articles (and drops
          the ones it no longer wants) as they are indexed; without it, the
          scores and tags given when they were cached are used
        - session_minutes: how long a pause (in minutes) between the
          articles of an obfuscated post starts a new one
        """
        self.db_file = db_file
        self.filters = filters or None
        self.chunk_size = max(1, int(chunk_size))
        self.settle_after = timedelta(
            seconds=max(0.0, float(settle_time)))

        # None never splits a collection
        self.repost_gap = timedelta(days=float(repost_days)) \
            if repost_days and float(repost_days) > 0 else None

        # Where one obfuscated post ends and the next begins
        self.session_gap = timedelta(minutes=max(
            1.0, float(session_minutes)))

        # The ways of posting obfuscated articles (see posting_key()) that
        # give every article a made up poster
        self.random_posters = set()

        # What our last index() did:
        #  - articles: articles indexed
        #  - files / collections: new ones found
        #  - dropped: articles our filters removed instead
        self.articles = 0
        self.dropped = 0
        self.files = 0
        self.collections = 0

        # How many collections our last settle() formed
        self.formed = 0

        # What our last delete() took away (see Purged)
        self.purged = Purged(0, 0, 0, 0, 0)

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
        self.articles = self.files = self.collections = self.dropped = 0

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

        # The obfuscated posts whose files can't be told apart
        self._unsplit = set()

        try:
            # Work through the articles in order, a chunk at a time; each
            # chunk is saved before the next one is read, so stopping part
            # way loses nothing
            while True:
                rows = cached.query(
                    Article.message_id, Article.article_no,
                    Article.subject, Article.poster, Article.size,
                    Article.posted_date, Article.xref)\
                    .filter(Article.indexed.is_(False))\
                    .order_by(Article.article_no)\
                    .limit(self.chunk_size).all()

                if not rows:
                    # We're done
                    break

                last = rows[-1].article_no
                rows, dropped = self._index_rows(
                    session, group, rows, collections, files)

                # The ones our filters no longer want are let go of
                for ids in chunked(dropped):
                    cached.query(Article)\
                        .filter(Article.message_id.in_(ids))\
                        .delete(synchronize_session=False)
                self.dropped += len(dropped)

                # Mark them as indexed; they are the first articles not yet
                # indexed, so every one up to the last of them is ours
                cached.query(Article)\
                    .filter(Article.indexed.is_(False))\
                    .filter(Article.article_no <= last)\
                    .update({Article.indexed: True},
                            synchronize_session=False)
                cached.commit()

                self.articles += len(rows)
                logger.debug('Indexed %d article(s) of %s (up to article %d).'
                             % (len(rows), group, last))

            if self._unsplit:
                # How many files they are likely to have
                self.lock(session)
                self._estimate_files(session, self._unsplit)
                session.commit()

            # Remember how far we have seen the group (see settle())
            seen = cache.clock()
            clock = session.get(GroupClock, group)
            if seen is not None and (clock is None or seen > clock.seen):
                session.merge(GroupClock(name=group, seen=seen))
                session.commit()

        finally:
            cache.close()

        return True

    def _index_rows(self, session, group, rows, collections, files):
        """
        Indexes (and commits) one chunk of cached articles, with the index
        to ourselves while we do (see lock()); returns (the rows kept, the
        Message-IDs our filters dropped).

        If another worker was in the way, it is undone and tried again (up
        to INDEX_TRIES times); what we looked up is forgotten first, since
        it may not have been saved.
        """
        before = (self.files, self.collections, set(self._unsplit))
        for attempt in range(1, INDEX_TRIES + 1):
            try:
                self.lock(session)

                # What our filters make of them
                ranks, dropped = self._rank(session, group, rows)
                kept = [row for row in rows
                        if row.message_id not in dropped] if dropped else rows

                self._index_chunk(
                    session, group, kept, collections, files, ranks)

                # Real file names the archive found in these articles
                self._archived_filenames(
                    session, [row.message_id for row in kept])
                session.commit()

            except SQLAlchemyError as e:
                session.rollback()
                if attempt >= INDEX_TRIES or not contended(e):
                    raise

                logger.info(
                    'Another worker was in the way while indexing %s; '
                    'trying again (%d of %d).' % (group, attempt + 1,
                                                  INDEX_TRIES))
                collections.clear()
                files.clear()
                self.files, self.collections, self._unsplit = \
                    before[0], before[1], set(before[2])
                sleep(attempt)
                continue

            # Our totals were updated directly in the database; anything
            # read before now is out of date
            session.expire_all()
            return kept, dropped

    @staticmethod
    def lock(session):
        """
        Has the index to ourselves until the session commits (or rolls
        back): other workers wait at this point for their turn, so two of
        them never add the same collection.  Call it before reading what
        is about to be changed.
        """
        taken = session.query(IndexLock).filter(IndexLock.id == 1)\
            .update({IndexLock.taken: IndexLock.taken + 1},
                    synchronize_session=False)
        if not taken:
            # The first time: make the row (another worker may be making
            # it too), then take it
            NNTPIndexer._insert_ignore(
                session, IndexLock, [{'id': 1, 'taken': 0}])
            session.query(IndexLock).filter(IndexLock.id == 1)\
                .update({IndexLock.taken: IndexLock.taken + 1},
                        synchronize_session=False)

    def _rank(self, session, group, rows):
        """
        Returns (ranks, dropped) for a chunk of cached articles:
        - ranks: Message-ID -> (score, tags)
        - dropped: the Message-IDs our filters remove

        With filters, every article is judged again (in every group its
        Xref lists, with anything else the index knows about it) and its
        rank saved (see NNTPArticleRanks); otherwise the ranks saved before
        are used.  Not committed.
        """
        if self.filters is None:
            # As they were worked out before
            ranks = NNTPArticleRanks.load(
                session, [row.message_id for row in rows])
            return {row.message_id: ranks.get(row.message_id, (0, frozenset()))
                    for row in rows}, set()

        # What else we know about them (only when a rule needs it)
        known = NNTPArticleRanks.known(
            session, [row.message_id for row in rows],
            bodies=self.filters.uses('body'),
            filenames=self.filters.uses('filename'))

        ranks = {}
        dropped = set()
        for row in rows:
            extra = known.get(row.message_id, {})
            where = [group, *(row.xref or '').split()]
            verdict = self.filters.check(
                where, subject=row.subject,
                poster=row.poster, message_id=row.message_id,
                posted=row.posted_date, size=row.size,
                body=extra.get('body'), filenames=extra.get('filenames'),
                crossposts=NNTPFilterRule.count_groups(where))

            if verdict.drop is not None:
                dropped.add(row.message_id)
                continue

            ranks[row.message_id] = (verdict.score, verdict.tags)

        # Kept in one place (and worked out again from 0 every time)
        NNTPArticleRanks.save(session, ranks)
        if dropped:
            NNTPArticleRanks.forget(session, dropped)

        return ranks, dropped

    def _index_chunk(self, session, group, rows, collections, files,
                     ranks=None):
        """
        Indexes one chunk of articles found in `group`

        - ranks: Message-ID -> (score, tags) of the articles (see _rank())
        """
        long_ids = [row for row in rows
                    if len(row.message_id or '') > MAX_MESSAGE_ID]
        if long_ids:
            logger.debug('Left out %d article(s) with a Message-ID longer '
                         'than %d characters.' % (
                             len(long_ids), MAX_MESSAGE_ID))
            rows = [row for row in rows
                    if len(row.message_id or '') <= MAX_MESSAGE_ID]

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
        parsed = [(row, parse_subject(row.subject or '')) for row in rows]
        self._learn_posters(
            [row for row, info in parsed if is_obfuscated(info)])

        for row, info in parsed:
            poster = row.poster or ''

            info['session'] = is_obfuscated(info)
            if info['session']:
                # Its subject says nothing about the post it is part of, so
                # it is put together from how it was posted: the poster, the
                # shape of its subject and Message-ID (the posting tool's)
                # and the groups it went to, split where posting paused
                # (see _window())
                how = posting_key(row)
                if how in self.random_posters:
                    # A made up poster for every article; only where they
                    # say they are from is the same
                    poster = '*@%s' % poster.rpartition('@')[2].strip(
                        ' <>').lower()
                    info['name'] = 'Obfuscated post (by %s)' % poster

                else:
                    info['name'] = 'Obfuscated post by %s' % poster

                info['name'] = info['name'][:256]
                info['collection_key'] = fit_key('%s\n%s\n%s' % (
                    SESSION_KEY, poster, how))

            else:
                # The files of one collection share a poster and a name
                name = info['desc'] or release_name(info['filename']) or \
                    info['base']
                info['name'] = name[:256]
                info['collection_key'] = fit_key('%s\n%s\n%d' % (
                    poster, name, info['files']))

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

        # The ones looked at earlier in this run may have been removed since
        # (nr delete); the index is ours now, so ask again
        earlier = {
            window['id']: window
            for key in {info['collection_key'] for _, _, info in entries}
            - missing for window in collections[key]
            if window['id'] is not None
            and window['state'] != CollectionState.Deleted}
        for ids in chunked(earlier):
            for collection_id, in session.query(Collection.id)\
                    .filter(Collection.id.in_(ids))\
                    .filter(Collection.state == CollectionState.Deleted):
                earlier[collection_id]['state'] = CollectionState.Deleted

        # Work out which collection each article belongs to
        added = []
        kept = []
        for row, poster, info in entries:
            windows = collections[info['collection_key']]
            window = self._window(
                windows, row.posted_date,
                self.session_gap if info['session'] else self.repost_gap)
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
        for row, _, info, window in kept:
            if info['session'] and info['parts'] <= 1:
                # Nothing says which file it is part of (the articles of
                # several are mixed together); they wait in one until their
                # real names are known (see learn_filenames())
                info['file_key'] = '%d\n%s' % (window['id'], SESSION_KEY)
                info['part'] = row.article_no
                info['parts'] = 0
                info['base'] = '(the files of an obfuscated post)'

            else:
                info['file_key'] = fit_key(
                    '%d\n%s' % (window['id'], info['base']))

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

        # The obfuscated posts whose files can't be told apart (how many
        # files they have is worked out once the run is done)
        self._unsplit.update(
            window['id'] for _, _, info, window in kept
            if info['session'] and not info['parts'])

        # Each collection gets the best score of its articles, and every tag
        # they were given
        best = {}
        for row, _, _, window in kept:
            score, tags = (ranks or {}).get(row.message_id, (0, frozenset()))
            found = best.get(window['id'])
            if found is None:
                best[window['id']] = (score, set(tags))

            else:
                best[window['id']] = (max(found[0], score), found[1] | tags)

        self.score_collections(
            session, best, {window['id'] for window in added})

    @classmethod
    def rank_collections(cls, session, collection_ids):
        """
        Works the score and tags of the collections given out again from
        their articles' (see NNTPArticleRanks): the best score, and every
        tag; not committed
        """
        from .objects.index.ArticleRank import ArticleRank

        # collection id -> [best score, tags, articles with a rank]
        found = {collection_id: [None, set(), 0]
                 for collection_id in collection_ids}
        for ids in chunked(found):
            for collection_id, score, tags in session.query(
                    CollectionFile.collection_id, ArticleRank.score,
                    ArticleRank.tags)\
                    .join(Segment, Segment.file_id == CollectionFile.id)\
                    .join(ArticleRank,
                          ArticleRank.message_id == Segment.message_id)\
                    .filter(CollectionFile.collection_id.in_(ids)):
                entry = found[collection_id]
                entry[0] = score if entry[0] is None else max(entry[0], score)
                entry[1] |= tags_from_text(tags)
                entry[2] += 1

            # An article without a rank scores 0
            for collection_id, articles in session.query(
                    CollectionFile.collection_id, func.count(Segment.message_id))\
                    .join(Segment, Segment.file_id == CollectionFile.id)\
                    .filter(CollectionFile.collection_id.in_(ids))\
                    .group_by(CollectionFile.collection_id):
                entry = found[collection_id]
                if entry[0] is None or articles > entry[2]:
                    entry[0] = max(entry[0] or 0, 0)

        best = {collection_id: (entry[0] or 0, entry[1])
                for collection_id, entry in found.items()}
        cls.score_collections(session, best, replace=True)
        return best

    @staticmethod
    def score_collections(session, best, new=(), replace=False):
        """
        Gives collections the score and tags of their articles (not
        committed)

        - best: collection id -> (best score, tags) of the articles added
        - new: the ids of collections that had nothing before (they take
          the score as it is)
        - replace: set the scores and tags given (instead of keeping the
          better score and adding the tags)
        """
        for ids in chunked(best):
            for collection_id, score, tags in session.query(
                    Collection.id, Collection.score, Collection.tags)\
                    .filter(Collection.id.in_(ids)):

                wanted, added = best[collection_id]
                if not (replace or collection_id in new):
                    wanted = max(wanted, score)
                    added = set(added) | tags_from_text(tags)

                text = tags_to_text(added)
                if wanted == score and text == tags:
                    # Nothing changed
                    continue

                session.query(Collection)\
                    .filter(Collection.id == collection_id)\
                    .update({Collection.score: wanted,
                             Collection.tags: text},
                            synchronize_session=False)

    def forget_articles(self, message_ids):
        """
        Removes articles (by Message-ID) from the collections they are in,
        with their scores and tags; returns (articles, files, collections)
        removed

        - a collection keeps what is left (counted again; a complete one is
          no longer complete)
        - a file left with no articles goes, and so does a collection left
          with no files
        - they are removed (and committed) a chunk at a time
        """
        session = self.session()
        if session is None:
            return 0, 0, 0

        totals = (0, 0, 0)
        for ids in id_chunks(message_ids):
            removed = self._forget_articles(session, ids)
            totals = tuple(a + b for a, b in zip(totals, removed))

        return totals

    def _forget_articles(self, session, ids):
        """
        Removes one chunk of articles from the index (see
        forget_articles()); returns (articles, files, collections) removed
        """
        self.lock(session)
        files = set()
        collections = set()
        for file_id, collection_id in session.query(
                Segment.file_id, CollectionFile.collection_id)\
                .join(CollectionFile, CollectionFile.id == Segment.file_id)\
                .filter(Segment.message_id.in_(ids)):
            files.add(file_id)
            collections.add(collection_id)

        articles = session.query(Segment)\
            .filter(Segment.message_id.in_(ids))\
            .delete(synchronize_session=False)
        NNTPArticleRanks.forget(session, ids)

        # The files left with nothing go (children first; the database
        # servers enforce their links)
        self._recount(session, files, ())
        empty = []
        for ids in chunked(sorted(files)):
            empty.extend(file_id for file_id, in session.query(
                CollectionFile.id)
                .filter(CollectionFile.id.in_(ids))
                .filter(CollectionFile.segments == 0))

        for ids in chunked(empty):
            session.query(FileGroup)\
                .filter(FileGroup.file_id.in_(ids))\
                .delete(synchronize_session=False)
            session.query(CollectionFile)\
                .filter(CollectionFile.id.in_(ids))\
                .delete(synchronize_session=False)

        self._recount(session, (), collections)

        # What lost a part is no longer complete
        for ids in chunked(sorted(collections)):
            session.query(Collection)\
                .filter(Collection.id.in_(ids))\
                .filter(Collection.state == CollectionState.Complete)\
                .filter(Collection.segments < Collection.segments_expected)\
                .update({Collection.state: CollectionState.Settled},
                        synchronize_session=False)

        # The collections nothing is left of
        emptied = []
        for ids in chunked(sorted(collections)):
            emptied.extend(c for c, in session.query(Collection.id)
                           .filter(Collection.id.in_(ids))
                           .filter(Collection.files == 0)
                           .filter(Collection.state !=
                                   CollectionState.Deleted))

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

        session.commit()

        # Counted and removed in the database; what was read is out of date
        session.expunge_all()
        return articles, len(empty), len(emptied)

    def forget_ranks(self, message_ids):
        """
        Lets go of the scores and tags of articles (ie: their cached headers
        were removed); see NNTPArticleRanks
        """
        session = self.session()
        if session is None:
            return

        NNTPArticleRanks.forget(session, message_ids)
        session.commit()

    @staticmethod
    def forget_posts(session, message_ids):
        """
        Removes kept posts (what 'nr update --content' kept) and everything
        tied to them: their files, PGP ties, why they wait to be decrypted
        and the groups they were found in.  Not committed.
        """
        # Children first; database servers check their foreign keys
        for ids in chunked(message_ids):
            for table in (ArchiveFile, PGPPost, PGPWait, ArchiveGroup,
                          ArchivedPost):
                session.query(table)\
                    .filter(table.message_id.in_(ids))\
                    .delete(synchronize_session=False)

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

    def _learn_posters(self, rows):
        """
        Notes the ways of posting obfuscated articles (see posting_key())
        that give every article a made up poster: most of the articles
        posted that way (in a chunk) have a poster of their own
        """
        found = {}
        for row in rows:
            entry = found.setdefault(posting_key(row), [0, set()])
            entry[0] += 1
            entry[1].add(row.poster or '')

        for how, (count, posters) in found.items():
            if count >= RANDOM_POSTER_SAMPLE and len(posters) > count / 2:
                self.random_posters.add(how)

    def _window(self, windows, posted, gap):
        """
        Returns the collection (of the ones sharing a key) an article posted
        at `posted` belongs to; None if it is a new one (ie: a repost)

        - gap: how far from a collection's posting times an article can be
          and still be part of it (None never splits them)

        Each entry of `windows` holds a collection's id, state and when its
        first and last articles were posted.
        """
        if not windows:
            # Nothing shares its key yet
            return None

        if posted is None or gap is None:
            # We can't (or shouldn't) tell them apart; use the newest
            return windows[-1]

        for window in windows:
            if window['first'] is None or (
                    window['first'] - gap <= posted <=
                    window['last'] + gap):
                # Posted around the same time
                return window

        # Posted long before or after any of them
        return None

    @staticmethod
    def _estimate_files(session, collection_ids):
        """
        Works out how many files obfuscated posts (whose files can't be
        told apart) have: every article of a file is the same size apart
        from its last one, which is shorter, so each short article (well
        under the post's biggest) ends a file.  Kept as the collection's
        files_expected; it is only an estimate (shown as ~N).  Not
        committed.
        """
        for collection_id in sorted(collection_ids):
            articles = session.query(Segment.size)\
                .join(CollectionFile, CollectionFile.id == Segment.file_id)\
                .filter(CollectionFile.collection_id == collection_id)

            biggest = articles.with_entities(func.max(Segment.size))\
                .scalar() or 0
            short = articles.filter(
                Segment.size < biggest * SHORT_ARTICLE_SHARE)\
                .with_entities(func.count()).scalar() or 0

            session.query(Collection)\
                .filter(Collection.id == collection_id)\
                .update({Collection.files_expected: short},
                        synchronize_session=False)

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
        `settle_time` (seconds) past its last article.  Collections are
        never marked incomplete just because we haven't scanned in a while.

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
            # How many parts an obfuscated post has isn't known; it can only
            # settle
            'session': SESSION_KEY + '\n%',
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

        # Update the scanned collections still forming, and the settled ones
        # that late parts have completed (once settled they stay that way,
        # so the rest of them are left alone; there can be millions)
        self.lock(session)
        complete = \
            'segments >= segments_expected AND ' \
            '(files_expected = 0 OR files >= files_expected) AND ' \
            '(segments_expected > 0 OR collection.key NOT LIKE :session)'
        session.execute(text(
            'UPDATE collection SET state = CASE '
            'WHEN ' + complete + ' THEN :complete '
            'WHEN state = :settled OR last_posted IS NULL THEN :settled '
            'WHEN ' + days('last_posted') + ' < ' + seen + ' - :days '
            '  THEN :settled '
            'ELSE :forming END '
            'WHERE source = :scan AND (state = :forming OR '
            '  (state = :settled AND ' + complete + '))')
            .bindparams(*params), values)

        # Give every newly formed collection its GID, a chunk at a time (a
        # first index can form a great many at once)
        session.commit()
        last = 0
        while True:
            self.lock(session)
            ids = [row.id for row in session.query(Collection.id)
                   .filter(Collection.gid.is_(None))
                   .filter(Collection.state.in_(COLLECTION_FORMED))
                   .filter(Collection.id > last)
                   .order_by(Collection.id).limit(IN_LIST_SIZE)]
            if not ids:
                break

            last = ids[-1]
            gids = self._gids(session, ids)

            # The collections that already have these GIDs: (id, state)
            taken = {}
            for row in session.query(
                    Collection.gid, Collection.id, Collection.state)\
                    .filter(Collection.gid.in_(set(gids.values()))):
                taken.setdefault(row.gid, (row.id, row.state))

            given = []
            for collection_id in ids:
                gid = gids.get(collection_id)
                if gid is None:
                    # It has no segments to work it out from
                    continue

                other = taken.get(gid)
                if other is None:
                    # Ours now (a later one of this chunk is a copy of it)
                    given.append({'_id': collection_id, 'gid': gid})
                    taken[gid] = (collection_id, None)
                    self.formed += 1

                elif other[1] == CollectionState.Deleted:
                    # A copy of something removed; remove it too
                    self._remove(session, collection_id)

                else:
                    # We already have this post (ie: it was imported from an
                    # NZB file); this is what was left over
                    logger.debug(
                        'Collection #%d is a part of collection #%d.' % (
                            collection_id, other[0]))

            if given:
                session.execute(
                    update(Collection.__table__)
                    .where(Collection.__table__.c.id == bindparam('_id'))
                    .values(gid=bindparam('gid')), given)

            session.commit()

        # Let go of the index (the last look found nothing more to do)
        session.commit()

        # The states were updated directly in the database; anything read
        # before now is out of date
        session.expire_all()
        return self.formed

    @staticmethod
    def _files(session, collection_id):
        """
        Yields a collection's files (CollectionFile) in the order an NZB
        file lists them (see _file_pages_in_order())
        """
        for page in NNTPIndexer._file_pages_in_order(session, collection_id):
            yield from page

    @staticmethod
    def _file_pages_in_order(session, collection_id, work_dir=None):
        """
        Yields a collection's files (CollectionFile) a page at a time, in
        the order an NZB file lists them; the first one is what its GID
        comes from

        - Files read from an NZB file keep the order it had them in.
        - Scanned files are sorted the way newznab sorts them (see
          gid_order()).
        - Only what each file is sorted by is read, and it is sorted in
          runs (see sorted_in_runs()), so a collection of any size is
          never held; the files are then read a page at a time (see
          _paged()).
        """
        def keys():
            # What each file is sorted by, read SORT_RUN_SIZE files at a
            # time (by id, so each read picks up where the last one ended)
            last = 0
            while True:
                rows = session.query(
                    CollectionFile.id, CollectionFile.position,
                    CollectionFile.subject, CollectionFile.segments)\
                    .filter(CollectionFile.collection_id == collection_id)\
                    .filter(CollectionFile.id > last)\
                    .order_by(CollectionFile.id)\
                    .limit(SORT_RUN_SIZE).all()
                if not rows:
                    return

                for file_id, position, subject, segments in rows:
                    yield (position is None, position or 0,
                           gid_order(subject), file_id, segments)

                last = rows[-1][0]
                rows = None

        order = ((file_id, segments) for *_, file_id, segments in
                 sorted_in_runs(keys(), work_dir=work_dir))

        for ids in NNTPIndexer._paged(order):
            found = {entry.id: entry for entry in session.query(
                CollectionFile).filter(CollectionFile.id.in_(ids))}
            yield [found[file_id] for file_id in ids if file_id in found]

    @staticmethod
    def _paged(files, size=IN_LIST_SIZE, articles=ARTICLE_PAGE_SIZE):
        """
        Splits files into pages of their ids (lists), in the order given

        - files: (file id, how many articles it has) for each
        - a page holds at most `size` files, with about `articles`
          articles between them (a file with more is a page of its own)
        """
        page, held = [], 0
        for file_id, segments in files:
            if page and (len(page) >= size
                         or held + (segments or 0) > articles):
                yield page
                page, held = [], 0

            page.append(file_id)
            held += segments or 0

        if page:
            yield page

    @staticmethod
    def _gids(session, collection_ids):
        """
        Works out the GIDs of the collections given from the first segment
        (lowest part) of each one's first file (in _files() order)

        - collection_ids: a chunk of collection ids (see IN_LIST_SIZE)
        - returns {collection id: GID}; one with no segments is left out
        """
        # Each collection's first file: (its sort key, file id)
        first = {}
        for row in session.query(
                CollectionFile.collection_id, CollectionFile.id,
                CollectionFile.position, CollectionFile.subject)\
                .filter(CollectionFile.collection_id.in_(collection_ids)):
            key = (row.position is None, row.position or 0,
                   gid_order(row.subject), row.id)
            best = first.get(row.collection_id)
            if best is None or key < best[0]:
                first[row.collection_id] = (key, row.id)

        if not first:
            return {}

        # The lowest part of each of those files, then the lowest Message-ID
        # holding it (a part can have been posted more than once)
        owners = {file_id: collection_id
                  for collection_id, (_, file_id) in first.items()}
        lowest = select(
            Segment.file_id, func.min(Segment.part).label('part'))\
            .where(Segment.file_id.in_(owners))\
            .group_by(Segment.file_id).subquery()

        return {
            owners[file_id]: make_gid(message_id)
            for file_id, message_id in session.query(
                Segment.file_id, func.min(Segment.message_id))
            .join(lowest, and_(
                Segment.file_id == lowest.c.file_id,
                Segment.part == lowest.c.part))
            .group_by(Segment.file_id)}

    def get(self, value, unfinished=False):
        """
        Returns a collection by its id or GID (None if there is no such
        collection, or it was removed)

        - unfinished: also find one a stopped delete() left part way
        """
        session = self.session()
        if session is None:
            return None

        kept = Collection.state != CollectionState.Deleted
        if unfinished:
            kept = or_(kept, exists().where(
                CollectionFile.collection_id == Collection.id))

        value = str(value).strip()
        query = session.query(Collection).filter(kept)
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
             limit=None, case_sensitive=False, tags=None, without_tags=None,
             min_score=None, max_score=None, terms=None, scores=None,
             after=None, before=None, oldest=False, stream=False):
        """
        Returns collections (newest first) with every keyword in their name
        or the subject or name of one of their files (see
        keyword_choices()):
        - a keyword starting with '-' must not be in any of them
        - a keyword starting with '%p' is looked for in the posters instead
        - 'a|b' finds what has either

        - groups: only collections with a file found in one of these groups
        - complete: True for only complete ones, False for only incomplete
          ones, None for both
        - forming: include collections still forming
        - case_sensitive: only match keywords with the same case (otherwise
          case, and accents, don't matter)
        - tags / without_tags: only (or leave out) collections with these
          tags (see tag_logic(); each one is a choice, the tags within one
          must all be there)
        - min_score / max_score: leave out collections scoring lower /
          higher
        - terms: find(1) style conditions (see NNTPSearchTerm) they must
          all match: subject (a collection's name or a file subject),
          filename, poster and message_id (one of its articles)
        - scores: score comparisons (ie: '<34, >23') they must pass
        - after / before: only collections with something posted at or
          after / before this (a date or how long ago, ie: 90d)
        - oldest: oldest first (instead of newest first)
        - stream: return them as they are read (an iterator) instead of a
          list

        Raises ValueError for a condition collections can't be searched
        by (ie: body).
        """
        for term in terms or ():
            if term.field not in COLLECTION_FIELDS:
                raise ValueError(
                    '--%s only works when searching posts (--posts)' %
                    term.option)

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

        # Each keyword finds what has any one of its choices
        no = 0
        for exclude, choices in keyword_choices(keywords, case_sensitive):
            found = []
            for column, value, term in choices:
                no += 1
                found.append(self._term(session, no, term) if term else
                             Collection.id.in_(self._search(
                                 session, no, column, value, case_sensitive)))

            found = or_(*found)
            query = query.filter(~found if exclude else found)

        # Every condition narrows the results down further
        for no, term in enumerate(terms or (), start=no + 1):
            query = query.filter(self._term(session, no, term))

        query = rank_query(
            query, Collection.score, Collection.tags,
            (Collection.first_posted, Collection.last_posted), tags=tags,
            without_tags=without_tags, min_score=min_score,
            max_score=max_score, scores=scores, after=after, before=before)

        if groups:
            # Found in (at least) one of the groups
            query = query.filter(Collection.id.in_(
                session.query(CollectionFile.collection_id)
                .join(FileGroup, FileGroup.file_id == CollectionFile.id)
                .filter(FileGroup.name.in_(list(groups)))))

        query = query.order_by(
            Collection.first_posted.asc(), Collection.id.asc()) if oldest \
            else query.order_by(
                Collection.first_posted.desc(), Collection.id.desc())

        if limit:
            query = query.limit(limit)

        # Read a few at a time (there can be millions)
        return streamed(query) if stream else query.all()

    @staticmethod
    def summary(entry):
        """
        Returns the line that describes a collection in a list: its id, how
        much of it was found, its size, files and name
        """
        percent = 100.0 * entry.segments / entry.segments_expected \
            if entry.segments_expected else 100.0

        if not entry.segments_expected and \
                (entry.key or '').startswith(SESSION_KEY + '\n'):
            # How much of an obfuscated post we have isn't known
            percent = None

        files = '%d/%d' % (entry.files, entry.files_expected) \
            if entry.files_expected else '%d' % entry.files

        if percent is None and entry.files_expected:
            # The files of an obfuscated post can't be told apart; how many
            # there are is a guess
            files = '~%d' % entry.files_expected

        line = '[%6d] %6s %10s %7s file(s)  %s' % (
            entry.id, '?' if percent is None else
            '%.1f%%' % min(percent, 100.0), bytes_to_strsize(entry.size),
            files, entry.name)

        return line + rank_text(entry)

    def _term(self, session, no, term):
        """
        Returns the condition for a collection matching a search condition
        (see NNTPSearchTerm)

        The search index finds the collections holding the text the
        pattern needs first; only those are checked against the pattern.
        """
        dialect = session.get_bind().dialect.name

        if term.field == 'message_id':
            # One of its articles; an exact Message-ID is looked up directly
            # (anything else has to look at every article)
            found = term.condition(dialect, Segment.message_id)
            if term.exact() is not None:
                found = and_(Segment.message_id == term.exact(), found)

            found = Collection.id.in_(
                select(CollectionFile.collection_id).where(
                    CollectionFile.id.in_(
                        select(Segment.file_id).where(found))))
            return ~found if term.negate else found

        # Where the field is kept: (the collection's id, the column)
        columns = {
            SEARCH_ANY: (
                (Collection.id, Collection.name),
                (CollectionFile.collection_id, CollectionFile.subject),
                (CollectionFile.collection_id, CollectionFile.filename)),
            'subject': (
                (Collection.id, Collection.name),
                (CollectionFile.collection_id, CollectionFile.subject)),
            'filename': (
                (CollectionFile.collection_id, CollectionFile.filename), ),
            'poster': (
                (Collection.id, Collection.poster),
                (CollectionFile.collection_id, CollectionFile.poster)),
        }[term.field]

        # What the search index can find for us
        narrowed = [
            self._search(
                session, TERM_PARAMS + no * TERM_PARAMS + piece_no,
                'poster' if term.field == 'poster' else 'subject', piece)
            for piece_no, piece in enumerate(term.pieces()[:TERM_PARAMS])]

        selects = []
        for owner, column in columns:
            found = select(owner.label('id'))\
                .where(term.condition(dialect, column))
            for ids in narrowed:
                found = found.where(owner.in_(ids))
            selects.append(found)

        # Searched once as a table of its own (see _search())
        found = union(*selects).subquery() if len(selects) > 1 \
            else selects[0].subquery()

        found = Collection.id.in_(select(found.c.id))
        return ~found if term.negate else found

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
        # from the NZB file (ie: by newznab) is the same as ours; they are
        # written as they are read (so a collection of any size is never
        # held in memory).  Number each segment by its part so a missing
        # part leaves a gap
        return nzb.save(path, numbering=NZBSegmentNumber.Article,
                        posts=self._nzb_posts(session, collection, work_dir))

    def _nzb_posts(self, session, collection, work_dir=None):
        """
        Yields each file of a collection as an NNTPSegmentedPost (for
        nzb()), in the order an NZB file lists them

        - the groups and articles of a page of files are read with one
          query each
        - a file with no articles is left out (an NZB file can't list it)
        """
        sort_no = 0
        for page in self._file_pages_in_order(
                session, collection.id, work_dir=work_dir):
            ids = [entry.id for entry in page]
            groups = {}
            for file_id, name in session.query(
                    FileGroup.file_id, FileGroup.name)\
                    .filter(FileGroup.file_id.in_(ids)):
                groups.setdefault(file_id, []).append(name)

            # One article per part; a part posted twice uses the lowest
            # Message-ID (so every database picks the same one)
            parts = {}
            for file_id, part, message_id, size in session.query(
                    Segment.file_id, Segment.part, Segment.message_id,
                    Segment.size)\
                    .filter(Segment.file_id.in_(ids))\
                    .order_by(Segment.file_id, Segment.part,
                              Segment.message_id):
                found = parts.setdefault(file_id, {})
                if part not in found:
                    found[part] = (message_id, size)

            for entry in page:
                if not parts.get(entry.id):
                    continue

                names = sorted(groups.get(entry.id, ()))
                post = NNTPSegmentedPost(
                    None, subject=entry.subject, poster=entry.poster,
                    groups=names, utc=entry.first_posted, work_dir=work_dir,
                    sort_no=sort_no)
                post.filename = entry.filename
                sort_no += 1

                for part, (message_id, size) in parts[entry.id].items():
                    article = NNTPArticle(
                        id=message_id, no=part, subject=entry.subject,
                        poster=entry.poster, groups=names, work_dir=work_dir)

                    # The article's content isn't downloaded; we only know
                    # its size
                    article.add(NNTPEmptyContent(
                        filepath=entry.filename, part=part, total_size=size,
                        work_dir=work_dir))
                    post.add(article)

                yield post

    def fetch_files(self, value=None, message_ids=None):
        """
        Returns what downloading a collection (by its id or GID), or some
        of the articles indexed, needs: (files, known)

        - files: [{'id', 'name', 'group', 'subject', 'poster', 'posted',
          'segments': [(part, message_id)], 'partial'}], in the order an
          NZB file lists them; posted is when its last article was, and
          partial is True when only some of its articles are listed.  A
          part posted twice uses the lowest Message-ID (as nzb() does).
        - known: {message_id: SegmentNote} for the articles a download
          learned about before
        - message_ids: only these articles, under the files they belong
          to (the ones that aren't indexed are left out)

        Both are empty when there is no such collection.
        """
        session = self.session()
        if session is None:
            return [], {}

        if message_ids is None:
            collection = self.get(value)
            if collection is None:
                return [], {}
            pages = self._file_pages_in_order(session, collection.id)
            wanted = None

        else:
            wanted = set(message_ids)
            file_ids = set()
            for ids in chunked(wanted):
                file_ids.update(file_id for file_id, in session.query(
                    Segment.file_id).filter(Segment.message_id.in_(ids)))

            entries = []
            for ids in chunked(file_ids):
                entries.extend(session.query(CollectionFile)
                               .filter(CollectionFile.id.in_(ids)))
            entries.sort(key=lambda entry: (
                entry.collection_id, entry.position is None,
                entry.position or 0, gid_order(entry.subject)))
            pages = chunked(entries)

        files, known = [], {}
        for chunk in pages:
            # The groups and segments of many files are read at once
            ids = [entry.id for entry in chunk]
            groups = dict(session.query(
                FileGroup.file_id, func.min(FileGroup.name))
                .filter(FileGroup.file_id.in_(ids))
                .group_by(FileGroup.file_id))

            rows = {}
            for row in session.query(
                    Segment.file_id, Segment.part, Segment.message_id,
                    Segment.kind)\
                    .filter(Segment.file_id.in_(ids))\
                    .order_by(Segment.file_id, Segment.part,
                              Segment.message_id):
                rows.setdefault(row.file_id, []).append(row)

            for entry in chunk:
                segments, seen, partial = [], set(), False
                for _, part, message_id, kind in rows.get(entry.id, ()):
                    if part in seen:
                        continue

                    if wanted is not None and message_id not in wanted:
                        partial = True
                        continue
                    seen.add(part)

                    segments.append((part, message_id))
                    if kind is not None:
                        known[message_id] = SegmentNote(kind)

                if segments:
                    files.append({
                        'id': entry.id, 'name': entry.filename,
                        'group': groups.get(entry.id),
                        'subject': entry.subject, 'poster': entry.poster,
                        'posted': entry.last_posted or entry.first_posted,
                        'segments': segments, 'partial': partial})

        return files, known

    @staticmethod
    def note(session, segments=None):
        """
        Writes what was learned of articles (see note_fetched()); committed
        as it goes.  A value not known (None) leaves the one noted before as
        it is, and a row that is gone (or was never indexed, ie: an article
        only cached) is skipped.

        - segments: {message_id: SegmentNote}
        """
        table = Segment.__table__
        statement = update(table).where(
            table.c.message_id == bindparam('row_id')).values(
            kind=func.coalesce(bindparam(
                'row_kind', type_=table.c.kind.type), table.c.kind))
        rows = [{'row_id': message_id, 'row_kind': note.kind}
                for message_id, note in (segments or {}).items()
                if note.kind is not None]

        # One statement for many rows
        for start in range(0, len(rows), NOTE_COMMIT_SIZE):
            session.execute(statement, rows[start:start + NOTE_COMMIT_SIZE])
            session.commit()

    def note_fetched(self, segments=None):
        """
        Keeps what a download learned (see NNTPFetcher), so running it
        again never fetches an article just to find out it carries a file;
        returns False (with a warning) if it couldn't be kept, which only
        means the next run fetches to find out again

        - segments: {message_id: SegmentNote}
        """
        session = None
        try:
            session = self.session()
            if session is None:
                return False

            self.note(session, segments)

        except (SQLAlchemyError, OSError) as e:
            if session is not None:
                try:
                    session.rollback()

                except (SQLAlchemyError, OSError):
                    pass

            logger.warning(
                'Could not note what was downloaded in the index (%s); the '
                'next run fetches to find out again.' % e)
            return False

        return True

    def delete(self, value, group_database=None):
        """
        Removes a collection (by its id or GID) and everything tied to it:
        its files and their articles, and what else is kept of those
        articles (see purge()).  What that took away is kept in `purged`.

        - it is marked as deleted first, so nothing new is indexed into it
          (and searches leave it out) while it is removed
        - its articles go a chunk at a time, each chunk together with what
          is kept of it elsewhere (and committed); if it fails, the chunk
          it was on is undone, and running it again finishes the job
        - the collection itself is kept (marked as deleted, with nothing in
          it) so articles that belong to it, and NZB files of it, aren't
          indexed again

        Returns True if the collection was removed.

        - group_database: see purge() (the header caches are left alone
          without it)
        """
        self.purged = Purged(0, 0, 0, 0, 0)
        collection = self.get(value, unfinished=True)
        if collection is None:
            logger.warning('There is no collection %s.' % value)
            return False

        session = self.session()
        try:
            self.purged = self._delete(session, collection.id, group_database)

        except BaseException:
            session.rollback()
            raise

        return True

    def _delete(self, session, collection_id, group_database):
        """
        Removes a collection (see delete()); returns what went (see Purged)
        """
        self.lock(session)
        session.query(Collection).filter(Collection.id == collection_id)\
            .update({Collection.state: CollectionState.Deleted},
                    synchronize_session=False)
        session.commit()

        headers = posts = articles = files = 0
        for file_ids in self._file_pages(session, collection_id):
            files += len(file_ids)
            more = True
            while more:
                # A chunk of their articles (one more is read to tell if
                # there are others); nobody else changes the index until it
                # is gone
                self.lock(session)
                ids = [m for m, in session.query(Segment.message_id)
                       .filter(Segment.file_id.in_(file_ids))
                       .limit(IN_LIST_SIZE + 1)]
                more = len(ids) > IN_LIST_SIZE
                del ids[IN_LIST_SIZE:]

                removed = (0, 0)
                if ids:
                    removed = self._purge_elsewhere(
                        session, ids, group_database)

                    # (the last chunk is all that is left of the files)
                    session.query(Segment).filter(
                        Segment.message_id.in_(ids) if more
                        else Segment.file_id.in_(file_ids))\
                        .delete(synchronize_session=False)

                session.commit()
                headers += removed[0]
                posts += removed[1]
                articles += len(ids)

        # Its files (with nothing left in them) and details
        self.lock(session)
        self._remove(session, collection_id)
        session.commit()

        # Forget everything read before now; some of it no longer exists
        session.expunge_all()
        return Purged(headers, posts, articles, files, 1)

    @staticmethod
    def _file_pages(session, collection_id, size=IN_LIST_SIZE):
        """
        Yields a collection's file ids a few at a time:

        - at most `size` files, holding about `size` articles between them
          (a file with more than that is a page of its own)
        - each page is read after the last one was used, so what is done
          with a page (and committed) in between is fine
        """
        last = None
        while True:
            query = session.query(CollectionFile.id, CollectionFile.segments)\
                .filter(CollectionFile.collection_id == collection_id)
            if last is not None:
                query = query.filter(CollectionFile.id > last)

            rows = query.order_by(CollectionFile.id).limit(size).all()
            if not rows:
                return

            yield from NNTPIndexer._paged(rows, size=size, articles=size)
            last = rows[-1][0]

    def purge(self, message_ids, group_database=None, groups=()):
        """
        Removes articles (by Message-ID, any number of them) everywhere
        they are kept, a chunk at a time; returns what went (see Purged):

        - their cached headers, in each group the index or the archive
          knows they were in (and in `groups`)
        - the posts the archive kept of them (with their files, groups and
          PGP ties)
        - their scores and tags
        - them in the index (see forget_articles()): a file left with
          nothing goes, and so does a collection left with no files

        - group_database: a function given a group's name that returns
          where its header cache is (ie: NNTPSettings.group_database); the
          header caches are left alone without it
        - groups: more groups whose header caches they are taken out of
          (ie: the ones a search found them in)

        If it fails, the chunk it was on is undone (the ones before it stay
        removed).
        """
        session = self.session()
        totals = [0] * len(Purged._fields)
        try:
            for ids in id_chunks(message_ids):
                if session is None:
                    removed = self._purge_headers(
                        group_database, set(groups), ids)

                else:
                    removed = self._purge_elsewhere(
                        session, ids, group_database, groups)
                    session.commit()

                removed += self.forget_articles(ids)
                totals = [
                    total + count for total, count in zip(totals, removed)]

        except BaseException:
            if session is not None:
                session.rollback()
            raise

        return Purged(*totals)

    def _purge_elsewhere(self, session, ids, group_database, groups=()):
        """
        Removes what is kept of articles outside of the index's files;
        returns (headers, posts) removed:

        - their cached headers go first (they are in other databases)
        - then the posts the archive kept, and their scores and tags (not
          committed), so a stop in between finds them all again next time
        """
        # The groups they were in, read before what says so goes
        names = set(groups)
        names.update(name for name, in session.query(FileGroup.name)
                     .join(Segment, Segment.file_id == FileGroup.file_id)
                     .filter(Segment.message_id.in_(ids)).distinct())
        names.update(name for name, in session.query(ArchiveGroup.name)
                     .filter(ArchiveGroup.message_id.in_(ids)).distinct())

        headers = self._purge_headers(group_database, names, ids)[0]
        posts = session.query(ArchivedPost)\
            .filter(ArchivedPost.message_id.in_(ids)).count()
        self.forget_posts(session, ids)
        NNTPArticleRanks.forget(session, ids)
        return headers, posts

    @staticmethod
    def _purge_headers(group_database, names, ids):
        """
        Removes the cached headers of articles from the header caches of
        the groups named (the ones there are); returns (headers, 0)
        """
        headers = 0
        if group_database is None:
            return headers, 0

        for name in sorted(names):
            path = group_database(name)
            if not isfile(path):
                # Nothing was ever cached for it
                continue

            cache = NNTPGroupDatabase.from_file(path)
            try:
                headers += cache.forget(ids)

            finally:
                cache.close()

        return headers, 0

    @classmethod
    def _remove(cls, session, collection_id):
        """
        Removes a collection's files and marks it as deleted (not committed)
        """
        values = {'id': collection_id}
        cls._clear(session, [collection_id])
        session.execute(text(
            'DELETE FROM collection_meta WHERE collection_id = :id'), values)

        # Its posting time is kept so what belongs to it is still recognised
        session.execute(text(
            'UPDATE collection SET state = :deleted, files = 0, '
            'segments = 0, size = 0 WHERE id = :id'),
            dict(values, deleted=CollectionState.Deleted))

    @classmethod
    def _clear(cls, session, collection_ids):
        """
        Removes the files of the collections given (and their articles and
        groups), not committed

        - collection_ids: a chunk of them (see IN_LIST_SIZE)

        Only a page of files is removed at a time (see _paged()), and their
        articles about ARTICLE_PAGE_SIZE at a time: removing many rows with
        one statement makes SQLite hold every one of them in memory.
        """
        while True:
            # (in no order: what is read is removed, so the next read finds
            # the rest)
            rows = session.query(CollectionFile.id, CollectionFile.segments)\
                .filter(CollectionFile.collection_id.in_(collection_ids))\
                .limit(IN_LIST_SIZE).all()
            if not rows:
                return

            for ids in cls._paged(rows):
                cls._clear_segments(session, ids)
                session.query(FileGroup)\
                    .filter(FileGroup.file_id.in_(ids))\
                    .delete(synchronize_session=False)
                session.query(CollectionFile)\
                    .filter(CollectionFile.id.in_(ids))\
                    .delete(synchronize_session=False)

    @staticmethod
    def _clear_segments(session, file_ids):
        """
        Removes the articles of the files given (a page of them), at most
        ARTICLE_PAGE_SIZE with each statement; not committed
        """
        mine = Segment.file_id.in_(file_ids)
        if session.get_bind().dialect.name == 'mysql':
            # (MySQL can't limit a list read for IN (...), but it can
            # limit what a DELETE removes)
            statement = delete(Segment).where(mine)\
                .with_dialect_options(mysql_limit=ARTICLE_PAGE_SIZE)

        else:
            statement = delete(Segment).where(Segment.message_id.in_(
                select(Segment.message_id).where(mine)
                .limit(ARTICLE_PAGE_SIZE)))

        # Until a statement finds less than it could have removed
        while session.execute(
                statement, execution_options={
                    'synchronize_session': False}).rowcount \
                >= ARTICLE_PAGE_SIZE:
            pass

    def forget_group(self, group, before=None):
        """
        Removes what was found in a group (as if it was never indexed):
        - the scanned files only it had (and their articles), and the
          collections that leaves empty (ones removed with nr delete stay
          removed)
        - the posts kept from it that no other group has
        - their scores and tags, and how far the group was seen

        With before (a UTC datetime), only the files and posts posted
        before then go; how far the group was seen stays.

        Files other groups have too, and anything read from an NZB file,
        are kept.  Returns (files, collections, posts) removed.
        """
        from .NNTPArticleRanks import NNTPArticleRanks
        from .objects.index.NzbImport import NzbImport

        session = self.session()
        if session is None:
            # Nothing was ever indexed
            return 0, 0, 0

        # A chunk at a time (committed as it goes), so memory stays the same
        # however big the group is.  Posts go first: a score is only let go
        # of once neither a kept post nor an indexed article has it.
        posts = 0
        while True:
            ids = [m for m, in self._group_posts(session, group, before)
                   .limit(IN_LIST_SIZE)]
            if not ids:
                break

            session.query(ArchiveGroup)\
                .filter(ArchiveGroup.name == group)\
                .filter(ArchiveGroup.message_id.in_(ids))\
                .delete(synchronize_session=False)

            # The posts kept from it (and nowhere else)
            others = {m for m, in session.query(ArchiveGroup.message_id)
                      .filter(ArchiveGroup.message_id.in_(ids))}
            lost = [m for m in ids if m not in others]
            self.forget_posts(session, lost)

            indexed = {m for m, in session.query(Segment.message_id)
                       .filter(Segment.message_id.in_(lost))}
            NNTPArticleRanks.forget(
                session, [m for m in lost if m not in indexed])
            posts += len(lost)
            session.commit()

        # The scanned files found in it
        files = 0
        collections = set()
        while True:
            ids = [file_id for file_id, in self._group_files(
                session, group, before).limit(IN_LIST_SIZE)]
            if not ids:
                break

            session.query(FileGroup)\
                .filter(FileGroup.name == group)\
                .filter(FileGroup.file_id.in_(ids))\
                .delete(synchronize_session=False)

            # The ones no other group has
            kept = {file_id for file_id, in session.query(FileGroup.file_id)
                    .filter(FileGroup.file_id.in_(ids))}
            lost = [file_id for file_id in ids if file_id not in kept]
            if lost:
                collections.update(c for c, in session.query(
                    CollectionFile.collection_id)
                    .filter(CollectionFile.id.in_(lost)))

                # Their articles, a chunk at a time too
                gone = [m for m, in session.query(Segment.message_id)
                        .filter(Segment.file_id.in_(lost))]
                for chunk in chunked(gone):
                    still = {m for m, in session.query(ArchivedPost.message_id)
                             .filter(ArchivedPost.message_id.in_(chunk))}
                    NNTPArticleRanks.forget(
                        session, [m for m in chunk if m not in still])
                    session.query(NzbImport)\
                        .filter(NzbImport.message_id.in_(chunk))\
                        .delete(synchronize_session=False)

                session.query(Segment)\
                    .filter(Segment.file_id.in_(lost))\
                    .delete(synchronize_session=False)
                session.query(CollectionFile)\
                    .filter(CollectionFile.id.in_(lost))\
                    .delete(synchronize_session=False)
                files += len(lost)

            session.commit()

        self._recount(session, (), collections)

        # The collections nothing is left of (children first; the database
        # servers enforce their links)
        emptied = []
        for ids in chunked(sorted(collections)):
            emptied.extend(c for c, in session.query(Collection.id)
                           .filter(Collection.id.in_(ids))
                           .filter(Collection.files == 0)
                           .filter(Collection.state !=
                                   CollectionState.Deleted))

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

        if before is None:
            session.query(GroupClock)\
                .filter(GroupClock.name == group)\
                .delete(synchronize_session=False)
        session.commit()

        # Forget everything read before now; some of it no longer exists
        session.expunge_all()
        return files, len(emptied), posts

    def count_group(self, group, before=None):
        """
        Returns (files, posts) forget_group() would let go of in a group
        (files other groups have too are counted; they only lose the group)
        """
        session = self.session()
        if session is None:
            return 0, 0

        return (self._group_files(session, group, before).count(),
                self._group_posts(session, group, before).count())

    @staticmethod
    def _group_files(session, group, before=None):
        """
        Returns a query of the scanned files found in a group (only the
        ones posted before `before` when given)
        """
        query = session.query(FileGroup.file_id)\
            .join(CollectionFile, CollectionFile.id == FileGroup.file_id)\
            .join(Collection, Collection.id == CollectionFile.collection_id)\
            .filter(FileGroup.name == group)\
            .filter(Collection.source == CollectionSource.Scan)
        if before is not None:
            query = query.filter(CollectionFile.last_posted < before)

        return query

    @staticmethod
    def _group_posts(session, group, before=None):
        """
        Returns a query of the posts kept from a group (only the ones
        posted before `before` when given)
        """
        query = session.query(ArchiveGroup.message_id)\
            .filter(ArchiveGroup.name == group)
        if before is not None:
            query = query.join(
                ArchivedPost,
                ArchivedPost.message_id == ArchiveGroup.message_id)\
                .filter(ArchivedPost.posted < before)

        return query

    def import_nzb(self, path, name=None, merge=False, work_dir=None):
        """
        Adds the post an NZB file describes as a collection; see
        _import_nzb() for how, and what is returned.

        - work_dir: where its temporary files go (the system's temporary
          folder by default)

        The index is ours until it is done: what it did is committed (or
        undone if it fails), so other workers can carry on.
        """
        try:
            found = self._import_nzb(
                path, name=name, merge=merge, work_dir=work_dir)

        except BaseException:
            if self._db is not None:
                self._db.session().rollback()
            raise

        if self._db is not None:
            self._db.session().commit()

        return found

    def _import_nzb(self, path, name=None, merge=False, work_dir=None):
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
          (counted in `left_out_count`)
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
        - left_out_count: how many files were not added (without merge)
        - left_out: the subjects of the first of them (LEFT_OUT_SAMPLES)
        - merging: True if it replaced collections we had (with merge)
        - merged / dropped: how many files that collection gained / lost
        - replaced_count: how many other collections it replaced (removed)
        - replaced: the ids of the first of them (ID_SAMPLES)
        """
        self.details = 0
        self.merging = False
        self.merged = 0
        self.dropped = 0
        self.replaced = []
        self.replaced_count = 0
        self.left_out = []
        self.left_out_count = 0

        # The NZB file is read more than once.  A copy of it is read
        # instead, so every read sees the same file even if it is changed
        # or replaced while it is imported
        source = self._copy_nzb(path, work_dir)
        if source is None:
            return None, False

        # Every article it lists, once each, and the collections (from other
        # NZB files) that have any of them, kept until we're done (on disk
        # once there are many of them)
        spools = []
        try:
            for _ in range(2):
                spools.append(
                    SpooledTemporaryFile(max_size=SPOOL_SIZE, dir=work_dir))

            return self._import_copy(
                path, source, name, merge, work_dir, *spools)

        finally:
            for spool in spools:
                spool.close()

            try:
                unlink(source)

            except OSError:
                pass

    @staticmethod
    def _copy_nzb(path, work_dir=None):
        """
        Returns the path of a copy of an NZB file (in work_dir) that only
        we use, or None if it couldn't be copied
        """
        try:
            with open(path, 'rb') as original:
                # (named after the original, so its messages make sense)
                with NamedTemporaryFile(
                        dir=work_dir, prefix='%s.' % basename(path)[:64],
                        suffix='.nzb', delete=False) as copy:
                    try:
                        copyfileobj(original, copy)

                    except OSError:
                        unlink(copy.name)
                        raise

                    return copy.name

        except OSError as e:
            logger.warning("'%s' could not be read: %s" % (path, e))
            return None

    def _import_copy(self, path, source, name, merge, work_dir, spool,
                     owners):
        """
        Imports an NZB file (see _import_nzb()) from our copy of it

        - path: the NZB file (its name and messages)
        - source: our copy of it (what is read)
        - spool: an empty file to keep its Message-IDs in
        - owners: an empty file to keep the ids of the collections that
          share them in
        """
        nzb = NNTPnzb(source)
        if not nzb.is_valid():
            logger.warning("'%s' is not a valid NZB file." % path)
            return None, False

        def posts():
            # Every file with its articles (lowest part first), in the order
            # the NZB file lists them; each pass reads the file again, a
            # file at a time (so an NZB file of any size is never held)
            for post in NNTPnzb(source):
                articles = list(post)
                if articles:
                    yield post, articles

        def message_ids():
            # Every article it lists, once each, as they were kept
            return self._replay(spool)

        first = next(posts(), None)
        if first is None:
            logger.warning("'%s' lists no articles." % path)
            return None, False

        # Its articles are sorted (see sorted_in_runs()) so one listed twice
        # is noticed, and kept so they are only read and sorted once
        listed = 0
        last = None
        for message_id in sorted_in_runs(
                (message_id.strip().strip('<>')
                 for message_id in nzb.message_ids()),
                work_dir=work_dir):
            if message_id != last:
                last = message_id
                marshal.dump(message_id, spool)
                listed += 1

        # Its GID is the one newznab reads from it: the first segment of the
        # first file, as the NZB file lists them
        gid = nzb.gid() or make_gid(first[1][0].id)
        poster = (first[0].poster or '')[:128]
        first = None

        session = self.session(create=True)

        # Nobody else changes the index until we're done
        self.lock(session)

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

        # The collections (from other NZB files) that have its articles.
        # The one with its GID counts even if it has none of them; the
        # others are kept in `owners` (by id) to be read again
        mine = existing.id if existing is not None \
            and existing.source == CollectionSource.Nzb else None
        shared = others = 0
        best = None
        shown = [mine] if mine is not None else []
        for collection_id, count in self._owners(
                session, message_ids(), work_dir):
            if collection_id == mine:
                shared = count
                continue

            marshal.dump(collection_id, owners)
            others += 1

            # The one sharing the most (the lowest id of those)
            if best is None or count > best[1]:
                best = (collection_id, count)

            if len(shown) < ID_SAMPLES:
                shown.append(collection_id)

        if merge and (mine is not None or others):
            # It replaces them: the one with its GID (else the one sharing
            # the most) keeps its id
            target = mine if mine is not None else best[0]
            return self._replace(
                session, target, lambda: (
                    i for i in self._replay(owners) if i != target),
                nzb, posts, gid, message_ids, poster, work_dir)

        total = others + (mine is not None)
        if total > 1:
            more = ' and %d more' % (total - len(shown)) \
                if total > len(shown) else ''
            logger.error(
                "'%s' shares articles with collections %s%s; it was not "
                "imported (use --merge to replace them with it)." % (
                    path, ', '.join('#%d' % i for i in sorted(shown)), more))
            return None, False

        if total:
            collection_id, shared = (mine, shared) if mine is not None \
                else best
            return self._overlap(
                session, session.get(Collection, collection_id), shared,
                listed, nzb, posts, gid, path)

        # Take its articles from the scanned collections that have them; a
        # scanned collection it covers completely becomes it
        kept = self._claim(
            session, message_ids(),
            prefer=existing.id if existing is not None else None,
            work_dir=work_dir)
        collection = session.get(Collection, kept) \
            if kept is not None else None

        if existing is not None and existing is not collection:
            # It keeps what the NZB file doesn't list, and gets a GID of its
            # own once it forms again
            existing.gid = None
            existing.state = CollectionState.Forming
            session.flush()

        if collection is None:
            collection = Collection()
            session.add(collection)

        collection.key = 'nzb\n%s' % gid
        collection.gid = gid
        collection.source = CollectionSource.Nzb
        collection.poster = poster
        collection.name = (
            name or nzb.title() or splitext(basename(path))[0])[:256]
        session.flush()

        # What the NZB file says about it
        session.query(CollectionMeta)\
            .filter(CollectionMeta.collection_id == collection.id).delete()
        session.add_all(
            CollectionMeta(collection_id=collection.id, type=key, value=value)
            for key, value in self._meta(nzb))

        collection.files_expected = \
            self._add_files(session, collection, posts())[1]
        self._formed(session, collection)

        return collection, True

    @staticmethod
    def _meta(nzb):
        """
        Returns the (type, value) of every detail an NZB file gives (in its
        order, without repeats)
        """
        # (a dict keeps their order and finds a repeat at once)
        meta = {}
        for key, values in (nzb.meta or {}).items():
            for value in (values if isinstance(values, list) else [values]):
                meta.setdefault((str(key)[:64], str(value)[:1024]))

        return list(meta)

    @classmethod
    def _owners(cls, session, message_ids, work_dir=None):
        """
        Yields (collection id, how many of the articles it has) for each
        collection (from an NZB file) that has any of the articles given,
        sorted by id; only a few are held at a time (see _summed())

        - message_ids: each one only once
        """
        def found():
            # (collection id, how many it has) for each chunk
            for ids in chunked(message_ids):
                counts = {}
                for _, _, collection_id, source in cls._holders(
                        session, ids):
                    if source == CollectionSource.Nzb:
                        counts[collection_id] = \
                            counts.get(collection_id, 0) + 1

                yield from counts.items()

        return cls._summed(found(), work_dir)

    @staticmethod
    def _summed(counts, work_dir=None):
        """
        Adds counts up: yields (id, its total) for each id once, sorted by
        id (see sorted_in_runs(), which only holds a few at a time)

        - counts: (id, count) for each, any id any number of times
        """
        last, total = None, 0
        for key, count in sorted_in_runs(counts, work_dir=work_dir):
            if key != last:
                if last is not None:
                    yield last, total
                last, total = key, 0
            total += count

        if last is not None:
            yield last, total

    @staticmethod
    def _replay(spool):
        """
        Yields what was kept in a spool (with marshal), in order; each
        reader keeps its own place, so several can read it at once
        """
        where = 0
        while True:
            spool.seek(where)
            try:
                item = marshal.load(spool)

            except EOFError:
                return

            where = spool.tell()
            yield item

    @staticmethod
    def _holders(session, ids):
        """
        Yields (message_id, file_id, collection_id, source) for each of the
        articles given that is indexed

        - ids: a chunk of Message-IDs (see IN_LIST_SIZE)

        Only the articles given are read.  Leave it to the caller to pick
        the collections it wants: asked for in the query, SQLite reads
        every article those collections have instead, for each chunk.
        """
        return session.query(
            Segment.message_id, Segment.file_id,
            CollectionFile.collection_id, Collection.source)\
            .join(CollectionFile, CollectionFile.id == Segment.file_id)\
            .join(Collection, Collection.id == CollectionFile.collection_id)\
            .filter(Segment.message_id.in_(ids))

    @staticmethod
    def _file_keys(session, collection_id):
        """
        Yields the keys of a collection's files, read SORT_RUN_SIZE at a
        time (by id, so each read picks up where the last one ended)
        """
        last = 0
        while True:
            rows = session.query(CollectionFile.id, CollectionFile.key)\
                .filter(CollectionFile.collection_id == collection_id)\
                .filter(CollectionFile.id > last)\
                .order_by(CollectionFile.id)\
                .limit(SORT_RUN_SIZE).all()
            if not rows:
                return

            for _, key in rows:
                yield key

            last = rows[-1][0]
            rows = None

    @staticmethod
    def _differences(old, new):
        """
        Returns (added, removed): how many entries only `new` has, and how
        many only `old` has

        - old / new: sorted iterators, each without repeats
        """
        added = removed = 0
        end = object()
        old, new = iter(old), iter(new)
        a, b = next(old, end), next(new, end)
        while a is not end or b is not end:
            if b is end or (a is not end and a < b):
                removed += 1
                a = next(old, end)

            elif a is end or b < a:
                added += 1
                b = next(new, end)

            else:
                a, b = next(old, end), next(new, end)

        return added, removed

    def _overlap(self, session, collection, shared, listed, nzb, posts,
                 gid, path):
        """
        Handles an NZB file sharing articles with a collection we have from
        another one, when it isn't merged (see import_nzb())

        - shared: how many of the NZB file's articles the collection has
        - listed: how many articles the NZB file lists (each one once)
        - posts: reads the NZB file's files (see import_nzb())
        """
        # Every article the collection has
        total = session.query(Segment.message_id)\
            .join(CollectionFile, CollectionFile.id == Segment.file_id)\
            .filter(CollectionFile.collection_id == collection.id).count()

        if shared < listed and shared < total:
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
        # added when merging.  A chunk of files at a time is checked
        for chunk in self._post_chunks(posts()):
            found = set()
            for ids in id_chunks(
                    article.id for _, (_, articles) in chunk
                    for article in articles):
                found.update(
                    message_id for message_id, _, collection_id, _ in
                    self._holders(session, ids)
                    if collection_id == collection.id)

            for _, (post, articles) in chunk:
                if all(article.id.strip().strip('<>') in found
                       for article in articles):
                    continue

                # Only the first few are named
                self.left_out_count += 1
                if len(self.left_out) < LEFT_OUT_SAMPLES:
                    self.left_out.append(post.subject or '')

            # Let go of this chunk before the next one is read
            chunk = found = None

        session.commit()
        return collection, False

    def _replace(self, session, target, others, nzb, posts, gid,
                 message_ids, poster, work_dir=None):
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

        - target: the id of the collection that becomes it
        - others: returns the ids of the others (sorted, each once) each
          time it is called; only a chunk of them is held at a time
        - posts / message_ids: read the NZB file's files / Message-IDs
          (see import_nzb())
        - poster: who posted it
        - work_dir: where long lists are kept while they're sorted
        """
        self.merging = True

        # The files it had (to report what changed), sorted (see
        # sorted_in_runs()); asking for the first one reads them all now,
        # before they're let go of
        before = sorted_in_runs(
            self._file_keys(session, target), work_dir=work_dir)
        before = chain(list(islice(before, 1)), before)

        # Every detail: ours, then what the others had (a chunk of them at
        # a time, in order), then the NZB file's
        for chunk in chunked(others()):
            self._merge_meta(session, target, [
                (key, value) for key, value in session.query(
                    CollectionMeta.type, CollectionMeta.value)
                .filter(CollectionMeta.collection_id.in_(chunk))
                .order_by(CollectionMeta.collection_id, CollectionMeta.id)])

        self._merge_meta(session, target, self._meta(nzb), replace=True)

        # What it had is let go of; the NZB file lists what it is now
        self._clear(session, [target])
        self._aliases(session, target, [
            old for old, in session.query(Collection.gid)
            .filter(Collection.id == target)], gid)

        # The others go, a chunk at a time
        for chunk in chunked(others()):
            old_gids = [old for old, in session.query(Collection.gid)
                        .filter(Collection.id.in_(chunk))]

            self._clear(session, chunk)
            session.query(CollectionMeta)\
                .filter(CollectionMeta.collection_id.in_(chunk))\
                .delete(synchronize_session=False)

            # Anything that found them finds ours now
            session.query(CollectionAlias)\
                .filter(CollectionAlias.collection_id.in_(chunk))\
                .update({CollectionAlias.collection_id: target},
                        synchronize_session=False)

            session.query(Collection)\
                .filter(Collection.id.in_(chunk))\
                .delete(synchronize_session=False)

            self._aliases(session, target, old_gids, gid)

            self.replaced_count += len(chunk)
            self.replaced.extend(chunk[:ID_SAMPLES - len(self.replaced)])

        # Its own GID is its own
        session.query(CollectionAlias)\
            .filter(CollectionAlias.gid == gid)\
            .delete(synchronize_session=False)
        session.flush()

        # Take its articles from the scanned collections that have them
        kept = self._claim(session, message_ids(), work_dir=work_dir)
        if kept is not None:
            # Nothing is left of it; it was all a part of this post
            session.query(Collection)\
                .filter(Collection.id == kept).delete()

        collection = session.get(Collection, target)
        collection.key = 'nzb\n%s' % gid
        collection.gid = gid
        collection.source = CollectionSource.Nzb
        collection.poster = poster
        if nzb.title():
            collection.name = nzb.title()[:256]
        session.flush()

        collection.files_expected = \
            self._add_files(session, collection, posts())[1]

        self.merged, self.dropped = self._differences(
            before, sorted_in_runs(
                self._file_keys(session, target), work_dir=work_dir))

        self._formed(session, collection)
        return collection, True

    @staticmethod
    def _aliases(session, target, gids, gid):
        """
        Lets the GIDs given (a chunk of them) find a collection from now on
        (not committed); its own GID, and a GID that already finds one, are
        left alone

        - target: the collection's id
        - gid: its own GID
        """
        gids = set(gids) - {gid, None}
        if not gids:
            return

        known = {alias for alias, in session.query(CollectionAlias.gid)
                 .filter(CollectionAlias.gid.in_(list(gids)))}
        session.add_all(
            CollectionAlias(gid=old, collection_id=target)
            for old in sorted(gids - known))
        session.flush()

    def _merge_meta(self, session, collection_id, meta, replace=False):
        """
        Adds the details given (type, value) that a collection doesn't have
        (not committed)

        - a type it doesn't have (ie: a password) is added
        - a type expected to repeat (see NZB_META_REPEATS) gets the new
          values added to the ones it has
        - any other type keeps its value, unless replace is set
        """
        # type -> its values, in order (a dict finds one at once)
        have = {}
        for key, value in session.query(
                CollectionMeta.type, CollectionMeta.value)\
                .filter(CollectionMeta.collection_id == collection_id)\
                .order_by(CollectionMeta.id):
            have.setdefault(key, {})[value] = None

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
                            collection_id, key, next(iter(have[key])),
                            value))
                    continue

                session.query(CollectionMeta)\
                    .filter(CollectionMeta.collection_id == collection_id)\
                    .filter(CollectionMeta.type == key).delete()
                have[key] = {}
                replaced.add(key)

            session.add(CollectionMeta(
                collection_id=collection_id, type=key, value=value))
            have.setdefault(key, {})[value] = None
            self.details += 1

        session.flush()

    def _add_files(self, session, collection, posts, position=0):
        """
        Adds the files (and their articles) from an NZB file to a collection
        (not committed)

        - posts: (NNTPSegmentedPost, its articles) for each file (ie: a
          generator; only a chunk of them is held at a time)
        - position: where the first one goes in the collection

        Returns (added, listed):
        - added: how many new files were added (the others already
          belonged to it, and only got more articles)
        - listed: how many files were given
        """
        added = listed = 0

        # A chunk of posts at a time: the files they belong to are looked
        # up with one query, the new ones (and their articles) are saved
        # together, and their totals are counted again
        for chunk in self._post_chunks(posts, position):
            listed += len(chunk)
            segments = []
            groups = []
            found = []
            for position, (post, articles) in chunk:
                subject = post.subject or ''
                info = parse_subject(subject)
                found.append((position, post, articles, subject, info,
                              '%d\n%s' % (collection.id, info['base'])))

            # Files whose subjects only differ by their counter are one
            # file (an earlier chunk may have added it already)
            files = {entry.key: entry for entry in session.query(
                CollectionFile).filter(CollectionFile.key.in_(
                    list({key for *_, key in found})))}
            dates = {}

            for position, post, articles, subject, info, key in found:
                if key not in files:
                    files[key] = CollectionFile(
                        key=key, collection_id=collection.id,
                        position=position, subject=subject[:256],
                        poster=(post.poster or '')[:128],
                        filename=(info['filename'] or release_name(
                            info['desc']) or subject)[:256],
                        file_no=info['file_no'], segments_expected=0)
                    session.add(files[key])
                    added += 1

            # The new files get their ids
            session.flush()

            for position, post, articles, subject, info, key in found:
                entry = files[key]
                entry.segments_expected = max(
                    entry.segments_expected or 0, info['parts'],
                    len(articles))
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
                session, [entry.id for entry in files.values()], [], dates)

            # Let go of this chunk before the next one is read
            chunk = found = segments = groups = files = dates = None

        self._recount(session, [], [collection.id])
        return added, listed

    @staticmethod
    def _post_chunks(posts, position=0):
        """
        Splits the files of an NZB file into chunks (lists) to work on
        together, each numbered by where it is in the NZB file

        - posts: (NNTPSegmentedPost, its articles) for each file
        - position: the number of the first one
        - a chunk holds at most IN_LIST_SIZE files, with about
          ARTICLE_PAGE_SIZE articles between them
        - yields lists of (position, (post, articles))
        """
        chunk, held = [], 0
        for numbered in enumerate(posts, start=position):
            chunk.append(numbered)
            held += len(numbered[1][1])
            if len(chunk) >= IN_LIST_SIZE or held >= ARTICLE_PAGE_SIZE:
                yield chunk
                chunk, held = [], 0

        if chunk:
            yield chunk

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

    def _claim(self, session, message_ids, prefer=None, work_dir=None):
        """
        Takes the articles given away from the scanned collections that
        have them (not committed)

        - the files they leave empty are removed
        - what those collections have left is counted again; their GID is
          worked out again once they form (see settle())
        - the collections left with nothing are removed, all but one: the
          one preferred (when it is one of them), else the one that lost
          the most articles.  Its id is returned (None when no collection
          was left with nothing).

        Only a chunk of articles, and of collections, is held at a time.
        """
        def taken():
            # Lets go of them a chunk at a time (articles an NZB file listed
            # stay where they are); yields (collection id, how many it
            # lost) for each chunk
            for ids in chunked(message_ids):
                # (a Message-ID listed twice is only looked for once)
                ids = list(dict.fromkeys(ids))
                found = []
                files = set()
                lost = {}
                for message_id, file_id, collection_id, source in \
                        self._holders(session, ids):
                    if source != CollectionSource.Scan:
                        continue

                    files.add(file_id)
                    lost[collection_id] = lost.get(collection_id, 0) + 1
                    found.append(message_id)

                if not found:
                    continue

                session.query(Segment)\
                    .filter(Segment.message_id.in_(found))\
                    .delete(synchronize_session=False)

                # Count what their files have left, and remove the ones
                # left with nothing
                self._recount(session, files, [])
                for chunk in chunked(files):
                    empty = [file_id for file_id, in session.query(
                        CollectionFile.id)
                        .filter(CollectionFile.id.in_(chunk))
                        .filter(CollectionFile.segments == 0)]

                    if empty:
                        session.query(FileGroup)\
                            .filter(FileGroup.file_id.in_(empty))\
                            .delete(synchronize_session=False)
                        session.query(CollectionFile)\
                            .filter(CollectionFile.id.in_(empty))\
                            .delete(synchronize_session=False)

                yield from lost.items()

        # The collection kept, as (not preferred, -lost, id): the lowest wins
        kept = None

        # Each collection that lost articles, once, with how many it lost in
        # all (see _summed())
        for chunk in chunked(self._summed(taken(), work_dir)):
            lost = dict(chunk)
            self._recount(session, [], lost)

            # They have to form again (with a GID of their own)
            session.query(Collection)\
                .filter(Collection.id.in_(list(lost)))\
                .update({Collection.gid: None,
                         Collection.state: CollectionState.Forming},
                        synchronize_session=False)

            # Anything read before now is out of date
            session.expire_all()

            # The ones left with nothing (they were all a part of this
            # post), and the one kept so far
            emptied = [
                (collection_id != prefer, -lost[collection_id], collection_id)
                for collection_id, in session.query(Collection.id)
                .filter(Collection.id.in_(list(lost)))
                .filter(Collection.files == 0)]
            if not emptied:
                continue

            if kept is not None:
                emptied.append(kept)

            # All but the one kept go, with one statement
            kept = min(emptied)
            session.query(Collection)\
                .filter(Collection.id.in_(
                    [found[2] for found in emptied if found != kept]))\
                .delete()

        return kept[2] if kept is not None else None

    def __str__(self):
        """
        A short summary of our last index()
        """
        dropped = ', %d dropped by filters' % self.dropped \
            if self.dropped else ''

        if not self.articles:
            return 'nothing new to index%s' % dropped

        return '%d article(s) indexed; %d new file(s) in %d new ' \
            'collection(s)%s' % (
                self.articles, self.files, self.collections, dropped)

    def __repr__(self):
        """
        Return an unambiguous version of the object
        """
        return '<NNTPIndexer db_file="%s" />' % self.db_file
