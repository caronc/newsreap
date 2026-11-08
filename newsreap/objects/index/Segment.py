# One article (segment) of a file
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
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import SmallInteger
from sqlalchemy import String
from newsreap.objects import BigNumber
from newsreap.objects import Key

from .ObjectBase import ObjectBase


class Segment(ObjectBase):
    """
    One article (segment) of a file.

    A Message-ID is unique across all of Usenet, so an article found in
    more than one group is only stored once.  The same part can show up
    more than once (ie: a repost); it is only counted (and put in an NZB)
    once.
    """

    __tablename__ = 'segment'

    # The article's Message-ID (without its < and >)
    message_id = Column(Key(250), primary_key=True)

    # The file it belongs to (many segments : one file)
    file_id = Column(
        BigNumber, ForeignKey('collection_file.id'), nullable=False,
        index=True)

    # Which part of the file it is (ie: 3 of (3/50))
    part = Column(Integer, nullable=False, default=1)

    # Its size (in bytes)
    size = Column(Integer, nullable=False, default=0)

    # Whether it carries a file or is only text (see ArchiveKind); learned
    # the first time it is fetched (None until then), so asking only for
    # text never fetches it again just to find out
    kind = Column(SmallInteger)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def __repr__(self):
        return "<Segment(message_id='%s', part=%d)>" % (
            self.message_id, self.part)
