# The NZB files posted to groups that were read into the index
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
from sqlalchemy import String

from .ObjectBase import ObjectBase


class NzbImport(ObjectBase):
    """
    An NZB file found posted to a group (see NNTPNzbReader), so it is only
    read once.  It is kept even when it couldn't be used, so it isn't
    fetched again for nothing.
    """

    __tablename__ = 'nzb_import'

    # The Message-ID of its first article (without its < and >)
    message_id = Column(String(250), primary_key=True)

    # Its name (as it was posted)
    filename = Column(String(256), nullable=False, default='')

    # The collection it became (None if it couldn't be used)
    collection_id = Column(Integer, nullable=True)

    # When it was read
    imported = Column(DateTime, nullable=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def __repr__(self):
        return "<NzbImport(message_id='%s', collection_id=%s)>" % (
            self.message_id, self.collection_id)
