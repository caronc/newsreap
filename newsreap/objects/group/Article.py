# -*- coding: utf-8 -*-
#
#  The Article Object found on UseNet Servers
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
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Boolean
from sqlalchemy import DateTime

from newsreap.objects.group.ObjectBase import ObjectBase


class Article(ObjectBase):
    """
    An Article class contains a mapping of one Article
    retrieved from usenet (via the xover() option)
    """

    __tablename__ = 'article'

    # Article (Unique) Message-ID
    message_id = Column(String(128), primary_key=True)

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

    # Article Score
    score = Column(Integer, default=0, nullable=False, index=True)

    # There are to just to many articles to delete them individually.
    # The easiest way is to mark them for purging and clear them when
    # the count gets to high.
    deleted = Column(DateTime, default=None, index=True)

    # A flag that can be used to determine if the record should be
    # displayed or not; this over-rides any calculated score.
    hidden = Column(Boolean, default=False, nullable=False, index=True)


    def __init__(self, *args, **kwargs):
        super(Article, self).__init__(*args, **kwargs)


    def __repr__(self):
        return "<Article(message_id=%s)>" % (self.message_id)
