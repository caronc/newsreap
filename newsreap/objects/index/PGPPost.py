# A PGP key an archived post names
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
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import SmallInteger
from sqlalchemy import String
from newsreap.objects import Key

from .ObjectBase import ObjectBase


class PGPRole:
    """
    How a post is tied to a key
    """
    # It was signed with the key
    Signed = 0

    # It was encrypted for the key (only its holder can read it)
    EncryptedFor = 1

    # It carries the key itself (someone shared it)
    Carries = 2


# The key ID kept for a post encrypted for someone who isn't named (the
# sender hid it; only trying a key tells whether it is yours)
HIDDEN_KEY_ID = 'HIDDEN'

# How each one reads
PGP_ROLES = {
    PGPRole.Signed: 'signed by',
    PGPRole.EncryptedFor: 'encrypted for',
    PGPRole.Carries: 'carries',
}


class PGPPost(ObjectBase):
    """
    A PGP key an archived post names (see NNTPKeyring).  The key itself
    doesn't have to be known: the key ID is what the post gives.
    """

    __tablename__ = 'pgp_post'

    # The post
    message_id = Column(
        Key(250), ForeignKey('archive_post.message_id'),
        primary_key=True)

    # The key ID it names (16 hex characters)
    key_id = Column(Key(16), primary_key=True, index=True)

    # How (see PGPRole)
    role = Column(SmallInteger, primary_key=True, autoincrement=False)

    # When it was signed (UTC), if it says
    created = Column(DateTime)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def __repr__(self):
        return "<PGPPost(message_id='%s', key_id='%s', role=%s)>" % (
            self.message_id, self.key_id, self.role)
