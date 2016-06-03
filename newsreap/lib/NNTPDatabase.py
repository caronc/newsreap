# -*- coding: utf-8 -*-
#
# A common NNTP Database management class for manipulating SQLAlchemy
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

import gevent.monkey
gevent.monkey.patch_all()

# Importing these libraries forces them associate themselves
# with the ObjectBase
from lib.objects.nntp.Group import Group
from lib.objects.nntp.GroupAlias import GroupAlias
from lib.objects.nntp.GroupTrack import GroupTrack
from lib.objects.nntp.Server import Server

# The ObjectBase which contains all of the data required to
# access our table.
from lib.objects.nntp.ObjectBase import ObjectBase
from lib.objects.nntp.Vsp import Vsp

from lib.Database import Database

# The catch wit SQLLite when referencing paths is:
# sqlite:///relative/path/to/where we are now
# sqlite:////absolute/path/
#
# TODO: When creating an error message, it might make sense to parse
# the database path 'IF using SQLite' to make the error
# messages more feasable
# configure Session class with desired options


class NNTPDatabase(Database):
    """
    A managment class to the NNTP Core Database manipulation.
    """

    def __init__(self, engine=None, reset=None):
        """
        Initialize NNTP Database Manager
        """
        super(NNTPDatabase, self).__init__(
            base=ObjectBase,
            vsp=Vsp,
            engine=engine,
            reset=reset,
        )
