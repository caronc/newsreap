# Other NZB files of a collection
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


class CollectionAlias(ObjectBase):
    """
    The GID of another NZB file of a collection we already had.

    An article only belongs to one collection, so a second NZB file of the
    same post doesn't become a collection of its own; its GID points to the
    one we kept instead.  Importing it again (or looking it up by its GID)
    finds that collection.
    """

    __tablename__ = 'collection_alias'

    # The other NZB file's GID
    gid = Column(String(32), primary_key=True)

    # The collection it is a copy of
    collection_id = Column(
        Integer, ForeignKey('collection.id'), nullable=False, index=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def __repr__(self):
        return "<CollectionAlias(gid='%s', collection_id=%s)>" % (
            self.gid, self.collection_id)
