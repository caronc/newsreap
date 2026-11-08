# NewsReap claims: what one worker is busy with
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
Lets several workers ('nr update' run more than once at the same time, on
one machine or several sharing a database) stay out of each other's way.

- A worker claims what it works on (a group, settling the index, a
  connection to a server); others skip it, or wait for it.
- Claims are kept in the main database (see Claim), renewed while the
  worker runs, and let go of when it is done.
- A worker that stopped without letting go (ie: it was killed) loses its
  claims once their time runs out; on the same machine they are taken
  over right away, since we can tell its process is gone.
"""

from contextlib import contextmanager
from os import getpid
from os import kill
from secrets import token_hex
from socket import gethostname
from threading import Event
from threading import Thread

from .Database import DatabaseUnavailable

import logging
from newsreap.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)

# What group and settle claims are called
GROUP_CLAIM = 'group:'
SETTLE_CLAIM = 'settle'

# How long (seconds) a claim lasts unless it is renewed; it is renewed
# every third of that while the worker runs
CLAIM_LEASE = 120

# How long close() waits (seconds) for a renewal that is under way
CLOSE_WAIT = 10


def running(pid):
    """
    Returns True if a process (on this machine) is still running
    """
    try:
        kill(pid, 0)

    except ProcessLookupError:
        return False

    except (PermissionError, OSError):
        # It is there, just not ours
        return True

    return True


class NNTPClaims:
    """
    The claims of one worker; see the module description
    """

    def __init__(self, database, lease=CLAIM_LEASE, owner=None):
        """
        - database: the main database (an NNTPDatabase, ie: NNTPSettings)
        - lease: how long (seconds) a claim lasts unless it is renewed
        - owner: who we are (the machine, our process and a random part
          when not given)
        """
        self.database = database
        self.lease = lease
        self.host = gethostname()[:80]
        self.owner = owner or '%s:%d:%s' % (self.host, getpid(), token_hex(4))

        # Renews our claims while we have any
        self._thread = None
        self._stop = Event()

    def claim(self, names, limit=None):
        """
        Claims what is named; returns the names we got (what another worker
        has is left out).  Raises DatabaseUnavailable if there is no
        database to keep them in.

        - limit: stop once this many are ours (ie: connection slots)
        """
        found = self.database.claim(
            names, self.owner, self.lease, limit=limit, free=self.stopped())
        if found:
            self._renewing()
        return found

    @contextmanager
    def holding(self, name):
        """
        Claims one thing for a block of work, and lets go of it afterwards:

            with claims.holding('settle') as ours:
                if ours:
                    ...

        It is ours when no other worker has it, or when there is no
        database to share it through.
        """
        try:
            ours = bool(self.claim([name]))

        except DatabaseUnavailable:
            # Nobody to share it with
            ours = True

        try:
            yield ours

        finally:
            if ours:
                self.release([name])

    def groups(self, names):
        """
        Claims groups (by name) for updating them; returns (the ones we
        got, the ones another worker is busy with).  Every group is ours
        when there is no database to share them through.
        """
        names = list(names)
        try:
            ours = {name[len(GROUP_CLAIM):] for name in self.claim(
                GROUP_CLAIM + name for name in names)}

        except DatabaseUnavailable:
            return names, []

        return ([name for name in names if name in ours],
                [name for name in names if name not in ours])

    def release(self, names=None):
        """
        Lets go of what we claimed (only the names given, when given)
        """
        try:
            self.database.release_claims(self.owner, names)

        except DatabaseUnavailable:
            pass

    def stopped(self):
        """
        Returns the owners on this machine whose process is gone (their
        claims can be taken over)
        """
        try:
            owners = {owner for owner, _ in self.database.claims().values()}

        except DatabaseUnavailable:
            return []

        found = []
        for owner in owners:
            host, _, rest = owner.rpartition(':')[0].rpartition(':')
            if host == self.host and rest.isdigit() and \
                    not running(int(rest)):
                found.append(owner)

        return found

    def _renewing(self):
        """
        Starts renewing our claims (once)
        """
        if self._thread is not None:
            return

        self._stop.clear()
        self._thread = Thread(target=self._renew, daemon=True,
                              name='NNTPClaims')
        self._thread.start()

    def _renew(self):
        """
        Renews our claims until we are closed
        """
        while not self._stop.wait(self.lease / 3.0):
            try:
                self.database.renew_claims(self.owner, self.lease)

            except Exception as e:
                # Tried again next time; our claims last a while yet
                logger.warning('Could not renew our claims: %s' % e)

    def close(self):
        """
        Stops renewing and lets go of everything we claimed
        """
        if self._thread is not None:
            # A renewal stuck on the database doesn't hold us up for long
            # (the thread ends with the program anyway)
            self._stop.set()
            self._thread.join(CLOSE_WAIT)
            self._thread = None

        self.release()

    def __repr__(self):
        return '<NNTPClaims owner=%s />' % self.owner
