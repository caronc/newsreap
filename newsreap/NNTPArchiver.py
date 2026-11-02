# Keeps small articles (ie: discussions) so they can be searched offline
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
Fetches the small articles of a group (the ones 'nr update search' cached
the headers of) and keeps what they say, so a group full of discussion can
be read and searched offline.

- Only articles no bigger than `max_size` are fetched (their size comes
  from XOVER, so nothing is fetched to find out).
- What is kept is worked out by hooks (see newsreap/hooks/archive/): text
  is decoded (base64, quoted-printable, its character set) so it can be
  searched; files (yEnc, uuencode, MIME attachments) are only noted.
  Hooks can be added (or the built in ones left out) to change this.
- Articles are kept in the index database, found by their Message-ID, so
  one posted to several groups is fetched and kept once.
- Work is saved a chunk at a time; stopping part way loses nothing.
"""

import gevent.monkey
gevent.monkey.patch_all()

import re
from collections import deque
from os.path import abspath
from os.path import dirname
from os.path import isfile
from os.path import join
from tempfile import gettempdir

from sqlalchemy import and_
from sqlalchemy import literal_column
from sqlalchemy import or_
from sqlalchemy import select
from sqlalchemy import text

from .HookManager import HookManager
from .NNTPArchivePost import NNTPArchivePost
from .NNTPConnectionRequest import NNTPConnectionRequest
from .NNTPFilterRule import tags_from_text
from .NNTPFilterRule import tags_to_text
from .NNTPSearchTerm import SEARCH_ANY
from .NNTPGroupDatabase import NNTPGroupDatabase
from .NNTPIndexDatabase import NNTPIndexDatabase
from .NNTPIndexDatabase import PG_POSTER_DOCUMENT
from .NNTPIndexDatabase import PG_POST_DOCUMENT
from .NNTPIndexer import FILENAME_CODECS
from .NNTPIndexer import NNTPIndexer
from .NNTPIndexer import chunked
from .NNTPIndexer import TERM_PARAMS
from .NNTPIndexer import keyword_terms
from .NNTPIndexer import parse_keywords
from .NNTPIndexer import rank_query
from .Utils import scan_pylib
from .Utils import strsize_to_bytes
from .Utils import utcnow
from .objects.group.Article import Article
from .objects.index.ArchiveFile import ArchiveFile
from .objects.index.ArchiveGroup import ArchiveGroup
from .objects.index.ArchivedPost import ArchiveKind
from .objects.index.ArchivedPost import ArchivedPost

# Logging
import logging
from newsreap.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)

# The biggest article fetched by default; text posts are almost always
# much smaller, files are usually much bigger
DEFAULT_ARCHIVE_MAX_SIZE = '64KB'

# How many cached articles are worked through (and saved) at a time
DEFAULT_ARCHIVE_CHUNK_SIZE = 500

# How many articles are asked for at once (across the connections)
DEFAULT_ARCHIVE_WINDOW = 10

# The kinds of posts searches return unless asked for others
ARCHIVE_SEARCHED = (ArchiveKind.Text, ArchiveKind.Binary)

# The words of a keyword (as the search indexes split text into words)
WORD_RE = re.compile(r'[^\W_]+')

# MySQL doesn't index words shorter than this (its
# innodb_ft_min_token_size); they are checked by looking through the text
MYSQL_MIN_WORD = 3



class NNTPArchiver:
    """
    Keeps small articles so they can be searched offline (see the module
    description)
    """

    # Where the built in hooks live
    default_hook_path = join(dirname(abspath(__file__)), 'hooks', 'archive')

    def __init__(self, db_file, hooks=None, default_hooks=True,
                 max_size=DEFAULT_ARCHIVE_MAX_SIZE,
                 chunk_size=DEFAULT_ARCHIVE_CHUNK_SIZE, filters=None):
        """
        Prepares an archiver

        - db_file: the index database (an SQLite file or a database URL;
          see NNTPSettings.index_database())
        - hooks: more hooks to use (modules, files or functions)
        - default_hooks: use the built in hooks (newsreap/hooks/archive/)
        - max_size: the biggest article fetched (ie: '64KB')
        - chunk_size: how many articles are worked through at a time
        - filters: the NNTPFilter that scores and tags articles (and drops
          the ones it no longer wants, without fetching them); without it,
          the scores and tags given when they were cached are used
        """
        self.db_file = db_file
        self.filters = filters or None

        # Rules that need an article's text (or file names) are checked again
        # once it is fetched
        self._after_fetch = self.filters is not None and (
            self.filters.uses('body') or self.filters.uses('filename'))
        self.max_size = strsize_to_bytes(max_size) \
            if isinstance(max_size, str) else int(max_size)
        if not self.max_size or self.max_size < 0:
            raise ValueError("'%s' is not a size we can use." % max_size)

        self.chunk_size = max(1, int(chunk_size))

        # The hooks that decide what is kept
        self.hooks = HookManager()
        if default_hooks:
            for name in sorted(scan_pylib(self.default_hook_path)):
                self.hooks.add(name, self.default_hook_path)

        if hooks:
            if callable(hooks) or isinstance(hooks, HookManager):
                self.hooks.add(hooks)

            else:
                self.hooks.add(hooks, ('.', self.default_hook_path))

        # What our last archive() did
        self.reset()

        # Our database (opened when first needed)
        self._db = None

    def reset(self):
        """
        Resets what our last archive() counted
        """
        self.fetched = 0
        self.texts = 0
        self.binaries = 0
        self.missing = 0
        self.refused = 0
        self.known = 0
        self.failed = 0
        self.dropped = 0

    def session(self, create=False):
        """
        Returns a session to the index database; None if it doesn't exist
        yet (unless `create` is set)
        """
        if self._db is None:
            if not create and not NNTPIndexDatabase.exists(self.db_file):
                # Nothing was ever kept
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

    def convert(self, post):
        """
        Works out what is kept of an article by running the hooks over it
        (see newsreap/hooks/archive/); returns the post given
        """
        if post.raw is None:
            # It couldn't be fetched
            post.kind = ArchiveKind.Missing
            return post

        # The real subject (its header can be encoded; the listing isn't
        # decoded)
        subject = post.header('Subject')
        if subject:
            post.subject = subject

        # Any hook can refuse it
        if any(response['result'] is False for response in
               self.hooks.call('pre_archive', post=post)):
            post.kind = ArchiveKind.Refused
            post.text = None
            post.files = []
            return post

        # Each one in turn fills in what it knows
        self.hooks.call('archive_convert', post=post)

        if post.kind == ArchiveKind.Text and post.text is None:
            # Nothing could make sense of it
            post.kind = ArchiveKind.Refused

        return post

    def store(self, session, post):
        """
        Keeps (or updates) a post in the index database (not committed)
        """
        entry = session.query(ArchivedPost)\
            .filter(ArchivedPost.message_id == post.message_id).first()

        if entry is None:
            entry = ArchivedPost(message_id=post.message_id)
            session.add(entry)

        entry.kind = post.kind
        entry.subject = (post.subject or '')[:256]
        entry.poster = (post.poster or '')[:128]
        entry.posted = post.posted
        entry.size = post.size or 0
        entry.references = post.header('References') or None
        entry.text = post.text
        entry.encoding = ','.join(post.encoding)[:64]
        entry.charset = (post.charset or '')[:32]
        entry.filenames = ' '.join(
            f['filename'] for f in post.files if f['filename']) or None
        entry.fetched = utcnow()
        entry.server = (post.server or '')[:128]
        entry.score = post.score
        entry.tags = tags_to_text(post.tags)

        # What its files say about themselves
        session.query(ArchiveFile)\
            .filter(ArchiveFile.message_id == post.message_id).delete()
        session.flush()
        session.add_all(
            ArchiveFile(message_id=post.message_id, **f) for f in post.files)

        # The groups it was found in
        NNTPIndexer._insert_ignore(session, ArchiveGroup, [
            {'message_id': post.message_id, 'name': str(group)[:128]}
            for group in post.groups])

    def archive(self, group, group_db_file, manager, work_dir=None,
                force=False, window=DEFAULT_ARCHIVE_WINDOW, server=''):
        """
        Fetches and keeps the small articles of a group that weren't kept
        before

        - group: the group's name
        - group_db_file: where its headers are cached (see
          NNTPSettings.group_database())
        - manager: the NNTPManager to fetch them with
        - work_dir: where articles are held while they are read
        - force: ask again for the articles no server had before
        - window: how many articles are asked for at once
        - server: the name noted as where they came from

        Returns True if it ran (False if nothing was ever cached for it).
        """
        self.reset()

        if not isfile(group_db_file):
            # Nothing was ever cached for this group
            return False

        session = self.session(create=True)
        cache = NNTPGroupDatabase.from_file(group_db_file)
        cached = cache.session()
        work_dir = work_dir or gettempdir()

        # Where we are (by article number)
        last = None

        try:
            while True:
                # The next small articles (all of them when forced, so the
                # missing ones are seen again)
                query = cached.query(
                    Article.message_id, Article.article_no,
                    Article.subject, Article.poster, Article.posted_date,
                    Article.size, Article.score, Article.tags)\
                    .filter(Article.size <= self.max_size)

                if not force:
                    query = query.filter(Article.archived.is_(False))

                if last is not None:
                    query = query.filter(Article.article_no > last)

                rows = query.order_by(Article.article_no)\
                    .limit(self.chunk_size).all()

                if not rows:
                    # We're done
                    break

                last = rows[-1].article_no

                # What we kept already (ie: from another group)
                ids = [row.message_id.strip().strip('<>') for row in rows]
                known = dict(session.query(
                    ArchivedPost.message_id, ArchivedPost.kind)
                    .filter(ArchivedPost.message_id.in_(ids)))

                # What our filters make of them (the ones they no longer want
                # are let go of instead of fetched)
                ranks = {}
                dropped = []
                for message_id, row in zip(ids, rows):
                    if self.filters is None:
                        ranks[message_id] = (
                            row.score or 0, tags_from_text(row.tags))
                        continue

                    verdict = self.filters.check(
                        group, subject=row.subject, poster=row.poster,
                        message_id=message_id, posted=row.posted_date,
                        size=row.size)

                    if verdict.drop is not None and message_id not in known:
                        dropped.append(row.message_id)
                        continue

                    ranks[message_id] = (verdict.score, verdict.tags)

                wanted = []
                for message_id, row in zip(ids, rows):
                    if message_id not in ranks:
                        # Dropped
                        continue

                    kind = known.get(message_id)
                    if kind is None or (
                            force and kind == ArchiveKind.Missing):
                        wanted.append((message_id, row))

                    else:
                        self.known += 1

                # Those only need this group noted
                NNTPIndexer._insert_ignore(session, ArchiveGroup, [
                    {'message_id': message_id, 'name': group[:128]}
                    for message_id in known])

                kept = []
                handled = [m for m, _ in zip(ids, rows) if m in known]
                for (message_id, row), (article, status) in self._fetch(
                        manager, wanted, work_dir, group, window):

                    if article is None and status != 'missing':
                        # Something went wrong (the server may still have
                        # it); it is asked for again next time
                        self.failed += 1
                        continue

                    handled.append(message_id)
                    post = NNTPArchivePost(
                        message_id, raw=None if article is None
                        else article.body.getvalue(),
                        subject=row.subject, poster=row.poster,
                        posted=row.posted_date, size=row.size,
                        groups={group}, server=server)

                    post.score, post.tags = ranks[message_id]
                    self.convert(post)

                    if self._after_fetch and post.kind != ArchiveKind.Missing:
                        # Rules on the text or file names can only be
                        # checked now that we have them
                        verdict = self.filters.check(
                            group, subject=post.subject, poster=post.poster,
                            message_id=message_id, posted=post.posted,
                            size=post.size, body=post.text,
                            filenames=[f['filename'] for f in post.files])

                        if verdict.drop is not None:
                            handled.remove(message_id)
                            dropped.append(row.message_id)
                            continue

                        post.score, post.tags = verdict.score, verdict.tags

                    self.store(session, post)
                    kept.append(post)
                    self._count(post)

                # Files whose articles were indexed get their real names
                NNTPIndexer.learn_filenames(session, {
                    post.message_id: post.files[0]['filename']
                    for post in kept if post.files and
                    post.files[0]['codec'] in FILENAME_CODECS and
                    post.files[0]['filename']})

                session.commit()

                # Let the hooks know what was kept
                for post in kept:
                    self.hooks.call('post_archive', post=post)

                # The articles our filters no longer want are let go of
                for chunk in chunked(dropped):
                    cached.query(Article)\
                        .filter(Article.message_id.in_(chunk))\
                        .delete(synchronize_session=False)
                self.dropped += len(dropped)

                # Mark them in the cache so they aren't looked at again
                cached.query(Article)\
                    .filter(Article.message_id.in_(handled))\
                    .update({Article.archived: True},
                            synchronize_session=False)
                cached.commit()

                logger.debug('Archived %s up to article %d.' % (group, last))

        finally:
            cached.close()
            cache.close()

        return True

    def _count(self, post):
        """
        Adds a post to what our last archive() counted
        """
        if post.kind == ArchiveKind.Missing:
            self.missing += 1
            return

        self.fetched += 1
        if post.kind == ArchiveKind.Text:
            self.texts += 1

        elif post.kind == ArchiveKind.Binary:
            self.binaries += 1

        else:
            self.refused += 1

    @staticmethod
    def _fetch(manager, wanted, work_dir, group, window):
        """
        Fetches the articles wanted, keeping `window` of them asked for at
        once; yields ((message_id, row), (article, status)) in the order
        given: the article (None if it wasn't fetched) and how the fetch
        went ('found', 'missing' or 'failed'; see
        NNTPConnection.fetch_status())
        """
        pending = deque()
        todo = iter(wanted)

        def submit():
            # Top our requests back up to our window size
            while len(pending) < window:
                entry = next(todo, None)
                if entry is None:
                    break

                # The article as it is (nothing decoded), then how the
                # fetch went (asked of the same connection)
                request = NNTPConnectionRequest(actions=[
                    ('get', (entry[0], work_dir), {
                        'decoders': False, 'group': group}),
                    ('fetch_status', (), {}),
                ])
                manager.put(request)
                pending.append((entry, request))

        try:
            submit()
            while pending:
                # Handle them in the order we asked
                entry, request = pending.popleft()
                request.wait()

                # Keep our connections busy while this one is kept
                submit()

                results = list(request.response) + [None, None]
                yield entry, (results[0], results[1] or 'failed')

        finally:
            # Anything not handled yet (ie: Ctrl-C) is no longer wanted
            for _, request in pending:
                request.abort()

    def find(self, keywords=(), groups=None, kinds=ARCHIVE_SEARCHED,
             limit=None, case_sensitive=False, tags=None, without_tags=None,
             min_score=None, max_score=None, terms=None, scores=None,
             after=None, before=None):
        """
        Returns the posts (newest first) with every keyword in their
        subject, text or file names (see parse_keywords(); %p looks in the
        posters, a leading - leaves out)

        Searches look for words: a keyword matches the posts with each of
        its words, the last one as the start of a word (ie: 'holid' finds
        'holiday').  Case and accents don't matter unless case_sensitive is
        set (then the keyword has to be there exactly as given).

        - groups: only posts found in one of these groups
        - kinds: the kinds of posts wanted (see ArchiveKind)
        - tags / without_tags: only (or leave out) posts with these tags
          (see tag_logic())
        - min_score / max_score: leave out posts scoring lower / higher
        - terms: find(1) style conditions (see NNTPSearchTerm) they must
          all match: subject, body (the text), filename, poster and
          message_id
        - scores: score comparisons (ie: '<34, >23') they must pass
        - after / before: only posts posted at or after / before this (a
          date or how long ago, ie: 90d)
        """
        session = self.session()
        if session is None:
            return []

        query = session.query(ArchivedPost)\
            .filter(ArchivedPost.kind.in_(list(kinds)))

        plain, wild = keyword_terms(keywords, case_sensitive)
        for no, (exclude, column, value) in enumerate(plain):
            found = ArchivedPost.id.in_(
                self._search(session, no, column, value, case_sensitive))
            query = query.filter(~found if exclude else found)

        # Every condition narrows the results down further
        for no, term in enumerate([*wild, *(terms or ())]):
            query = query.filter(self._term(session, no, term))

        query = rank_query(
            query, ArchivedPost.score, ArchivedPost.tags,
            (ArchivedPost.posted, ArchivedPost.posted), tags=tags,
            without_tags=without_tags, min_score=min_score,
            max_score=max_score, scores=scores, after=after, before=before)

        if groups:
            # Found in (at least) one of the groups
            query = query.filter(ArchivedPost.message_id.in_(
                select(ArchiveGroup.message_id)
                .where(ArchiveGroup.name.in_(list(groups)))))

        query = query.order_by(
            ArchivedPost.posted.desc(), ArchivedPost.id.desc())

        return query.limit(limit).all() if limit else query.all()

    def _term(self, session, no, term):
        """
        Returns the condition for a post matching a search condition (see
        NNTPSearchTerm)

        The search index finds the posts holding the words the pattern
        needs first; only those are checked against the pattern.
        """
        dialect = session.get_bind().dialect.name

        # The columns of the post the field is kept in, and whether its
        # file names count too
        columns, names = {
            SEARCH_ANY: ((ArchivedPost.subject, ArchivedPost.text), True),
            'subject': ((ArchivedPost.subject, ), False),
            'body': ((ArchivedPost.text, ), False),
            'poster': ((ArchivedPost.poster, ), False),
            'filename': ((), True),
            'message_id': ((ArchivedPost.message_id, ), False),
        }[term.field]

        found = [term.condition(dialect, column) for column in columns]
        if names:
            # The names of the files it holds (each one on its own)
            found.append(ArchivedPost.message_id.in_(
                select(ArchiveFile.message_id).where(
                    term.condition(dialect, ArchiveFile.filename))))

        found = or_(*found) if len(found) > 1 else found[0]
        if term.negate:
            return ~found

        if term.field == 'message_id' and term.exact() is not None:
            # Looked up directly
            return and_(ArchivedPost.message_id == term.exact(), found)

        # What the search index can find for us (it doesn't hold
        # Message-IDs)
        narrowed = [] if term.field == 'message_id' else [
            ArchivedPost.id.in_(self._search(
                session, TERM_PARAMS + no * TERM_PARAMS + piece_no,
                'poster' if term.field == 'poster' else 'subject', words))
            for piece_no, words in enumerate(term.words()[:TERM_PARAMS])]

        return and_(*narrowed, found) if narrowed else found

    @staticmethod
    def _search(session, no, column, value, case_sensitive=False):
        """
        Returns a query of the ids of the posts with `value` in their
        subject, text or file names (column 'subject'), or in their poster
        (column 'poster'); see find()

        - no: makes its parameters unique (one per keyword)
        """
        dialect = session.get_bind().dialect.name
        words = WORD_RE.findall(value)
        columns = ('poster', ) if column == 'poster' \
            else ('subject', 'text', 'filenames')

        # Everything searched, as one piece of text
        if dialect == 'mysql':
            haystack = 'CONCAT_WS(\' \', %s)' % ', '.join(columns)

        else:
            haystack = " || ' ' || ".join(
                "COALESCE(%s, '')" % c for c in columns)

        # The keyword as a LIKE pattern (% and _ kept as they are)
        pattern = '%%%s%%' % value.replace('/', '//')\
            .replace('%', '/%').replace('_', '/_')

        params = {}
        conditions = []
        if dialect == 'sqlite':
            if words:
                # The search index finds the words
                params['match%d' % no] = '{%s} : (%s)' % (
                    ' '.join(columns), ' AND '.join(
                        ['"%s"' % w for w in words[:-1]] +
                        ['"%s"*' % words[-1]]))
                conditions.append(
                    'id IN (SELECT rowid FROM archive_search '
                    'WHERE archive_search MATCH :match%d)' % no)

            else:
                # Nothing but symbols; look through every post
                params['like%d' % no] = pattern
                conditions.append(
                    "%s LIKE :like%d ESCAPE '/'" % (haystack, no))

            if case_sensitive:
                params['exact%d' % no] = value
                conditions.append('instr(%s, :exact%d) > 0' % (haystack, no))

        elif dialect == 'postgresql':
            document = PG_POSTER_DOCUMENT if column == 'poster' \
                else PG_POST_DOCUMENT
            if words:
                params['match%d' % no] = ' & '.join(
                    words[:-1] + ['%s:*' % words[-1]])
                conditions.append(
                    "%s @@ to_tsquery('simple'::regconfig, "
                    'newsreap_unaccent(:match%d))' % (document, no))

            else:
                params['like%d' % no] = pattern
                conditions.append(
                    "newsreap_unaccent(%s) ILIKE newsreap_unaccent(:like%d) "
                    "ESCAPE '/'" % (haystack, no))

            if case_sensitive:
                params['exact%d' % no] = value
                conditions.append(
                    'strpos(%s, :exact%d) > 0' % (haystack, no))

        else:
            # MySQL (and anything else) looks for long enough words with
            # its search index and checks the rest with LIKE (its usual _ci
            # collations ignore case and accents)
            indexed = [w for w in words if len(w) >= MYSQL_MIN_WORD] \
                if dialect == 'mysql' else []
            if indexed:
                params['match%d' % no] = ' '.join(
                    '+%s%s' % (w, '*' if w == words[-1] else '')
                    for w in indexed)
                conditions.append(
                    'MATCH (%s) AGAINST (:match%d IN BOOLEAN MODE)' % (
                        ', '.join(columns), no))

            for w_no, word in enumerate(
                    (w for w in words if w not in indexed) if words
                    else [value]):
                key = 'like%d_%d' % (no, w_no)
                params[key] = '%%%s%%' % word.replace('/', '//')\
                    .replace('%', '/%').replace('_', '/_')
                conditions.append(
                    "%s LIKE :%s ESCAPE '/'" % (haystack, key))

            if case_sensitive:
                params['exact%d' % no] = pattern
                conditions.append(
                    "%s COLLATE utf8mb4_bin LIKE :exact%d ESCAPE '/'" % (
                        haystack, no) if dialect == 'mysql' else
                    "%s LIKE :exact%d ESCAPE '/'" % (haystack, no))

        found = select(literal_column('id'))\
            .select_from(text('archive_post'))\
            .where(text(' AND '.join(conditions)).bindparams(**params))

        if dialect == 'mysql':
            # Searched once as a table of its own; MySQL would otherwise
            # check every post against it instead of using its index
            found = found.subquery()
            return select(found.c.id)

        return found

    def get(self, message_id):
        """
        Returns a kept post by its Message-ID (None if we don't have it)
        """
        session = self.session()
        if session is None:
            return None

        return session.query(ArchivedPost)\
            .filter(ArchivedPost.message_id ==
                    message_id.strip().strip('<>')).first()

    def files(self, message_id):
        """
        Returns what the files of a kept post say about themselves
        """
        session = self.session()
        if session is None:
            return []

        return session.query(ArchiveFile)\
            .filter(ArchiveFile.message_id ==
                    message_id.strip().strip('<>'))\
            .order_by(ArchiveFile.id).all()

    def groups(self, message_id):
        """
        Returns the (sorted) names of the groups a kept post was found in
        """
        session = self.session()
        if session is None:
            return []

        return sorted(name for name, in session.query(ArchiveGroup.name)
                      .filter(ArchiveGroup.message_id ==
                              message_id.strip().strip('<>')))

    @staticmethod
    def snippet(post, keywords=(), terms=(), width=70):
        """
        Returns a short piece of a post's text around the first keyword (or
        word a body condition needs; see NNTPSearchTerm) it has (or its
        start), on one line
        """
        body = ' '.join((post.text or '').split())
        if not body:
            return ''

        lowered = body.casefold()
        start = 0

        # What we're looking for, in order
        wanted = [(exclude, column, value) for exclude, column, value
                  in parse_keywords(keywords)]
        wanted.extend(
            (False, 'subject', words) for term in terms
            if term.field in (SEARCH_ANY, 'body') for words in term.words())

        for exclude, column, value in wanted:
            if exclude or column != 'subject':
                continue

            words = WORD_RE.findall(value) or [value]
            found = lowered.find(words[0].casefold())
            if found >= 0:
                start = max(0, found - width // 3)
                break

        piece = body[start:start + width]
        return '%s%s%s' % (
            '...' if start else '', piece,
            '...' if start + width < len(body) else '')

    def __str__(self):
        """
        A short summary of our last archive()
        """
        text = '%d article(s) kept (%d text, %d binary), %d missing, ' \
            '%d refused, %d already kept' % (
                self.texts + self.binaries, self.texts, self.binaries,
                self.missing, self.refused, self.known)

        if self.dropped:
            text += ', %d dropped by filters' % self.dropped

        if self.failed:
            text += ', %d failed (asked for again next time)' % self.failed

        return text

    def __repr__(self):
        """
        Return an unambiguous version of the object
        """
        return '<NNTPArchiver db_file="%s" />' % self.db_file
