# -*- coding: utf-8 -*-
#
# A class to make posting easy on usenet
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
from lib.Utils import strsize_to_bytes

# Logging
import logging
from lib.Logging import NEWSREAP_LOGGER
logger = logging.getLogger(NEWSREAP_LOGGER)


class PostType(object):
    PLAIN = 'text/plain'
    HTML = 'text/html'
    YENC = 'text/yenc'
    UENCODE = 'text/uencode'

POST_TYPES = (
    PostType.PLAIN,
    PostType.HTML,
    PostType.YENC,
    PostType.UENCODE,
)

# There are many formating types
RELEASE_FORMAT_TITLE = {
    PostType.YENC: {
        'subject': '{title} ({fileno}/{filemax}) "{filename}" yEnc ({partno}/{partmax})',
        'parse': re.compile(
            r'\s*(?P<title>[^\s].*[^\s]?)\s+' +\
            '\((?P<fileno>[0-9]+)\s*/\s*(?P<filemax>[0-9]+)\)\s+' +\
            '["\']?(?P<filename>.+)["\']?\s+yEnc\s+' +\
            '\((?P<partno>[0-9]+)\s*/(?P<partmax>[0-9]+)\)',
        ),
    }
}

class NNTPPostBase(object):
    """
    Defines the base class others can inherit from to simplify
    posting.  the NNTPConnection post() function is dependant on
    this class as it's argument.  It defines the payload
    and operates 'like' a stream object (but is not one)
    """
    def __init__(self, poster='', max_article_size='25M',
                 ptype=PostType.YENC, *args, **kwargs):
        """
        Initialize Posting Connection
        """

        # Store Poster
        self.poster = poster
        if not self.poster:
            # Default if none was specified
            self.poster = 'Unknown Poster <unknown@poster.com>'

        # Used for breaking a post into multi parts.  This occurs when
        # the size of the post is determined to be larger then this
        # specified value (in Bytes)
        self.max_article_size = strsize_to_bytes(max_article_size)

        # These variables are initialized when put() functions are
        # called
        self.title = None
        self.file_index = 0
        self.file_total = 0

        self.post_type = PostType.YENC
        # Yenc
        self.yenc_part_index = 0
        self.yenc_part_total = 0


    def _get_subject(self):
        re_map = {
            '{title}': self.title,
            '{fileno}': self.file_index,
            '{filemax}': self.file_total,
            '{partno}': self.yenc_part_index,
            '{partmax}': self.yenc_part_total,
        }

        # Iterate over above list and store content accordingly
        re_table = re.compile(
            r'(' + '|'.join(re_map.keys()) + r')',
            re.IGNORECASE,
        )

        return re_table.sub(
            lambda x: re_map[x.group()],
            RELEASE_FORMAT_TITLE[self.post_type]['subject'],
        )


    def post_file(self, filename, title=None, total=None,
                 index=None, ptype=None, *args, **kwargs):
        """
        Pushes a file (identified by the filename to the server)

        """

        # Handle Title Variable
        if title is not None:
            self.title = title

        # Handle Total Variable
        if total is None:
            if not self.file_total:
                self.file_total = 1
                # Also adjust our index
                self.file_index = 1

        elif total > 0:
            # Initialize the total to 1
            self.file_total = total
            # Also adjust our index
            self.file_index = 1

        # We allow the setting of total to be zero (0) or less
        # these prevent the total/index from even being displayed
        # in the subject

        # Handle Index Variable
        if index is None:
            if not self.file_index:
                self.file_index = 1
            else:
                # Increment our index
                self.file_index += 1
        else:
            # Initialize the index
            self.file_index = index

        # Retrieve the subject
        subject = self._get_subject()
