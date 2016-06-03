# -*- coding: utf-8 -*-
#
# The GroupTrack Object used to track specific Groups
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

from sqlalchemy import Column
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import DateTime
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from datetime import datetime

# These are required since they are referenced inside the Tracking object
from lib.objects.nntp.Server import Server
from lib.objects.nntp.Group import Group
from lib.objects.nntp.ObjectBase import ObjectBase


class GroupTrack(ObjectBase):
    """
    A GroupTrack class contains a mapping of groups supported by the
    server in question in addition to any tracking details about it.

    Since we support multiple servers, we want to track each of
    their tracking pointers seperately.

    This table only only contains entries for the watched tables
    """
    __tablename__ = 'group_track'

    # A pointer to the server table tracking the NNTP Server
    server_id = Column(Integer, ForeignKey("server.id"))

    # A pointer to the group these statistics pertain to
    group_id = Column(Integer, ForeignKey("group.id"))

    # Low Watermark (head)
    low = Column(Integer, nullable=False, default=0)

    # High Watermark (tail)
    high = Column(Integer, nullable=False, default=0)

    # Current Pointer
    index_pointer = Column(Integer, nullable=False)

    # Scan Pointer
    scan_pointer = Column(Integer, nullable=False)

    # Tracks the last time a scan was done (moving the current pointer)
    last_scan = Column(DateTime, nullable=False, default=datetime.fromtimestamp(0))

    # Tracks the last time an index was done (moving the current pointer)
    last_index = Column(DateTime, nullable=False, default=datetime.fromtimestamp(0))

    # Track the last time the record was updated
    last_updated = Column(DateTime, nullable=False, server_default=func.now())

    # Associate our database relation
    group = relationship("Group")
    server = relationship("Server")

    # Create our primary key based on the server and group id's
    __mapper_args__ = { "primary_key": (server_id, group_id) }


    def __init__(self, *args, **kwargs):
        super(GroupTrack, self).__init__(*args, **kwargs)


    def __repr__(self):
        return "<GroupTrack(name='%s', current='%s', end='%s')>" % (
                             self.name, self.current, self.end)
