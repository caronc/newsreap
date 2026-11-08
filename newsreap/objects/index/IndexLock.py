# Lets one worker at a time change the index
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

from sqlalchemy import BigInteger
from sqlalchemy import Column
from sqlalchemy import Integer

from .ObjectBase import ObjectBase


class IndexLock(ObjectBase):
    """
    A single row every worker changes first when it indexes a chunk (or
    settles collections).  The database lets only one of them change it at
    a time, until that one commits, so two workers never add the same
    collection twice; the others wait their turn.
    """
    __tablename__ = 'index_lock'

    # Always 1
    id = Column(Integer, primary_key=True, autoincrement=False)

    # How many times it was taken (only changed to take the lock)
    taken = Column(BigInteger, nullable=False, default=0)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def __repr__(self):
        return "<IndexLock(taken=%s)>" % self.taken
