# Test that dates are kept as UTC wherever they come from
#
# Copyright (C) 2026 Chris Caron <lead2gold@gmail.com>
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

import time
from datetime import datetime

import pytest

from newsreap.NNTPSegmentedPost import NNTPSegmentedPost
from newsreap.NNTPnzb import utc_seconds


@pytest.fixture
def toronto(monkeypatch):
    """
    Runs a test in a time zone that isn't UTC
    """
    monkeypatch.setenv('TZ', 'America/Toronto')
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_nzb_dates_are_kept_as_utc(toronto):
    """
    An NZB file's date (seconds since 1970) is kept as UTC, and written
    back the same, whatever time zone we run in
    """
    post = NNTPSegmentedPost(None, utc='1577836800')
    assert post.utc == datetime(2020, 1, 1, 0, 0)
    assert utc_seconds(post.utc) == '1577836800'


def test_an_unreadable_overview_date_is_utc_too(toronto):
    """
    An overview whose date can't be read is dated the start of 1970 (UTC),
    like every other date we keep
    """
    from newsreap.codecs.CodecArticleIndex import CodecArticleIndex

    found = CodecArticleIndex().detect(
        '1\tSubject\tme@x\tnot a date\t<a@b>\t\t100\t10')
    assert found['date'] == datetime(1970, 1, 1)

