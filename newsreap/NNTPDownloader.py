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
from os import replace
from os.path import basename
from os.path import exists
from os.path import isdir
from os.path import isfile
from os.path import join
from shutil import move
from shutil import rmtree

try:
    import fcntl

except ImportError:
    # Windows; two runs of the same download aren't kept apart there
    fcntl = None

from .HookManager import HookManager
from .NNTPArchiver import NNTPArchiver
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

# How many segments are noted before they are committed
COMMIT_EVERY = 50

# The file name a subject gives (in quotes, as nearly every poster does)
SUBJECT_NAME_RE = re.compile(r'"(?P<name>[^"]+)"')

# What can't be part of a file or directory name we make
UNSAFE_NAME_RE = re.compile(r'[\x00-\x1f/\\:*?"<>|]+')

# A par2 file, and a recovery volume of one (with how many blocks it holds)
PAR2_RE = re.compile(r'\.par2$', re.IGNORECASE)
PAR2_VOLUME_RE = re.compile(
    r'\.vol[0-9]+\+(?P<blocks>[0-9]+)\.par2$', re.IGNORECASE)

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


def safe_name(name, default='download'):
    """
    Returns a name that is safe to use for a file or directory (no path,
    nothing hidden)
    """
    name = UNSAFE_NAME_RE.sub('_', basename(str(name or ''))).strip()
    return name.lstrip('.').strip() or default


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

        name = safe_name(name or title or nzb_file and basename(
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

        finally:
            db.close()
            lock.close()

        if done and self.cleanup:
            rmtree(self.staging, ignore_errors=True)

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

        if whole is None and not all(
                complete for _, complete in assembled.values()):
            self.problem = 'some of it is missing or damaged (and there ' \
                'are no par2 files to repair it)'
            self.folder = self._place(name, assembled.values())
            return False

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
                if row is not None and (row.missing or (
                        row.valid and row.path and isfile(row.path))):
                    # Had already (or no server has it)
                    self.resumed += 1
                    continue

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
        (path, True if every part was there and whole)}
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
            if not have:
                continue

            # The name its articles give (they know best), else the NZB's
            name = Counter(row.filename for row in have).most_common(1)[0][0]
            path = join(self.staging, STAGING_FILES, safe_name(name))

            with open(path + '.tmp', 'wb') as fp:
                for row in sorted(have, key=lambda row: row.begin):
                    # A part no server has is left as zeros
                    fp.seek(row.begin)
                    with open(row.path, 'rb') as part:
                        fp.write(part.read())

                fp.truncate(max(row.end for row in have))

            replace(path + '.tmp', path)
            assembled[f['name']] = (path, len(have) == len(ids) and all(
                row.valid for row in have))

        return assembled

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
    def _unique(path):
        """
        Returns path, or 'path (2)' and so on if it is taken
        """
        if not exists(path):
            return path

        base, dot, extension = basename(path).rpartition('.')
        if not base or isdir(path):
            base, dot, extension = basename(path), '', ''

        folder = path[:-len(basename(path))]
        no = 2
        while True:
            found = join(folder, '%s (%d)%s%s' % (base, no, dot, extension))
            if not exists(found):
                return found
            no += 1

    def _place(self, name, files, extracted=None):
        """
        Puts what was downloaded in a directory of its own under the
        download directory: what was extracted (keeping its layout) and the
        files given; returns that directory
        """
        folder = self._unique(join(self.download_dir, name))
        makedirs(folder)

        if extracted:
            self._move_into(extracted, folder)

        for path, _ in files:
            if isfile(path):
                move(path, self._unique(join(folder, basename(path))))

        return folder

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
