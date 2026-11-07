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
- statistics: the daily transfer statistics of each server, older than
  `statistics` days

Each kind takes its days from the configuration when not given, and 0 days
leaves that kind alone.  Out of the box only work files and statistics are
cleared, so nothing you may want to keep goes unless you ask for it.

find() says what would go (nothing is changed); clear() removes it.
"""

from collections import namedtuple
from datetime import timedelta
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

from .Database import DatabaseUnavailable
from .NNTPDownloader import NNTPDownloader
from .NNTPGroupDatabase import NNTPGroupDatabase
from .NNTPSettings import DEFAULT_DOWNLOAD_VARIABLES
from .NNTPSettings import DEFAULT_PROCESSING_VARIABLES
from .NNTPDatabase import UnknownGroups
from .Utils import utcnow

import logging
from newsreap.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)

# What find() found:
#  - headers: [(group, its cache file, how many headers)]
#  - downloads: [(staging directory, bytes)]
#  - work: [(file, bytes)]
#  - statistics: how many days of server statistics (rows)
PruneFound = namedtuple(
    'PruneFound', ('headers', 'downloads', 'work', 'statistics'))

# What prune_group() removed (or would, with dry_run):
#  - days: how many days were kept (0: the group keeps everything)
#  - headers, files, posts: how many went (files other groups have too
#    only lose the group)
#  - collections: how many were left empty and went (None on a dry run)
GroupPruned = namedtuple(
    'GroupPruned', ('days', 'headers', 'files', 'collections', 'posts'))

# What forget_group() removed:
#  - freed: bytes of cached headers
#  - files, collections, posts: what was indexed and kept from it
GroupForgotten = namedtuple(
    'GroupForgotten', ('freed', 'files', 'collections', 'posts'))


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

    def __init__(self, settings, headers=None, downloads=None, work=None,
                 statistics=None):
        """
        Prepares a pruner; 0 days leaves that kind alone

        - settings: our NNTPSettings (where everything is kept)
        - headers: how many days indexed headers are kept (the
          raw_retention setting when not given)
        - downloads: how many days untouched downloads that didn't finish
          are kept (the prune_downloads setting when not given)
        - work: how many days leftover work files are kept (the prune_work
          setting when not given)
        - statistics: how many days of transfer statistics are kept (the
          statistics setting when not given)
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
        self.statistics = statistics if statistics is not None else \
            self._setting(settings.nntp_processing, 'statistics',
                          DEFAULT_PROCESSING_VARIABLES['statistics'])

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
            [(path, size_of(path)) for path in self._work_files()],
            self._old_statistics())

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

        if found.statistics:
            self.settings.prune_stats(self.statistics)

        return found

    def retention(self, group):
        """
        Returns how many days of a group prune_group() keeps: its own
        retention setting, otherwise the configuration's (0 keeps it all)
        """
        own = self.settings.group_options([group])[group]['retention']
        return own if own is not None else self.settings.retention()

    def prune_group(self, group, days=None, dry_run=False):
        """
        Removes what was posted to a group more than `days` days ago (its
        retention when not given): its cached headers, the scanned files
        and kept posts (see NNTPIndexer.forget_group()), and collections
        left empty.  Returns a GroupPruned; with dry_run nothing changes.
        """
        if days is None:
            days = self.retention(group)

        if not days:
            # Everything is kept
            return GroupPruned(0, 0, 0, 0, 0)

        from .NNTPIndexer import NNTPIndexer

        before = utcnow() - timedelta(days=days)
        servers = self.settings.nntp_servers
        headers = 0

        path = self.settings.group_database(group)
        if isfile(path):
            cache = NNTPGroupDatabase.from_file(path)
            try:
                # Headers are always fetched from the first server
                headers = cache.prune(
                    before, server=servers[0]['host'] if servers else None,
                    dry_run=dry_run)

            finally:
                cache.close()

        indexer = NNTPIndexer(self.settings.index_database())
        try:
            if dry_run:
                files, posts = indexer.count_group(group, before=before)
                collections = None

            else:
                files, collections, posts = indexer.forget_group(
                    group, before=before)

        finally:
            indexer.close()

        return GroupPruned(days, headers, files, collections, posts)

    def forget_group(self, group):
        """
        Removes everything kept of a group, watched or not, to free its
        space: its cached headers, how far it was fetched, and what was
        indexed and kept from it (see NNTPIndexer.forget_group()).  The
        group itself, its settings and whether it is watched stay.

        Returns a GroupForgotten; raises UnknownGroups if there is no such
        group (an alias isn't one).
        """
        from .NNTPIndexer import NNTPIndexer

        found = self.settings.groups(group)
        if group not in found:
            raise UnknownGroups([group])

        freed = self.settings.group_cache_size(group)
        path = self.settings.group_database(group)
        for part in (path, path + '-journal', path + '-wal', path + '-shm'):
            if isfile(part):
                remove(part)

        self.settings.forget_tracking([found[group]])

        indexer = NNTPIndexer(self.settings.index_database())
        try:
            files, collections, posts = indexer.forget_group(group)

        finally:
            indexer.close()

        return GroupForgotten(freed, files, collections, posts)

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

    def _old_statistics(self):
        """
        Returns how many days of server statistics are older than we keep
        them (0 when there is no database yet)
        """
        if not self.statistics:
            return 0

        try:
            return self.settings.prune_stats(self.statistics, dry_run=True)

        except DatabaseUnavailable:
            return 0

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
        return '<NNTPPruner headers=%s downloads=%s work=%s ' \
            'statistics=%s />' % (self.headers, self.downloads, self.work,
                                  self.statistics)
