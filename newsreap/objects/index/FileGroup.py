# A group a file was found in
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
from newsreap.objects import BigNumber
from newsreap.objects import Key

from .ObjectBase import ObjectBase


class FileGroup(ObjectBase):
    """
    A group a file was found in.

    A file can be found in many groups (ie: it was crossposted), and the
    files of one collection don't have to share the same groups.  The
    groups end up in the NZB file, and are how collections are listed by
    group.
    """

    __tablename__ = 'file_group'

    # The file (see CollectionFile)
    file_id = Column(
        BigNumber, ForeignKey('collection_file.id'), primary_key=True)

    # The group's name
    name = Column(Key(128), primary_key=True, index=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def __repr__(self):
        return "<FileGroup(file_id=%s, name='%s')>" % (
            self.file_id, self.name)
