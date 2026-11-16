# An article a download using a staging directory listed
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

from itertools import batched

from sqlalchemy import Boolean
from sqlalchemy import Column
from sqlalchemy import String
from sqlalchemy import text

from .ObjectBase import ObjectBase

# How many Message-IDs are written per statement
LIST_CHUNK = 2000


class ListedSegment(ObjectBase):
    """
    One article a download using a staging directory listed (see
    NNTPDownloader).  A staging directory is named after a download's
    name and first article, so another download can come to the same one;
    what was listed tells them apart:

    - every article any run listed is kept (mine is set for the ones the
      run using it now lists)
    - a run listing everything listed before is the same download (ie: a
      collection that grew), and carries on
    - one that doesn't is another download; its parts, files and where it
      was placed are not this one's to use or remove
    """

    __tablename__ = 'listed_segment'

    # The article
    message_id = Column(String(250), primary_key=True)

    # True when the run using the staging directory now lists it
    mine = Column(Boolean, nullable=False, default=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @classmethod
    def begin(cls, session):
        """
        Starts a run: what was listed before is nobody's until this run
        lists it too (not committed)
        """
        session.query(cls).update(
            {cls.mine: False}, synchronize_session=False)

    @classmethod
    def add(cls, session, message_ids):
        """
        Notes articles this run lists (a couple thousand per statement; any
        number of them, given as any iterable); not committed
        """
        statement = text(
            'INSERT INTO listed_segment (message_id, mine) VALUES (:id, 1) '
            'ON CONFLICT (message_id) DO UPDATE SET mine = 1')
        for chunk in batched(message_ids, LIST_CHUNK):
            session.execute(statement, [{'id': m} for m in chunk])

    @classmethod
    def others(cls, session):
        """
        Returns how many articles listed before this run doesn't list (0
        when everything here is this run's)
        """
        return session.query(cls).filter(cls.mine.is_(False)).count()

    def __repr__(self):
        return "<ListedSegment(message_id='%s', mine=%s)>" % (
            self.message_id, self.mine)
