# A kept post still waiting to be decrypted
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


class WaitReason:
    """
    Why a post is still waiting to be decrypted
    """
    # The server no longer has it
    Gone = 'gone'

    # The server won't let us have it (ie: our account can't read it)
    Denied = 'denied'

    # It couldn't be fetched (ie: the connection dropped)
    Unreachable = 'unreachable'

    # None of our secret keys can open it (ie: a hidden recipient that
    # wasn't us)
    NoKey = 'no key'

    # No passphrase is known for the key (or gpg-agent didn't have it)
    Passphrase = 'passphrase'

    # The passphrase known for the key is wrong
    BadPassphrase = 'bad passphrase'

    # What it holds is bigger than we decrypt
    TooBig = 'too big'

    # gpg took too long
    Timeout = 'timeout'

    # gpg couldn't make sense of it (ie: it is damaged)
    Failed = 'failed'


# Answers that trying again won't change; only something changing here can
# (ie: a new secret key), so these posts are given up on at once
WAIT_FINAL = frozenset((
    WaitReason.Gone, WaitReason.Denied, WaitReason.NoKey, WaitReason.TooBig,
    WaitReason.Failed,
))

# Answers from the server; a new key or passphrase doesn't change them
WAIT_SERVER = frozenset((WaitReason.Gone, WaitReason.Denied))

# How a fetch that didn't return an article went (see
# NNTPConnection.fetch_status()) -> why the post still waits
WAIT_FETCH = {
    'missing': WaitReason.Gone,
    'denied': WaitReason.Denied,
}


class PGPWait(ObjectBase):
    """
    A kept post encrypted for one of our secret keys that couldn't be
    decrypted yet; it is gone once it is (see NNTPKeyring.locked())
    """

    __tablename__ = 'pgp_wait'

    # The post
    message_id = Column(
        Key(250), ForeignKey('archive_post.message_id'),
        primary_key=True)

    # Why it couldn't be decrypted the last time (see WaitReason)
    reason = Column(String(16), nullable=False, default='')

    # How many times it was tried again since it was kept
    tries = Column(SmallInteger, nullable=False, default=0)

    # When it was last tried (UTC)
    tried = Column(DateTime)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def __repr__(self):
        return "<PGPWait(message_id='%s', reason='%s', tries=%s)>" % (
            self.message_id, self.reason, self.tries)
