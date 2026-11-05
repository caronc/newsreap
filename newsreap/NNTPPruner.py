# Finds (and clears out) what NewsReap keeps that is no longer needed
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
Finds what NewsReap keeps that is no longer needed, and clears it out:

- headers: indexed article headers posted more than `headers` days ago
  (their collections keep everything an NZB file needs)
- downloads: what was kept of downloads that didn't finish, once nothing
  has touched them for `downloads` days (one being run is never touched,
  and finished downloads are never looked at)
- work files: files left in the work directory for `work` days or more
  (only when the work directory is inside our cache directory)

Each kind takes its days from the configuration when not given, and 0 days
leaves that kind alone.  Out of the box only work files are cleared, so
nothing you may want to keep goes unless you ask for it.

find() says what would go (nothing is changed); clear() removes it.
"""

from collections import namedtuple
from os import listdir
from os import remove
from os.path import getmtime
from os.path import getsize
from os.path import isdir
from os.path import isfile
from os.path import join
from os.path import realpath
from shutil import rmtree
from time import time

from .NNTPDownloader import NNTPDownloader
from .NNTPGroupDatabase import NNTPGroupDatabase
from .NNTPSettings import DEFAULT_DOWNLOAD_VARIABLES

import logging
from newsreap.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)

# What find() found:
#  - headers: [(group, its cache file, how many headers)]
#  - downloads: [(staging directory, bytes)]
#  - work: [(file, bytes)]
PruneFound = namedtuple('PruneFound', ('headers', 'downloads', 'work'))


def size_of(path):
    """
    Returns how much a file or directory holds (bytes)
    """
    if isfile(path):
        return getsize(path)

    total = 0
    for name in listdir(path):
        try:
            total += size_of(join(path, name))

        except OSError:
            # Gone while we looked
            pass
    return total


class NNTPPruner:
    """
    Finds and clears out what is no longer needed; see the module
    description
    """

    def __init__(self, settings, headers=None, downloads=None, work=None):
        """
        Prepares a pruner; 0 days leaves that kind alone

        - settings: our NNTPSettings (where everything is kept)
        - headers: how many days indexed headers are kept (the
          raw_retention setting when not given)
        - downloads: how many days untouched downloads that didn't finish
          are kept (the prune_downloads setting when not given)
        - work: how many days leftover work files are kept (the prune_work
          setting when not given)
        """
        self.settings = settings
        self.headers = headers if headers is not None else self._setting(
            settings.nntp_processing, 'raw_retention', 0)
        self.downloads = downloads if downloads is not None else \
            self._setting(settings.nntp_downloads, 'prune_downloads',
                          DEFAULT_DOWNLOAD_VARIABLES['prune_downloads'])
        self.work = work if work is not None else self._setting(
            settings.nntp_downloads, 'prune_work',
            DEFAULT_DOWNLOAD_VARIABLES['prune_work'])

    @staticmethod
    def _setting(section, key, default):
        """
        Returns a number of days from the configuration; a value that isn't
        a whole number of days is warned about and the default used
        """
        value = section.get(key)
        if value is None:
            return default

        try:
            return max(0, int(value))

        except (TypeError, ValueError):
            logger.warning("%s: '%s' isn't a number of days; using %d." % (
                key, value, default))
            return default

    def find(self):
        """
        Returns what would be cleared out (a PruneFound); nothing changes
        """
        return PruneFound(
            list(self._old_headers()),
            [(path, size_of(path)) for path in NNTPDownloader.stale(
                self.settings.staging_dir(), self.downloads)]
            if self.downloads else [],
            [(path, size_of(path)) for path in self._work_files()])

    def clear(self, found=None):
        """
        Clears out what find() found (it is looked for again when not
        given); returns that PruneFound
        """
        found = found or self.find()
        for _, path, _ in found.headers:
            cache = NNTPGroupDatabase.from_file(path)
            try:
                cache.purge(self.headers)

            finally:
                cache.close()

        for path, _ in found.downloads:
            if not NNTPDownloader.remove_staging(path):
                logger.warning('Left %s alone (a download is using it).' %
                               path)

        for path, _ in found.work:
            if isdir(path):
                rmtree(path, ignore_errors=True)
                continue

            try:
                remove(path)

            except OSError as e:
                logger.warning('Could not remove %s (%s).' % (path, e))

        return found

    def _old_headers(self):
        """
        Yields (group, cache file, how many) for each group cache holding
        indexed headers older than we keep them
        """
        folder = join(self.settings.cache_dir, 'search')
        if not self.headers or not isdir(folder):
            return

        for name in sorted(listdir(folder)):
            if not name.endswith('.db'):
                continue

            path = join(folder, name)
            cache = NNTPGroupDatabase.from_file(path)
            try:
                count = cache.purge(self.headers, dry_run=True)

            finally:
                cache.close()

            if count:
                yield name[:-3], path, count

    def _work_files(self):
        """
        Yields the files left in our work directory long enough to go (only
        when it is inside our cache directory, so a shared one is never
        touched)
        """
        work_dir = self.settings.work_dir
        if not self.work or not work_dir or not isdir(work_dir) or \
                not realpath(work_dir).startswith(
                    realpath(self.settings.cache_dir)):
            return

        oldest = time() - self.work * 86400
        for name in sorted(listdir(work_dir)):
            path = join(work_dir, name)
            try:
                if getmtime(path) < oldest:
                    yield path

            except OSError:
                # Gone while we looked
                pass

    def __repr__(self):
        return '<NNTPPruner headers=%s downloads=%s work=%s />' % (
            self.headers, self.downloads, self.work)
