# -*- coding: utf-8 -*-
#
# A representation of an actual NNTPArticle() which can contain 1 or more
# NNTPContent() objects in addition to header information.
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
from copy import deepcopy
from itertools import chain
from os.path import isfile
from string import ascii_uppercase
from string import digits
from string import ascii_lowercase
from random import choice
from datetime import datetime

from newsreap.NNTPContent import NNTPContent
from newsreap.NNTPBinaryContent import NNTPBinaryContent
from newsreap.NNTPAsciiContent import NNTPAsciiContent
from newsreap.NNTPHeader import NNTPHeader
from newsreap.NNTPSettings import NNTP_EOL
from newsreap.NNTPSettings import NNTP_EOD

# Logging
import logging
from newsreap.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)

DEFAULT_NNTP_SUBJECT = 'unknown.file'
DEFAULT_NNTP_POSTER = 'newsreaper <news@reap.er>'


class NNTPArticle(object):
    """
    An object for maintaining retrieved article content. The primary
    reason for this object is it makes it easier to sort the results
    in memory tracking both the content downloaded (using the
    NNTPConnection.get() call along with the file stored locally on disk
    that it's responsible for.

    This function does it's best to behave like a stream. But provides
    some functions to make manipulating and merging with other articles
    easier to do.

    Articles by default assume a roll of 'attached'.  This means that
    the files written to disk are removed the second the object is
    destroyed.  This is intentional!  You can call detach() at any
    time you want but now you are responsible for cleaning up the
    filename.

    """

    def __init__(self, subject=DEFAULT_NNTP_SUBJECT,
                 poster=DEFAULT_NNTP_POSTER, groups=None, *args, **kwargs):
        """
        Initialize NNTP Article

        """
        # The Subject
        self.subject = subject

        # The Poster
        self.poster = poster

        # TODO: Rename id to article_id (readability and id is a reserved
        # keyword)
        # The Article Message-ID
        self.id = kwargs.get(u'id', '')

        # TODO: Rename no to index_no (readability)
        # The NNTP Group Index #
        try:
            self.no = int(kwargs.get(u'no', 1000))
        except:
            self.no = int(kwargs.get(u'no', 1000))

        # Track the groups this article resides in.
        # This is populated for meta information when an article is
        # retrieved; but its contents are also used when posting an article.
        self.groups = groups
        if not self.groups:
            self.groups = set()

        elif isinstance(self.groups, basestring):
            self.groups = set((self.groups, ))

        elif isinstance(self.groups, (list, tuple)):
            self.groups = set([ x.lower() for x in self.groups])

        elif not isinstance(self.groups, set):
            raise AttributeError("Invalid group set specified.")

        # A hash of header entries
        self.header = NNTPHeader()

        # Our body contains non-decoded content
        self.body = NNTPAsciiContent()

        # TODO: rename decoded to content because decoded implies this object
        # is only used when retrieving articles when in fact it's used for
        # posting them too.
        # Contains a list of decoded content
        self.decoded = sortedset(key=lambda x: x.key())

    def load_response(self, response):
        """
        Loads an article by it's NNTPResponse
        """
        # Our body contains non-decoded content
        self.body = response.body

        # Store decoded content
        self.decoded = response.decoded

        # Store Header
        self.header = next((d for d in self.decoded \
                    if isinstance(d, NNTPHeader)), None)

        if self.header is not None:
            # Remove Header from decoded list
            self.decoded.remove(self.header)

            # TODO: Parse header information (if present) and populate
            # some obvious fields (such as subject, groups, etc)

        return True

    def post_iter(self, update_headers=True):
        """
        Returns NNTP string as it would be required for posting
        """

        if update_headers:
            self.header['Newsgroups'] = ','.join(self.groups)
            self.header['Subject'] = self.subject
            self.header['From'] = self.poster
            self.header['X-Newsposter'] = self.poster
            self.header['Message-ID'] = '<%s>' % self.msgid()

        args = [
            iter(self.header.post_iter()),
        ]

        if len(self.body):
            args.append(iter(self.body.post_iter()))
            args.append(NNTP_EOL)

        if len(self.decoded):
            for entry in self.decoded:
                args.append(iter(entry.post_iter()))
                args.append(NNTP_EOL)

        args.extend([NNTP_EOL, NNTP_EOD])
        return chain(*args)

    def encode(self, encoders):
        """
        A wrapper to the encoding of content. The function returns None if
        a problem occurs, otherwise the function returns an NNTPArtice()
        object.

        The power of this function comes from the fact you can pass in
        multiple encoders to have them all fire after one another.
        """
        if len(self) == 0:
            # Nothing to encode
            return None

        objs = sortedset(key=lambda x: x.key())
        for content in self:
            obj = content.encode(encoders)
            if obj is None:
                return None

            # Successful, add our object to our new list
            objs.add(obj)

        # If we reach here we encoded our entire article
        # Create a copy of our article
        article = deepcopy(self)

        # In our new copy; store our new encoded content
        article.decoded = objs

        # Return our article
        return article

    def split(self, size=81920, mem_buf=1048576, body_mirror=0):
        """
        Split returns a set of NNTPArticle() objects containing the split
        version of the data it already represents.

        Even if the object can't be split any further given the parameters, a
        set of at least 1 entry will always be returned.  None is returned if
        an error occurs. None is also returned if split() is called while there
        is more then one NNTPContent objects since it makes the situation
        Ambiguous.

        The body_mirror flag has a series of meanings; since we start with a
        single post before calling this (which goes on and splits the post) we
        have to decide if we want the contents of our message body to appear
        in every post. So here is how it breaks down.

        If you set it to False, None then the body content is ignored
        and nothing is done with it.

        If you set it to True, then the body is copied to every single part.

        If you set it to -X (negative #) then the body is stored in the
        part generated as if you called the python slice expression:
            parts[-X].body <-- store body here

        If you set it to X (positive #), then the body is stored in
        that index value similar to above:
            parts[X].body <-- store body here

        IndexErrors are automatially and silently handled.  If you access
        a part that is out of bounds then the bounds are limited to fit
        automtatically.  For instance, if you specified -100 as the
        part and there were only 4 parts generated, (-4 is the maximum
        it could have been) then -4 is used in place of the -100.

        consiquently if you passed in 100 (a positive value). since
        (positive) indexes are measured started at 0 (zero), this means a
        with a list of 4 items the largest value can be a 3.  Therefore
        the index of 3 is used instead.
        """
        if len(self) > 1:
            # Ambiguous
            return None

        if len(self) == 0:
            # Nothing to split
            return None

        content = next((c for c in self.decoded \
                     if isinstance(c, NNTPContent)), None)

        if not content:
            # Well this isn't good; we have decoded entries that are not
            # of type NNTPContent.
            return None

        # Split our content
        new_content = content.split(size=size, mem_buf=mem_buf)

        if new_content is None:
            # something bad happened
            return None

        # If we get here, we have content to work with.  We need to generate
        # a list of articles based on our existing one.
        articles = sortedset(key=lambda x: x.key())

        for no, c in enumerate(new_content):
            a = NNTPArticle(
                # TODO: Apply Subject Template here which the user can set when
                # initializing htis function
                subject=self.subject,
                poster=self.poster,
                groups=self.groups,
                # Increment our index #
                no=self.no+no,
            )

            # Set our header to be a copy of what we already have
            a.header = deepcopy(self.header)

            # Store our NNTPContent() object
            a.add(c)

            if body_mirror is True:
                # body is mirrored to everything
                a.body = self.body

            # Store our Article
            articles.add(a)

        # Now we transfer over our body if nessisary
        if isinstance(body_mirror, int):
            if body_mirror > -1:
                try:
                    articles[body_mirror].body = self.body

                except IndexError:
                    # Store at last entry
                    articles[-1].body = self.body
            else:
                # Negative Number
                try:
                    articles[body_mirror].body = self.body

                except IndexError:
                    # Store at first entry
                    articles[0].body = self.body

        # Return our articles
        return articles

    def is_valid(self):
        """
        Iterates over article content an returns True if all of it is valid
        based on it's crc32/md5 and/or whatever mechanism is used to determine
        a NNTPContents validity
        """
        if len(self.decoded) == 0:
            # No articles means no validity
            return False

        return next((False for c in self.decoded \
                     if c.is_valid() is False), True)

    def files(self):
        """
        Returns a list of the files within article
        """
        return [x.path() for x in self.decoded]

    def key(self):
        """
        Returns a key that can be used for sorting with:
            lambda x : x.key()
        """
        return '%.5d%s' % (self.no, self.id)

    def detach(self):
        """
        Detach the article stored on disk from being further managed by this
        class
        """
        for a in self.decoded:
            if isinstance(a, NNTPBinaryContent):
                a.detach()
        return

    def attach(self):
        """
        Detach the article stored on disk from being further managed by this
        class
        """
        for a in self.decoded:
            if isinstance(a, NNTPBinaryContent):
                a.attach()
        return

    def add(self, content):
        """
        Used for adding content to the self.decoded class
        """
        if isinstance(content, basestring) and isfile(content):
            # Support strings
            content = NNTPContent(content)

        if not isinstance(content, NNTPContent):
            return False

        # duplicates are ignored in a blist and therefore
        # we just capture the length of our list before
        # and after so that we can properly return a True/False
        # value
        _bcnt = len(self.decoded)
        self.decoded.add(content)

        return len(self.decoded) > _bcnt

    def msgid(self, host=None):
        """
        Returns a message ID; if one hasn't been generated yet, it is
        based on the content in the article and used
        """
        if self.id:
            return self.id

        if not host:
            # Generate a 32-bit string we can use
            host = ''.join(
                choice(
                    ascii_uppercase + digits + ascii_lowercase,
                ) for _ in range(32))

        if len(self.decoded):
            partno = self.decoded[0].part
        else:
            partno = 1
        # If we reach here an ID hasn't been generated yet; generate one
        self.id = '%s%d@%s' % (
            datetime.utcnow().strftime('%Y%m%d%H%M%S%f'),
            partno,
            host
        )

    def size(self):
        """
        return the total size of our decoded content
        """
        return sum(len(d) for d in self.decoded)

    def __iter__(self):
        """
        Grants usage of the next()
        """

        # Ensure our stream is open with read
        return iter(self.decoded)

    def __len__(self):
        """
        Returns the number of decoded content entries found
        """
        return len(self.decoded)

    def __lt__(self, other):
        """
        Handles less than for storing in btrees
        """
        return self.key() < other.key()

    def __eq__(self, other):
        """
        Handles equality

        """
        return self.__dict__ == other.__dict__

    def __getitem__(self, index):
        """
        Support accessing NNTPContent objects by index
        """
        return self.decoded[index]

    def __str__(self):
        """
        Return a printable version of the article
        """
        return '%s' % self.id

    def __unicode__(self):
        """
        Return a printable version of the article
        """
        return u'%s' % self.id

    def __repr__(self):
        """
        Return an unambigious version of the object
        """

        return '<NNTPArticle Message-ID="%s" attachments="%d" />' % (
            self.id,
            len(self.decoded),
        )
