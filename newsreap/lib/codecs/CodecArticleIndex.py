# -*- coding: utf-8 -*-
#
# A decoder to handle for handling xover (article indexes) fetches
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
from blist import sorteddict
from datetime import datetime
from pytz import UTC
from dateutil.tz import tzutc
from dateutil.parser import parse

from lib.codecs.CodecBase import CodecBase
from lib.NNTPMetaContent import NNTPMetaContent
from lib.NNTPIOStream import NNTP_DEFAULT_ENCODING

# Logging
import logging
from lib.Logging import NEWSREAP_CODEC
logger = logging.getLogger(NEWSREAP_CODEC)

# Defines the parsing of an XOVER Response Entry
NNTP_XOVER_RESPONSE_RE = re.compile(
    # Article Number (entry 1)
    # This is unique to the Usenet/NNTP Service Provider only and will vary
    # when tested across different providers.
    r'^[ ]*(?P<article_no>[0-9]+)[ ]*\t' + \
    # Subject (entry 2)
    r'[ ]*(?P<subject>[^\t]+)[ ]*\t' + \
    # Poster (entry 3)
    r'[ ]*(?P<poster>[^\t]+)[ ]*\t' + \
    # Date/Time (entry 4)
    r'[ ]*(?P<date>[^\t]+)[ ]*\t' + \
    # Unique Message-ID (entry 5)
    r'[ ]*<?[ ]*(?P<id>[^\t>]+)[ ]*>?[ ]*\t' + \
    # unknown? (entry 6)
    r'[ ]*(?P<ignored>[^\t]*)[ ]*\t' + \
    # Message Size (in Bytes) (entry 7)
    r'[ ]*(?P<size>[0-9]+)[ ]*\t' + \
    # Lines (entry 8)
    r'[ ]*(?P<lines>[0-9]+)?[ ]*\t' + \
    # Groups (entry 9)
    r'[ ]*(?P<xref>Xref[ ]*:)?(?P<group>[ ]*([^\t]+))+\s*',
    re.IGNORECASE,
)

class XoverGrouping(object):
    """
    Defines the xover grouping
    """
    BY_ARTICLE_NO = 0
    BY_POSTER_TIME = 1
    BY_TIME = 2


XOVER_GROUPINGS = (
    # The article number the server assigned it (like a primary key)
    # It always increments from the low to high watermark and is the
    # default fetch order an NNTP returns content in
    XoverGrouping.BY_ARTICLE_NO,
    # Sorts content by poster and then by time
    XoverGrouping.BY_POSTER_TIME,
    # Sorts content by time
    XoverGrouping.BY_TIME,
)


class CodecArticleIndex(CodecBase):
    """
    This is the codec used to store general content parsed that is not encoded
    on an NNTP Server.
    """

    def __init__(self, descriptor=None, filters=None, sort=None,
                 encoding=None, tmp_dir=None, *args, **kwargs):
        super(CodecArticleIndex, self).__init__(descriptor=descriptor,
            tmp_dir=tmp_dir, *args, **kwargs)

        # Our Meta Content
        self.decoded = NNTPMetaContent(tmp_dir=self.tmp_dir)

        # Switch our content subvalue to be a sorteddict()
        self.decoded.content = sorteddict()

        # Filters
        self.filters = filters

        # Sort Order
        self.sort = sort
        if self.sort is None or self.sort not in XOVER_GROUPINGS:
            self.sort = XoverGrouping.BY_POSTER_TIME

        # The character set encoding usenet content is retrieved in
        if encoding is None:
            self.encoding = NNTP_DEFAULT_ENCODING
        else:
            self.encoding = encoding


    def detect(self, line, relative=True):
        """
        A Simple function that can be used to determine if there is a group
        entry or not. The relative flag is not used for this codec but is
        defined as per the codec standards.

        It returns None if there this is not a group entry line, otherwise
        it returns a dictionary of the keys and their mapped values.

        """
        result = NNTP_XOVER_RESPONSE_RE.match(line)
        if not result:
            return None

        try:
            # handle datetime
            article_date = parse(result.group('date'))

        except (ValueError, TypeError):
            try:
                # We weren't able to parse the date
                article_date = parse(result.group('date'), fuzzy=True)

            except (ValueError, TypeError):
                # It would be great if users of the product would email
                # us these errors so we can work on making them better
                logger.warning(
                    "Unparsable XOVER date '%s'" % (result.group('date')),
                )
                article_date = datetime.fromtimestamp(0)

        if article_date.tzinfo is not None:
            # ensure all times are naive
            if isinstance(article_date.tzinfo, tzutc):
                article_date = article_date.replace(tzinfo=None)
            else:
                article_date = article_date.astimezone(UTC)\
                        .replace(tzinfo=None)


        try:
            lines = int(result.group('lines'))
        except (TypeError, ValueError):
            # Initialize lines to -1 if they weren't specified
            lines = -1

        entry = {
            'id': result.group('id'),
            'article_no': int(result.group('article_no')),
            'poster': result.group('poster').decode(self.encoding),
            'date': article_date,
            'subject': result.group('subject').decode(self.encoding),
            'size': int(result.group('size')),
            'lines': lines,
            'group': None,
            'score': 0,
            'xgroups': {},
        }

        # Split results
        results = re.split('\s+', result.group('group').strip())

        # Append remaining groups
        for x in results:
            grp = x.split(':')
            if len(grp) > 1:
                # we're dealing with a Cross Post
                try:
                    entry['xgroups'][grp[0]] = int(grp[1])
                except ValueError:
                    # This happens from time to time when a group
                    # doesn't have it's cross-group identifier
                    # identified; so we set it to zero for
                    # compatibility for anyone else using this
                    # wrapper
                    entry['xgroups'][grp[0]] = 0
            else:
                entry['group'] = grp[0]

        return entry


    def decode(self, stream):
        """ Decode the group content
        """

        # We need to parse the content until we either reach
        # the end of the file or get to an 'end' tag
        while self.decode_loop():

            # Read in our data
            data = stream.readline()
            if not data:
                # We're done
                break

            # Total Line Tracking
            self._total_lines += 1

            # Detect a group line
            entry = self.detect(data, relative=False)
            if entry is None:
                continue

            # A key we'll use hash into our btree with.
            # This grouping will allow us to destroy content
            # as it is processed as well as the handling of content
            # together
            if self.sort == XoverGrouping.BY_POSTER_TIME:
                key = '%s:%s:%.10d' % (
                    entry['poster'],
                    entry['date'].strftime('%Y%m%d%H%M%S'),
                    entry['article_no'],
                )

            elif self.sort == XoverGrouping.BY_TIME:
                key = '%s:%.10d' % (
                    entry['date'].strftime('%Y%m%d%H%M%S'),
                    entry['article_no'],
                )

            else: # XoverGrouping.BY_ARTICLE_NO
                key = '%.10d' % entry['article_no']

            # Check to see if we've added filters
            if self.filters:
                # First Apply our scores
                entry['score'] += sum(
                    [ f.score(**entry) for f in self.filters ],
                )

                # Scan our whitelist(s) and break on our first match
                if not next((True for f in self.filters \
                             if f.whitelist(**entry) == True), False):

                    # Scan our blacklist(s) and break on our first
                    # match
                    if next((True for f in self.filters \
                            if f.blacklist(**entry) == True), False):

                        # Skip entries matched on our blacklist
                        continue

            self.decoded.content[key] = entry
            #logger.debug('len=%d' % NNTP_XOVER_RESPONSE_RE.groups)
            #for x in range(1, NNTP_XOVER_RESPONSE_RE.groups):
            #    logger.debug('  %d=%s' % (x, str(result.group(x))))

            # Track the number of bytes decoded
            self._decoded += len(data)

        # Line Tracking
        self._lines = len(self.decoded.content)

        # With this group type; we're always expecting more
        # there are no if/ands or buts!
        return True


    def is_valid(self):
        """
        A simple function that returns whether or not the block just read
        checks out okay.
        """
        return len(self.decoded)


    def reset(self):
        """
        Reset our decoded content
        """
        super(CodecArticleIndex, self).reset()

        # Our Meta Content
        self.decoded = NNTPMetaContent(tmp_dir=self.tmp_dir)

        # Switch our decoded subvalue to be a sorteddict()
        self.decoded.content = sorteddict()


    def __str__(self):
        """
        Return a printable version of the codec
        """
        return repr(self)


    def __repr__(self):
        """
        Return a printable object
        """
        return '<CodecArticleIndex lines_processed=%d />' % (
            self._lines,
        )
