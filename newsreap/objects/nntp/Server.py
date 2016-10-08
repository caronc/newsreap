# -*- coding: utf-8 -*-
#
#  The Server Object used to track NNTP Servers
#
# Copyright (C) 2015-2016 Chris Caron <lead2gold@gmail.com>
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
from sqlalchemy import Sequence
from sqlalchemy import Integer
from sqlalchemy import String

from newsreap.objects.nntp.ObjectBase import ObjectBase


class Server(ObjectBase):
    """
    A Server class contains a mapping of nntp servers we're
    scanning and connection details for them.
    """

    __tablename__ = 'server'

    id = Column(Integer, Sequence('server_id_seq'), primary_key=True)

    # Descriptive NNTP Server name
    name = Column(String(256), nullable=False)

    # NNTP Host Identifier
    host = Column(String(128), index=True,
                  nullable=False, unique=True)

    def __init__(self, *args, **kwargs):
        super(Server, self).__init__(*args, **kwargs)

    def __repr__(self):
        """
        Printable string
        """
        return "<Server(id=%s, host='%s')>" % (self.id, self.host)
