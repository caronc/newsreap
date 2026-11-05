# Fetches what a search found and saves it as it was posted
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
Fetches what a search found and saves it as it was posted, in a directory
named after it under the download directory.  Nothing is repaired or
extracted.  What can be saved:

- files: the files the articles carry (rar, zip, mkv, ...), put back
  together from their parts; articles that are only text are left out
- text: a .txt file (in text/) for each article that is only text, with
  its subject, poster, date and groups on top; articles carrying a file
  are left out
- articles: each article exactly as the server sent it (an .eml file, in
  articles/)

Each article is fetched once, whatever is asked for, and nothing is
fetched that is already known:
- the archive ('nr update --content') is looked in first: the text of a
  post it kept is written from there, and a post it knows carries a file
  isn't fetched for its text
- what is fetched and small enough for the archive is kept in it too
  (see NNTPArchiver.keep_fetched()), so it can be searched and is never
  fetched again for its text

What is learned on the way is kept in `learned_files` and
`learned_segments` (see NNTPIndexer.note_fetched()):
- the size and crc32 of each file put together whole (only when all of
  its articles were given)
- the crc32 of each article, and of the text file written of it
- whether an article carries a file or is only text

Given back to a later run (see NNTPIndexer.fetch_files()), anything
already there and still right is skipped without fetching it, and so are
the articles that turned out to carry a file when only text is wanted.

Files are put together in a staging directory (the one 'nr get' uses for
the same files), so a run that stopped carries on where it left off.  A
file missing a part (or with a damaged one) is still placed, with zeros
where the part goes, but its articles are noted in `unfinished`.
"""

import gevent.monkey
gevent.monkey.patch_all()

from email.utils import parsedate_to_datetime
from hashlib import sha1
from io import BytesIO
from os import makedirs
from os import remove
from os import replace
from os.path import basename
from os.path import getsize
from os.path import isfile
from os.path import join
from shutil import copyfile
from shutil import move
from shutil import rmtree
from datetime import UTC
from zlib import crc32

from .NNTPArchivePost import NNTPArchivePost
from .NNTPArchivePost import sent
from .NNTPArchiver import NNTPArchiver
from .NNTPBinaryContent import NNTPBinaryContent
from .NNTPDownloader import NNTPDownloader
from .NNTPDownloader import STAGING_DATABASE
from .NNTPDownloader import STAGING_PARTS
from .NNTPDownloader import STAGING_FILES
from .NNTPDownloader import STAGING_WORK
from .NNTPDownloader import COMMIT_EVERY
from .NNTPDownloader import SUBJECT_NAME_RE
from .NNTPDownloader import safe_name
from .NNTPDownloader import ERRORS_EXTENSION
from .NNTPDownloader import clear_errors
from .NNTPDownloader import place_file
from .NNTPDownloader import tidy_name
from .NNTPDownloader import write_errors
from .NNTPArticle import NNTPArticle
from .NNTPEmptyContent import NNTPEmptyContent
from .NNTPGetDatabase import NNTPGetDatabase
from .NNTPIndexer import SegmentNote
from .NNTPSegmentedPost import NNTPSegmentedPost
from .NNTPnzb import NNTPnzb
from .NNTPnzb import NZBSegmentNumber
from .codecs.CodecUU import CodecUU
from .codecs.CodecYenc import CodecYenc
from .objects.get.DownloadedSegment import DownloadedSegment
from .objects.index.ArchivedPost import ArchiveKind

import logging
from newsreap.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)

# Where the text files and the articles go (inside what was found's
# directory)
TEXT_FOLDER = 'text'
ARTICLES_FOLDER = 'articles'

# How much of a file is read at a time when its crc32 is checked
CRC_BLOCK_SIZE = 1048576

# The headers put on top of a text file (in this order)
TEXT_HEADERS = ('Subject', 'From', 'Date', 'Newsgroups', 'Message-ID')

# How many fetched posts are handed to the archive at a time
KEEP_EVERY = 500

# Nothing known yet of an article
UNKNOWN = SegmentNote(None, None, None)


def posted_on(post):
    """
    Returns when an article says it was posted (a naive UTC datetime), or
    None if it doesn't say (or says it badly)
    """
    try:
        found = parsedate_to_datetime(post.header('Date'))

    except (TypeError, ValueError, IndexError):
        return None

    if found.tzinfo is not None:
        found = found.astimezone(UTC).replace(tzinfo=None)
    return found


def file_crc32(path):
    """
    Returns the crc32 (8 hex digits) of a file
    """
    checksum = 0
    with open(path, 'rb') as fp:
        while True:
            block = fp.read(CRC_BLOCK_SIZE)
            if not block:
                break
            checksum = crc32(block, checksum)

    return '%08x' % checksum


def still_right(path, crc, size=None):
    """
    Returns True if a file is there with the size (when given) and crc32
    noted; False when it isn't (or nothing was noted)
    """
    if not crc or not isfile(path):
        return False

    try:
        if size is not None and getsize(path) != size:
            return False

        return file_crc32(path) == crc

    except OSError:
        return False


def file_parts(body, work_dir=None):
    """
    Yields the parts of files (yEnc or uuencoded) an article's body holds;
    a part cut short is yielded too (it won't be valid)
    """
    stream = BytesIO(body)
    while True:
        start = stream.tell()
        line = stream.readline()
        if not line:
            return

        codec = next((codec for codec in (
            CodecYenc(work_dir=work_dir), CodecUU(work_dir=work_dir))
            if codec.detect(line) is not None), None)
        if codec is None:
            continue

        stream.seek(start)
        found = codec.decode(stream)
        if found is True:
            # It ended before the part did
            found = codec.decoded

        if isinstance(found, NNTPBinaryContent):
            yield found

        if stream.tell() <= start:
            # Nothing was read; move past the line so we never loop
            stream.seek(start)
            stream.readline()


def write_file(path, data):
    """
    Writes a file under a temporary name and renames it once it is whole;
    returns its crc32 (8 hex digits)
    """
    with open(path + '.tmp', 'wb') as fp:
        fp.write(data)
    replace(path + '.tmp', path)
    return '%08x' % crc32(data)


def replace_if_changed(source, target):
    """
    Moves a file into place unless what is there already holds the same;
    returns True if it was replaced
    """
    if isfile(target) and getsize(target) == getsize(source) and \
            file_crc32(target) == file_crc32(source):
        remove(source)
        return False

    place_file(source, target)
    return True


class NNTPFetcher(NNTPDownloader):
    """
    Fetches what a search found and saves it as it was posted; see the
    module description
    """

    def __init__(self, manager, staging_dir, download_dir, files=True,
                 text=False, articles=False, archiver=None, keep=True,
                 hooks=None):
        """
        Prepares a fetcher

        - manager: the NNTPManager articles are fetched with
        - staging_dir: where files are put together (see
          NNTPSettings.staging_dir())
        - download_dir: where what was found goes (each in a directory of
          its own)
        - files / text / articles: what is saved (see the module
          description)
        - archiver: the NNTPArchiver whose hooks tell text from files, and
          whose archive is looked in and added to (one with the built in
          hooks and no archive when not given)
        - keep: keep what is fetched in the archive (see the module
          description)
        - hooks: hooks to call around each step (pre_/post_ download and
          move)
        """
        super().__init__(manager, staging_dir, download_dir, repair=False,
                         extract=False, cleanup=True, hooks=hooks)
        self.files = bool(files)
        self.text = bool(text)
        self.articles = bool(articles)
        self.keep = bool(keep)
        self._archiver = archiver

    def reset(self):
        """
        Forgets what our last fetch() did
        """
        super().reset()

        # Files, text files and articles that were already there and right
        self.skipped = 0

        # What was written: files, text files and articles
        self.written = {'files': 0, 'text': 0, 'articles': 0}

        # The Message-IDs of the articles whose files, text or copy didn't
        # all come out (missing, damaged or couldn't be fetched)
        self.unfinished = set()

        # What was learned (see NNTPIndexer.note_fetched())
        self.learned_files = {}
        self.learned_segments = {}

    @property
    def archiver(self):
        """
        The NNTPArchiver whose hooks tell text from files
        """
        if self._archiver is None:
            self._archiver = NNTPArchiver(None)
        return self._archiver

    def fetch(self, name, files, known=None, texts=None):
        """
        Fetches what is wanted of the files given (see
        NNTPIndexer.fetch_files()); returns True when all of it came out.
        It goes in a directory called `name` under the download directory
        (see folder).

        - known: {message_id: SegmentNote} learned by a run before
        - texts: {message_id: {'text', 'subject', 'poster', 'posted',
          'groups'}} for articles whose text is already known (ie: kept by
          'nr update --content'); written as they are, without fetching
        """
        self.reset()
        known = dict(known or {})
        texts = dict(texts) if self.text and texts else {}
        self.folder = join(self.download_dir, tidy_name(name))

        # What the archive already knows saves fetching it: whether a post
        # carries a file, and the text of the ones that don't
        if self.text or self.files:
            unknown = [m for f in files for _, m in f['segments']
                       if known.get(m, UNKNOWN).kind is None or (
                           self.text and m not in texts)]
            for post in self.archiver.posts(unknown):
                if post.kind not in (ArchiveKind.Text, ArchiveKind.Binary):
                    continue

                note = known.get(post.message_id, UNKNOWN)
                if note.kind is None:
                    known[post.message_id] = note._replace(kind=post.kind)
                    self.learned_segments[post.message_id] = \
                        known[post.message_id]

                if self.text and post.kind == ArchiveKind.Text and \
                        post.text and post.message_id not in texts:
                    texts[post.message_id] = {
                        'text': post.text, 'subject': post.subject,
                        'poster': post.poster, 'posted': post.posted}

        for message_id, entry in texts.items():
            self.write_text(message_id, entry['text'], **{
                key: entry.get(key) for key in (
                    'subject', 'poster', 'posted', 'groups')})

        # What is still needed of each article ('file', 'text', 'article')
        needed = {}
        assemble = []
        for f in files:
            if self.files:
                target = self._target(f)
                if target and still_right(
                        target, f.get('crc32'), f.get('size')):
                    self.skipped += 1
                    clear_errors(target)

                elif any(known.get(m, SegmentNote(None, None, None)).kind
                         != ArchiveKind.Text for _, m in f['segments']):
                    assemble.append(f)
                    for _, message_id in f['segments']:
                        if known.get(message_id, SegmentNote(
                                None, None, None)).kind != ArchiveKind.Text:
                            needed.setdefault(message_id, set()).add('file')

            for _, message_id in f['segments']:
                note = known.get(message_id, SegmentNote(None, None, None))
                if self.articles:
                    if still_right(self._article_path(message_id),
                                   note.crc32):
                        self.skipped += 1
                    else:
                        needed.setdefault(message_id, set()).add('article')

                if self.text and note.kind != ArchiveKind.Binary and \
                        message_id not in texts:
                    if note.kind == ArchiveKind.Text and still_right(
                            self._text_path(message_id), note.text_crc32):
                        self.skipped += 1
                    else:
                        needed.setdefault(message_id, set()).add('text')

        if not needed:
            return True

        db = lock = None
        session = None
        if assemble:
            # Named as 'nr get' names it, so the two share what they fetch
            key = sha1('\n'.join(sorted(
                message_id for f in files for _, message_id in f['segments']
            )).encode('utf-8')).hexdigest()[:12]
            self.staging = join(self.staging_dir, '%s-%s' % (
                tidy_name(name), key))
            for folder in (STAGING_PARTS, STAGING_FILES, STAGING_WORK):
                makedirs(join(self.staging, folder), exist_ok=True)

            lock = self._lock()
            if lock is None:
                self.problem = '%s is already being downloaded (%s)' % (
                    name, self.staging)
                self.unfinished.update(needed)
                return False

            db = NNTPGetDatabase(engine='sqlite:///%s' % join(
                self.staging, STAGING_DATABASE), reset=False)
            session = db.session()

        try:
            self._step('download', lambda: self._grab(
                session, files, needed, known), name=name,
                staging=self.staging)

            if assemble:
                self._step('move', lambda: self._put_together(
                    session, assemble, known), name=name,
                    staging=self.staging)

            if db is not None:
                db.close()

            if assemble and not self.unfinished:
                # Still ours while it goes (another run can't take it up)
                rmtree(self.staging, ignore_errors=True)

        finally:
            if db is not None:
                db.close()
            if lock is not None:
                lock.close()

        if self.unfinished and not self.problem:
            self.problem = '%d article(s) could not all be saved' % len(
                self.unfinished)

        return not self.unfinished

    def _target(self, f):
        """
        Returns where a file goes (None when its name isn't known until it
        is fetched)
        """
        name = safe_name(f.get('name'), '')
        return join(self.folder, name) if name else None

    @staticmethod
    def _id_name(message_id):
        """
        Returns the file name (without its extension) of an article: its
        Message-ID tidied up (see tidy_name()), with a short hash of it
        added when tidying changed it, so two Message-IDs never share one
        """
        name = tidy_name(message_id)
        if name != message_id:
            name = '%s-%s' % (name[:180], sha1(
                message_id.encode('utf-8')).hexdigest()[:8])
        return name

    def _article_path(self, message_id):
        """
        Returns where an article is saved as it was sent
        """
        return join(self.folder, ARTICLES_FOLDER, '%s.eml' % self._id_name(
            message_id))

    def _text_path(self, message_id):
        """
        Returns where the text of an article is saved
        """
        return join(self.folder, TEXT_FOLDER, '%s.txt' % self._id_name(
            message_id))

    def _grab(self, session, files, needed, known):
        """
        Fetches the articles needed (each once) and saves what is wanted of
        them
        """
        noted = {} if session is None else {
            row.message_id: row for row in session.query(DownloadedSegment)}

        # The parts of files fetched before (ie: by 'nr get') aren't
        # fetched again for them
        for message_id, wants in needed.items():
            row = noted.get(message_id)
            if 'file' in wants and row is not None and row.valid and \
                    row.path and isfile(row.path):
                wants.discard('file')
                self.resumed += 1

        # By group (each is asked for in the group its file was posted to)
        wanted = {}
        for f in files:
            for number, message_id in f['segments']:
                if needed.get(message_id):
                    wanted.setdefault(f.get('group'), []).append(
                        (message_id, (f, number)))

        parts = None if session is None else join(self.staging, STAGING_PARTS)
        work_dir = join(self.staging, STAGING_WORK) if self.staging else None
        fetched = 0

        # Fetched posts small enough to keep in the archive
        keeping = []
        max_size = self.archiver.max_size
        for group, entries in wanted.items():
            for (message_id, (f, number)), (article, status) in \
                    NNTPArchiver._fetch(
                        self.manager, entries, work_dir, group,
                        self.manager.window(), decoders=False):
                wants = needed[message_id]
                if article is None:
                    if status == 'missing':
                        self.missing += 1
                        if 'file' in wants:
                            # No server has it (its place is left as zeros)
                            session.merge(DownloadedSegment(
                                message_id=message_id,
                                filename=safe_name(f.get('name'))[:256],
                                number=number, missing=True))

                    else:
                        # Asked for again next time
                        self.failed += 1

                    self.unfinished.add(message_id)
                    continue

                fetched += 1
                raw = sent(article.body.getvalue() or b'')
                note = known.get(message_id, SegmentNote(None, None, None))
                post = NNTPArchivePost(message_id, raw=raw)
                found = next(file_parts(post.body, work_dir), None)
                kind = ArchiveKind.Binary if found is not None \
                    else ArchiveKind.Text
                text_crc32 = note.text_crc32

                if 'article' in wants:
                    makedirs(join(self.folder, ARTICLES_FOLDER),
                             exist_ok=True)
                    write_file(self._article_path(message_id), raw)
                    self.written['articles'] += 1

                if 'text' in wants and found is None:
                    self.archiver.convert(post)
                    if post.kind == ArchiveKind.Text and post.text:
                        text_crc32 = self.write_text(message_id, post.text, {
                            name: post.header(name) for name in TEXT_HEADERS
                        }, subject=f.get('subject'), poster=f.get('poster'),
                            groups=group)

                    elif post.kind == ArchiveKind.Binary:
                        # It carries a file we can't put together (ie: a
                        # MIME attachment)
                        kind = ArchiveKind.Binary

                if 'file' in wants and found is not None:
                    # Written under a temporary name, and only noted once
                    # it is whole
                    path = join(parts, '%s.part' % sha1(
                        message_id.encode('utf-8')).hexdigest()[:20])
                    write_file(path, found.getvalue() or b'')
                    session.merge(DownloadedSegment(
                        message_id=message_id,
                        filename=safe_name(
                            found.filename, f.get('name'))[:256],
                        number=number, begin=found.begin(), end=found.end(),
                        path=path, valid=found.is_valid(), missing=False))
                    if not found.is_valid():
                        self.damaged += 1
                        self.unfinished.add(message_id)

                self.learned_segments[message_id] = SegmentNote(
                    kind, '%08x' % crc32(raw), text_crc32)

                if self.keep and len(raw) <= max_size:
                    # A post of its own now, as the archive keeps them
                    keeping.append(NNTPArchivePost(
                        message_id, raw=raw, subject=f.get('subject'),
                        poster=f.get('poster'), posted=posted_on(post),
                        size=len(raw), groups={group} if group else ()))
                    if len(keeping) >= KEEP_EVERY:
                        self.archiver.keep_fetched(keeping)
                        keeping = []

                if session is not None and fetched % COMMIT_EVERY == 0:
                    session.commit()

        if keeping:
            self.archiver.keep_fetched(keeping)

        if session is not None:
            session.commit()
        self.fetched += fetched
        return fetched

    def write_text(self, message_id, text, headers=None, subject=None,
                   poster=None, posted=None, groups=None):
        """
        Saves an article's text (in our folder's text/ directory) with a few
        of its headers on top; returns the crc32 of what was written

        - headers: {name: value} of the article (the ones TEXT_HEADERS
          names are used)
        - subject / poster / posted / groups: used for a header it doesn't
          have (ie: its text was kept by 'nr update --content')
        """
        headers = headers or {}
        fallback = {
            'Subject': subject, 'From': poster,
            'Date': posted.strftime('%a, %d %b %Y %H:%M:%S +0000')
            if posted else None,
            'Newsgroups': groups if isinstance(groups, str) or not groups
            else ','.join(sorted(groups)),
            'Message-ID': '<%s>' % message_id.strip().strip('<>'),
        }
        lines = ['%s: %s' % (name, headers.get(name) or fallback[name])
                 for name in TEXT_HEADERS
                 if headers.get(name) or fallback[name]]

        makedirs(join(self.folder, TEXT_FOLDER), exist_ok=True)
        self.written['text'] += 1
        return write_file(
            self._text_path(message_id.strip().strip('<>')), (
                '%s\n\n%s\n' % ('\n'.join(lines), text.rstrip())
            ).encode('utf-8'))

    @staticmethod
    def files_of(articles):
        """
        Returns articles that aren't indexed as files (see
        NNTPIndexer.fetch_files()): the ones whose subjects name the same
        file (in quotes) in the same group are parts of one file, put in
        the order they were given; any other is a file of its own

        - articles: [{'message_id', 'subject', 'poster', 'group'}]
        """
        files = {}
        for no, article in enumerate(articles):
            found = SUBJECT_NAME_RE.search(article.get('subject') or '')
            key = (article.get('group'), found.group('name')) if found \
                else no
            f = files.get(key)
            if f is None:
                f = files[key] = {
                    'id': None, 'name': found.group('name') if found else '',
                    'group': article.get('group'),
                    'subject': article.get('subject') or '',
                    'poster': article.get('poster') or '',
                    'segments': [], 'size': None, 'crc32': None}
            f['segments'].append((
                len(f['segments']) + 1,
                article['message_id'].strip().strip('<>')))

        return list(files.values())

    @staticmethod
    def write_nzb(path, files, title=None, work_dir=None):
        """
        Saves an NZB file listing the files given (see
        NNTPIndexer.fetch_files()); it is only replaced when what it lists
        changed.  Returns True if it was written (or already held the
        same).
        """
        nzb = NNTPnzb(work_dir=work_dir)
        nzb.meta = {'title': title} if title else {}
        for sort_no, f in enumerate(files):
            groups = [f['group']] if f.get('group') else []
            post = NNTPSegmentedPost(
                None, subject=f.get('subject') or f.get('name') or '',
                poster=f.get('poster') or '', groups=groups,
                work_dir=work_dir, sort_no=sort_no)
            post.filename = f.get('name') or ''
            for part, message_id in f['segments']:
                article = NNTPArticle(
                    id=message_id, no=part, subject=post.subject,
                    poster=post.poster, groups=groups, work_dir=work_dir)
                article.add(NNTPEmptyContent(
                    filepath=post.filename, part=part, total_size=0,
                    work_dir=work_dir))
                post.add(article)
            nzb.add(post)

        if not nzb.save(path + '.new', numbering=NZBSegmentNumber.Article):
            return False

        replace_if_changed(path + '.new', path)
        return True

    def _put_together(self, session, files, known):
        """
        Puts each file together and places it; returns how many were
        placed whole
        """
        whole = 0
        for f in files:
            # One at a time; files whose name isn't known yet can't clash
            lost = len(self.lost)
            assembled = self._assemble(session, [f])
            ids = [m for _, m in f['segments']
                   if self.learned_segments.get(m, known.get(
                       m, SegmentNote(None, None, None))).kind
                   != ArchiveKind.Text]

            def wrong(lines):
                # What is wrong with its parts (an article that is only
                # text isn't one)
                return [line for line in lines
                        if any('<%s>' % m in line for m in ids)] or lines

            if f['name'] not in assembled:
                # None of its parts came (an article that is only text
                # isn't a part, so a file of nothing else is fine)
                self.unfinished.update(ids)
                target = self._target(f)
                if ids and target:
                    makedirs(self.folder, exist_ok=True)
                    write_errors(target, wrong([
                        line for _, lines in self.lost[lost:]
                        for line in lines]))
                continue

            path, complete = assembled[f['name']]
            whole_file = complete and path in self.checks
            target = self._target(f) or join(self.folder, basename(path))
            if not whole_file and isfile(target) and \
                    not isfile(target + ERRORS_EXTENSION):
                # A copy is there already (that we couldn't check, and that
                # isn't one we left unfinished); what didn't all come goes
                # beside it instead of over it
                target += '.partial'

            makedirs(self.folder, exist_ok=True)
            place_file(path, target)
            self.written['files'] += 1

            if not whole_file:
                # Placed as it is, but not all of it is there; none of its
                # articles count as saved, and what is wrong is said beside
                # it
                self.unfinished.update(ids)
                write_errors(target, wrong(self.errors.get(path) or [
                    'Some of it is missing or damaged.']))
                continue

            # All there now: whatever was said was wrong before goes (and
            # so does an unfinished copy left beside one of yours)
            clear_errors(target)
            for leftover in (target + '.partial', ):
                clear_errors(leftover)
                try:
                    remove(leftover)

                except FileNotFoundError:
                    pass

                except OSError as e:
                    logger.warning('Could not remove %s (%s).' % (
                        leftover, e))

            whole += 1
            if f.get('id') is not None and not f.get('partial'):
                # Only all of its articles make the whole file
                self.learned_files[f['id']] = self.checks[path]

        return whole

    def __str__(self):
        """
        A short summary of our last fetch()
        """
        text = ', '.join('%d %s' % (count, name)
                         for name, count in self.written.items() if count)
        text = text or 'nothing new'
        if self.skipped:
            text += ', %d already there' % self.skipped
        if self.missing:
            text += ', %d article(s) missing' % self.missing
        if self.damaged:
            text += ', %d damaged' % self.damaged
        if self.failed:
            text += ', %d failed (asked for again next time)' % self.failed
        return text

    def __repr__(self):
        return '<NNTPFetcher files=%s text=%s articles=%s />' % (
            self.files, self.text, self.articles)
