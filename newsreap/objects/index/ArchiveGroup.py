# The groups an archived article was found in
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
from sqlalchemy import String

from .ObjectBase import ObjectBase


class ArchiveGroup(ObjectBase):
    """
    A group an archived article was found in (an article can be posted to
    several; it is only fetched and kept once)
    """

    __tablename__ = 'archive_group'

    # The article
    message_id = Column(
        String(250), ForeignKey('archive_post.message_id'),
        primary_key=True)

    # The group's name
    name = Column(String(128), primary_key=True, index=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def __repr__(self):
        return "<ArchiveGroup(message_id='%s', name='%s')>" % (
            self.message_id, self.name)
