# -*- coding: utf-8 -*-
#
# A common NNTP Posting Database management class used by SQLAlchemy
#
# Copyright (C) 2017 Chris Caron <lead2gold@gmail.com>
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

# The ObjectBase which contains all of the data required to
# access our table.
from .objects.post.ObjectBase import ObjectBase
from .objects.post.Vsp import Vsp
from .Database import Database


class NNTPPostDatabase(Database):
    """
    A managment class to handle Group/Article Databases
    """

    def __init__(self, engine=None, reset=None):
        """
        Initialize NNTP Posting Database
        """
        super(NNTPPostDatabase, self).__init__(
            base=ObjectBase,
            vsp=Vsp,
            engine=engine,
            reset=reset,
        )
