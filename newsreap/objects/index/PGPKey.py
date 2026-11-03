# A PGP public key (found in a post, or imported)
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
from sqlalchemy import Integer
from sqlalchemy import SmallInteger
from sqlalchemy import String

from .ArchivedPost import LongText
from .ObjectBase import ObjectBase


class PGPKeySource:
    """
    Where a key came from
    """
    # Found in a post (see NNTPArchiver)
    Post = 0

    # Imported by hand (nr pgp import)
    Imported = 1


class PGPKey(ObjectBase):
    """
    A PGP public key (see NNTPKeyring).  Only public details are kept;
    secret keys and passphrases never are.

    A key's subkeys (the ones it encrypts with, usually) get a row of their
    own pointing at it (`parent`), so a post naming a subkey is tied to
    the key it belongs to.
    """

    __tablename__ = 'pgp_key'

    id = Column(Integer, primary_key=True)

    # The key's fingerprint (40 hex characters, 64 for newer keys)
    fingerprint = Column(String(64), nullable=False, unique=True)

    # Its key ID (16 hex characters), which is what posts name
    key_id = Column(String(16), nullable=False, index=True)

    # The fingerprint of the key a subkey belongs to; empty for a key
    parent = Column(String(64), nullable=False, default='', index=True)

    # Its version (4 for most keys) and method (ie: RSA, EdDSA)
    version = Column(SmallInteger, nullable=False, default=4)
    algorithm = Column(String(16), nullable=False, default='')

    # When the key was made (UTC)
    created = Column(DateTime)

    # The names it gives (ie: 'Name <email>'), one per line; keys only
    user_ids = Column(LongText)

    # The public key itself (ASCII armored, so it can be handed on);
    # keys only
    armored = Column(LongText)

    # Where it came from (see PGPKeySource), when we first saw it, and the
    # post it was first seen in (if any)
    source = Column(SmallInteger, nullable=False, default=PGPKeySource.Post)
    first_seen = Column(DateTime)
    message_id = Column(String(250))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def __repr__(self):
        return "<PGPKey(key_id='%s', fingerprint='%s')>" % (
            self.key_id, self.fingerprint)
