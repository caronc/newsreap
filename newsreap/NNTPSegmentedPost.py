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
from datetime import datetime

from newsreap.NNTPArticle import NNTPArticle
from newsreap.NNTPArticle import DEFAULT_NNTP_SUBJECT
from newsreap.NNTPArticle import DEFAULT_NNTP_POSTER

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

        # When writing files, this is the memory buffer to fill when
        # dealing with very large files. If this is set to None, then
        # no buffer is used. Default 500K
        self.mem_buffer = 512000

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

        # A sorted set of articles
        self.articles = sortedset(key=lambda x: x.key())

    def pop(self, index=0):
        """
        Pops an Article at the specified index out of the segment table
        """
        return self.articles.pop(index)

    def add(self, article):
        """
        Add an NNTPArticle()
        """
        if not isinstance(article, NNTPArticle):
            return False

        # duplicates are ignored in a blist and therefore
        # we just capture the length of our list before
        # and after so that we can properly return a True/False
        # value
        _bcnt = len(self.articles)

        if not article.groups and self.groups:
            article.groups = set(self.groups)

        if not article.subject and self.subject:
            article.subject = self.subject

        if not article.poster and self.poster:
            article.poster = self.poster

        self.articles.add(article)

        return len(self.articles) > _bcnt

    def is_valid(self):
        """
        Iterates over article content an returns True if all of it is valid
        based on it's crc32/md5 and/or whatever mechanism is used to determine
        a NNTPContents validity
        """
        if len(self.articles) == 0:
            # No articles means no validity
            return False

        return next((False for c in self.articles \
                     if c.is_valid() is False), True)

    def split(self, size=81920, mem_buf=1048576):
        """
        If there is one Article() and one (valid) NNTPContent() object within
        it, this object will split the Article() into several and break apart
        the contents of the file into several pieces.

        This function returns True if this action was successful, otherwise
        this function returns False.  Newly split content is 'not' in a detached
        form meaning content is removed if the object goes out of scope
        """
        if len(self.articles) > 1:
            # Not possible to split a post that is already split
            return False

        articles = self.articles[0].split(size=size, mem_buf=mem_buf)
        if articles is None:
            return False

        # Otherwise store our goods
        self.articles = articles
        return True

    def join(self):
        """
        If there are more then one Article() objects containing one valid()
        NNTPContent() object, they can be joined together into one single
        Article() object.

        This function returns True if this action was successful, otherwise
        this function returns False.  Newly joined content is 'not' in a detached
        form meaning content is removed if the object goes out of scope
        """

        # TODO
        return False

    def files(self):
        """
        Returns a list of the files within article
        """
        return [ x.keys() for x in self.articles ]

    def size(self):
        """
        return the total size of our articles
        """
        return sum(a.size() for a in self.articles)

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
        return iter(self.articles)

    def __len__(self):
        """
        Return the length of the articles
        """
        return len(self.articles)

    def __lt__(self, other):
        """
        Handles less than for storing in btrees
        """
        return str(self.filename) < str(other.filename)

    def __eq__(self, other):
        """
        Handles equality

        """
        return self.__dict__ == other.__dict__

    def __getitem__(self, index):
        """
        Support accessing NNTPArticle objects by index
        """
        return self.articles[index]

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
        return '<NNTPSegmentedPost filename="%s" articles=%d />' % (
            self.filename,
            len(self.articles),
        )
