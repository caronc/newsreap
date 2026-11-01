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

from datetime import timedelta
from os import makedirs
from os.path import dirname

# Importing these libraries forces them associate themselves
# with the ObjectBase
from .objects.group.Article import Article
from .objects.group.ScannedRange import ScannedRange

# The ObjectBase which contains all of the data required to
# access our table.
from .objects.group.ObjectBase import ObjectBase
from .objects.group.Vsp import Vsp

from .Database import Database
from .Utils import utcnow

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

        with db._engine.begin() as conn:
            # Favour speed; our callers commit their progress as they go
            conn.exec_driver_sql('PRAGMA journal_mode = MEMORY')
            conn.exec_driver_sql('PRAGMA synchronous = OFF')
            conn.exec_driver_sql('PRAGMA temp_store = MEMORY')

        return db

    def purge(self, days):
        """
        Removes the indexed article headers posted more than `days` days ago
        and returns how many were removed.

        The collections they were indexed into keep everything an NZB file
        needs, so only the raw headers go.  The ranges they came from stay
        marked as scanned, so they aren't fetched again.
        """
        if not days or days <= 0:
            # Keep everything
            return 0

        session = self.session()
        removed = session.query(Article)\
            .filter(Article.indexed.is_(True))\
            .filter(Article.posted_date < utcnow() - timedelta(
                days=days))\
            .delete(synchronize_session=False)
        session.commit()
        return removed
