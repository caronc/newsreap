# Downloads what an NZB file lists and puts it back together (nr get)
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
Downloads what an NZB file lists and puts it back together:

1. Everything happens in a staging directory of its own, named after the
   NZB and a hash of the articles it lists.  Running the same NZB again
   carries on where it stopped, and different downloads never share one.
   A lock keeps two runs of the same NZB apart.
2. The data files (and the small par2 index files) are fetched first, two
   articles for each connection at a time.  Each decoded part is written
   under a temporary name, renamed once it is whole and only then noted
   (see DownloadedSegment), so a part cut short is fetched again, and so
   is one whose crc32 doesn't match what was posted.
3. Each file is put together from its parts.  A part no server has is left
   as zeros, for par2 to fill in.
4. par2 checks the files; when something is damaged, just enough of the
   recovery volumes are fetched to repair it.
5. Archives (rar, 7z, zip) are extracted, trying the password given, then
   the ones the NZB file holds, then none.
6. What came out (and any other files, ie: an .nfo) goes in a directory of
   its own under the download directory, keeping its layout.
7. The staging directory is removed (unless cleanup is off).

When something can't be opened (a password we don't have, or damage par2
couldn't fix), the files put together are placed for you to look at
instead, and nothing is removed.

Hooks are called around each step: pre_/post_ download, verify, repair,
extract and move.  A pre_ hook answering False skips that step.
"""

import gevent.monkey
gevent.monkey.patch_all()

import re
from collections import Counter
from hashlib import sha1
from os import listdir
from os import makedirs
from os import remove
from os import replace
from os import walk
from os.path import basename
from os.path import getmtime
from os.path import exists
from os.path import isdir
from os.path import isfile
from os.path import join
from shutil import copyfile
from shutil import move
from shutil import rmtree
from time import time
from zlib import crc32

try:
    import fcntl

except ImportError:
    # Windows; two runs of the same download aren't kept apart there
    fcntl = None

from .HookManager import HookManager
from .NNTPArchiver import NNTPArchiver
from .Utils import utcnow
from .NNTPBinaryContent import NNTPBinaryContent
from .NNTPGetDatabase import NNTPGetDatabase
from .NNTPnzb import NNTPnzb
from .codecs.Codec7Zip import Codec7Zip
from .codecs.CodecPar import CodecPar
from .codecs.CodecPar import ParReturnCode
from .codecs.CodecRar import CodecRar
from .codecs.CodecZip import CodecZip
from .objects.get.DownloadedSegment import DownloadedSegment

import logging
from newsreap.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)

# The directories of a staging directory: the decoded parts, the files put
# together from them, what their archives held, and work in progress
STAGING_PARTS = 'parts'
STAGING_FILES = 'files'
STAGING_EXTRACTED = 'extracted'
STAGING_WORK = 'tmp'

# What a staging directory notes its segments in, and keeps runs apart with
STAGING_DATABASE = 'download.db'
STAGING_LOCK = '.lock'

# Where a staging directory notes the folder (and the files) a download that
# didn't work out was placed in, so running it again finishes it there
STAGING_PLACED = 'placed'

# What is added to the name of a file that couldn't all be put together, for
# the file that says what is wrong with it (as Pan does)
ERRORS_EXTENSION = '.ERRORS'

# The biggest file a part can say it belongs to (1 TiB); a part placed
# beyond it is damaged
MAX_FILE_SIZE = 1 << 40

# How many segments are noted before they are committed
COMMIT_EVERY = 50

# The file name a subject gives (in quotes, as nearly every poster does)
SUBJECT_NAME_RE = re.compile(r'"(?P<name>[^"]+)"')

# What can't be part of a file or directory name we make
UNSAFE_NAME_RE = re.compile(r'[\x00-\x1f/\\:*?"<>|]+')

# What is left out of the names we make up (see tidy_name()): anything a
# shell treats specially; path and control characters separate words
UNTIDY_NAME_RE = re.compile(r'[!$`\'"&;|<>*?()\[\]{}~#]+')
SEPARATOR_RE = re.compile(r'[\x00-\x1f\x7f/\\:]+')

# The longest name we give a file (bytes); file systems allow 255, and
# room is left for what we add while working on it (ie: '.tmp', ' (2)')
MAX_NAME_BYTES = 230

# Runs of whitespace, and of periods
WHITESPACE_RE = re.compile(r'\s+')
PERIODS_RE = re.compile(r'\.{2,}')

# A par2 file, and a recovery volume of one (with how many blocks it holds)
PAR2_RE = re.compile(r'\.par2$', re.IGNORECASE)
PAR2_VOLUME_RE = re.compile(
    r'\.vol[0-9]+\+(?P<blocks>[0-9]+)\.par2$', re.IGNORECASE)

# What par2 leaves of a file it repaired (ie: notes.txt.1)
PAR2_BACKUP_RE = re.compile(r'^(?P<name>.+)\.[0-9]+$')

# The volumes of an archive (any of them), and the first one of each kind
ARCHIVE_RE = re.compile(
    r'(\.rar|\.r[0-9]{2,3}|\.7z|\.7z\.[0-9]{3}|\.zip|\.z[0-9]{2})$',
    re.IGNORECASE)
ARCHIVE_FIRST = (
    ('rar', re.compile(r'\.part0*1\.rar$', re.IGNORECASE)),
    ('rar', re.compile(r'(?<!\.part[0-9])(?<!\.part[0-9]{2})'
                       r'(?<!\.part[0-9]{3})\.rar$', re.IGNORECASE)),
    ('7z', re.compile(r'\.7z(\.0*1)?$', re.IGNORECASE)),
    ('zip', re.compile(r'\.zip$', re.IGNORECASE)),
)


def fit_name(name, limit=MAX_NAME_BYTES):
    """
    Returns a file name no longer than the file system allows (`limit`
    bytes once encoded); one too long keeps its extension and as much of
    the rest as fits
    """
    if len(name.encode('utf-8')) <= limit:
        return name

    base, dot, extension = name.rpartition('.')
    if not base or len(extension.encode('utf-8')) > 16:
        base, dot, extension = name, '', ''

    room = limit - len((dot + extension).encode('utf-8'))
    base = base.encode('utf-8')[:room].decode('utf-8', 'ignore')
    return base + dot + extension


def safe_name(name, default='download'):
    """
    Returns a name that is safe to use for a file or directory (no path,
    nothing hidden, not too long)
    """
    name = UNSAFE_NAME_RE.sub('_', basename(str(name or ''))).strip()
    return fit_name(name.lstrip('.').strip()) or default


def place_file(source, target):
    """
    Moves a file into place, replacing what is there (it is copied in
    under a temporary name first when it comes from another drive)
    """
    try:
        replace(source, target)

    except OSError:
        copyfile(source, target + '.tmp')
        replace(target + '.tmp', target)
        remove(source)


def write_errors(path, lines):
    """
    Writes what is wrong with a file into the file beside it (its name with
    .ERRORS added); returns True if it could be.  Not being able to is only
    warned about.
    """
    target = path + ERRORS_EXTENSION
    text = (
        '%s could not all be put together (%s UTC).\n'
        'What is missing or damaged is left as zeros.  Running the same '
        'download again\nasks for it again (a server can take a while to '
        'receive what was just posted);\nthis file goes once it is all '
        'there.\n\n%s\n' % (
            basename(path), utcnow().strftime('%Y-%m-%d %H:%M'),
            '\n'.join(lines)))
    try:
        with open(target + '.tmp', 'w', encoding='utf-8') as fp:
            fp.write(text)
        replace(target + '.tmp', target)

    except OSError as e:
        logger.warning('Could not write %s (%s).' % (target, e))
        try:
            remove(target + '.tmp')

        except OSError:
            pass
        return False

    return True


def clear_errors(path):
    """
    Removes the .ERRORS file of a file that is now whole (if there is one);
    not being able to is only warned about
    """
    target = path + ERRORS_EXTENSION
    try:
        remove(target)

    except FileNotFoundError:
        pass

    except OSError as e:
        logger.warning('Could not remove %s (%s).' % (target, e))


def tidy_name(name, default='download'):
    """
    Returns a name made from a subject (or anything else) that is easy to
    use on the command line, for the directories and files we name
    ourselves (posted files keep their own names; par2 looks for them):

    - characters a shell treats specially (ie: ! $ & and quotes) are left
      out; path and control characters (ie: / and tabs) separate words
    - whitespace at either end is trimmed, and each run of it inside
      becomes a period
    - each run of periods becomes one, and none are left at either end
      (so the name is never hidden)
    """
    name = UNTIDY_NAME_RE.sub('', SEPARATOR_RE.sub(' ', str(name or '')))
    name = name.strip()
    name = PERIODS_RE.sub('.', WHITESPACE_RE.sub('.', name)).strip('.')
    return fit_name(name).strip('.') or default


def newest_change(path):
    """
    Returns when anything in a directory last changed (seconds since the
    epoch)
    """
    newest = getmtime(path)
    for root, folders, files in walk(path):
        for name in (*folders, *files):
            try:
                newest = max(newest, getmtime(join(root, name)))

            except OSError:
                # Gone while we looked
                pass
    return newest


def archive_kind(name):
    """
    Returns 'rar', '7z' or 'zip' if a file is the first volume of an
    archive (the one it is opened from); None otherwise
    """
    for kind, pattern in ARCHIVE_FIRST:
        if pattern.search(name):
            return kind
    return None


class NNTPDownloader:
    """
    Downloads an NZB file's content and puts it back together; see the
    module description
    """

    def __init__(self, manager, staging_dir, download_dir, password=None,
                 repair=True, extract=True, cleanup=True, hooks=None):
        """
        Prepares a downloader

        - manager: the NNTPManager articles are fetched with
        - staging_dir: where each download is put together (in a directory
          of its own; see NNTPSettings.staging_dir())
        - download_dir: where finished downloads go (each in a directory of
          its own; see NNTPSettings.download_dir())
        - password: tried first on a password protected archive (the ones
          the NZB file holds are tried next)
        - repair: repair damaged files with their par2 files
        - extract: extract the archives downloaded
        - cleanup: remove the staging directory once everything worked
        - hooks: hooks to call around each step (modules, files or
          functions)
        """
        self.manager = manager
        self.staging_dir = staging_dir
        self.download_dir = download_dir
        self.password = password
        self.repair = bool(repair)
        self.extract = bool(extract)
        self.cleanup = bool(cleanup)

        self.hooks = HookManager()
        if hooks:
            self.hooks.add(hooks)

        # What our last download() did
        self.reset()

    def reset(self):
        """
        Forgets what our last download() did
        """
        # Where it was put together, and where it went
        self.staging = None
        self.folder = None

        # Segments: fetched now, had already (from a run before), no server
        # has them, damaged, and couldn't be fetched (asked again next time)
        self.fetched = 0
        self.resumed = 0
        self.missing = 0
        self.damaged = 0
        self.failed = 0

        # True once par2 repaired something
        self.repaired = False

        # The size and crc32 (8 hex digits) of each file put together whole
        # ({its path: (size, crc32)})
        self.checks = {}

        # The file each staged path was given to ({path: file}), so two
        # files of the same name never share one
        self._staged = {}

        # What is wrong with each file put together ({its path: [lines]}),
        # and the files nothing of could be had ([(name, [lines])]); see
        # write_errors()
        self.errors = {}
        self.lost = []

        # Why it didn't all work (empty when it did)
        self.problem = ''

    def download(self, nzb_file, name=None):
        """
        Downloads what an NZB file lists; returns True once it is all put
        together and placed in the download directory (see folder).  When
        it isn't (see problem), what could be put together is placed
        instead and the staging directory is kept.

        - name: what to call it (the NZB file's name by default)
        """
        self.reset()
        files, passwords, title = self.read(nzb_file)
        if not files:
            self.problem = 'the NZB file lists nothing to download'
            return False

        name = tidy_name(name or title or nzb_file and basename(
            nzb_file).rsplit('.', 1)[0])

        # Named after what it downloads, so running it again carries on
        key = sha1('\n'.join(sorted(
            message_id for f in files for _, message_id in f['segments']
        )).encode('utf-8')).hexdigest()[:12]
        self.staging = join(self.staging_dir, '%s-%s' % (name, key))
        for folder in (STAGING_PARTS, STAGING_FILES, STAGING_WORK):
            makedirs(join(self.staging, folder), exist_ok=True)

        lock = self._lock()
        if lock is None:
            self.problem = '%s is already being downloaded (%s)' % (
                name, self.staging)
            return False

        db = NNTPGetDatabase(
            engine='sqlite:///%s' % join(self.staging, STAGING_DATABASE),
            reset=False)
        try:
            done = self._run(db.session(), name, files, passwords)
            db.close()

            if done and self.cleanup:
                # Still ours while it goes (another run can't take it up)
                rmtree(self.staging, ignore_errors=True)

        finally:
            db.close()
            lock.close()

        return done

    @staticmethod
    def read(nzb_file):
        """
        Returns what an NZB file lists: (files, passwords, title)

        - files: [{'name': ..., 'group': ..., 'segments': [(number,
          message_id), ...]}] (segments in their order)
        - passwords: the passwords its meta data gives (in order)
        """
        nzb = NNTPnzb(nzb_file)
        files = []
        for post in nzb:
            found = SUBJECT_NAME_RE.search(post.subject or '')
            groups = sorted(
                getattr(g, 'name', str(g)) for g in (post.groups or ()))
            files.append({
                'name': safe_name(
                    found.group('name') if found else post.filename, ''),
                'group': groups[0] if groups else None,
                'segments': sorted(
                    (article.no, article.id.strip().strip('<>'))
                    for article in post.articles),
            })

        meta = nzb.meta or {}
        passwords = meta.get('password') or []
        if isinstance(passwords, str):
            passwords = [passwords]

        return files, [p for p in passwords if p], nzb.title()

    def _lock(self):
        """
        Returns the open lock of our staging directory; None if another run
        has it
        """
        lock = open(join(self.staging, STAGING_LOCK), 'w')
        if fcntl is None:
            return lock

        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

        except OSError:
            lock.close()
            return None

        return lock

    def _step(self, action, run, **kwargs):
        """
        Runs a step between its pre_ and post_ hooks; returns what it
        returned (None if a pre_ hook skipped it)
        """
        if not HookManager.allows(
                self.hooks.call('pre_%s' % action, **kwargs)):
            logger.info('The %s step was skipped by a hook.' % action)
            return None

        status = None
        try:
            status = run()
            return status

        finally:
            self.hooks.call('post_%s' % action, status=status, **kwargs)

    def _run(self, session, name, files, passwords):
        """
        Downloads, puts together, repairs, extracts and places one NZB's
        content (see download())
        """
        data = [f for f in files if not PAR2_VOLUME_RE.search(f['name'])]
        volumes = [f for f in files if PAR2_VOLUME_RE.search(f['name'])]

        self._step('download', lambda: self._fetch(session, data),
                   name=name, staging=self.staging)
        assembled = self._assemble(session, data)
        if not assembled:
            self.problem = 'none of its files could be fetched'
            return False

        whole = self._step(
            'verify', lambda: self._verify(session, assembled, volumes),
            name=name, staging=self.staging)
        if whole is False:
            # par2 couldn't make it right; what we have is yours to see
            self.folder = self._place(name, assembled.values())
            return False

        if whole is None and (len(assembled) < len(data) or not all(
                complete for _, complete in assembled.values())):
            # A file none of whose articles came counts too
            self.problem = 'some of it is missing or damaged (and there ' \
                'are no par2 files to repair it)'
            self.folder = self._place(name, assembled.values())
            return False

        if whole:
            # par2 says it is all there; that includes any file it rebuilt
            # (which we never put together ourselves), and nothing is wrong
            # with any of them now
            assembled = self._staged_files()
            self.errors = {}
            self.lost = []

        extracted = None
        archives = [path for path, _ in assembled.values()
                    if archive_kind(basename(path))]
        if archives and self.extract:
            extracted = self._step(
                'extract', lambda: self._extract(archives, passwords),
                name=name, staging=self.staging)
            if extracted is False:
                self.folder = self._place(name, assembled.values())
                return False

        # What goes along with what was extracted (ie: an .nfo); never the
        # par2 files, nor the archives once they are opened
        others = [
            (path, True) for path, _ in assembled.values()
            if not PAR2_RE.search(path) and not (
                extracted and ARCHIVE_RE.search(path))]

        self.folder = self._step(
            'move', lambda: self._place(name, others, extracted),
            name=name, staging=self.staging)
        return self.folder is not None

    def _staged_files(self):
        """
        Returns what is in our staging directory's files ({name: (path,
        True)}), leaving out work in progress and what par2 kept of the
        files it repaired
        """
        folder = join(self.staging, STAGING_FILES)
        names = set(listdir(folder))
        found = {}
        for name in sorted(names):
            backup = PAR2_BACKUP_RE.match(name)
            if name.endswith('.tmp') or (
                    backup and backup.group('name') in names):
                continue
            found[name] = (join(folder, name), True)
        return found

    def _fetch(self, session, files):
        """
        Fetches the segments of the files given that we don't have yet;
        returns how many were fetched now
        """
        noted = {row.message_id: row for row in
                 session.query(DownloadedSegment)}

        # The segments still needed, by group (each is asked for in the
        # group its file was posted to)
        wanted = {}
        for f in files:
            for number, message_id in f['segments']:
                row = noted.get(message_id)
                if row is not None and row.valid and row.path and \
                        isfile(row.path):
                    # Had already
                    self.resumed += 1
                    continue

                # One no server had is asked for again too (a post can take
                # a while to reach every server)

                wanted.setdefault(f['group'], []).append(
                    (message_id, (f['name'], number)))

        parts = join(self.staging, STAGING_PARTS)
        work_dir = join(self.staging, STAGING_WORK)
        fetched = 0
        for group, entries in wanted.items():
            for (message_id, (filename, number)), (article, status) in \
                    NNTPArchiver._fetch(
                        self.manager, entries, work_dir, group,
                        self.manager.window(), decoders=None):
                row = noted.get(message_id) or DownloadedSegment(
                    message_id=message_id, filename=filename, number=number)

                if article is None:
                    if status == 'missing':
                        # No server has it; par2 may be able to fill it in
                        row.missing = True
                        self.missing += 1
                        session.merge(row)

                    else:
                        # Asked for again next time
                        self.failed += 1
                    continue

                part = next((c for c in article.decoded
                             if isinstance(c, NNTPBinaryContent)), None)
                if part is None:
                    # Not a part of a file at all; it is damaged as far as
                    # we are concerned
                    self.damaged += 1
                    continue

                # Written under a temporary name, and only renamed (and
                # noted) once it is whole
                path = join(parts, '%s.part' % sha1(
                    message_id.encode('utf-8')).hexdigest()[:20])
                with open(path + '.tmp', 'wb') as fp:
                    fp.write(part.getvalue() or b'')
                replace(path + '.tmp', path)

                row.filename = safe_name(part.filename, filename)[:256]
                row.begin = part.begin()
                row.end = part.end()
                row.path = path
                row.valid = part.is_valid()
                row.missing = False
                session.merge(row)

                fetched += 1
                if not row.valid:
                    self.damaged += 1

                if fetched % COMMIT_EVERY == 0:
                    session.commit()

        session.commit()
        self.fetched += fetched
        return fetched

    def _assemble(self, session, files):
        """
        Puts each file together from its parts; returns {its NZB name:
        (path, True if every part was there and whole)}.  The size and
        crc32 of each one put together whole is noted in checks.
        """
        assembled = {}
        for f in files:
            ids = [message_id for _, message_id in f['segments']]
            rows = {}
            for start in range(0, len(ids), 500):
                rows.update((row.message_id, row) for row in session.query(
                    DownloadedSegment).filter(DownloadedSegment.message_id
                                              .in_(ids[start:start + 500])))

            have = [rows[m] for m in ids if m in rows and rows[m].path and
                    isfile(rows[m].path)]
            problems = self._problems(f, rows)
            if not have:
                # Nothing of it to put together; still said what is wrong
                if problems:
                    self.lost.append((safe_name(f['name'], 'file'), problems))
                continue

            # The name its articles give (they know best), else the NZB's;
            # a file of a name another file already has gets ' (2)' and so
            # on (the same one each run, as files come in the same order)
            name = Counter(row.filename for row in have).most_common(1)[0][0]
            path = join(self.staging, STAGING_FILES, safe_name(name))
            owner = ids[0]
            base, dot, extension = path.rpartition('.')
            if not base or '/' in extension:
                base, dot, extension = path, '', ''
            no = 2
            while self._staged.setdefault(path, owner) != owner:
                path = '%s (%d)%s%s' % (base, no, dot, extension)
                no += 1

            # Where each part goes: where it says, or (parts that don't say,
            # ie: uuencoded ones) one after another in part order, which a
            # missing part leaves unknown
            if len(have) > 1 and not any(row.begin for row in have):
                offset, placed = 0, []
                for row in sorted(have, key=lambda row: row.number):
                    placed.append((offset, row))
                    offset += max(0, row.end - row.begin)
                laid_out = len(have) == len(ids)

            else:
                placed = [(row.begin, row) for row in have]
                laid_out = True

            # A part claiming to be somewhere no file reaches is damaged
            fits = [(at, row) for at, row in placed
                    if at + max(0, row.end - row.begin) <= MAX_FILE_SIZE]
            laid_out = laid_out and len(fits) == len(placed)
            problems.extend(
                'Part %d <%s>: it says it goes beyond any file (left out)' % (
                    row.number, row.message_id)
                for at, row in placed if (at, row) not in fits)
            if len(have) < len(ids) and not laid_out and len(fits) == len(
                    placed):
                problems.append(
                    'Its parts don\'t say where they go (ie: uuencoded), so '
                    'nothing after a missing one could be placed.')

            # Its crc32 is worked out while it is written; only parts that
            # follow on from each other make the whole file's
            checksum, written, size = 0, 0, 0
            with open(path + '.tmp', 'wb') as fp:
                for at, row in sorted(fits, key=lambda entry: entry[0]):
                    # A part no server has is left as zeros
                    fp.seek(at)
                    with open(row.path, 'rb') as part:
                        data = part.read()
                    fp.write(data)
                    size = max(size, at + max(0, row.end - row.begin))

                    if checksum is not None and at == written:
                        checksum = crc32(data, checksum)
                        written += len(data)

                    else:
                        checksum = None

                fp.truncate(size)

            replace(path + '.tmp', path)
            complete = laid_out and len(have) == len(ids) and all(
                row.valid for row in have)

            # Two NZB entries can share a name too
            key, no = f['name'], 2
            while key in assembled:
                key = '%s (%d)' % (f['name'], no)
                no += 1
            assembled[key] = (path, complete)
            if complete and checksum is not None and written == size:
                self.checks[path] = (size, '%08x' % checksum)

            if complete:
                self.errors.pop(path, None)
            else:
                self.errors[path] = problems or [
                    'Some of it is missing or damaged.']

        return assembled

    @staticmethod
    def _problems(f, rows):
        """
        Returns what is wrong with each part of a file (a line each; none
        when they all came whole)

        - rows: {message_id: DownloadedSegment} of the parts we have noted
        """
        lines = []
        for number, message_id in f['segments']:
            row = rows.get(message_id)
            if row is not None and row.path and isfile(row.path):
                if row.valid:
                    continue
                reason = "it is damaged (its crc32 doesn't match what was " \
                    'posted)'

            elif row is not None and row.missing:
                reason = 'no server has it (yet)'

            else:
                reason = "it couldn't be fetched (it is asked for again " \
                    'next time)'

            where = ''
            if row is not None and row.end > row.begin:
                where = ' (bytes %d-%d)' % (row.begin, row.end - 1)
            lines.append('Part %d%s <%s>: %s' % (
                number, where, message_id, reason))

        return lines

    def _verify(self, session, assembled, volumes):
        """
        Checks the files with par2, fetching recovery volumes and repairing
        them when something is damaged.  Returns True when they are right,
        False when they couldn't be made right, None when there is no par2
        to check with.
        """
        indexes = sorted(
            (path for path, _ in assembled.values() if PAR2_RE.search(path)),
            key=len)
        if not indexes:
            return None

        par = CodecPar(work_dir=join(self.staging, STAGING_WORK))
        if not par.can_exe(par._par):
            logger.warning(
                "par2 isn't installed; the files can't be checked or "
                "repaired.")
            return None

        # True when every file is right; otherwise par2's answer (which is
        # a number, so 1 for Repairable mustn't be taken as True)
        result = par.test(indexes[0])
        if result is True or result == ParReturnCode.NoRepairRequired and \
                not isinstance(result, bool):
            return True

        if not self.repair:
            self.problem = 'some of it is damaged (repairing is turned off)'
            return False

        # Just enough recovery volumes (the smallest that will do); none
        # when par2 already has what it needs
        needed = par.needed if result == ParReturnCode.Unrepairable else 0
        if result == ParReturnCode.Unrepairable and not needed:
            # It didn't say how many; every volume, to be sure
            needed = float('inf')

        chosen = []
        for f in sorted(volumes, key=lambda f: int(
                PAR2_VOLUME_RE.search(f['name']).group('blocks'))):
            if needed <= 0:
                break
            chosen.append(f)
            needed -= int(PAR2_VOLUME_RE.search(f['name']).group('blocks'))

        if chosen:
            self._fetch(session, chosen)
            assembled.update(self._assemble(session, chosen))

        if not self._step('repair', lambda: par.decode(
                [path for path, _ in assembled.values()]) is not None,
                staging=self.staging):
            self.problem = "par2 couldn't repair it (not enough recovery " \
                'blocks)'
            return False

        self.repaired = True
        return True

    def _extract(self, archives, passwords):
        """
        Extracts the archives (the first volume of each); returns the
        directory they were extracted to, or False if one of them couldn't
        be opened
        """
        target = join(self.staging, STAGING_EXTRACTED)
        rmtree(target, ignore_errors=True)
        makedirs(target)

        work_dir = join(self.staging, STAGING_WORK)
        tries = list(dict.fromkeys(
            [p for p in (self.password, *passwords) if p] + [None]))

        for path in sorted(archives):
            kind = archive_kind(basename(path))
            codec = {'rar': CodecRar, '7z': Codec7Zip, 'zip': CodecZip}[kind](
                work_dir=work_dir)
            tool = getattr(codec, '_unrar', None) or getattr(
                codec, '_bin', None)
            if tool is not None and not codec.can_exe(tool):
                self.problem = "%s can't be extracted (%s isn't " \
                    'installed)' % (basename(path), basename(str(tool)))
                return False

            found = None
            for password in tries:
                found = codec.decode(path, password=password)
                if found:
                    break

            if not found:
                self.problem = "%s couldn't be opened (the password isn't " \
                    'known, or it is damaged)' % basename(path)
                return False

            for content in found:
                # What it held, keeping its layout
                content.detach()
                self._move_into(content.path(), target)
                rmtree(content.path(), ignore_errors=True)

        return target

    @staticmethod
    def _move_into(source, target):
        """
        Moves what a directory holds into another, keeping its layout (a
        name already there gets ' (2)' and so on)
        """
        for entry in sorted(listdir(source)):
            destination = join(target, entry)
            if exists(destination) and isdir(destination) and \
                    isdir(join(source, entry)):
                NNTPDownloader._move_into(join(source, entry), destination)
                continue

            move(join(source, entry), NNTPDownloader._unique(destination))

    @staticmethod
    def _unique(path, tidy=False):
        """
        Returns path, or 'path (2)' and so on if it is taken ('path.2' with
        tidy, for a directory we named; see tidy_name())
        """
        if not exists(path):
            return path

        base, dot, extension = basename(path).rpartition('.')
        if not base or isdir(path) or tidy:
            base, dot, extension = basename(path), '', ''

        folder = path[:-len(basename(path))]
        no = 2
        while True:
            found = join(folder, ('%s.%d%s%s' if tidy else '%s (%d)%s%s') % (
                base, no, dot, extension))
            if not exists(found):
                return found
            no += 1

    def _place(self, name, files, extracted=None):
        """
        Puts what was downloaded in a directory of its own under the
        download directory: what was extracted (keeping its layout) and the
        files given; returns that directory.

        A download that didn't work out before is placed where it was then
        (what was placed then is taken away first), so finishing it leaves
        one folder.  A file that isn't all there gets a .ERRORS file beside
        it saying why (see write_errors()); one that now is loses its own.
        """
        folder, before = self._placed_before()
        if folder is None:
            folder = self._unique(join(self.download_dir, name), tidy=True)
            makedirs(folder)

        else:
            # What we put there last time makes way
            for entry in before:
                for path in (join(folder, entry),
                             join(folder, entry) + ERRORS_EXTENSION):
                    try:
                        remove(path)

                    except FileNotFoundError:
                        pass

                    except OSError as e:
                        logger.warning('Could not remove %s (%s).' % (
                            path, e))

        if extracted:
            self._move_into(extracted, folder)

        placed = []
        for path, _ in files:
            if not isfile(path):
                continue

            target = self._unique(join(folder, basename(path)))
            move(path, target)
            placed.append(basename(target))
            if path in self.errors:
                if write_errors(target, self.errors[path]):
                    placed.append(basename(target) + ERRORS_EXTENSION)

            else:
                clear_errors(target)

        for entry, lines in self.lost:
            # Nothing of it came; only what is wrong with it is said
            target = join(folder, entry)
            if write_errors(target, lines):
                placed.append(entry + ERRORS_EXTENSION)

        self._note_placed(folder, placed)
        return folder

    def _placed_before(self):
        """
        Returns (folder, [the names placed in it]) of the last time this
        download was placed without working out; (None, []) if it never was
        (or that folder is gone)
        """
        try:
            with open(join(self.staging, STAGING_PLACED),
                      encoding='utf-8') as fp:
                lines = fp.read().splitlines()

        except OSError:
            return None, []

        if not lines or not isdir(lines[0]):
            return None, []

        # Only names of ours, inside it
        return lines[0], [
            entry for entry in lines[1:] if entry and safe_name(entry, '')
            == entry]

    def _note_placed(self, folder, names):
        """
        Notes where a download was placed (see _placed_before()); not being
        able to only means a run again places it in a folder of its own
        """
        target = join(self.staging, STAGING_PLACED)
        try:
            with open(target + '.tmp', 'w', encoding='utf-8') as fp:
                fp.write('\n'.join([folder, *names]) + '\n')
            replace(target + '.tmp', target)

        except OSError as e:
            logger.warning('Could not note where %s was placed (%s).' % (
                folder, e))

    @staticmethod
    def remove_staging(path):
        """
        Removes a staging directory, unless a run is using it (its lock is
        held while it is removed, so no run can take it up meanwhile);
        returns True if it was removed
        """
        lock_path = join(path, STAGING_LOCK)
        if fcntl is None or not isfile(lock_path):
            rmtree(path, ignore_errors=True)
            return not exists(path)

        try:
            lock = open(lock_path, 'a')

        except OSError:
            return False

        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

        except OSError:
            # Being downloaded right now
            lock.close()
            return False

        try:
            rmtree(path, ignore_errors=True)

        finally:
            lock.close()

        return not exists(path)

    @staticmethod
    def stale(staging_dir, days):
        """
        Yields the staging directories of downloads nothing has touched for
        `days` days (and that no run is using); they are kept after a
        download that didn't work out, so it can carry on
        """
        if not isdir(staging_dir):
            return

        oldest = time() - days * 86400
        for name in sorted(listdir(staging_dir)):
            path = join(staging_dir, name)
            if not isdir(path) or newest_change(path) >= oldest:
                continue

            lock = join(path, STAGING_LOCK)
            if fcntl is not None and isfile(lock):
                with open(lock) as fp:
                    try:
                        fcntl.flock(fp, fcntl.LOCK_EX | fcntl.LOCK_NB)

                    except OSError:
                        # Being downloaded right now
                        continue

            yield path

    def __str__(self):
        """
        A short summary of our last download()
        """
        text = '%d segment(s) fetched' % self.fetched
        if self.resumed:
            text += ', %d had already' % self.resumed
        if self.missing:
            text += ', %d missing' % self.missing
        if self.damaged:
            text += ', %d damaged' % self.damaged
        if self.failed:
            text += ', %d failed (asked for again next time)' % self.failed
        if self.repaired:
            text += ', repaired'
        return text

    def __repr__(self):
        return '<NNTPDownloader staging_dir="%s" download_dir="%s" />' % (
            self.staging_dir, self.download_dir)
