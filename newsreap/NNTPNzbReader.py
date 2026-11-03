# Reads the NZB files posted to groups into the index
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
People post NZB files too (often the only way to put together a post with
made up names).  The ones the index found in a group (a file whose name
ends in .nzb, no bigger than `max_size`, with every part found) are
fetched, decoded and read in (see NNTPIndexer.import_nzb()).

- Each one is only read once (see NzbImport); --force reads them again.
- One that can't be used (ie: it isn't a valid NZB file) is still noted,
  so it isn't fetched again for nothing.
- Nothing is kept of the NZB file itself, only what it describes.
"""

import re
from io import BytesIO
from os import unlink
from os.path import basename
from os.path import join

from sqlalchemy import func

from .NNTPArchivePost import NNTPArchivePost
from .NNTPArchiver import NNTPArchiver
from .Utils import mkdir
from .Utils import strsize_to_bytes
from .Utils import utcnow
from .codecs.CodecYenc import CodecYenc
from .objects.index.CollectionFile import CollectionFile
from .objects.index.FileGroup import FileGroup
from .objects.index.NzbImport import NzbImport
from .objects.index.Segment import Segment

# Logging
import logging
from newsreap.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)

# What isn't safe in a file name
SAFE_NAME_RE = re.compile(r'[^\w.+=@,-]+')

# An NZB file posted as plain text (not encoded)
NZB_TEXT_RE = re.compile(rb'(<\?xml[^>]*>\s*)?<nzb\b.*?</nzb\s*>', re.DOTALL)


class NNTPNzbReader:
    """
    Reads the NZB files posted to groups; see the module description
    """

    def __init__(self, indexer, max_size='64KB'):
        """
        - indexer: the NNTPIndexer they are read into
        - max_size: the biggest NZB file fetched (ie: 64KB)
        """
        self.indexer = indexer
        self.max_size = strsize_to_bytes(max_size)
        if not self.max_size:
            raise ValueError("'%s' isn't a size (ie: 64KB)." % max_size)

        self.reset()

    def reset(self):
        """
        Resets what the last read() found
        """
        # Read in, and of those how many added a collection
        self.read_in = 0
        self.added = 0

        # Read before; couldn't be used; couldn't be fetched (tried again
        # next time)
        self.known = 0
        self.unusable = 0
        self.failed = 0

    def found(self, session, group):
        """
        Returns (file, Message-IDs) of the NZB files the index found in a
        group: every part of each one, lowest first
        """
        files = session.query(CollectionFile)\
            .join(FileGroup, FileGroup.file_id == CollectionFile.id)\
            .filter(FileGroup.name == group)\
            .filter(func.lower(CollectionFile.filename).like('%.nzb'))\
            .filter(CollectionFile.size <= self.max_size)\
            .order_by(CollectionFile.id)

        for entry in files:
            parts = [message_id for message_id, in session.query(
                Segment.message_id).filter(Segment.file_id == entry.id)
                .order_by(Segment.part)]

            if parts and (not entry.segments_expected or
                          len(parts) >= entry.segments_expected):
                yield entry, parts

    def read(self, group, manager, work_dir, force=False):
        """
        Reads the NZB files found in a group that weren't read before (all
        of them with force); returns False if the index has nothing for
        it yet
        """
        self.reset()

        session = self.indexer.session()
        if session is None:
            return False

        wanted = []
        for entry, parts in self.found(session, group):
            if not force and session.get(NzbImport, parts[0]) is not None:
                self.known += 1
                continue

            wanted.append((entry.filename, parts))

        if not wanted:
            return True

        work_dir = join(work_dir, 'nzb')
        if not mkdir(work_dir):
            logger.error("Could not create '%s'." % work_dir)
            return False

        # Every part of every one, asked for together
        articles = {}
        for (message_id, _), (article, status) in NNTPArchiver._fetch(
                manager, [(m, None) for _, parts in wanted for m in parts],
                work_dir, group, manager.window()):
            articles[message_id] = (article, status)

        for filename, parts in wanted:
            fetched = [articles.get(m, (None, 'failed')) for m in parts]
            if any(article is None and status != 'missing'
                   for article, status in fetched):
                # Asked for again next time
                self.failed += 1
                continue

            data = self.decode([
                None if article is None else article.body.getvalue()
                for article, _ in fetched], work_dir)

            collection = None
            if data:
                # Named as it was posted (it names the collection when the
                # NZB file doesn't)
                path = join(work_dir, SAFE_NAME_RE.sub(
                    '_', basename(filename or '')).lstrip('.') or 'post.nzb')
                with open(path, 'wb') as fp:
                    fp.write(data)

                try:
                    collection, added = self.indexer.import_nzb(path)

                finally:
                    unlink(path)

                if collection is not None:
                    self.read_in += 1
                    self.added += 1 if added else 0

            if collection is None:
                logger.info("'%s' (in %s) isn't a usable NZB file." % (
                    filename, group))
                self.unusable += 1

            # Not read again
            session = self.indexer.session()
            session.merge(NzbImport(
                message_id=parts[0], filename=(filename or '')[:256],
                collection_id=collection.id if collection else None,
                imported=utcnow()))
            session.commit()

        return True

    @staticmethod
    def decode(articles, work_dir):
        """
        Returns the NZB file the articles given (its parts, in order) hold;
        None if they don't hold one (or one of them is missing)
        """
        if not articles or any(raw is None for raw in articles):
            return None

        data = b''
        for raw in articles:
            post = NNTPArchivePost('', raw=raw)
            content = CodecYenc(work_dir=work_dir).decode(BytesIO(post.body))
            if not hasattr(content, 'getvalue'):
                # Not yEnc encoded
                data = None
                break

            data += content.getvalue()

        if data is None:
            # Posted as it is (not encoded)
            found = NZB_TEXT_RE.search(b'\n'.join(articles))
            return found.group(0) if found else None

        return data

    def __str__(self):
        """
        A short summary of our last read()
        """
        if not (self.read_in or self.unusable or self.failed):
            return 'no new NZB files' + (
                ' (%d read before)' % self.known if self.known else '')

        return '%d NZB file(s) read (%d new collection(s)), %d unusable, ' \
            '%d not fetched' % (
                self.read_in, self.added, self.unusable, self.failed)
