# A segment (article) of a download, and how fetching it went
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
from sqlalchemy import Boolean
from sqlalchemy import Column
from sqlalchemy import Integer
from sqlalchemy import String

from .ObjectBase import ObjectBase


class DownloadedSegment(ObjectBase):
    """
    One article of a download (see NNTPDownloader).  A row is only written
    once its decoded part is safely on disk, so a download stopped part way
    knows exactly which segments it still needs.
    """

    __tablename__ = 'downloaded_segment'

    # The article
    message_id = Column(String(250), primary_key=True)

    # The file it is part of (the NZB's name for it, until the article says)
    filename = Column(String(256), nullable=False, default='')

    # Its place among the file's segments (the NZB's number)
    number = Column(Integer, nullable=False, default=0)

    # Where its bytes go in the file (from its yEnc =ypart line)
    begin = Column(BigInteger, nullable=False, default=0)
    end = Column(BigInteger, nullable=False, default=0)

    # Its decoded part (a file of the staging directory); empty when it
    # couldn't be had
    path = Column(String(512), nullable=False, default='')

    # True when its crc32 matched what was posted
    valid = Column(Boolean, nullable=False, default=False)

    # True when no server has it (it isn't asked for again)
    missing = Column(Boolean, nullable=False, default=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def __repr__(self):
        return "<DownloadedSegment(message_id='%s', filename='%s', " \
            "number=%d)>" % (self.message_id, self.filename, self.number)
