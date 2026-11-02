# A small article kept (and searchable) offline
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
from sqlalchemy import Integer
from sqlalchemy import SmallInteger
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy.dialects.mysql import MEDIUMTEXT

from .ObjectBase import ObjectBase

# Text columns; MySQL's own TEXT only holds 64KB (less once a character
# takes more than one byte)
LongText = Text().with_variant(MEDIUMTEXT(), 'mysql')


class ArchiveKind:
    """
    What an archived article turned out to be
    """
    # Text; its body is kept so it can be searched
    Text = 0

    # A file (or part of one); only what it says about itself is kept
    # (see ArchiveFile)
    Binary = 1

    # No server had it when we asked
    Missing = 2

    # A hook refused to keep it (see NNTPArchiver)
    Refused = 3


# A list of the kinds defined above
ARCHIVE_KINDS = (
    ArchiveKind.Text,
    ArchiveKind.Binary,
    ArchiveKind.Missing,
    ArchiveKind.Refused,
)


class ArchivedPost(ObjectBase):
    """
    A small article (ie: a discussion post) fetched and kept so it can be
    read and searched offline.

    It is found by its Message-ID, the same one its header (Article) has
    in each group's cache.  Its text is what the article said, decoded
    (from base64, quoted-printable, its character set, ...) so it can be
    searched; encoded files are never kept, only what they say about
    themselves.
    """

    __tablename__ = 'archive_post'

    # A number of our own (the search index refers to posts by it)
    id = Column(Integer, primary_key=True)

    # The article's Message-ID (without its < and >)
    message_id = Column(String(250), nullable=False, unique=True)

    # What it is (see ArchiveKind)
    kind = Column(SmallInteger, nullable=False, default=ArchiveKind.Text,
                  index=True)

    # Its subject and poster (as the server listed them)
    subject = Column(String(256), nullable=False, default='')
    poster = Column(String(128), nullable=False, default='')

    # When it was posted (UTC)
    posted = Column(DateTime, index=True)

    # How big the article was (in bytes, as the server listed it)
    size = Column(Integer, nullable=False, default=0)

    # The Message-IDs it replies to (its References header), so threads can
    # be put back together
    references = Column(LongText)

    # Its text (decoded, so it can be searched); None for binaries
    text = Column(LongText)

    # How its text was stored in the article (ie: base64,
    # quoted-printable); empty when it was plain
    encoding = Column(String(64), nullable=False, default='')

    # The character set its text was written in
    charset = Column(String(32), nullable=False, default='')

    # The names of the files it holds or carries (space separated), so they
    # can be searched too (see ArchiveFile for the details)
    filenames = Column(LongText)

    # When we fetched it (or found it was missing), and from which server
    fetched = Column(DateTime)
    server = Column(String(128), nullable=False, default='')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def __repr__(self):
        return "<ArchivedPost(message_id='%s', kind=%s)>" % (
            self.message_id, self.kind)
