# -*- coding: utf-8 -*-
#
# The Group Object used to wrap NNTP Groups
#
# Copyright (C) 2015 Chris Caron <lead2gold@gmail.com>
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


from sqlalchemy.sql import func
from sqlalchemy import Column
from sqlalchemy import Sequence
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Boolean
from sqlalchemy import DateTime

from newsreap.objects.nntp.ObjectBase import ObjectBase


class Group(ObjectBase):
    """
    A Group class contains a mapping of groups supported by the
    server in question.

    This group is rather heavy on the indexing; but it's generally
    pretty static and changes very infrequently after it's built
    once. So the heavy indexing only poses an initial expense
    """
    __tablename__ = 'group'

    # primary key
    id = Column(Integer, Sequence('id_seq'), primary_key=True)

    # group name
    name = Column(String(128), nullable=False, unique=True, index=True)

    # Some statistics that are just fetched from the NNTP Server; this
    # is a lose value since counts will vary across multiple usenet
    # servers with multiple retentions.  This value is based on the
    # primary server.
    count = Column(Integer, nullable=False, default=0)

    # group flags (associated with group)
    flags = Column(String, nullable=False, default='')

    # watch flag
    watch = Column(Boolean, nullable=False, default=False, index=True)

    # Track the time the group statistics were last updated
    last_updated = Column(DateTime, server_default=func.now())


    def __init__(self, *args, **kwargs):
        super(Group, self).__init__(*args, **kwargs)


    def __repr__(self):
        return "<Group(id='%d', name='%s', flag=%s', watch='%s')>" % (
                self.id, self.name, self.flag, self.watch)
