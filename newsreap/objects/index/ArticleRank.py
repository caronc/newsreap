# The score and tags of an article
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
from sqlalchemy import Integer
from sqlalchemy import String

from .ObjectBase import ObjectBase


class ArticleRank(ObjectBase):
    """
    The score and tags our filters (see NNTPFilter) gave an article.

    It is the one place they are kept: every copy of the article (in each
    group's header cache, the index and the archive) is found by its
    Message-ID.  Only articles with a score or tags have an entry; the
    rest score 0 and have no tags.
    """

    __tablename__ = 'article_rank'

    # The article's Message-ID (without its < and >)
    message_id = Column(String(250), primary_key=True)

    # Its score and tags (space separated)
    score = Column(Integer, nullable=False, default=0, index=True)
    tags = Column(String(256), nullable=False, default='')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def __repr__(self):
        return "<ArticleRank(message_id='%s', score=%d)>" % (
            self.message_id, self.score or 0)
