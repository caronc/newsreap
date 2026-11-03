# What 'nr update' does for a group on its own (set with 'nr group watch')
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

from sqlalchemy import Boolean
from sqlalchemy import Column
from sqlalchemy import ForeignKey
from sqlalchemy import Integer

from .ObjectBase import ObjectBase


class GroupOption(ObjectBase):
    """
    The steps 'nr update' always does for a group (without being asked on
    the command line).  None means the group follows the setting of the
    same name under processing: in the configuration file.
    """
    __tablename__ = 'group_option'

    # The group
    group_id = Column(Integer, ForeignKey('group.id'), primary_key=True)

    # Keep what its small articles say (as 'nr update --content')
    content = Column(Boolean)

    # Read the NZB files posted to it (as 'nr update --nzb')
    nzb = Column(Boolean)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def __repr__(self):
        return "<GroupOption(group_id=%s, content=%s, nzb=%s)>" % (
            self.group_id, self.content, self.nzb)
