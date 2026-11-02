# A collection of files posted together (ie: one release)
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
from sqlalchemy import SmallInteger
from sqlalchemy import String
from sqlalchemy import DateTime

from .ObjectBase import ObjectBase


class CollectionState:
    """
    Where a collection is at
    """
    # Its articles are still arriving
    Forming = 0

    # Every file and every segment was found
    Complete = 1

    # Some parts are missing, but nothing new arrived for a while; it is
    # as complete as it is going to get
    Settled = 2

    # Removed (nr delete); new articles that belong to it are ignored
    Deleted = 3


class CollectionSource:
    """
    Where a collection came from
    """
    # Put together from the article headers we scanned (our best guess)
    Scan = 0

    # Read from an NZB file; this is the real post, so scanning never
    # changes it
    Nzb = 1


# The states a collection is ready to be downloaded in
COLLECTION_FORMED = (CollectionState.Complete, CollectionState.Settled)


class Collection(ObjectBase):
    """
    Files a poster posted together (ie: the .rar, .par2 and .nfo files of
    one release), found in one or more groups.

    It keeps everything an NZB file needs (with its files, segments and
    CollectionMeta), so it can be saved as one long after the article
    headers it was built from are gone.  The counts are kept up to date by `nr update index`,
    so listing collections never has to count their segments.
    """

    __tablename__ = 'collection'

    # primary key
    id = Column(Integer, primary_key=True)

    # What its articles have in common (the poster, its name and how many
    # files it has); a repost long after the first gets a collection of
    # its own with the same key
    key = Column(String(512), nullable=False, index=True)

    # A lasting id given once it has formed: the md5 of the Message-ID of
    # the first segment of its first file (files sorted by subject).  The
    # same post always gets the same one, wherever it was found (an NZB
    # file of it gets the same one too).
    gid = Column(String(32), unique=True)

    # Where it came from (see CollectionSource)
    source = Column(
        SmallInteger, nullable=False, default=CollectionSource.Scan,
        index=True)

    # Where it is at (see CollectionState)
    state = Column(
        SmallInteger, nullable=False, default=CollectionState.Forming,
        index=True)

    # Who posted it
    poster = Column(String(128), nullable=False, index=True)

    # Its name (the description posted with it, or the file name without
    # its extension)
    name = Column(String(256), nullable=False, index=True)

    # How many files the subjects said there would be (0 if unknown) and
    # how many we found
    files_expected = Column(Integer, nullable=False, default=0)
    files = Column(Integer, nullable=False, default=0)

    # How many segments (articles) all of its files need, and how many we
    # found
    segments_expected = Column(Integer, nullable=False, default=0)
    segments = Column(Integer, nullable=False, default=0)

    # The size of everything we found (in bytes)
    size = Column(Integer, nullable=False, default=0)

    # When its first and last article were posted
    first_posted = Column(DateTime, index=True)
    last_posted = Column(DateTime)

    # The best score of its articles, and every tag they were given (space
    # separated); see NNTPFilter
    score = Column(
        Integer, nullable=False, default=0, server_default='0', index=True)
    tags = Column(
        String(256), nullable=False, default='', server_default='')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @property
    def complete(self):
        """
        True when every file and every segment was found
        """
        return self.segments >= self.segments_expected and (
            not self.files_expected or self.files >= self.files_expected)

    def __repr__(self):
        return "<Collection(id=%s, name='%s')>" % (self.id, self.name)
