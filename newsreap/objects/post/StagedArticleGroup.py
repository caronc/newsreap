# -*- coding: utf-8 -*-
#
# The Group Object used to wrap NNTP Groups associated with an Article
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
from sqlalchemy import ForeignKey
from sqlalchemy import Sequence

from .ObjectBase import ObjectBase


class StagedArticleGroup(ObjectBase):
    """
    A Group class contains a mapping of groups associated
    with an staged article.

    """
    __tablename__ = 'staged_article_group'

    # Our unique identifier
    id = Column(
        Integer, Sequence('staged_article_group_id_seq'), primary_key=True)

    # Group name associated with our staged_article
    name = Column(String(128), nullable=False, index=False)

    # The local file this Group entry applies to.
    article_id = Column(
        Integer,
        ForeignKey("staged_article.id"),
        nullable=False,
        index=True,
    )

    def __init__(self, *args, **kwargs):
        super(StagedArticleGroup, self).__init__(*args, **kwargs)

    def __repr__(self):
        return "<StagedArticleGroup(article_id=%ld, name='%s')>" % (
            self.article_id, self.name,
        )
