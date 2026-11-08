# Something one worker is busy with, so others leave it alone
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
from sqlalchemy import String
from newsreap.objects import Key

from .ObjectBase import ObjectBase

# The longest name a claim can have (MySQL can only index so much of a
# text column)
CLAIM_LENGTH = 191


class Claim(ObjectBase):
    """
    Something a worker ('nr update' running somewhere) is busy with: a
    group it is updating, settling the index, or a connection to a server.
    Other workers leave it alone until it is let go of, or until its time
    runs out (a worker that stopped without letting go).
    """
    __tablename__ = 'claim'

    # What is claimed (ie: group:alt.binaries.test, settle,
    # connection:news.example.com:3)
    name = Column(Key(CLAIM_LENGTH), primary_key=True)

    # Who has it (the machine, process and a random part); None when free
    owner = Column(String(128))

    # When the claim runs out unless it is renewed (UTC)
    until = Column(DateTime)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def __repr__(self):
        return "<Claim(name=%s, owner=%s, until=%s)>" % (
            self.name, self.owner, self.until)
