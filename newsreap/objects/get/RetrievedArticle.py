# -*- coding: utf-8 -*-
#
# Used when Retrieving content from an NNTP Server
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

from sqlalchemy import Column
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import UnicodeText
from sqlalchemy import Sequence

from .ObjectBase import ObjectBase


class RetrievedArticle(ObjectBase):
    """
    Retrieved articles tracking

    """

    __tablename__ = 'retrieved_article'

    # Our unique identifier
    id = Column(
        Integer, Sequence('retrieved_article_id_seq'), primary_key=True)

    # The filename to associate with the retrieved content; if no filename is
    # specified then the filename associated with the filepath is used.
    localfile = Column(String(256), nullable=False)

    # Article (Unique) Message-ID
    message_id = Column(String(128), index=True)

    # Article Subject
    subject = Column(String(256), nullable=False)

    # Article Body (this does not include the yEnc attachment)
    body = Column(UnicodeText(), nullable=False)

    # Article Poster
    poster = Column(String(128), nullable=False)

    # Article Size
    size = Column(Integer, default=0, nullable=False)

    # The sequence # associated with the filename.
    sequence_no = Column(Integer, default=0, nullable=False)

    # The Sort no should only differ between segmented files
    sort_no = Column(Integer, default=0, nullable=False)

    def __init__(self, *args, **kwargs):
        super(RetrievedArticle, self).__init__(*args, **kwargs)

    def __repr__(self):
        return "<RetrievedArticle(localfile=%s)>" % (self.localfile)
