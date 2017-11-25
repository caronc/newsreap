# -*- coding: utf-8 -*-
#
# A representation of an actual NNTPArticle() which can contain 1 or more
# NNTPContent() objects in addition to header information.
#
# Copyright (C) 2015-2017 Chris Caron <lead2gold@gmail.com>
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

import re
import hashlib
from blist import sortedset
from copy import deepcopy
from itertools import chain
from datetime import datetime

from os.path import isfile
from os.path import abspath
from os.path import basename
from os.path import expanduser
from os.path import isdir

from .codecs.CodecYenc import CodecYenc
from .codecs.CodecBase import CodecBase
from .NNTPResponse import NNTPResponse
from .NNTPContent import NNTPContent
from .NNTPGroup import NNTPGroup
from .NNTPBinaryContent import NNTPBinaryContent
from .NNTPAsciiContent import NNTPAsciiContent
from .NNTPHeader import NNTPHeader
from .NNTPSettings import NNTP_EOL
from .NNTPSettings import DEFAULT_TMP_DIR
from .Utils import random_str
from .Utils import bytes_to_strsize
from .Utils import strsize_to_bytes
from .Utils import mkdir
from .Utils import pushd
from .Mime import Mime
from .Mime import DEFAULT_MIME_TYPE
from . import __version__
from . import __title__

# Logging
import logging
from newsreap.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)

DEFAULT_NNTP_SUBJECT = 'unknown.file'
DEFAULT_NNTP_POSTER = 'newsreaper <news@reap.er>'

# Used when parsing groups from a header
GROUP_DELIMITER_RE = re.compile('[ \t,]+')

# A regular expression that can be used to check if a string is a valid
# Message-ID
MESSAGE_ID_RE = re.compile(r'^\s*<?\s*(?P<id>[a-z0-9@!.$-]+)\s*>?\s*$', re.I)


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

    def __init__(self, id=None, subject=None, poster=None, groups=None,
                 work_dir=None, body=None, codecs=None, *args, **kwargs):
        """
        Initialize NNTP Article

        """

        # The Article Message-ID
        self.id = id if isinstance(id, basestring) else ''

        # The NNTP Group Index #
        self.no = 1000
        try:
            self.no = int(kwargs.get(u'no', self.no))
        except:
            # Just use the default
            pass

        # The Subject
        self.subject = subject
        if self.subject is None:
            self.subject = DEFAULT_NNTP_SUBJECT

        # The Poster
        self.poster = poster
        if self.poster is None:
            self.poster = DEFAULT_NNTP_POSTER

        if work_dir is None:
            self.work_dir = DEFAULT_TMP_DIR
        else:
            self.work_dir = abspath(expanduser(work_dir))

        # Contains a list of decoded content; it's effectively the articles
        # attachments
        self.decoded = sortedset(key=lambda x: x.key())

        # The group(s) associated with our article
        self.groups = NNTPGroup.split(groups)

        # A hash of header entries
        self.header = NNTPHeader(work_dir=self.work_dir)

        if isinstance(body, NNTPAsciiContent):
            self.body = body
        else:
            # Our body contains non-decoded content
            self.body = NNTPAsciiContent(work_dir=self.work_dir)

        if isinstance(body, basestring) and len(body) > 0:
            # Store our body content
            self.body.write(body)

        # Load our Codecs
        self._codecs = codecs

        # Provide default codecs if required
        if not self._codecs:
            self._codecs = [CodecYenc(), ]

        elif isinstance(self._codecs, CodecBase):
            self._codecs = [self._codecs, ]

    def load(self, response):
        """
        Loads an article by it's NNTPResponse or from another NNTPArticle

        """
        if isinstance(response, NNTPResponse):
            # Our body contains non-decoded content
            self.body = response.body

            # Store decoded content
            self.decoded = response.decoded

            # Store Header
            self.header = next(
                (d for d in self.decoded if isinstance(d, NNTPHeader)), None)

            # Our groups associated with the post (if we know it)
            self.groups = set()

            if self.header is not None:
                # Remove Header from decoded list
                self.decoded.remove(self.header)

                if u'Newsgroups' in self.header:
                    # Parse our groups out of the header
                    self.groups = NNTPGroup.split(self.header[u'Newsgroups'])

        elif isinstance(response, NNTPArticle):
            # We basically save everything except the work-dir since
            # our new work dir could be a new location

            # The NNTP Message-ID
            self.id = response.id

            # The NNTP Group Index
            self.no = response.no

            # Store the subject
            self.subject = response.subject

            # Store the poster
            self.poster = response.poster

            # The NNTP Groups
            self.groups = response.groups

            # Store the header from the other side
            self.header = response.header

            # Our body contains non-decoded content
            self.body = response.body

            # Store decoded content
            self.decoded = response.decoded

        else:
            # Unsupported
            return False

        return True

    def post_iter(self, update_headers=True):
        """
        Returns NNTP string as it would be required for posting
        """

        if not self.id:
            # Generate ourselves a Message-ID
            self.msgid()

        if not len(self.body) and not len(self.decoded):
            # Nothing to post
            logger.error(
                'Post Denied / Article %s has no content to post.' %
                self.msgid())
            return None

        if not len(self.groups):
            # Nothing to post
            logger.error(
                'Post Denied / Article %s has no groups defined.' %
                self.msgid())
            return None

        try:
            if not self.subject.strip():
                logger.error(
                    'Post Denied / Article %s has no subject.' %
                    self.msgid())
                return None

        except AttributeError:
            logger.error(
                'Post Denied / Article %s has no subject.' % self.msgid())
            return None

        try:
            if not self.poster.strip():
                logger.error(
                    'Post Denied / Article %s has no poster.' % self.msgid())
                return None

        except AttributeError:
            logger.error(
                'Post Denied /Article %s has no poster.' % self.msgid())
            return None

        if update_headers:
            self.update_headers()

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
        article = self.copy(include_attachments=False)

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

        content = next((
            c for c in self.decoded if isinstance(c, NNTPContent)), None)

        if not content:
            # Well this isn't good; we have decoded entries that are not
            # of type NNTPContent.
            return None

        # ensure our size is set to some value
        size = strsize_to_bytes(size)
        if size is None:
            # You can't call split() with a size value set at None
            return None

        # Size needs to consider our message body
        # The length of our headers are not applicable
        if len(self.body):
            size -= len(self.body) + len(NNTP_EOL)

        if size < 0:
            # we can't perform the split
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
                subject=self.subject,
                poster=self.poster,
                groups=self.groups,
                # Increment our index #
                no=self.no + no,
                work_dir=self.work_dir,
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

    def append(self, articles):
        """
        Appends one article onto another. This is the reverse of split()
        articles must be an NNTPArticle object, or a set-of NNTPArticle()
        objects.

        """
        if isinstance(articles, NNTPArticle):
            # Convert our entry into a list object
            articles = [articles, ]

        # Change to an iterator
        articles = iter(articles)
        if len(self) > 1:
            # Ambiguous; we can't append content if we have
            # more then one possible thing to append to
            return False

        for article in articles:
            if len(article) == 0:
                # No attachments; move along silently
                continue

            elif len(article) > 1:
                # To many attachments in article
                return False

            if len(self.decoded) == 0:
                # Store a copy of our decoded object
                content = article[0].copy()
                if content is None:
                    # We failed to create a copy
                    return False

                # Add our content to our object
                self.decoded.add(content)
                continue

            # If we reach here, we have 1 entry to append our content to
            if not self.decoded[0].append(article[0]):
                return False

        # Return our success
        return True

    def copy(self, include_attachments=True):
        """
        Creates a copy of the article returning one

        """

        article = NNTPArticle(
            subject=self.subject,
            poster=self.poster,
            groups=self.groups,
        )

        article.id = self.id
        article.no = self.no
        article.work_dir = self.work_dir
        article.header = self.header.copy()
        article.body = self.body.copy()

        if include_attachments:
            for content in self.decoded:
                obj = content.copy()
                if obj is None:
                    return None

                # Add our copied NNTP object
                article.decoded.add(obj)

        # Return our copy
        return article

    def is_valid(self):
        """
        Iterates over article content an returns True if all of it is valid
        based on it's crc32/md5 and/or whatever mechanism is used to determine
        a NNTPContents validity
        """
        if len(self.decoded) == 0:
            # No articles means no validity
            return False

        return next((
            False for c in self.decoded if c.is_valid() is False), True)

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
        if isinstance(content, basestring):
            # A mime object we can use to detect the type of file
            m = Mime()

            if isfile(content):
                # file relative to current path; get our mime response
                mr = m.from_bestguess(content)

                # Our NNTPContent object will depend on whether or not we're
                # dealing with an ascii file or binary
                instance = NNTPBinaryContent \
                    if mr.is_binary() else NNTPAsciiContent

                content = instance(
                    filepath=content,
                    work_dir=self.work_dir,
                )

            else:
                # Not relative; is the file relative to the work_dir?
                with pushd(self.work_dir, create_if_missing=True):
                    if isfile(content):

                        # file relative to current path; get our mime response
                        mr = m.from_bestguess(content)

                        # Our NNTPContent object will depend on whether or not
                        # we're dealing with an ascii file or binary
                        instance = NNTPBinaryContent \
                            if mr.is_binary() else NNTPAsciiContent

                        # load our file
                        content = instance(
                            filepath=content,
                            work_dir=self.work_dir,
                        )

                    else:
                        # we're dealing with a new file; just save what we have
                        content = NNTPBinaryContent(
                            filepath=content,
                            work_dir=self.work_dir,
                        )

        if not isinstance(content, NNTPContent):
            return False

        # duplicates are ignored in a blist and therefore
        # we just capture the length of our list before
        # and after so that we can properly return a True/False
        # value
        _bcnt = len(self.decoded)
        self.decoded.add(content)

        return len(self.decoded) > _bcnt

    def msgid(self, host=None, reset=False):
        """
        Returns a message ID; if one hasn't been generated yet, it is
        based on the content in the article and used

        If reset is set to True, then a new ID is generated
        """
        if not reset and self.id:
            return self.id

        if not host:
            # Generate a 32-bit string we can use
            host = random_str(32)

        if len(self.decoded):
            partno = self.decoded[0].part
        else:
            partno = 1

        m = hashlib.sha1()
        reference = datetime.utcnow()
        m.update(datetime.utcnow().strftime('%Y%m%d%H%M%S%f'))
        key = m.hexdigest()

        # If we reach here an ID hasn't been generated yet; generate one
        self.id = '%s%x.%x!%x%x%x%x%x@%s%s%s' % (
            key[0:5],
            reference.second,
            reference.microsecond,
            reference.year,
            reference.month,
            reference.day,
            reference.minute,
            partno,
            reference.hour,
            host,
            key[5:10],
        )

        # Then return it
        return self.id

    def save(self, filepath=None, copy=False):
        """
        Saves all of the NNTPContent into the directory specified by
        the filepath; if no filepath is specified, then content is written
        into it's work_dir
        """

        if filepath:
            if not isdir(filepath) and not mkdir(filepath):
                # We failed
                return False

        for attachment in self.decoded:
            if not attachment.save(filepath=filepath, copy=copy):
                return False

        return True

    def deobsfucate(self, filebase='', codecs=None):
        """
        Using the information we have in the article generate a filename to
        the best of our ability.

        filebase: provide a fallback filename base (the part of the file
                  before the extension) to build on if we can't detect
                  the file on our own.

        codecs:   The codec(s) you wish to use to assist in the deobsfucation
        """

        if len(self.decoded) != 1:
            # There is ambiguity if there is not one attachment
            return None

        if filebase is None:
            # Safety
            filebase = ''

        # The detected article filename and extension
        a_name = ''
        a_fext = ''
        a_mime = None

        # The detected attachment filename and extension
        d_name = ''
        d_fext = ''
        d_mime = None

        # Create our Mime object since we'll be using it a lot
        m = Mime()

        if codecs is None:
            codecs = self._codecs

        elif isinstance(codecs, CodecBase):
            codecs = [codecs, ]

        # Use our Codec(s) to extract our Yenc Subject
        matched = None
        for c in codecs:
            # Use our Defined Codec(s) to extract content from our subject
            matched = c.parse_article(
                id=self.id,
                article_no=self.no,
                subject=self.subject,
                poster=self.poster,
            )

            if matched:
                # We succesfully got a filename from our subject line
                a_fname = matched.get('fname', '').strip()
                if a_fname:
                    # always allow the name to over-ride the detected filename
                    # if we actually have a real name we can assciated with it
                    # by
                    a_fext = m.extension_from_filename(a_fname)
                    a_name = a_fname[:-len(a_fext)]
                    a_mime = m.from_filename(a_fname)
                    if a_mime and a_mime.type() == DEFAULT_MIME_TYPE:
                        a_mime = None

                # We can break out
                break

        ####################################
        # Now we want to scan our attachment
        ####################################

        # Our mime object for our attachment
        d_mime = self.decoded[0].mime()
        d_fname = self.decoded[0].filename if self.decoded[0].filename \
            else basename(self.decoded[0].path())
        d_mime = m.from_bestguess(d_fname)
        if d_mime and d_mime.type() == DEFAULT_MIME_TYPE:
            d_mime = None

        d_fext = m.extension_from_filename(d_fname)
        d_name = d_fname[:-len(d_fext)]

        # At this point we have to make a decision to go with the filename
        # pulled from our content object, or go with the filename detected
        # from the article.

        _name = filebase
        _fext = None

        # Using the mime types, we decide which one is more likely the name
        if a_mime is None:
            # Attachment was not parsable
            if d_mime is not None:
                # However we did detect a file through this method
                _fext = d_fext
                _name = filebase if filebase else d_name

        elif d_mime is not None:
            # Attachment is parsable and so is our attachment
            if d_mime == a_mime:
                # Same type
                _fext = d_fext if d_fext else a_fext
                _name = _name if _name else d_name

            else:
                # We need to choose one over the other
                if len(d_fext):
                    # we have a filesize associated with our content which
                    # means there is a good chance we parsed our mime typefrom
                    # here
                    _fext = d_fext if d_fext else d_mime.extension()
                else:
                    # Use whatever we parsed
                    _fext = a_fext if a_fext else a_mime.extension()

                _name = _name if _name else d_name

        else:
            # Attachment is parsable and our Article isn't, easy-peasy
            # just use the content
            _fext = a_fext if a_fext else a_mime.extension()
            _name = filebase if filebase else a_name

        if not _fext:
            # Last resort
            _fext = d_fext if d_fext else a_fext
            if not _fext:
                return None

        if not _name:
            # Last resort
            _name = d_name if d_name else a_name
            if not _name:
                return None

        # Return our assembled identifier
        return "{0}{1}".format(_name, _fext)

    def size(self):
        """
        return the total size of our decoded content; we factor in the body if
        it exists.
        """
        if len(self.body):
            return sum(len(d) for d in self.decoded) + \
                len(self.body) + len(NNTP_EOL)
        return sum(len(d) for d in self.decoded)

    def strsize(self):
        """
        return the total size of our decoded content in a human readable
        string.
        """
        return bytes_to_strsize(self.size())

    def update_headers(self):
        """
        Updates our header information based on specified content
        """
        # Our Newsgroups can be of type NNTPGroup, but it can also just
        # be a plain string 'alt.binaries.test', etc. to support both
        # types we need to do a loop inside of our join below
        self.header['Newsgroups'] = ','.join([str(x) for x in self.groups])
        self.header['Subject'] = self.subject.strip()
        self.header['From'] = self.poster.strip()
        self.header['X-Newsposter'] = '%s v%s' % (__title__, __version__)
        self.header['Message-ID'] = '<%s>' % self.msgid()

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
