# The database every collection found (across all groups) is kept in
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

import gevent.monkey
gevent.monkey.patch_all()

from os import makedirs
from os.path import dirname

# Importing these libraries forces them associate themselves
# with the ObjectBase
from .objects.index.Collection import Collection
from .objects.index.CollectionFile import CollectionFile
from .objects.index.FileGroup import FileGroup
from .objects.index.Segment import Segment

# The ObjectBase which contains all of the data required to
# access our table.
from .objects.index.ObjectBase import ObjectBase
from .objects.index.Vsp import Vsp

from .Database import Database


class NNTPIndexDatabase(Database):
    """
    A managment class to handle the Index Database (the collections,
    files and segments found in every group)
    """

    def __init__(self, engine=None, reset=None):
        """
        Initialize NNTP Index Database
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
