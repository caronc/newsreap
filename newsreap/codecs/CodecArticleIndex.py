# A decoder to handle for handling xover (article indexes) fetches
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

import re
from sortedcontainers import SortedDict as sorteddict
from datetime import datetime
from datetime import timedelta
from email.errors import HeaderParseError
from email.header import decode_header
from email.header import make_header
from email.utils import parsedate_tz
from pytz import UTC
from dateutil.tz import tzutc
from dateutil.parser import parse

from newsreap.codecs.CodecBase import CodecBase
from newsreap.NNTPMetaContent import NNTPMetaContent
from newsreap.NNTPIOStream import NNTP_DEFAULT_ENCODING

# Logging
import logging
from newsreap.Logging import NEWSREAP_CODEC
logger = logging.getLogger(NEWSREAP_CODEC)

# The (RFC 5322) date format almost every article uses; these can be read
# much faster than the free form dates we otherwise have to deal with
#   ie: Sat, 01 Jan 2020 00:00:07 +0000
RFC5322_DATE_RE = re.compile(
    r'^\s*([A-Za-z]{3},\s*)?\d{1,2}\s+[A-Za-z]{3}\s+\d{4}\s+'
    r'\d{1,2}:\d{2}(:\d{2})?\s+[+-]\d{4}\s*$', re.ASCII)


def parse_date(date):
    """
    Returns the (naive UTC) datetime of a date found in an article; None
    is returned if the date can't be read.
    """
    if RFC5322_DATE_RE.match(date):
        parsed = parsedate_tz(date)
        if parsed is not None:
            try:
                return datetime(*parsed[:6]) - timedelta(seconds=parsed[9])

            except (ValueError, OverflowError):
                # Fall back to our slower (but more forgiving) parser
                pass

    try:
        article_date = parse(date)

    except (ValueError, TypeError, OverflowError):
        try:
            # We weren't able to parse the date
            article_date = parse(date, fuzzy=True)

        except (ValueError, TypeError, OverflowError):
            return None

    if article_date.tzinfo is not None:
        # ensure all times are naive
        if isinstance(article_date.tzinfo, tzutc):
            article_date = article_date.replace(tzinfo=None)
        else:
            article_date = article_date.astimezone(UTC)\
                    .replace(tzinfo=None)

    return article_date


# Defines the parsing of an XOVER Response Entry
NNTP_XOVER_RESPONSE_RE = re.compile(
    # Article Number (entry 1)
    # This is unique to the Usenet/NNTP Service Provider only and will vary
    # when tested across different providers.
    br'^[ ]*(?P<article_no>[0-9]+)[ ]*\t' + \
    # Subject (entry 2)
    br'[ ]*(?P<subject>[^\t]+)[ ]*\t' + \
    # Poster (entry 3)
    br'[ ]*(?P<poster>[^\t]+)[ ]*\t' + \
    # Date/Time (entry 4)
    br'[ ]*(?P<date>[^\t]+)[ ]*\t' + \
    # Unique Message-ID (entry 5)
    br'[ ]*<?[ ]*(?P<id>[^\t>]+)[ ]*>?[ ]*\t' + \
    # unknown? (entry 6)
    br'[ ]*(?P<ignored>[^\t]*)[ ]*\t' + \
    # Message Size (in Bytes) (entry 7)
    br'[ ]*(?P<size>[0-9]+)[ ]*\t' + \
    # Lines (entry 8)
    br'[ ]*(?P<lines>[0-9]+)?[ ]*\t' + \
    # Groups (entry 9)
    br'[ ]*(?P<xref>Xref[ ]*:)?(?P<group>[ ]*([^\t]+))+\s*',
    re.IGNORECASE | re.ASCII,
)

# What every overview line holds (after its article number) in this order
# (RFC 3977 8.4)
OVERVIEW_FIELDS = (
    'Subject:', 'From:', 'Date:', 'Message-ID:', 'References:', ':bytes',
    ':lines')

# How many fields an overview line has at least (the article number, then
# the fields above)
OVERVIEW_COUNT = len(OVERVIEW_FIELDS) + 1

# What we take the fields after them to be when a server doesn't say (see
# LIST OVERVIEW.FMT); nearly every server sends the Xref there
OVERVIEW_EXTRAS = ('Xref:full', )


def overview_extras(lines):
    """
    Returns the fields (after the first seven) the lines of a LIST
    OVERVIEW.FMT reply say an overview line holds; OVERVIEW_EXTRAS when
    there aren't any
    """
    names = [line.strip() for line in lines if line.strip()]
    if len(names) <= len(OVERVIEW_FIELDS):
        # Only the usual ones (or nothing we can read)
        return OVERVIEW_EXTRAS if not names else ()

    return tuple(names[len(OVERVIEW_FIELDS):])


class XoverGrouping:
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
                 encoding=None, work_dir=None, fields=None, *args, **kwargs):
        super().__init__(descriptor=descriptor,
            work_dir=work_dir, *args, **kwargs)

        # Our Meta Content
        self.decoded = NNTPMetaContent(work_dir=self.work_dir)

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

        # What the fields after the first seven are (see overview_extras()),
        # and which of them is the Xref
        self.fields = tuple(fields) if fields is not None \
            else OVERVIEW_EXTRAS
        self.xref_at = next((
            no for no, name in enumerate(self.fields)
            if name.lower().startswith('xref:')), None)


    def detect(self, line, relative=True):
        """
        A Simple function that can be used to determine if there is a group
        entry or not. The relative flag is not used for this codec but is
        defined as per the codec standards.

        It returns None if there this is not a group entry line, otherwise
        it returns a dictionary of the keys and their mapped values.

        An overview line holds the article number, then the seven fields
        every server sends in this order (RFC 3977): Subject, From, Date,
        Message-ID, References, bytes and lines.  Anything after them is
        what LIST OVERVIEW.FMT says it is (see the `fields` we were given);
        the Xref (the groups it was posted to) is the one we use.
        """
        if isinstance(line, str):
            line = line.encode(self.encoding)

        fields = line.rstrip(b'\r\n').split(b'\t')
        if len(fields) < OVERVIEW_COUNT:
            # Not an overview line (or a broken one)
            return None

        try:
            article_no = int(fields[0].strip())

        except ValueError:
            # Every line starts with its article number
            return None

        message_id = fields[4].strip().strip(b'<>').strip()
        if not message_id:
            # Nothing to find it by
            return None

        date = fields[3].strip().decode(self.encoding, 'replace')
        article_date = parse_date(date)
        if article_date is None:
            # It would be great if users of the product would email
            # us these errors so we can work on making them better
            logger.warning(
                "Unparsable XOVER date '%s'" % (date),
            )
            article_date = datetime.fromtimestamp(0)

        try:
            size = int(fields[6].strip())

        except ValueError:
            # Missing (or garbage); we don't know it
            size = 0

        try:
            lines = int(fields[7].strip())

        except ValueError:
            # Initialize lines to -1 if they weren't specified
            lines = -1

        entry = {
            'id': message_id.decode(self.encoding, 'replace'),
            'article_no': article_no,
            'poster': self.text(fields[2]),
            'date': article_date,
            'subject': self.text(fields[1]),
            'size': size,
            'lines': lines,
            'group': None,
            'score': 0,
            'xgroups': {},
        }

        xref = self.xref(fields[OVERVIEW_COUNT:])
        if not xref:
            return entry

        # "Xref: server group:number group:number ..."; the first entry
        # is the server's name
        for x in xref.split():
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

    def text(self, value):
        """
        Returns a subject or poster as text: UTF-8 if it reads as UTF-8,
        otherwise in the server's encoding, with any RFC 2047 encoded words
        (ie: =?UTF-8?B?Q2Fmw6k=?=) decoded
        """
        value = value.strip()
        try:
            text = value.decode('utf-8')

        except UnicodeDecodeError:
            # Not UTF-8; the server's own encoding (latin-1 by default,
            # which reads anything)
            text = value.decode(self.encoding, 'replace')

        if '=?' not in text:
            # The usual case
            return text

        try:
            return str(make_header(decode_header(text))).strip()

        except (UnicodeError, LookupError, ValueError, TypeError,
                HeaderParseError):
            # Badly encoded (or an unknown character set); keep it as it is
            return text

    def xref(self, extras):
        """
        Returns the Xref value (without its 'Xref:' name) of the fields
        that follow the first seven; empty if there isn't one
        """
        if self.xref_at is not None and len(extras) > self.xref_at:
            value = extras[self.xref_at]

        else:
            # Where we expected it isn't there; look for it by name
            value = next((e for e in extras
                          if e.lstrip()[:5].lower() == b'xref:'), b'')

        value = value.strip()
        if value[:5].lower() == b'xref:':
            # Sent with its name (a ':full' field)
            value = value[5:]

        return value.decode(self.encoding, 'replace').strip()


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
        super().reset()

        # Our Meta Content
        self.decoded = NNTPMetaContent(work_dir=self.work_dir)

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
