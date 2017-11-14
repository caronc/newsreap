# -*- coding: utf-8 -*-
#
# The Header Object used to wrap NNTP Headers associated with an Article
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


class StagedArticleHeader(ObjectBase):
    """
    A Header class contains a mapping of headers associated with an staged
    article.

    """
    __tablename__ = 'staged_article_header'

    # Our unique identifier
    id = Column(
        Integer, Sequence('staged_article_header_id_seq'), primary_key=True)

    # Header Key associated with our staged_article
    key = Column(String(64), nullable=False, index=False)

    # Header Value
    value = Column(String(256), nullable=False, index=False)

    # The local file this Header entry applies to.
    article_id = Column(
        Integer,
        ForeignKey("staged_article.id"),
        nullable=False,
        index=True,
    )

    def __init__(self, *args, **kwargs):
        super(StagedArticleHeader, self).__init__(*args, **kwargs)

    def __repr__(self):
        return "<StagedArticleHeader(key=%s, value='%s')>" % (
            self.key, self.value,
        )
