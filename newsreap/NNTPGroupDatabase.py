# A common Group Database management class used by SQLAlchemy
#
# Copyright (C) 2015-2017 Chris Caron <lead2gold@gmail.com>
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

import gevent.monkey
gevent.monkey.patch_all()

from datetime import datetime
from datetime import timedelta
from os import makedirs
from os.path import dirname

from sqlalchemy import func

# Importing these libraries forces them associate themselves
# with the ObjectBase
from .objects.group.Article import Article
from .objects.group.ScannedRange import ScannedRange

# The ObjectBase which contains all of the data required to
# access our table.
from .objects.group.ObjectBase import ObjectBase
from .objects.group.Vsp import Vsp

from .Database import Database

# How many Message-IDs are looked up at a time (see forget())
FORGET_CHUNK_SIZE = 500
from .Utils import utcnow

# Where we remember (per server) when a scan last reached the newest
# article of the group
CAUGHT_UP_GROUP = 'newsreap.caught_up'

# How those times are written down
CAUGHT_UP_FORMAT = '%Y-%m-%d %H:%M:%S'

# The catch wit SQLite when referencing paths is:
# sqlite:///relative/path/to/where we are now
# sqlite:////absolute/path/


class NNTPGroupDatabase(Database):
    """
    A managment class to handle Group/Article Databases
    """

    def __init__(self, engine=None, reset=None):
        """
        Initialize NNTP Group/Article Database
        """
        super().__init__(
            base=ObjectBase,
            vsp=Vsp,
            engine=engine,
            reset=reset,
        )

    @classmethod
    def from_file(cls, db_file):
        """
        Opens the (SQLite) database stored in db_file, creating it (and its
        directory) if it doesn't exist yet
        """
        makedirs(dirname(db_file), exist_ok=True)
        db = cls(engine='sqlite:///%s' % db_file, reset=False)

        # Older databases may not have every table yet
        ObjectBase.metadata.create_all(db._engine)

        # A scan stopped hard (ie: killed) while filling a new database may
        # have left it without the indexes it drops for speed
        for index in Article.__table__.indexes:
            index.create(bind=db._engine, checkfirst=True)

        return db

    def forget(self, message_ids):
        """
        Lets go of the cached headers of these articles (Message-IDs, with
        or without their < and >) and returns how many there were.  The
        ranges they came from stay marked as scanned, so they aren't
        fetched again.
        """
        ids = [m.strip().strip('<>') for m in message_ids]
        session = self.session()
        removed = 0
        for start in range(0, len(ids), FORGET_CHUNK_SIZE):
            chunk = ids[start:start + FORGET_CHUNK_SIZE]

            # Looked up by the Message-ID itself (so its index is used);
            # both ways it may have been cached are asked for
            removed += session.query(Article)\
                .filter(Article.message_id.in_(
                    chunk + ['<%s>' % m for m in chunk]))\
                .delete(synchronize_session=False)
        session.commit()
        return removed

    def purge(self, days, dry_run=False):
        """
        Removes the indexed article headers posted more than `days` days ago
        and returns how many were removed (or would be, with `dry_run`).

        The collections they were indexed into keep everything an NZB file
        needs, so only the raw headers go.  The ranges they came from stay
        marked as scanned, so they aren't fetched again.
        """
        if not days or days <= 0:
            # Keep everything
            return 0

        session = self.session()
        query = session.query(Article)\
            .filter(Article.indexed.is_(True))\
            .filter(Article.posted_date < utcnow() - timedelta(
                days=days))
        if dry_run:
            return query.count()

        removed = query.delete(synchronize_session=False)
        session.commit()
        return removed

    def caught_up(self, server, checked):
        """
        Remembers that a scan of `server` reached the newest article it had
        when we asked at `checked` (UTC); nothing posted before then is
        missing from the cache
        """
        self.session()
        return self.set(
            server, checked.strftime(CAUGHT_UP_FORMAT), group=CAUGHT_UP_GROUP)

    def clock(self):
        """
        Returns how far (in UTC) we have seen the group: the later of the
        newest article cached and the last time a scan reached the newest
        article a server had.  None if nothing is cached yet.

        Collections only settle once the clocks of their groups have moved
        well past them; a group we haven't scanned in a while can't make
        a collection look older than it is.
        """
        session = self.session()

        # The newest article we have
        times = [session.query(func.max(Article.posted_date)).scalar()]

        # When each server was last caught up with
        for value, in session.query(self.Vsp.value)\
                .filter(self.Vsp.group == CAUGHT_UP_GROUP):
            try:
                times.append(datetime.strptime(value, CAUGHT_UP_FORMAT))

            except (TypeError, ValueError):
                # Not something we wrote; ignore it
                continue

        times = [t for t in times if t is not None]
        return max(times) if times else None
