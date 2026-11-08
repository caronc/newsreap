# One file of a collection
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
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import DateTime
from newsreap.objects import BigNumber
from newsreap.objects import Key

from .ObjectBase import ObjectBase


class CollectionFile(ObjectBase):
    """
    One file of a collection; its segments are the articles it was posted
    in (see Segment) and FileGroup lists the groups they were found in
    """

    __tablename__ = 'collection_file'

    # primary key
    id = Column(BigNumber, primary_key=True)

    # What makes this file unique (its collection and the subject with its
    # segment counter left out)
    key = Column(Key(512), nullable=False, unique=True)

    # The collection the file belongs to (many files : one collection)
    collection_id = Column(
        Integer, ForeignKey('collection.id'), nullable=False, index=True)

    # The subject of its first segment and who posted it
    subject = Column(String(256), nullable=False)
    poster = Column(String(128), nullable=False)

    # The file name (from the subject)
    filename = Column(String(256), nullable=False)

    # The file's number within its collection (ie: 3 of [3/10]); 0 if the
    # subject didn't say
    file_no = Column(Integer, nullable=False, default=0)

    # Where it was listed in the NZB file it came from (None when it was
    # scanned; those are sorted the way newznab sorts them)
    position = Column(Integer)

    # How many segments the subject said there would be, how many we found
    # (each part number counted once) and their size in bytes
    segments_expected = Column(Integer, nullable=False, default=1)
    segments = Column(Integer, nullable=False, default=0)
    size = Column(BigInteger, nullable=False, default=0)

    # When its first and last segment were posted
    first_posted = Column(DateTime)
    last_posted = Column(DateTime)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def __repr__(self):
        return "<CollectionFile(id=%s, filename='%s')>" % (
            self.id, self.filename)
