# -*- coding: utf-8 -*-
#
# NNTPSegmentedFile is an object that manages several NNTPArticles.
#
# Copyright (C) 2015-2016 Chris Caron <lead2gold@gmail.com>
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

from blist import sortedset
from newsreap.NNTPArticle import NNTPArticle

from datetime import datetime

# Logging
import logging
from newsreap.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)


class NNTPSegmentedFile(object):
    """
    An object for maintaining retrieved nzb content. Large files need
    to be split across multiple Articles in order to be posted.

    When combined into one, They create a SegmentedFile

    """

    def __init__(self, filename, *args, **kwargs):
        """
        Initialize NNTP Segmented File

        """
        # The Filename
        self.filename = filename

        # These fields get populated when reading in an nzb file
        self.poster = kwargs.get('poster')
        self.utc = kwargs.get('utc')
        self.subject = kwargs.get('subject')
        self.groups = kwargs.get('groups', '')

        # Convert into datetime
        try:
            self.utc = datetime.fromtimestamp(int(self.utc))

        except (TypeError, ValueError):
            # Used None, uninitialized, or Used bad value
            # Default timezone to 'now' but make it consistent
            # with the world, use the UTC as a common source
            self.utc = datetime.utcnow()

        if not isinstance(self.groups, set):
            set(self.groups)

        # A sorted set of segments
        self.segments = sortedset(key=lambda x: x.key())

    def add_segment(self, article_id, index_no, size):
        """
        This is used in NNTPnzb when parsing an existing NZB File.

        Segments are spread amongst articles and when assembled amount to the
        complete file.

        Segments are cataloged by their article_id on Usenet.

        To tell them apart from one another and help with assembly, we need to
        know the index no of it.

        Finally the size represents the size of the entire message when
        downloaded.  This is important to us when we need to verify that the
        file contents are whole

        """
        article = NNTPArticle(
            subject=self.subject,
            poster=self.poster,
            id=article_id,
            no=index_no,
        )

        # TODO: Use the `size` variable (probably should play a role with the
        # NNTPArticle object
        self.segments.add(article)

        # We're Done
        return True

    def files(self):
        """
        Returns a list of the files within article
        """
        return [ x.keys() for x in self.segments ]

    def key(self):
        """
        Returns a key that can be used for sorting with:
            lambda x : x.key()
        """
        return '%s' % self.filename

    def __len__(self):
        """
        Return the length of the segments
        """
        return len(self.segments)

    def __lt__(self, other):
        """
        Handles less than for storing in btrees
        """
        return str(self.filename) < str(other.filename)

    def __str__(self):
        """
        Return a printable version of the article
        """
        return '%s' % self.filename

    def __unicode__(self):
        """
        Return a printable version of the article
        """
        return u'%s' % self.filename

    def __repr__(self):
        """
        Return an unambigious version of the object
        """

        return '<NNTPSegmentedFile filename="%s" segments=%d />' % (
            self.filename,
            len(self.segments),
        )
