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
from os.path import isfile
from os.path import abspath
from os.path import expanduser

from newsreap.codecs.CodecBase import DEFAULT_TMP_DIR
from newsreap.NNTPArticle import NNTPArticle
from newsreap.NNTPArticle import DEFAULT_NNTP_SUBJECT
from newsreap.NNTPArticle import DEFAULT_NNTP_POSTER
from newsreap.NNTPContent import NNTPContent
from newsreap.NNTPBinaryContent import NNTPBinaryContent

# Logging
import logging
from newsreap.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)


class NNTPSegmentedPost(object):
    """
    An object for maintaining retrieved nzb content. Large files need
    to be split across multiple Articles in order to be posted.

    When combined into one, They create a SegmentedPost

    Similarily; this file makes posting easy by allowing you to add files
    to it in order to prepare an object worthy of posting.

    Every file you wish to post to an NNTP Server should be done through
    this file for the most power/control

    """

    def __init__(self, filename, subject=DEFAULT_NNTP_SUBJECT,
                 poster=DEFAULT_NNTP_POSTER, groups=None,
                 utc=None, work_dir=None, sort_no=None, *args, **kwargs):
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

        # The sort order which helps with file ordering, if set to None
        # or a common value, then it has no bearing on anything.  Its
        # main goal is to provide a method of sorting multiple SegmentedPosts
        self.sort_no = sort_no

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

        self.groups = groups
        if not self.groups:
            self.groups = set()

        if isinstance(self.groups, basestring):
            self.groups = [self.groups]

        elif isinstance(self.groups, basestring):
            self.groups = set((self.groups, ))

        elif isinstance(self.groups, list):
            self.groups = set(self.groups)

        elif not isinstance(self.groups, set):
            raise AttributeError("Invalid group set specified.")

        # A sorted set of articles
        self.articles = sortedset(key=lambda x: x.key())

        if work_dir is None:
            self.work_dir = DEFAULT_TMP_DIR
        else:
            self.work_dir = abspath(expanduser(work_dir))

    def pop(self, index=0):
        """
        Pops an Article at the specified index out of the segment table
        """
        return self.articles.pop(index)

    def add(self, content):
        """
        Add an NNTPArticle()
        """
        if isinstance(content, basestring) and isfile(content):
            # Create a content object from the data
            # This isn't always the best route because no part #'s
            # are assigned this way; but if it's only a single file
            # the user is working with; this way is much easier.
            content = NNTPBinaryContent(
                filepath=content,
                work_dir=self.work_dir,
            )
            # At this point we fall through and the next set of
            # if checks will catch our new content object we created

        # duplicates are ignored in a blist and therefore
        # we just capture the length of our list before
        # and after so that we can properly return a True/False
        # value
        _bcnt = len(self.articles)

        if isinstance(content, NNTPArticle):
            if not content.groups and self.groups:
                content.groups = set(self.groups)

            if not content.subject and self.subject:
                content.subject = self.subject

            if not content.poster and self.poster:
                content.poster = self.poster

            self.articles.add(content)

        elif isinstance(content, NNTPContent):
            # Create an Article and store our content
            a = NNTPArticle(
                subject=self.subject,
                poster=self.poster,
                groups=self.groups,
                work_dir=self.work_dir,
            )

            # Add the content to our article
            a.add(content)

            # Store the article in our collection
            self.articles.add(content)

        else:
            # Unsupported
            return False

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

        return next((False for c in self.articles
                     if c.is_valid() is False), True)

    def split(self, size=81920, mem_buf=1048576):
        """
        If there is one Article() and one (valid) NNTPContent() object within
        it, this object will split the Article() into several and break apart
        the contents of the file into several pieces.

        This function returns True if this action was successful, otherwise
        this function returns False.  Newly split content is 'not' in a
        detached form meaning content is removed if the object goes out of
        scope
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
        this function returns False.  Newly joined content is 'not' in a
        detached form meaning content is removed if the object goes out of
        scope
        """

        if len(self.articles) == 1:
            # Nothing to do; no need to fail
            return True

        if len(self.articles) < 1:
            # Not possible to join less than 1 article
            return False

        # acquire the first entry; this will be what we build from
        articles = iter(self.articles)

        # Create a copy of our first object
        head_article = articles.next().copy()

        # Iterate over all our remaining article entries and stack their
        # content onto our head_article
        for content in articles:

            # Append our content
            if not head_article.append(content):

                # Clean up our copy
                del head_article

                # We failed
                return False

        # Reset with a new sorted set of articles
        self.articles.clear()

        # Add our single head_article entry as our primary entry
        self.articles.add(head_article)

        # We're done!
        return True

    def files(self):
        """
        Returns a list of the files within article
        """
        return [x.path() for x in self.articles]

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
        if self.sort_no is not None:
            # Compare based on a sort number, undefined sorting always
            # trumps those with sorting defined.
            if other.sort_no is not None:
                if self.sort_no == other.sort_no:
                    return str(self.filename) < str(other.filename)

                # Compare our sorting values
                return str(self.sort_no) < str(other.sort_no)

            # If no other sort_no then we are not less than it; those without
            # a sort_no should always trump those with one
            return False

        elif other.sort_no is not None:
            # We don't have a sort_no, and the other does; we're less than it
            # based on our set rules.
            return True

        # If we reach here, neither comparison objects have a sort_no defined.
        # as a result, we just base our match on the filename specified
        return str(self.filename) < str(other.filename)

    def __eq__(self, other):
        """
        Handles equality

        """
        if self.sort_no:
            # Compare based on a sort number, undefined sorting always
            # trumps those with sorting defined.
            if other.sort_no:
                if self.sort_no == other.sort_no:
                    return self.__dict__ == other.__dict__

            # Any other posibility is False
            return False

        elif other.sort_no:
            # they have a sort and we don't. there is no equality in that
            return False

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
        if not self.sort_no:
            return '<NNTPSegmentedPost filename="%s" articles=%d />' % (
                self.filename,
                len(self.articles),
            )

        return '<NNTPSegmentedPost sort="%d" filename="%s" articles=%d />' % (
            self.sort_no,
            self.filename,
            len(self.articles),
        )
