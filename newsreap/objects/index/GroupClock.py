# How far each group has been seen
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

from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import String
from newsreap.objects import Key

from .ObjectBase import ObjectBase


class GroupClock(ObjectBase):
    """
    How far (in UTC) we have seen a group (see NNTPGroupDatabase.clock()).

    A collection still forming only settles once every group it was found
    in has been seen well past its last article; a group we haven't
    scanned in a while can't make a collection look older than it is.
    """

    __tablename__ = 'group_clock'

    # The group's name
    name = Column(Key(128), primary_key=True)

    # Everything posted before this has been seen
    seen = Column(DateTime, nullable=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def __repr__(self):
        return "<GroupClock(name='%s', seen=%s)>" % (self.name, self.seen)
