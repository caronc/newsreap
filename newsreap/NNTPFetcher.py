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

Whether each article carries a file or is only text is kept in
`learned_segments` (see NNTPIndexer.note_fetched()); given back to a later
run (see NNTPIndexer.fetch_files()), an article that carries a file isn't
fetched again when only text is wanted.

Anything already there is skipped without fetching it: a file without a
.ERRORS file beside it is whole (see NNTPErrorsFile), and so is a text
file or an article that is there.

Files are put together in a staging directory (the one 'nr get' uses for
the same files), so a run that stopped carries on where it left off, and
are only moved into place once everything was tried.  A file missing a
part (or with a damaged one) is still placed, with zeros where the part
goes and a .ERRORS file beside it, but its articles are noted in
`unfinished`.  The text and articles that couldn't be had are listed in
text.ERRORS and articles.ERRORS the same way.  An article no server had
is only asked for again when that can help (see NNTPRetry); the .ERRORS
file remembers it.
"""


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
from .NNTPDownloader import safe_name
from .NNTPDownloader import clear_errors
from .NNTPDownloader import place_file
from .NNTPDownloader import sweep
from .NNTPDownloader import tidy_name
from .NNTPDownloader import write_errors
from .NNTPErrorsFile import ERRORS_EXTENSION
from .NNTPErrorsFile import NNTPErrorsFile
from .NNTPErrorsFile import PART_DAMAGED
from .NNTPErrorsFile import PART_MISSING
from .NNTPArticle import NNTPArticle
from .Utils import utcnow
from .NNTPEmptyContent import NNTPEmptyContent
from .NNTPGetDatabase import NNTPGetDatabase
from .NNTPIndexer import SegmentNote
from .NNTPIndexer import parse_subject
from .NNTPRetry import DEFAULT_RETRY_WINDOW
from .NNTPRetry import NNTPRetry
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
UNKNOWN = SegmentNote(None)


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


def errors_id(line):
    """
    Returns the Message-ID a line of text.ERRORS or articles.ERRORS is about
    (they start with it, ie: '<id>: why'), or None
    """
    end = line.find('>:')
    return line[1:end] if line.startswith('<') and end > 0 else None


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
                 hooks=None, retry_window=DEFAULT_RETRY_WINDOW,
                 retry_missing=False):
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
        - retry_window / retry_missing: when an article no server had is
          asked for again (see NNTPDownloader)
        """
        super().__init__(manager, staging_dir, download_dir, repair=False,
                         extract=False, cleanup=True, hooks=hooks,
                         retry_window=retry_window,
                         retry_missing=retry_missing)
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
        self.learned_segments = {}

        # The articles whose text (or copy) was wanted and didn't come
        # ({'text': [message_id], 'article': [...]}), for text.ERRORS and
        # articles.ERRORS
        self.unsaved = {'text': [], 'article': []}

        # The articles our last fetch() was about
        self.covered = set()

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

        # The articles this fetch is about (what text.ERRORS and
        # articles.ERRORS say of any other is left as it is)
        self.covered = {m for f in files for _, m in f['segments']}

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

        # What a run before remembers of the articles no server had
        for folder, wanted in ((TEXT_FOLDER, self.text),
                               (ARTICLES_FOLDER, self.articles)):
            if wanted:
                self.memory.update(NNTPErrorsFile(
                    join(self.folder, folder)).load().parts)

        # What is still needed of each article ('file', 'text', 'article')
        needed = {}
        assemble = []
        for f in files:
            if self.files:
                target = self._target(f)
                errors = NNTPErrorsFile(target).load() if target else None
                if target and isfile(target) and not errors.found:
                    # There already, and whole (one that isn't has a
                    # .ERRORS file beside it)
                    self.skipped += 1

                elif any(known.get(m, UNKNOWN).kind != ArchiveKind.Text
                         for _, m in f['segments']):
                    if errors is not None:
                        self.memory.update(errors.parts)

                    assemble.append(f)
                    for _, message_id in f['segments']:
                        if known.get(message_id, UNKNOWN).kind != \
                                ArchiveKind.Text:
                            needed.setdefault(message_id, set()).add('file')

            for _, message_id in f['segments']:
                note = known.get(message_id, UNKNOWN)
                if self.articles:
                    if isfile(self._article_path(message_id)):
                        self.skipped += 1
                    else:
                        needed.setdefault(message_id, set()).add('article')

                if self.text and note.kind != ArchiveKind.Binary and \
                        message_id not in texts:
                    if isfile(self._text_path(message_id)):
                        self.skipped += 1
                    else:
                        needed.setdefault(message_id, set()).add('text')

        if not needed:
            self._note_unsaved()
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

            self._note_unsaved()
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

    def _note_unsaved(self):
        """
        Writes text.ERRORS and articles.ERRORS listing the text and articles
        that were wanted and didn't come (see unsaved); each goes once
        everything it listed is there.

        Only the articles this fetch was about are changed in them: what
        they say of any other (ie: one an earlier batch or another search
        saved into the same folder) is kept.
        """
        for kind, folder, wanted in (
                ('text', TEXT_FOLDER, self.text),
                ('article', ARTICLES_FOLDER, self.articles)):
            if not wanted:
                continue

            path = join(self.folder, folder)
            before = NNTPErrorsFile(path).load()
            others = [line for line in before.problems
                      if errors_id(line) not in self.covered]
            ids = sorted(set(self.unsaved[kind]))
            if not ids and not others:
                clear_errors(path)
                continue

            makedirs(self.folder, exist_ok=True)
            sweep(self.folder)
            now = utcnow()
            parts = {m: entry for m, entry in before.parts.items()
                     if m not in self.covered}
            parts.update({m: self.memory[m] for m in ids if m in self.memory})
            write_errors(path, others + [
                '<%s>: %s' % (m, self._why(m, now=now)) for m in ids], parts,
                heading='Some of the %s could not be saved' % (
                    'text' if kind == 'text' else 'articles'))

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
        now = utcnow()
        noted = {} if session is None else \
            DownloadedSegment.noted(session, needed)

        # The parts of files fetched before (ie: by 'nr get') aren't
        # fetched again for them
        for message_id, wants in needed.items():
            row = noted.get(message_id)
            if 'file' in wants and row is not None and row.valid and \
                    row.path and isfile(row.path):
                wants.discard('file')
                self.resumed += 1

        # By group (each is asked for in the group its file was posted to);
        # one no server had is only asked for again when that can help
        wanted = {}
        for f in files:
            posted = NNTPRetry.posted(f.get('posted'))
            for number, message_id in f['segments']:
                wants = needed.get(message_id)
                if not wants:
                    continue

                if self._state(message_id, noted.get(message_id),
                               now) != 'ask':
                    # No server had it (whole), and asking again won't help
                    # yet; what we have of a damaged one is used
                    if self.memory[message_id]['state'] == PART_DAMAGED:
                        wants.discard('file')
                    else:
                        self._unsaved(message_id, wants)
                        wants.clear()

                if wants:
                    wanted.setdefault(f.get('group'), []).append(
                        (message_id, (f, number, posted)))
                else:
                    self.held += 1
                    self.unfinished.add(message_id)

        parts = None if session is None else join(self.staging, STAGING_PARTS)
        work_dir = join(self.staging, STAGING_WORK) if self.staging else None

        # Fetched posts small enough to keep in the archive
        keeping = []
        before = self.fetched
        try:
            for group, entries in wanted.items():
                self._grab_group(session, noted, needed, group, entries,
                                 parts, work_dir, keeping, now)

        except KeyboardInterrupt:
            # Stopped; what came is kept for the next run
            self._keep(keeping)
            if session is not None:
                session.commit()
            raise

        self._keep(keeping)
        if session is not None:
            session.commit()
        return self.fetched - before

    def _keep(self, keeping):
        """
        Hands the posts fetched to the archive (see keep_fetched()); the
        list is emptied
        """
        if keeping:
            self.archiver.keep_fetched(keeping)
            keeping.clear()

    def _unsaved(self, message_id, wants):
        """
        Notes that the text or copy of an article that was wanted didn't
        come (see unsaved)
        """
        for kind in ('text', 'article'):
            if kind in wants:
                self.unsaved[kind].append(message_id)

    def _grab_group(self, session, noted, needed, group, entries, parts,
                    work_dir, keeping, now):
        """
        Fetches the articles wanted of one group (see _grab()); each one
        fetched is counted in fetched
        """
        max_size = self.archiver.max_size
        for (message_id, (f, number, posted)), (article, status) in \
                NNTPArchiver._fetch(
                    self.manager, entries, work_dir, group,
                    self.manager.window(), decoders=False):
            wants = needed[message_id]
            if article is None:
                if status == 'missing':
                    self.missing += 1
                    self._tried(message_id, number, PART_MISSING, posted, now)
                    if 'file' in wants:
                        # No server has it (its place is left as zeros; any
                        # part of it we had is kept)
                        row = noted.get(message_id) or DownloadedSegment(
                            message_id=message_id,
                            filename=safe_name(f.get('name'))[:256],
                            number=number)
                        row.missing = True
                        session.merge(row)

                else:
                    # Asked for again next time
                    self.failed += 1

                self._unsaved(message_id, wants)
                self.unfinished.add(message_id)
                continue

            self.fetched += 1
            raw = sent(article.body.getvalue() or b'')
            post = NNTPArchivePost(message_id, raw=raw)
            found = next(file_parts(post.body, work_dir), None)
            kind = ArchiveKind.Binary if found is not None \
                else ArchiveKind.Text

            # Had, so nothing is remembered of it any more (unless it only
            # came damaged; see below)
            self.memory.pop(message_id, None)

            if 'article' in wants:
                makedirs(join(self.folder, ARTICLES_FOLDER), exist_ok=True)
                write_file(self._article_path(message_id), raw)
                self.written['articles'] += 1

            if 'text' in wants and found is None:
                self.archiver.convert(post)
                if post.kind == ArchiveKind.Text and post.text:
                    self.write_text(message_id, post.text, {
                        name: post.header(name) for name in TEXT_HEADERS
                    }, subject=f.get('subject'), poster=f.get('poster'),
                        groups=group)

                elif post.kind == ArchiveKind.Binary:
                    # It carries a file we can't put together (ie: a MIME
                    # attachment)
                    kind = ArchiveKind.Binary

            if 'file' in wants and found is not None:
                # Written under a temporary name, and only noted once it is
                # whole
                path = join(parts, '%s.part' % sha1(
                    message_id.encode('utf-8')).hexdigest()[:20])
                write_file(path, found.getvalue() or b'')
                row = noted.get(message_id) or DownloadedSegment(
                    message_id=message_id)
                row.filename = safe_name(found.filename, f.get('name'))[:256]
                row.number = number
                row.begin, row.end = found.begin(), found.end()
                row.path, row.valid = path, found.is_valid()
                row.missing = False
                if not row.valid:
                    # Asked for again only when that can help
                    self._tried(message_id, number, PART_DAMAGED, posted, now)
                    self.damaged += 1
                    self.unfinished.add(message_id)
                session.merge(row)

            self.learned_segments[message_id] = SegmentNote(kind)

            if self.keep and len(raw) <= max_size:
                # A post of its own now, as the archive keeps them
                keeping.append(NNTPArchivePost(
                    message_id, raw=raw, subject=f.get('subject'),
                    poster=f.get('poster'), posted=posted_on(post),
                    size=len(raw), groups={group} if group else ()))
                if len(keeping) >= KEEP_EVERY:
                    self._keep(keeping)

            if session is not None and self.fetched % COMMIT_EVERY == 0:
                session.commit()

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
    def loose_key(article):
        """
        Returns (key, parsed subject) for an article that isn't indexed:
        every part of one file shares the key (its group and its subject
        with the part counter taken out); the key is None when the subject
        names no file (see parse_subject())
        """
        parsed = parse_subject(article.get('subject') or '')
        if not parsed['filename']:
            return None, parsed

        return '%s\0%s' % (article.get('group') or '', parsed['base']), \
            parsed

    @staticmethod
    def files_of(articles):
        """
        Returns articles that aren't indexed as files (see
        NNTPIndexer.fetch_files()):

        - articles with the same key (see loose_key()) are parts of one
          file, numbered as their subjects say (ie: 3 of (3/10))
        - a part seen again belongs to another copy of the file (ie: a
          repost): the second time a part is seen it goes to the second
          copy, and so on (NNTPArticleBatches keeps copies apart the same
          way)
        - an article whose subject names no file is a file of its own
        - files come back in the order they were first found

        - articles: [{'message_id', 'subject', 'poster', 'group'}]
        """
        files = []

        # The copies of each file, and how many of them have each part
        copies = {}
        seen = {}
        for article in articles:
            key, parsed = NNTPFetcher.loose_key(article)
            part = parsed['part'] if key is not None else 1

            f = None
            if key is not None:
                copy = seen.get((key, part), 0)
                seen[key, part] = copy + 1
                held = copies.setdefault(key, [])
                if copy < len(held):
                    f = held[copy]

            if f is None:
                f = {
                    'id': None, 'name': parsed['filename'],
                    'group': article.get('group'),
                    'subject': article.get('subject') or '',
                    'poster': article.get('poster') or '',
                    'segments': [], 'size': None, 'crc32': None}
                files.append(f)
                if key is not None:
                    held.append(f)

            f['segments'].append((
                part, article['message_id'].strip().strip('<>')))

        for f in files:
            f['segments'].sort()

        return files

    @staticmethod
    def write_nzb(path, files, title=None, work_dir=None):
        """
        Saves an NZB file listing the files given (see
        NNTPIndexer.fetch_files()); it is only replaced when what it lists
        changed.  Returns True if it was written (or already held the
        same).

        - files: a list or any iterable (ie: a generator); each file is
          written as it comes, so only one is held in memory at a time
        """
        def posts():
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
                yield post

        nzb = NNTPnzb(work_dir=work_dir)
        nzb.meta = {'title': title} if title else {}
        if not nzb.save(path + '.new', numbering=NZBSegmentNumber.Article,
                        posts=posts()):
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
                   if self.learned_segments.get(m, known.get(m, UNKNOWN)).kind
                   != ArchiveKind.Text]

            def wrong(lines):
                # What is wrong with its parts (an article that is only
                # text isn't one)
                return [line for line in lines
                        if any('<%s>' % m in line for m in ids)] or lines

            def remembered(parts):
                # What is remembered of its parts (see NNTPErrorsFile)
                return {m: entry for m, entry in (parts or {}).items()
                        if m in ids}

            makedirs(self.folder, exist_ok=True)
            sweep(self.folder)
            if f['name'] not in assembled:
                # None of its parts came (an article that is only text
                # isn't a part, so a file of nothing else is fine)
                self.unfinished.update(ids)
                target = self._target(f)
                if ids and target:
                    # Said before anything of it placed before goes, so it
                    # never looks whole
                    if write_errors(target, wrong([
                            line for _, lines in self.lost[lost:]
                            for line in lines]), remembered({
                                m: self.memory[m] for m in ids
                                if m in self.memory})):
                        NNTPErrorsFile.discard(target)
                continue

            path, complete = assembled[f['name']]
            target = self._target(f) or join(self.folder, basename(path))
            if not complete and isfile(target) and \
                    not isfile(target + ERRORS_EXTENSION):
                # A whole copy is there already (one that isn't has a
                # .ERRORS file); what didn't all come goes beside it
                # instead of over it
                target += '.partial'

            if not complete:
                # Placed as it is, but not all of it is there; none of its
                # articles count as saved, and what is wrong is said beside
                # it (first, so it never looks whole)
                self.unfinished.update(ids)
                if not write_errors(target, wrong(self.errors.get(path) or [
                        'Some of it is missing or damaged.']),
                        remembered(self.parts_of.get(path))):
                    logger.warning(
                        '%s stays in %s (its .ERRORS file could not be '
                        'written).' % (basename(path), self.staging))
                    continue

                place_file(path, target)
                self.written['files'] += 1
                continue

            # All there now: whatever was said was wrong before goes (after
            # it is in place), and so does an unfinished copy left beside
            # one of yours
            place_file(path, target)
            self.written['files'] += 1
            clear_errors(target)
            if NNTPErrorsFile.discard(target + '.partial'):
                clear_errors(target + '.partial')

            whole += 1

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
        if self.held:
            text += ', %d not asked for again (no server had them)' % \
                self.held
        return text

    def __repr__(self):
        return '<NNTPFetcher files=%s text=%s articles=%s />' % (
            self.files, self.text, self.articles)
