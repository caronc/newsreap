# Decides when an article no server had is worth asking for again
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
Decides when an article no server had (or only had damaged) is worth
asking for again.  Asking again can only help when:

- a server was added since it was last asked for, or
- the post was new when it was last asked for; a post takes a while to
  reach every server (usually minutes), so it is asked for again for a
  while (the window) after it was posted

Otherwise the answer won't change, so it isn't asked for again.

What is remembered of such an article (in the .ERRORS file of the file it
belongs to; see NNTPErrorsFile) is when it was asked for and a short hash
of each server asked: its host:port name in lower case.  Logins, and which
one is the main server, don't count.  Removing a server doesn't make
anything worth asking for again; adding one does.
"""

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from hashlib import sha1

from .Utils import utcnow

# How long after it was posted an article is asked for again (minutes)
DEFAULT_RETRY_WINDOW = 60

# Asked for again no sooner than this after the last time
RETRY_GAP = timedelta(minutes=2)

# A post date before this isn't a real one (ie: an NZB file without one)
EARLIEST_POST = datetime(1990, 1, 1)


def server_name(server):
    """
    Returns 'host:port' (in lower case) of a server: a dict with a host and
    port (as the configuration has them), or a (host, port) pair
    """
    if isinstance(server, dict):
        host, port = server.get('host'), server.get('port')

    else:
        host, port = server

    return '%s:%s' % (str(host or '').strip().rstrip('.').lower(),
                      str(port or '').strip())


def server_hash(server):
    """
    Returns the short hash a server is remembered by (12 hex digits of the
    sha1 of its server_name())
    """
    return sha1(server_name(server).encode('utf-8')).hexdigest()[:12]


class NNTPRetry:
    """
    Decides when an article that couldn't be had is worth asking for
    again; see the module description
    """

    def __init__(self, servers=(), window=DEFAULT_RETRY_WINDOW, always=False):
        """
        Prepares the rule for the servers we have now

        - servers: the servers articles are asked of (see server_name())
        - window: how many minutes after it was posted an article is asked
          for again
        - always: ask again for everything (whatever was remembered)
        """
        self.hosts = frozenset(
            name for name in map(server_name, servers) if name != ':')

        # The hash of each (see server_hash()), sorted as they are written
        self.hashes = tuple(sorted({
            sha1(name.encode('utf-8')).hexdigest()[:12]
            for name in self.hosts}))

        try:
            self.window = timedelta(minutes=max(0.0, float(window)))

        except (TypeError, ValueError, OverflowError):
            self.window = timedelta(minutes=DEFAULT_RETRY_WINDOW)

        self.always = bool(always)

    @staticmethod
    def posted(when):
        """
        Returns a post date we can use, as naive UTC (None when it isn't a
        real one)
        """
        if not isinstance(when, datetime):
            return None

        if when.tzinfo is not None:
            when = when.astimezone(UTC).replace(tzinfo=None)
        return when if when >= EARLIEST_POST else None

    def state(self, tried_by, tried_at, posted, now=None):
        """
        Returns what to do about an article that couldn't be had:

        - 'ask': ask for it again
        - 'wait': it is a new post that was asked for a moment ago; ask a
          little later
        - 'settled': none of our servers will have it, ask no more

        - tried_by: the hashes of the servers asked last time (see
          server_hash(); empty when it never was)
        - tried_at: when it was last asked for
        - posted: when it was posted (None when that isn't known)
        """
        if self.always or not tried_by or not isinstance(
                tried_at, datetime):
            return 'ask'

        if not set(self.hashes) <= set(tried_by):
            # A server was added since
            return 'ask'

        posted = self.posted(posted)
        if posted is not None and abs(tried_at - posted) < self.window:
            # New when it was asked for; it may have reached a server since
            now = now or utcnow()
            return 'ask' if now - tried_at >= RETRY_GAP else 'wait'

        return 'settled'

    def __str__(self):
        return ', '.join(sorted(self.hosts)) or 'no servers'

    def __repr__(self):
        return '<NNTPRetry servers=%d window=%s />' % (
            len(self.hosts), self.window)
