# -*- coding: utf-8 -*-
#
# NNTPSegmentedPost is an object that manages several NNTPArticles.
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
from newsreap.NNTPArticle import DEFAULT_NNTP_SUBJECT
from newsreap.NNTPArticle import DEFAULT_NNTP_POSTER
from datetime import datetime

# Logging
import logging
from newsreap.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)


class NNTPSegmentedPost(object):
    """
    An object for maintaining retrieved nzb content. Large files need
    to be split across multiple Articles in order to be posted.

    When combined into one, They create a SegmentedPost

    """

    def __init__(self, filename, subject=DEFAULT_NNTP_SUBJECT,
                 poster=DEFAULT_NNTP_POSTER, groups=None,
                 utc=None, *args, **kwargs):
        """Initialize NNTP Segmented File

        Args:
            filename (str): Either a path of an existing filename or if
                            you're initializing a new file from scratch this
                            should be the name of the filename being
                            assembled.
        Kwargs:
            subject (str): The subject to associate with the file. This is
                            only required if you're posting the content (to
                            Usenet).
            poster (str): The poster to associate with the file. This is
                           only required if you're posting the content (to
                           Usenet).
            group (str, set): This can be either a string identifying a single
                               Usenet group, or it can be a set() of strings
                               identifying all of the Usenet groups you want
                               to cross-post to. This is only required if you
                               intend on posting the content (to Usenet).
            utc (int, datetime): Should always be in UTC format and can be
                                either defined as the number of seconds from
                                epoch (usually produced from gmtime()) or it
                                can be a datetime() object. This identifies the
                                date to associate with the file.  It's mostly
                                used for sorting.  If no time is specified
                                then it defaults to datetime.utcnow(). If a
                                valid `filename` was specified then the utc
                                defaults to the time associated with the files
                                modification date.
        Returns:
            Nothing

        Raises:
            AttributeError() if you don't follow group isn't a set() of str(),
                              None or str().

        """
        # The Filename
        self.filename = filename

        # These fields get populated when reading in an nzb file
        self.poster = poster
        self.utc = utc
        self.subject = subject
        self.groups = groups

        if not isinstance(self.utc, datetime):
            # Convert into datetime
            try:
                self.utc = datetime.fromtimestamp(int(self.utc))

            except (TypeError, ValueError):
                # Used None, uninitialized, or Used bad value
                # Default timezone to 'now' but make it consistent
                # with the world, use the UTC as a common source
                self.utc = datetime.utcnow()

        if not self.groups:
            self.groups = set()

        elif isinstance(self.groups, basestring):
            self.groups = set((self.groups, ))

        elif isinstance(self.groups, list):
            self.groups = set(self.groups)

        elif not isinstance(self.groups, set):
            raise AttributeError("Invalid group set specified.")

        # A sorted set of segments
        self.segments = sortedset(key=lambda x: x.key())

    def add(self, article):
        """
        Add an article
        """
        # TODO: This function should support NNTPContent types too which we can
        # generaate an Article from and still add it
        if not isinstance(article, NNTPArticle):
            return False

        # duplicates are ignored in a blist and therefore
        # we just capture the length of our list before
        # and after so that we can properly return a True/False
        # value
        _bcnt = len(self.segments)

        if not article.groups and self.groups:
            article.groups = set(self.groups)

        if not article.subject and self.subject:
            article.subject = self.subject

        if not article.poster and self.poster:
            article.poster = self.poster

        self.segments.add(article)

        return len(self.segments) > _bcnt

    def files(self):
        """
        Returns a list of the files within article
        """
        return [ x.keys() for x in self.segments ]

    def size(self):
        """
        return the total size of our articles
        """
        return sum(a.size() for a in self.segments)

    def key(self):
        """
        Returns a key that can be used for sorting with:
            lambda x : x.key()
        """
        return '%s' % self.filename

    def __iter__(self):
        """
        Grants usage of the next()
        """

        # Ensure our stream is open with read
        return iter(self.segments)

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

        return '<NNTPSegmentedPost filename="%s" segments=%d />' % (
            self.filename,
            len(self.segments),
        )
