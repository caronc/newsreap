# -*- coding: utf-8 -*-
#
# An NNTPResponse Object used by the NNTPManagaer
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

import gevent.monkey
gevent.monkey.patch_all()

from blist import sortedset
from datetime import datetime

from .NNTPAsciiContent import NNTPAsciiContent
from .NNTPContent import NNTPContent


class NNTPResponseCode(object):
    """
    A Simple lookup table that makes the error codes returned by the NNTP
    server a bit more human readable.  These codes are based on:
       - http://tools.ietf.org/html/rfc3977
       - http://tools.ietf.org/html/rfc2980
    """

    # Custom Responce Codes
    NO_ARTICLE_DMCA = 499
    NO_ARTICLE_NUKED = 498

    # There are some codes that aren't defined here which were evented
    # A Custom error code to handle situations where what was
    # received didn't line up to what we should have received
    BAD_RESPONSE = 504
    NO_CONNECTION = 505
    CONNECTION_LOST = 506
    FETCH_ERROR = 507
    INVALID_GROUP = 508
    INVALID_INPUT = 509

    # to also work with the NNTP wrapper to make life easy to
    # reference back (simply check you're return code against this
    # list.  If it's not here, then we have a bug and should add it!

    # 200 Service available, posting allowed
    # 201 Service available, posting prohibited
    # 203 Streaming is OK
    # 205 Connection closing
    # 235 Article transferred OK
    # 238 No such article found, please send it to me
    # 239 Article transferred OK
    # 240 Article received OK
    # 250 Authorization accepted
    # 281 Authentication accepted
    # 290 features updated
    SUCCESS = \
        (111, 200, 201, 203, 205, 211, 223, 235, 238, 239, 240, 250, 281, 290)

    # A Success message that will be followed with data
    # This is done when calling NEWSGROUPS, XOVER, etc

    # 100 Help text follows (multi-line)
    # 101 Capability list follows (multi-line)
    # 211 list of article numbers follow (multi-line)
    # 215 Information follows (multi-line)
    # 218 tin-style index follows (multi-line)
    # 224 Overview information follows (multi-line)
    # 225 Headers follow (multi-line)
    # 231 List of new newsgroups follows (multi-line)
    # 230 list of new articles by message-id follows (multi-line)
    # 282 list of groups and descriptions follows (multi-line)
    # 288 Binary data to follow (multi-line)
    SUCCESS_MULTILINE = (
        100, 101, 211, 215, 218, 220, 221, 222, 224, 225, 230, 231, 282, 288)

    # Pending is a state the NNTP server will enter
    # when it's waiting for 'you' to continue to doing something.
    # it's partially a success message, but is awaiting the next
    # set of data from you to proceed

    # 335 Send article to be transferred
    # 340 Send article to be posted
    # 350 Continue with authorization sequence
    # 381 More authentication information required
    PENDING = (335, 381, 340, 350)

    # Posting Failures
    # 400 Service temporarily unavailable
    # 400 not accepting articles
    # 435 Article not wanted
    # 435 Duplicate
    # 437 Article rejected; don't send again
    # 438 Already have it, please don't send it to me
    # 450 Authorization required for this command
    # 480 Transfer permission denied
    # 480 Authentication required
    ACTION_DENIED = (400, 435, 437, 438, 440, 450, 480)

    # 431 Try sending it again later
    # 436 Transfer not possible; try again later
    # 436 Transfer failed
    # 436 Retry later
    # 439 Article transfer failed
    # 441 Posting failed
    # 452 Authentication rejected
    # 482 Authentication rejected
    ACTION_FAILED = (431, 436, 439, 441, 452, 482)

    # 420 No current article selected
    # 420 No article with that number
    # 421 No next article in this group
    # 422 No previous article in this group
    # 423 No article with that number
    # 423 No such article in this group
    # 423 Empty range
    # 430 No article with that message-id
    # 430 No Such Article Found
    NO_ARTICLE = (
        420, 421, 422, 423, 430, 435, NO_ARTICLE_DMCA, NO_ARTICLE_NUKED,
    )

    # 411 No such newsgroup
    # 412 No newsgroup selected
    # 412 Not currently in newsgroup
    # 418 no tin-style index is available for this news group
    # 481 Groups and descriptions unavailable
    NO_GROUP = (411, 412, 418, 481)

    # 500 Command not understood
    # 501 Syntax Error
    # 502 Service permanently unavailable
    # 503 Data item not stored
    # 503 Overview by message-id unsupported
    # 503 program error, function not performed
    ERROR = (
        500, 501, 502, 503, BAD_RESPONSE, NO_CONNECTION, INVALID_GROUP,
        CONNECTION_LOST,
    )


class NNTPResponse(object):
    """
    This is used with the NNTPManager class; specificially the query()
    function.

    you feed NNTPManager.query() NNTPRequest() objects and get NNTPResponse()
    objects in return.
    """

    def __init__(self, code=None, code_str=None, work_dir=None,
                 *args, **kwargs):
        """
        Initializes a request object and the 'action' must be a function
        name that exists in the NNTPConnection(), you can optionally specify
        it the args and kwargs too.
        """

        # The response information is placed here
        self.code = code
        if self.code is None:
            self.code = 0

        self.code_str = code_str
        if self.code_str is None:
            self.code_str = ''

        # Track the time our response object was created; this is
        # useful for multi-threading since we know when our response
        # was generated
        self.created = datetime.now()

        # Our body contains non-decoded content
        self.body = NNTPAsciiContent(work_dir=work_dir)

        # Contains a list of decoded content
        self.decoded = sortedset(key=lambda x: x.key())

        # For iterating over decoded items
        self._iter = None

    def is_success(self, multiline=None):
        """
        Returns True if the code falls in the success category

        If multline is set to None, then we're just checking for a success
        flag. Both (both multi-lined and non success messages are checked
        here). This is the defalt option

        If multline is set to False, then we're just checking for a success
        flag associated with only a non-multi lined response.

        If multline is set to True, then we're just checking for a success
        flag associated with only multi lined response.
        """

        if multiline is None:
            # any 200 reponse is good
            return self.code/200 == 1

        elif multiline is True:
            # Check multiline only
            if self.code in NNTPResponseCode.SUCCESS_MULTILINE:
                return True

        else:
            # Check non-multiline only
            if self.code in NNTPResponseCode.SUCCESS:
                return True

        # No match
        return False

    def detach(self):
        """
        Detach the article stored on disk from being further managed by this
        class
        """
        for entry in self.decoded:
            if isinstance(entry, NNTPContent):
                entry.detach()
        return


    def key(self):
        """
        Returns a key that can be used for sorting with:
            lambda x : x.key()
        """
        return self.body.key()

    def next(self):
        """
        Python 2 support
        Support stream type functions and iterations
        """
        if not self._iter:
            self._iter = iter(self.decoded)

        return next(self._iter)

    def __next__(self):
        """
        Python 3 support
        Support iterating through list
        """
        if not self._iter:
            self._iter = iter(self.decoded)

        return next(self._iter)

    def __iter__(self):
        """
        Mimic iter()
        """
        return iter(self.decoded)

    def __len__(self):
        """
        Returns the length of the article
        """
        length = 0
        for a in self.decoded:
            length += len(a)
        return length

    def __contains__(self, code):
        """
        Enables usage of the 'in' keyword.  This allows us to check
        if a code is set by it's value or by a set/tuple

        """

        if isinstance(code, (set, tuple, list)):
            # find if one item in set matches us
            return self.code in code

        elif isinstance(code, int):
            return code == self.code

    def __str__(self):
        """
        Returns the response information returned from the NNTP Server
        """
        if not self.code:
            return ''
        elif not self.code_str:
            return '%d' % self.code

        return '%d: %s' % (self.code, self.code_str)

    def __repr__(self):
        """
        Return an unambigious version of the object
        """
        return '<NNTPResponse id="%s" />' % id(self)
