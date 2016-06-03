# -*- coding: utf-8 -*-
#
# A decoder to handle for handling group fetches
#
# Copyright (C) 2015 Chris Caron <lead2gold@gmail.com>
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
from lib.codecs.CodecBase import CodecBase
from lib.NNTPMetaContent import NNTPMetaContent

# Logging
import logging
from lib.Logging import NEWSREAP_CODEC
logger = logging.getLogger(NEWSREAP_CODEC)

# Defines the parsing of an LIST ACTIVE Response Entry
NNTP_LIST_ACTIVE_RESPONSE_RE = re.compile(
    # Group
    r'\s*(?P<group>[^\s]+)\s+' + \
    # High-Water Mark
    r'[0]*(?P<low>([1-9]+[0-9]*|0))\s+' + \
    # Low-Water Mark
    r'[0]*(?P<high>([1-9]+[0-9]*|0))\s*' + \
    # Flags (may or may not exist)
    r'(?P<flags>[^\s]+)?\s*$'
)

class GroupStatus(object):
    POSTING_ALLOWED = 'y'
    POSTING_DENIED = 'n'
    POSTING_MODERATED = 'm'

GROUP_STATUS_FLAGS = (
    GroupStatus.POSTING_ALLOWED,
    GroupStatus.POSTING_DENIED,
    GroupStatus.POSTING_MODERATED,
)

class CodecGroups(CodecBase):
    """
    This is the codec used to store general content parsed that is not encoded
    on an NNTP Server.
    """

    def __init__(self, descriptor=None, tmp_dir=None, *args, **kwargs):
        super(CodecGroups, self).__init__(descriptor=descriptor,
            tmp_dir=tmp_dir, *args, **kwargs)

        # Our Decoded Content
        self.decoded = NNTPMetaContent(tmp_dir=self.tmp_dir)


    def detect(self, line, relative=True):
        """
        A Simple function that can be used to determine if there is a group
        entry or not. The relative flag is not used for this codec but is
        defined as per the codec standards.

        It returns None if there this is not a group entry line, otherwise
        it returns a dictionary of the keys and their mapped values.

        """
        group_re = NNTP_LIST_ACTIVE_RESPONSE_RE.match(line)
        if not group_re:
            return None

        try:
            low = int(group_re.group('low'))
            high = int(group_re.group('high'))
        except (ValueError, TypeError):
            # can't be a group line
            return None

        if low < 0:
            # can't be a group line
            return None

        if high < 0:
            # can't be a group line
            return None

        # Detect empty (based on the 3 possibilities)
        # * high is >= low
        # * low - high == 1
        # * low - high == 0 (this check isn't needed if we check >=)
        if high >= low or (low - high) == 1:
            count = 0
        else:
            count = low-high

        try:
            # We intentionally do not strip unsupported flags
            # so it alright for usenet servers to impliment new ones
            # and we can keep on trucking with what we have. Feel
            # free to use the mapped valid defined in here though
            # in the GroupStatus object
            flags = list(group_re.group('flags'))

        except TypeError:
            # There were no flags defined
            # Always set a list (for consistency)
            flags = []

        return {
            'group': group_re.group('group').lower(),
            'low': low,
            'high': high,
            'count': count,
            'flags': flags,
        }


    def decode(self, stream):
        """ Decode the group content
        """

        # We need to parse the content until we either reach
        # the end of the file or get to an 'end' tag
        # We do not need to use the decode_loop() simply because this
        # function will never be called concurrently with other threads.
        while True:
            # Read in our data
            data = stream.readline()
            if not data:
                # We're done
                break

            # Total Line Tracking
            self._total_lines += 1

            # Detect a group line
            decoded = self.detect(data, relative=False)
            if decoded is not None:
                # We're good
                self.decoded.content.append(decoded)

            # Track the number of bytes decoded
            self._decoded += len(data)

        # Line Tracking
        self._lines = len(self.decoded.content)

        # With this group type; we're always expecting more
        # there are no if/ands or buts!
        return True


    def reset(self):
        """
        Reset our decoded content
        """
        super(CodecGroups, self).reset()

        # Reset Our Result set
        self.decoded = NNTPMetaContent(tmp_dir=self.tmp_dir)


    def __str__(self):
        """
        Return a printable version of the codec
        """
        return repr(self)


    def __repr__(self):
        """
        Return a printable object
        """
        return '<CodecGroups lines_processed=%d />' % (
            self._lines,
        )
