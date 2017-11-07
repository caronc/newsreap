# -*- coding: utf-8 -*-
#
# NewsReap NNTP Group Alias Object
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
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import String

from .Group import Group
from .ObjectBase import ObjectBase


class GroupAlias(ObjectBase):
    """
    Group Aliases allow you to map NNTP Group names into you're
    own notation.  Hence map 'alt.binaries.test' into just 'a.b.test'.
    Or maybe just map alt.binaries.test to just the word 'test'.

    Short forming group names makes it easier to work with them
    from the CLI (Command Line Interface)

    Aliases are intentionally kept in their own group so they don't
    conflict with peoples configurations to which they've defined
    multiple servers. One alias to rule them all.
    """
    __tablename__ = 'group_alias'

    # A pointer to the group we're creating an alias to
    group_id = Column(Integer, ForeignKey("group.id"))

    # Allow alias mapping to groups, ie, swap alt.binaries to a.b.
    # Aliases greatly simplify command line control
    name = Column(String(64), index=True, nullable=False)

    # Create our primary key based on the server and group id's
    __mapper_args__ = {"primary_key": (group_id, name)}

    def __repr__(self):
        return "<GroupAlias(name='%s')>" % (self.name)
