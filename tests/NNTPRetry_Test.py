# Test when an article no server had is asked for again
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

import gevent.monkey
gevent.monkey.patch_all()

from datetime import datetime
from datetime import timedelta
from datetime import timezone

from newsreap.NNTPRetry import NNTPRetry
from newsreap.NNTPRetry import server_hash

MAIN = {'host': 'news.example.com', 'port': 563, 'username': 'me'}
BACKUP = ('backup.example.net', 119)
ADDED = ('added.example.org', 443)

# The servers an article was asked of last time
TRIED_BY = [server_hash(MAIN), server_hash(BACKUP)]

# An old post, asked for long after it was posted
POSTED = datetime(2020, 1, 1)
TRIED = datetime(2026, 1, 1)
LATER = TRIED + timedelta(days=1)


def test_each_server_is_known_by_its_host_and_port():
    """
    Only the host and port count (in lower case); logins and which one is
    the main server don't
    """
    assert server_hash(('News.Example.COM.', '563')) == server_hash(MAIN)
    assert server_hash({**MAIN, 'username': 'other', 'secure': False}) == \
        server_hash(MAIN)
    assert server_hash(('news.example.com', 119)) != server_hash(MAIN)
    assert NNTPRetry([BACKUP, MAIN]).hashes == \
        NNTPRetry([MAIN, BACKUP]).hashes == tuple(sorted(TRIED_BY))


def test_old_posts_are_asked_for_again_only_when_a_server_is_added():
    """
    No server had it long after it was posted: asking the same servers (or
    fewer) again can't help, a server added since might
    """
    for servers in ([MAIN, BACKUP], [BACKUP, MAIN], [MAIN]):
        assert NNTPRetry(servers).state(TRIED_BY, TRIED, POSTED, LATER) \
            == 'settled'

    assert NNTPRetry([MAIN, BACKUP, ADDED]).state(
        TRIED_BY, TRIED, POSTED, LATER) == 'ask'


def test_new_posts_are_asked_for_again_for_a_while():
    """
    Asked for within the window after it was posted, it is asked for again
    (no sooner than a couple of minutes later); asked for after the window,
    it is settled
    """
    retry = NNTPRetry([MAIN, BACKUP])
    posted = TRIED - timedelta(minutes=20)
    assert retry.state(TRIED_BY, TRIED, posted,
                       TRIED + timedelta(seconds=30)) == 'wait'
    assert retry.state(TRIED_BY, TRIED, posted,
                       TRIED + timedelta(minutes=5)) == 'ask'

    later = posted + timedelta(minutes=61)
    assert retry.state(TRIED_BY, later, posted, later + timedelta(hours=1)) \
        == 'settled'

    # The window can be set
    retry = NNTPRetry([MAIN, BACKUP], window=10)
    assert retry.state(TRIED_BY, TRIED, posted, LATER) == 'settled'


def test_dates_that_cant_be_right_dont_keep_it_asking():
    """
    No date (or one from an NZB file that gives none) is no reason to ask
    again, and nor is one far in the future
    """
    retry = NNTPRetry([MAIN, BACKUP])
    for posted in (None, datetime(1970, 1, 1), TRIED + timedelta(days=30)):
        assert retry.state(TRIED_BY, TRIED, posted, LATER) == 'settled'


def test_post_dates_with_a_timezone_are_moved_to_utc():
    """
    A post date with a timezone is the same moment in naive UTC
    """
    eastern = timezone(timedelta(hours=-5))
    assert NNTPRetry.posted(datetime(2026, 1, 1, 7, 0, tzinfo=eastern)) == \
        datetime(2026, 1, 1, 12, 0)
    assert NNTPRetry.posted(datetime(1970, 1, 1, tzinfo=eastern)) is None


def test_what_wasnt_remembered_is_always_asked_for():
    """
    Never tried, tried without a date, or told to ask regardless: it is
    asked for
    """
    retry = NNTPRetry([MAIN, BACKUP])
    assert retry.state([], None, POSTED, LATER) == 'ask'
    assert retry.state(TRIED_BY, None, POSTED, LATER) == 'ask'
    assert retry.state(TRIED_BY, 'yesterday', POSTED, LATER) == 'ask'

    retry = NNTPRetry([MAIN, BACKUP], always=True)
    assert retry.state(TRIED_BY, TRIED, POSTED, LATER) == 'ask'


def test_bad_windows_fall_back_to_the_default():
    """
    A window that isn't a number of minutes uses the default (an hour)
    """
    for window in ('soon', None, float('inf')):
        assert NNTPRetry([MAIN], window=window).window == timedelta(hours=1)
    assert NNTPRetry([MAIN], window=-5).window == timedelta(0)
