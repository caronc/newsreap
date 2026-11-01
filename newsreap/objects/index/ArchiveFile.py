# A file an archived article holds or carries
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
from sqlalchemy import String

from .ObjectBase import ObjectBase


class ArchiveFile(ObjectBase):
    """
    What a file in an archived article says about itself (ie: the name,
    size and part from a yEnc header, or a MIME attachment's name and
    type).  The file itself is never kept.
    """

    __tablename__ = 'archive_file'

    id = Column(Integer, primary_key=True)

    # The article it was found in
    message_id = Column(
        String(250), ForeignKey('archive_post.message_id'), nullable=False,
        index=True)

    # The file's name
    filename = Column(String(256), nullable=False, default='')

    # How it was put in the article (ie: yenc, uuencode, mime)
    codec = Column(String(16), nullable=False, default='')

    # Its type (ie: image/jpeg) when the article says so
    content_type = Column(String(128), nullable=False, default='')

    # The size of the whole file (in bytes) when known
    size = Column(Integer)

    # Which part of the file this article holds, and of how many
    part = Column(Integer)
    total = Column(Integer)

    # The crc32 checksum given for the whole file (hex) when known
    crc32 = Column(String(8), nullable=False, default='')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def __repr__(self):
        return "<ArchiveFile(message_id='%s', filename='%s')>" % (
            self.message_id, self.filename)
