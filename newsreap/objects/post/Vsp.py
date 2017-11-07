# -*- coding: utf-8 -*-
#
#  The Variable System Parameter Object
#
# Copyright (C) 2015-2017 Chris Caron <lead2gold@gmail.com>
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
from sqlalchemy import String

from .ObjectBase import ObjectBase


class Vsp(ObjectBase):
    """
    A Variable System Parameter class contains a mapping of simple
    1:1 indexing.

    The table is effectively a hash table
    """

    __tablename__ = 'vsp'

    # Group (makes it easier to fetch groups)
    group = Column(String(256), nullable=False, index=True)

    # Hash Key
    key = Column(String(256), nullable=False)

    # Key Value
    value = Column(String(512))

    # Order
    order = Column(Integer())

    # Create our primary key based on the group and hash key
    __mapper_args__ = {"primary_key": (group, key)}

    def __init__(self, group, key, value=None, order=0, *args, **kwargs):
        super(Vsp, self).__init__(*args, **kwargs)
        self.group = group
        self.key = key
        self.value = value
        self.order = order

    def __repr__(self):
        return "<Vsp(key=%s, value='%s')>" % (self.key, self.value)
