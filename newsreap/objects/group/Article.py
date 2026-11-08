#  The Article Object found on UseNet Servers
#
# Copyright (C) 2015-2017 Chris Caron <lead2gold@gmail.com>
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
from sqlalchemy import Index
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import Boolean
from sqlalchemy import DateTime

from .ObjectBase import ObjectBase


class Article(ObjectBase):
    """
    An Article class contains a mapping of one Article
    retrieved from usenet (via the xover() option)
    """

    __tablename__ = 'article'

    # Indexing and archiving read the articles not done yet in article
    # order, a chunk at a time; these let them do that without sorting
    # every article left on each chunk
    __table_args__ = (
        Index('ix_article_indexed_no', 'indexed', 'article_no'),
        Index('ix_article_archived_no', 'archived', 'article_no'),
    )

    # Article (Unique) Message-ID; 250 characters is the longest RFC 5536
    # allows (the same as the index keeps)
    message_id = Column(String(250), primary_key=True)

    # Article No (unique to usenet server only)
    article_no = Column(Integer, nullable=False, unique=True, index=True)

    # Article Subject
    subject = Column(String(256), nullable=False, index=True)

    # Article Poster
    poster = Column(String(128), nullable=False, index=True)

    # Article Size
    size = Column(Integer, default=0, nullable=False)

    # Article Line Count
    lines = Column(Integer, default=0, nullable=False)

    # Article Post Date
    posted_date = Column(DateTime, index=True)

    # The groups it was posted to (space separated, from its Xref); its
    # score and tags are kept with the index (see ArticleRank)
    xref = Column(Text, default='', server_default='', nullable=False)

    # Set once `nr update` has put the article into a collection
    indexed = Column(
        Boolean, default=False, server_default='0', nullable=False)

    # Set once `nr update --content` has fetched (and kept) the article
    archived = Column(
        Boolean, default=False, server_default='0', nullable=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def __repr__(self):
        return "<Article(message_id=%s)>" % (self.message_id)
