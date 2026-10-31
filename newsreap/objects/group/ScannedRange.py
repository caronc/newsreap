#  A range of articles already fetched (scanned) from a group
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
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import DateTime
from sqlalchemy.sql import func

from .ObjectBase import ObjectBase


class ScannedRange(ObjectBase):
    """
    A range of article numbers (low to high) already fetched from a server.

    - Ranges that touch or overlap are merged into one, so the fewer rows
      there are the fewer gaps are left to fetch.
    - Article numbers are only meaningful to the server they came from, so
      each range belongs to a server.
    - It lives in the group's own database; removing that database starts
      the group over.
    """

    __tablename__ = 'scanned_range'

    # primary key
    id = Column(Integer, primary_key=True)

    # The server (host) the article numbers belong to
    server = Column(String(128), nullable=False, index=True)

    # The first and last article number fetched (both included)
    low = Column(Integer, nullable=False, index=True)
    high = Column(Integer, nullable=False)

    # When this range was last added to
    scanned = Column(DateTime, nullable=False, server_default=func.now())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def __repr__(self):
        return "<ScannedRange(server='%s', low=%d, high=%d)>" % (
            self.server, self.low, self.high)
