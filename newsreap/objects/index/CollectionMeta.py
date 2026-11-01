# Extra details about a collection (from an NZB file)
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


class CollectionMeta(ObjectBase):
    """
    One detail an NZB file gives about its post (one <meta type="...">
    entry of its <head>, ie: title, password, category).

    They are kept just as the NZB file had them (in the same order), so
    saving the collection again writes them all back.  A type can be given
    more than once (ie: several passwords to try).
    """

    __tablename__ = 'collection_meta'

    # Keeps them in the order the NZB file had them
    id = Column(Integer, primary_key=True)

    # The collection it describes
    collection_id = Column(
        Integer, ForeignKey('collection.id'), nullable=False, index=True)

    # What it is (the meta type, ie: password)
    type = Column(String(64), nullable=False)

    # Its value
    value = Column(String(1024), nullable=False, default='')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def __repr__(self):
        return "<CollectionMeta(collection_id=%s, type='%s')>" % (
            self.collection_id, self.type)
