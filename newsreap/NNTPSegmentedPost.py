# -*- coding: utf-8 -*-
#
# NNTPSegmentedPost is an object that manages several NNTPArticles.
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
from blist import sortedset
from datetime import datetime
from os.path import isfile
from os.path import abspath
from os.path import expanduser
from os.path import splitext
from os.path import basename

from .codecs.CodecBase import CodecBase
from .codecs.CodecYenc import CodecYenc
from .NNTPSettings import DEFAULT_TMP_DIR
from .NNTPGroup import NNTPGroup
from .NNTPArticle import NNTPArticle
from .NNTPArticle import DEFAULT_NNTP_SUBJECT
from .NNTPArticle import DEFAULT_NNTP_POSTER
from .NNTPContent import NNTPContent
from .NNTPBinaryContent import NNTPBinaryContent
from .NNTPAsciiContent import NNTPAsciiContent
from .Utils import bytes_to_strsize
from .Utils import pushd
from .Mime import Mime
from .Mime import DEFAULT_MIME_TYPE

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
                 utc=None, work_dir=None, sort_no=None, codecs=None,
                 *args, **kwargs):
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
            codecs (CodecBase): The codec to use as our deobsfucation engine
                                You can specify as many Codecs as you want in
                                an interable form.

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

        # Load our Codecs
        self._codecs = codecs

        # Provide default codecs if required
        if not self._codecs:
            self._codecs = [CodecYenc(), ]

        elif isinstance(self._codecs, CodecBase):
            self._codecs = [self._codecs, ]

        # A sorted set of articles
        self.articles = sortedset(key=lambda x: x.key())

        if work_dir is None:
            self.work_dir = DEFAULT_TMP_DIR
        else:
            self.work_dir = abspath(expanduser(work_dir))

        # The group(s) associatd with our article(s)
        self.groups = NNTPGroup.split(groups)

        if self.filename:
            # attempt to add our filename
            self.add(filename)

    def pop(self, index=0):
        """
        Pops an Article at the specified index out of the segment table
        """
        return self.articles.pop(index)

    def add(self, content):
        """
        Add an NNTPArticle()
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
                with pushd(self.work_dir, create_if_missing=True):
                    # Not relative; is the file relative to the work_dir?
                    if isfile(content):
                        # Create a content object from the data
                        # This isn't always the best route because no part #'s
                        # are assigned this way; but if it's only a single file
                        # the user is working with; this way is much easier.

                        # file relative to current path; get our mime response
                        mr = m.from_bestguess(content)

                        # Our NNTPContent object will depend on whether or not
                        # we're dealing with an ascii file or binary
                        instance = NNTPBinaryContent \
                            if mr.is_binary() else NNTPAsciiContent

                        content = instance(
                            filepath=content,
                            work_dir=self.work_dir,
                        )

                    else:
                        # Nothing to add
                        return False

                    # At this point we fall through and the next set of
                    # if checks will catch our new content object we created

        # duplicates are ignored in a blist and therefore
        # we just capture the length of our list before
        # and after so that we can properly return a True/False
        # value
        _bcnt = len(self.articles)

        if isinstance(content, NNTPContent):
            # Create an Article and store our content
            article = NNTPArticle(
                subject=self.subject,
                poster=self.poster,
                groups=self.groups,
                work_dir=self.work_dir,
            )

            # Add the content to our article
            article.add(content)

            # Store the article in our collection
            self.articles.add(article)

        elif isinstance(content, NNTPArticle):
            # Add our article
            self.articles.add(content)

        return len(self.articles) > _bcnt

    def apply_template(self, custom=None, relative=None, strftime=True):
        """
        Iterates over all of the articles defined in the segment and replaces
        any defined {{directive}} with their translated value. All tokens must
        be wrapped in {{ }} braces.

        There are some built in directives, but you can alternatively provide
        your own as well.  The built in directives are:

            {{filename}}        : Translates to the filename associated with
                                    the 'first' attachment inside of the
                                    article.

            {{filesize}}        : Translates to the size of the 'first'
                                    attachment inside of the article

            {{filenameXXX}}     : Handles situations where you know there are
                                    multiple attachments and you want to
                                    exclusively index the filename of one of
                                    these entries. XXX is a numeric
                                    represetation starting a 001 and can range
                                    to 999

            {{filesizeXXX}}     : Handles situations where you know there are
                                    multiple attachments and you want to
                                    exclusively index the filesize of one of
                                    these entries. XXX is a numeric
                                    represetation starting a 001 and can range
                                    to 999

            {{totalsize}}       : Translates to the total size of all
                                    accumulated attachments in the article.

            {{index}}           : Translates to NNTPArticles index as
                                    represented inside of the SegmentedPost

            {{count}}           : Translates to total number of NNTPArticles
                                    defined inside of the SegmentedPost

        You can add additionally use your own custom dictionary to which you
        just provide it keys and their translated values.  If you provide any
        custom dictionary, it will always be applied 'before' the built in
        ones.  This allows you to over-ride them if you like.

        All braces are replaced, so in the event braces are detected that
        could not be translated, then they are replaced with 'nothing'

        if strftime is set to True, then the contents of the subject is also
        passed through datetime.strftime() as a last task. this allows custom
        templating to even insert datetime directives if required knowing they
        will be masked shortly thereafter.

        The time strftime() bases itself off is relative to the time specified
        (the actual variable 'relative').  If 'relative' is not specified then
        'now()' is used instead

        The following information was taken from:
                https://docs.python.org/2/library/datetime.html

        It will give you an idea of what datetime objects can be set to. Note
        that datetime.stftime() entries are 'NOT' wrapped in {{ }} objects.

        %a  Weekday as locale’s abbreviated name.
                Sun, Mon, …, Sat (en_US)
                So, Mo, …, Sa (de_DE)

        %A  Weekday as locale’s full name.
                Sunday, Monday, …, Saturday (en_US)
                Sonntag, Montag, …, Samstag (de_DE)

        %w  Weekday as a decimal number, where 0 is Sunday and 6 is Saturday.
                0, 1, …, 6

        %d  Day of the month as a zero-padded decimal number.
                01, 02, …, 31

        %b  Month as locale’s abbreviated name.
                Jan, Feb, …, Dec (en_US)
                Jan, Feb, …, Dez (de_DE)

        %B  Month as locale’s full name.
                January, February, …, December (en_US);
                Januar, Februar, …, Dezember (de_DE)

        %m  Month as a zero-padded decimal number.
                01, 02, …, 12

        %y  Year without century as a zero-padded decimal number.
                00, 01, …, 99

        %Y  Year with century as a decimal number.
                1970, 1988, 2001, 2013

        %H  Hour (24-hour clock) as a zero-padded decimal number.
                00, 01, …, 23

        %I  Hour (12-hour clock) as a zero-padded decimal number.
                01, 02, …, 12

        %p  Locale's equivalent of either AM or PM.
                AM, PM (en_US)
                am, pm (de_DE)

        %M  Minute as a zero-padded decimal number.
                00, 01, …, 59

        %S  Second as a zero-padded decimal number.
                00, 01, …, 59

        %f  Microsecond as a decimal number, zero-padded on the left.
                000000, 000001, …, 999999

        %z  UTC offset in the form +HHMM or -HHMM (empty string if the the
            object is naive).
                (empty), +0000, -0400, +1030

        %Z  Time zone name (empty string if the object is naive).
                (empty), UTC, EST, CST

        %j  Day of the year as a zero-padded decimal number.
                001, 002, …, 366

        %U  Week number of the year (Sunday as the first day of the week) as a
            zero padded decimal number. All days in a new year preceding the
            first Sunday are considered to be in week 0.
                00, 01, …, 53

        %W  Week number of the year (Monday as the first day of the week) as a
            decimal number. All days in a new year preceding the first Monday
            are considered to be in week 0.
                00, 01, …, 53

        %c  Locale’s appropriate date and time representation.
                Tue Aug 16 21:30:00 1988 (en_US);
                Di 16 Aug 21:30:00 1988 (de_DE)

        %x  Locale’s appropriate date representation.
                08/16/88 (None)
                08/16/1988 (en_US)
                16.08.1988 (de_DE)

        %X  Locale’s appropriate time representation.
                21:30:00 (en_US)
                21:30:00 (de_DE)

        %%  A literal '%' character.
                %
        """
        if custom is None:
            custom = {}

        for index, article in enumerate(self.articles):

            # Make a copy of our subject
            subject = self.subject \
                if isinstance(self.subject, basestring) else ''

            # Make a copy of our poster
            poster = self.poster \
                if isinstance(self.poster, basestring) else ''

            if custom:
                # Apply our custom object if one is defined; first escape
                # content
                cmask = {re.escape(str(key)): str(value)
                         for (key, value) in custom.items()}

                # Build ourselves a translation map
                cmask_r = re.compile(
                    r'(' + '|'.join(cmask.keys()) + r')',
                    re.IGNORECASE,
                )

                # Apply our custom masks
                subject = cmask_r.sub(
                    lambda x: cmask[re.escape(x.group())], subject)
                poster = cmask_r.sub(
                    lambda x: cmask[re.escape(x.group())], poster)

            # Initialize ourselves a master translation table
            mask = {
                re.escape('{{count}}'): str(len(self)),
                re.escape('{{index}}'): str(index+1),
                re.escape('{{totalsize}}'): str(article.size()),
            }

            # Our first item can be referenced as
            #   {{filename001}} or {{filename}}
            #
            # Similarily the first time (only) can be reference as
            #   {{filesize001}} or {{filesize}}
            #
            try:
                mask[re.escape('{{filename}}')] = article[0].filename
                mask[re.escape('{{filesize}}')] = str(len(article[0]))

            except IndexError:
                mask[re.escape('{{filename}}')] = ''
                mask[re.escape('{{filesize}}')] = ''

            # for each file, create a filenameNo and filesizeNo
            for no, content in enumerate(article):
                try:
                    mask[re.escape('{{filename%.3d}}' % (no+1))] = \
                        article[0].filename
                    mask[re.escape('{{filesize%.3d}}' % (no+1))] = \
                        str(len(article[0]))

                except IndexError:
                    # No files found
                    mask[re.escape('{{filename%.3d}}' % (no+1))] = ''
                    mask[re.escape('{{filesize%.3d}}' % (no+1))] = ''

            # Build ourselves a translation map
            mask_r = re.compile(
                r'(' + '|'.join(mask.keys()) + r')',
                re.IGNORECASE,
            )

            # Apply our common masks
            subject = mask_r.sub(lambda x: mask[re.escape(x.group())], subject)
            poster = mask_r.sub(lambda x: mask[re.escape(x.group())], poster)

            # Final Tidy
            subject = re.sub('{{[^}]*}}', '', subject)
            poster = re.sub('{{[^}]*}}', '', poster)

            if strftime:
                # Time reference
                _relative = relative
                if _relative is None:
                    # A reference time
                    _relative = datetime.now()

                # apply time support
                subject = _relative.strftime(subject)
                poster = _relative.strftime(poster)

            # Store our subject
            article.subject = subject
            # Store our poster
            article.poster = poster

        return True

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
        if len(self.articles) != 1:
            # Not possible to split a post that is already split
            return False

        articles = self.articles[0].split(size=size, mem_buf=mem_buf)
        if articles is None:
            return False

        # Otherwise store our goods
        self.articles = articles
        return True

    def encode(self, encoders):
        """
        Iterates over all articles and encodes them based on the specified
        encoders.
        """

        # Prepare a new article set
        articles = sortedset(key=lambda x: x.key())
        for article in self.articles:
            result = article.encode(encoders)
            if not result:
                return False

            articles.add(result)

        # Store our new article set
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

    def deobsfucate(self, filebase='', codecs=None):
        """
        Using the article information we have, attempt to generate the
        filename to the best of our ability
        """
        if not len(self.articles) > 0:
            # we need at least 1 article
            return None

        if filebase is None:
            # Safety
            filebase = ''

        if codecs is None:
            codecs = self._codecs

        elif isinstance(codecs, CodecBase):
            codecs = [codecs, ]

        # Create our Mime object
        m = Mime()

        # Initialize our objects
        _name = filebase
        _mime = m.from_filename(self.filename)
        _fext = None

        if _mime and _mime.type() != DEFAULT_MIME_TYPE:
            # Store our potential extension
            _fext = _mime.extension()

        if not _name and self.filename:
            # create a filebase
            _name = splitext(basename(self.filename))[0]

        # Because our articles could amount to a lot of crap, we can only hope
        # that one of them provides us with a valid file we can work with.
        # our map allows us to pick and choose what looks good amongst
        # everything else.

        # A sorted set of matches; index 0 is our index our element was found
        # we want this to prevail when match hunting
        ext_map = dict()

        for no, article in enumerate(self.articles):
            # Detect our article _fname
            _fname = article.deobsfucate(filebase=_name, codecs=codecs)
            # Detect our type
            mr = m.from_filename(_fname)
            if mr:
                # Store our tuple if we can
                ext_map[mr.type()] = (no, _fname, mr)

        # Now comes the hard part... if we're lucky we'll have just 2 types at
        # the most (one being DEFAULT_MIME_TYPE).  We can ignore these entries
        # unless they're all we have to pick from.  Otherwise hopefully we
        # have an option #2, that will be our official extension and filename

        # Initialize our findings
        match = None
        for _, potential in ext_map.iteritems():
            if potential[2].type() == DEFAULT_MIME_TYPE or _mime is None:
                if match is None:
                    # We allow this match if we have to
                    match = potential

            elif _mime and _mime.type() == potential[2].type():
                # Our detected mime type is exactly what we expected

                # Store our match
                match = potential

                if not _name and _mime and _mime.type() != DEFAULT_MIME_TYPE:
                    # Update our basename
                    _name = splitext(match[1])[0]

                # Safely break because we can't match any better than this
                break

            elif _mime and _mime.type() == DEFAULT_MIME_TYPE:
                # Update our candidate
                match = potential

        if not match:
            return None

        # use the extension gathered from our match and apply it to our base
        # if we can
        if not _fext:
            _fext = match[2].extension()
            # Return our matched filename
            return '%s%s' % (_name, _fext)

        # Return your file
        return match[1]

    def save(self, filepath=None, copy=False):
        """
        Saves all of the NNTPContent into the directory specified by
        the filepath; if no filepath is specified, then content is written
        into it's work_dir
        """

        for article in self.articles:
            if not article.save(filepath=filepath, copy=copy):
                return False

        return True

    def files(self):
        """
        Returns a list of the files within article
        """
        _files = []
        for article in self.articles:
            _files.extend(article.files())
        return _files

    def size(self):
        """
        return the total size of our articles
        """
        return sum(a.size() for a in self.articles)

    def strsize(self):
        """
        return the total size of our articles
        """
        return bytes_to_strsize(self.size())

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
