# -*- coding: utf-8 -*-
#
#  The Server Object used to track NNTP Servers
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
from sqlalchemy import Sequence
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Enum
from sqlalchemy import Boolean

from newsreap.NNTPIOStream import NNTP_DEFAULT_ENCODING
from newsreap.NNTPIOStream import NNTPIOStream
from newsreap.NNTPIOStream import NNTP_SUPPORTED_IO_STREAMS
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
    host = Column(String(128), default='localhost', index=True,
                  nullable=False, unique=True)

    # NNTP Port
    port = Column(Integer, default=119, nullable=False)

    # NNTP Username
    username = Column(String(128), default=None, nullable=True)

    # NNTP Password
    password = Column(String(128), default=None, nullable=True)

    # NNTP SSL Enabled
    secure = Column(Boolean, default=True, nullable=False)

    # Verify SSL Key
    verify_cert = Column(Boolean, default=True, nullable=False)

    # NNTP Stream Type
    iostream = Column(
        Enum(*NNTP_SUPPORTED_IO_STREAMS),
        default=NNTPIOStream.RFC3977_GZIP,
        nullable=False,
    )

    # NNTP Join Group
    join_group = Column(Boolean, default=True, nullable=False)

    # NNTP use BODY over ARTICLE
    use_body = Column(Boolean, default=True, nullable=False)

    # NNTP use HEAD over STAT
    use_head = Column(Boolean, default=True, nullable=False)

    # Server Priority
    # The lowest defined prioirty is always treated
    # as the main server, any others found are treated
    # as block accounts
    priority = Column(Integer, default=0, nullable=False)

    # Default Encoding the NNTP Server stores it's Articles
    encoding = Column(
        String(32),
        default=NNTP_DEFAULT_ENCODING,
        nullable=False,
    )

    # This allows us to define a configuration but leave
    # it disabled.
    enabled = Column(Boolean, default=True, nullable=False)


    def __init__(self, host, *args, **kwargs):
        super(Server, self).__init__(*args, **kwargs)
        self.host = host

        name = kwargs.get('name')
        if not name:
            # if no name is specified, then use the hostname
            self.name = self.host

        # We add the 'compress' flag to allow simplified True/False
        # fields which automatically set our iostream to
        # NNTPIOStream.RFC3977_GZIP (if set to True) and
        # NNTPIOStream.RFC3977 (if set to False)
        compress = kwargs.get('compress')
        if isinstance(compress, bool):
            if compress:
                # NNTP Compression
                self.iostream = NNTPIOStream.RFC3977_GZIP
            else:
                # NNTP Standard
                self.iostream = NNTPIOStream.RFC3977


    def __repr__(self):
        return "<Server(id=%s, name='%s', port=%d', ssl='%s')>" % (
                             self.id, self.host, self.port, self.secure)


    def dict(self):
        """
        returns a mapped dictionary of all of the fields and their values
        that can be used to load up the NNTPConnection() object.

        It can also be used for writing back configuration to a flat file
        controlled by NNTPSettings() too.
        """
        return {
            # NNTPConnection() variables
            'username': self.username,
            'password': self.password,
            'secure': self.secure,
            'verify_cert': self.verify_cert,
            'join_group': self.join_group,
            'use_head': self.use_head,
            'use_body': self.use_body,
            'priority': self.priority,
            'compress': self.iostream is NNTPIOStream.RFC3977_GZIP,

            # SocketBase() variables
            'host': self.host,
            'port': self.port,

            # Group Encoding
            'encoding': self.encoding,

            # We pass along the enabled flag too
            'enabled': self.enabled,
        }
