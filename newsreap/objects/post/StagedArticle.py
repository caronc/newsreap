# -*- coding: utf-8 -*-
#
# Used when Staging a NNTP Post
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
from sqlalchemy import Boolean
from sqlalchemy import DateTime

from .ObjectBase import ObjectBase


class StagedArticle(ObjectBase):
    """
    An article associated with an NZB-File. This object is used when posting.
    It provides a means of tracking what was posted and what hasn't been
    posted yet.

    """

    __tablename__ = 'staged_article'

    # The filename to associate with the staged content; if no filename is
    # specified then the filename associated with the filepath is used.
    localfile = Column(String(256), nullable=False, primary_key=True)

    # Article (Unique) Message-ID
    message_id = Column(String(128), index=True)

    # Article Subject
    subject = Column(String(256), nullable=False)

    # Article Poster
    poster = Column(String(128), nullable=False)

    # Filename as it should appear to Usenet
    remotefile = Column(String(256), nullable=False)

    # Article Size
    size = Column(Integer, default=0, nullable=False)

    # Local files sha1 checksum; this is verified prior to posting to ensure
    # the contents has not changed
    sha1 = Column(String(40), default=None, nullable=True)

    # Article Post Date; This is only initialized after the post has been
    # successful.
    posted_date = Column(DateTime, default=None, nullable=True)

    # Upon posting, this boolean flag is toggled after verifying the posted
    # content was performed successfully.
    verified = Column(Boolean, default=False, nullable=False)

    # The sequence # associated with the filename. A sequence value of zero (0)
    # always identifies the root/main file. This is used when generating our
    # posted content content will always be posted in order of the sequence
    # number and then by the filename
    sequence_no = Column(Integer, default=0, nullable=False)

    # The Sort no should only differ between segmented files
    sort_no = Column(Integer, default=0, nullable=False)

    def __init__(self, *args, **kwargs):
        super(StagedArticle, self).__init__(*args, **kwargs)

    def __repr__(self):
        return "<StagedArticle(message_id=%s)>" % (self.message_id)
