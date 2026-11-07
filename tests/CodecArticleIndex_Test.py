# Test the NNTP Index Header (XOVER Fetching) Codec
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

import sys
import pytest
from os.path import dirname
from os.path import abspath
from datetime import datetime

try:
    from tests.TestBase import TestBase
except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

from newsreap.codecs.CodecArticleIndex import CodecArticleIndex


class Codec_ArticleIndex(TestBase):

    @classmethod
    def test_bad_groups(cls):
        """
        Make sure we fail on bad groups
        """
        # Initialize Codec
        ch = CodecArticleIndex()

        # Empty lines are not valid
        assert ch.detect("") == None
        # white space
        assert ch.detect("    ") == None

    @classmethod
    def test_strange_formated(cls):
        """
        A List of strange errors that surfaced when indexing
        usenet.  I put them here to try to catch them and bullet
        proof it for next time.
        """

        # Initialize Codec
        ch = CodecArticleIndex()

        # astraweb.us: alt.binaries.zune.videos
        # date failed to parse: 'mercredi, 23 jul 2014 20:01:02 -0600'
        assert ch.detect(
            "24423	Dawn of the Planet of the Apes 2014 480p Webrip XviD " +\
            "AC3-Osiris - Dawn of the Planet of the Apes 2014 480p Webrip" +\
            "XviD AC3.US-Osiris.nzb 241176 bytes (1/1)	Randrup@dcfre.net" +\
            "	mercredi, 23 jul 2014 20:01:02 -0600	" +\
            "<23071420.0102@dcfre.net>		338850	5363	" +\
            "Xref: news-big.astraweb.com alt.binaries.zappateers:169669 " +\
            "alt.binaries.zoogz-rift:160178 alt.binaries.zune.videos:24423 " +\
            "alt.binaries.zygomorphic:27068"
        ) == {
            'id': '23071420.0102@dcfre.net',
            'article_no': 24423,
            'score': 0,
            'group': 'news-big.astraweb.com',
            'poster': 'Randrup@dcfre.net',
            'date': datetime(2014, 7, 24, 2, 1, 2),
            'xgroups': {
                'alt.binaries.zappateers': 169669,
                'alt.binaries.zoogz-rift': 160178,
                'alt.binaries.zune.videos': 24423,
                'alt.binaries.zygomorphic': 27068,
            },
            'size': 338850,
            'lines': 5363,
            'subject':
                'Dawn of the Planet of the Apes 2014 480p Webrip ' + \
                'XviD AC3-Osiris - Dawn of the Planet of the Apes ' + \
                '2014 480p WebripXviD AC3.US-Osiris.nzb 241176 bytes (1/1)',
        }

    def test_article_dates(self):
        """
        Dates are read into naive UTC datetimes; odd ones still work
        """
        from datetime import datetime
        from newsreap.codecs.CodecArticleIndex import parse_date

        # The common (RFC 5322) format
        assert parse_date('Sat, 01 Feb 2020 10:11:12 +0000') == \
            datetime(2020, 2, 1, 10, 11, 12)
        assert parse_date('1 Feb 2020 10:11 -0130') == \
            datetime(2020, 2, 1, 11, 41)
        assert parse_date('Sun,02 Feb 2020 01:00:00 +0200') == \
            datetime(2020, 2, 1, 23, 0)

        # Less common formats
        assert parse_date('11 Aug 2014 08:33:07 GMT') == \
            datetime(2014, 8, 11, 8, 33, 7)
        assert parse_date('2015-01-05 10:00') == datetime(2015, 1, 5, 10)
        assert parse_date('Fri, 31 Jul 2015 22:12:03 -0000') == \
            datetime(2015, 7, 31, 22, 12, 3)

        # Garbage
        assert parse_date('garbage') is None
        assert parse_date('Mon, 31 Feb 2015 10:00:00 +0000') is None


def overview(*fields):
    """
    Returns an overview (XOVER) line of the fields given (tab separated)
    """
    return '\t'.join(str(f) for f in fields).encode('utf-8')


STANDARD = (1, 'Subject', 'poster@example.com', '01 Jan 2020 00:00:00 +0000',
            '<id@x>', '', 1000, 10)


def test_overview_lines_are_read_by_their_fields():
    """
    The first seven fields come in the order RFC 3977 gives them; the
    Xref is found where LIST OVERVIEW.FMT says (or by its name)
    """
    from newsreap.codecs.CodecArticleIndex import CodecArticleIndex

    entry = CodecArticleIndex().detect(
        overview(*STANDARD, 'Xref: news.example a.b:1 c.d:7'))
    assert (entry['id'], entry['subject'], entry['size'], entry['lines']) == (
        'id@x', 'Subject', 1000, 10)
    assert entry['xgroups'] == {'a.b': 1, 'c.d': 7}
    assert entry['group'] == 'news.example'

    # A server whose Xref comes after something else, without its name
    codec = CodecArticleIndex(fields=('X-Extra:', 'Xref:'))
    entry = codec.detect(overview(*STANDARD, 'extra', 'news a.b:1'))
    assert entry['xgroups'] == {'a.b': 1}

    # Not where we were told, but named
    entry = codec.detect(overview(*STANDARD, 'Xref: news a.b:2'))
    assert entry['xgroups'] == {'a.b': 2}

    # No Xref at all
    entry = CodecArticleIndex(fields=()).detect(overview(*STANDARD))
    assert entry['xgroups'] == {}


def test_empty_and_broken_fields_are_survived():
    """
    An empty subject or poster, a missing size or line count, and a line
    with junk in it never stop the listing
    """
    from newsreap.codecs.CodecArticleIndex import CodecArticleIndex

    codec = CodecArticleIndex()
    entry = codec.detect(overview(
        5, '', '', '01 Jan 2020 00:00:00 +0000', '<id@x>', '', '', ''))
    assert (entry['subject'], entry['poster'], entry['size'],
            entry['lines']) == ('', '', 0, -1)

    # Not overview lines
    assert codec.detect(b'224 Overview information follows') is None
    assert codec.detect(overview('x', *STANDARD[1:])) is None
    assert codec.detect(overview(*STANDARD[:4], '<>', '', 1, 1)) is None
    assert codec.detect(b'\x00\x00' + overview(*STANDARD)) is None


@pytest.mark.parametrize('raw, expected', [
    # UTF-8 (very common now)
    ('Café'.encode(), 'Café'),
    # Not UTF-8: the server's encoding (latin-1 by default)
    ('Café'.encode('latin-1'), 'Café'),
    # RFC 2047 encoded words
    (b'=?UTF-8?B?Q2Fmw6k=?= photos', 'Café photos'),
    (b'=?iso-8859-1?q?Caf=E9?=', 'Café'),
    (b'Re: =?utf-8?Q?na=C3=AFve?= question', 'Re: naïve question'),
    # Broken or unknown ones are kept as they are
    (b'=?bogus?B?xx?=', '=?bogus?B?xx?='),
    (b'=?UTF-8?B?not base64?=', '=?UTF-8?B?not base64?='),
])
def test_subjects_and_posters_are_decoded(raw, expected):
    """
    Subjects and posters read as UTF-8 when they are, otherwise in the
    server's encoding, with any RFC 2047 encoded words decoded
    """
    from newsreap.codecs.CodecArticleIndex import CodecArticleIndex

    line = b'\t'.join([
        b'1', raw, raw, b'01 Jan 2020 00:00:00 +0000', b'<id@x>', b'',
        b'10', b'1'])
    entry = CodecArticleIndex().detect(line)
    assert entry['subject'] == expected
    assert entry['poster'] == expected


def test_overview_formats_are_read():
    """
    What LIST OVERVIEW.FMT lists after the first seven fields is what an
    overview line holds after them
    """
    from newsreap.codecs.CodecArticleIndex import OVERVIEW_EXTRAS
    from newsreap.codecs.CodecArticleIndex import overview_extras

    standard = ['Subject:', 'From:', 'Date:', 'Message-ID:', 'References:',
                ':bytes', ':lines']
    assert overview_extras([*standard, 'Xref:full', '']) == ('Xref:full', )
    assert overview_extras([*standard, 'X-A:', 'Xref:full']) == (
        'X-A:', 'Xref:full')
    assert overview_extras(standard) == ()
    assert overview_extras([]) == OVERVIEW_EXTRAS
