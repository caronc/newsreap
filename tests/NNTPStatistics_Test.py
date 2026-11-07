# Tests the transfer statistics (NNTPStatistics)
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


from os.path import join

import pytest
from click.testing import CliRunner

from newsreap.NNTPArticle import NNTPArticle
from newsreap.NNTPSegmentedPost import NNTPSegmentedPost
from newsreap.NNTPStatistics import NNTPStatistics
from newsreap.NNTPStatistics import duration

from tests.FakeNNTPServer import Reply
from tests.FakeNNTPServer import VAR_PATH
from tests.conftest import connect_to

GROUP = 'alt.binaries.test'


def action(rx_bytes=0, busy_time=0.0, started=0.0, finished=0.0, **kwargs):
    """
    Returns the statistics of a made up action
    """
    return NNTPStatistics(
        rx_bytes=rx_bytes, busy_time=busy_time, started=started,
        finished=finished, **kwargs)


def sent(server):
    """
    The commands a server received (closing the connection isn't tracked)
    """
    return [c for c in server.commands() if c != 'QUIT']


#
# Adding statistics together
#
def test_averages_stay_exact_when_added():
    """
    Speeds come from the totals, not from averaging averages
    """
    slow = action(rx_bytes=1000, busy_time=1.0)
    fast = action(rx_bytes=9000, busy_time=3.0)

    # The average of the two speeds would be 2000; the real speed is 2500
    total = slow + fast
    assert total.rx_bytes == 10000
    assert total.busy_time == 4.0
    assert total.connection_rate == 2500

    # The order things are added in doesn't matter
    assert (fast + slow).connection_rate == 2500


def test_adding_leaves_the_originals_alone():
    """
    a + b makes a new object; a += b grows a
    """
    a = action(rx_bytes=10, retries=1)
    b = action(rx_bytes=20, retries=2)

    total = a + b
    assert (a.rx_bytes, b.rx_bytes, total.rx_bytes) == (10, 20, 30)

    a += b
    assert (a.rx_bytes, a.retries) == (30, 3)
    assert b.rx_bytes == 20


def test_sum_and_combine():
    """
    Lists of statistics can be added up with sum() or combine()
    """
    items = [action(rx_bytes=n, fetched=1) for n in (1, 2, 3)]
    assert sum(items).rx_bytes == 6
    assert NNTPStatistics.combine(items).fetched == 3
    assert NNTPStatistics.combine([]).rx_bytes == 0

    # Only statistics can be added
    with pytest.raises(TypeError):
        items[0] + 5


def test_connections_working_at_the_same_time():
    """
    Overall speed is measured across the time both connections ran
    """
    # Two connections, each moving 1000 bytes over the same 2 seconds
    first = action(rx_bytes=1000, busy_time=2.0, started=10.0, finished=12.0)
    second = action(rx_bytes=1000, busy_time=2.0, started=10.0, finished=12.0)

    total = first + second
    assert total.elapsed == 2.0
    assert total.rate == 1000
    assert total.connection_rate == 500

    # Spread out over time, the elapsed time covers them both
    later = action(rx_bytes=1000, busy_time=1.0, started=20.0, finished=21.0)
    total += later
    assert (total.started, total.finished, total.elapsed) == (10.0, 21.0, 11.0)


def test_peak_is_the_fastest_action():
    """
    The peak speed is the best any single action managed
    """
    a = NNTPStatistics(rx_bytes=1000)
    started = a.start()
    a.stop(started - 1.0)
    assert 0 < a.peak_rate <= 1000

    b = NNTPStatistics(peak_rate=5000)
    assert (a + b).peak_rate == 5000


def test_waiting_to_retry_is_not_busy_time():
    """
    Time spent waiting to retry is kept apart from time spent working
    """
    stats = NNTPStatistics(retry_wait=5.0)
    started = stats.start()
    stats.stop(started - 6.0)
    assert stats.busy_time == pytest.approx(1.0, abs=0.1)


#
# The summary
#
def test_summary_of_nothing():
    """
    An empty object still gives a readable summary
    """
    stats = NNTPStatistics()
    assert not stats

    summary = stats.summary()
    assert 'Received      0.00B' in summary
    assert 'Articles      none' in summary
    assert 'Retries       none' in summary
    assert 'waiting to retry' not in summary


def test_summary_details():
    """
    The summary spreads the speeds out and explains the retries
    """
    stats = action(
        rx_bytes=3 * 1024 * 1024, busy_time=2.0, started=0.0, finished=1.0,
        fetched=1842, missing=3, connections=7, retries=7, dropped=5,
        stalled=2, retry_wait=14.0)

    lines = str(stats).splitlines()
    assert lines[0] == 'Received      3.00MB'
    assert 'Speed         3.00MB/s overall' in lines
    assert '              1.50MB/s per connection' in lines
    assert 'Articles      1,842 fetched, 3 missing' in lines
    assert 'Connections   7 opened' in lines
    assert 'Retries       7 (5 dropped, 2 stalled)' in lines
    assert '              14s waiting to retry' in lines

    assert 'rx_bytes=3145728' in repr(stats)


def test_durations_are_easy_to_read():
    """
    Seconds are shown as hours, minutes and seconds
    """
    assert duration(0.25) == '0.25s'
    assert duration(42) == '42s'
    assert duration(252) == '4m 12s'
    assert duration(3723) == '1h 2m 3s'


#
# Statistics gathered while talking to a server
#
def test_fetching_records_statistics(nntp_server, tmp_path):
    """
    A fetch records what it took on the article and the connection
    """
    path = join(VAR_PATH, '00000005.ntx')
    nntp_server.add_article('5', path, GROUP)

    sock = connect_to(nntp_server, join_group=True)
    assert sock.connect(timeout=5.0) is True
    article = sock.get('5', work_dir=str(tmp_path), group=GROUP)

    # The article knows what fetching it took
    assert article.stats.fetched == 1
    # Selecting the group and then the article
    assert article.stats.commands == 2
    assert article.stats.rx_bytes > 0
    assert article.stats.busy_time > 0

    # The connection also counts logging in
    assert sock.stats.connections == 1
    assert sock.stats.fetched == 1
    assert sock.stats.commands == len(sent(nntp_server))
    assert sock.stats.rx_bytes > article.stats.rx_bytes
    assert sock.stats.busy_time <= sock.stats.elapsed + 0.01
    sock.close()


def test_missing_and_bad_replies_are_counted(nntp_server, tmp_path):
    """
    Articles that aren't found or can't be fetched are counted
    """
    nntp_server.add_group(GROUP)
    sock = connect_to(nntp_server, join_group=True, retries=2)
    assert sock.connect(timeout=5.0) is True

    assert sock.get('missing', work_dir=str(tmp_path), group=GROUP) is None
    assert sock.stats.missing == 1

    nntp_server.on(r'^ARTICLE', Reply(raw=b'\x00 garbage\r\n'))
    assert sock.get('5', work_dir=str(tmp_path), group=GROUP) is None
    assert sock.stats.failed == 1
    assert sock.stats.bad_replies == 3
    assert sock.stats.retries == 2
    sock.close()


def test_retries_are_counted_once(nntp_server, tmp_path):
    """
    A retried fetch counts each retry, reconnect and command once
    """
    path = join(VAR_PATH, '00000005.ntx')
    with open(path, 'rb') as fp:
        content = fp.read()

    nntp_server.add_article('5', path, GROUP)
    nntp_server.on(
        r'^ARTICLE <5>$',
        Reply('220 0 <5>', raw=content[:500], close=True), once=True)

    sock = connect_to(nntp_server, join_group=True)
    assert sock.connect(timeout=5.0) is True
    article = sock.get('5', work_dir=str(tmp_path), group=GROUP)

    # Logging in again (and selecting our group) is part of the fetch
    assert article.stats.fetched == 1
    assert article.stats.retries == 1
    assert article.stats.dropped == 1
    assert article.stats.connections == 1

    assert sock.stats.connections == 2
    assert sock.stats.retries == 1
    assert sock.stats.commands == len(sent(nntp_server))
    sock.close()


def test_stalls_are_counted(nntp_server, tmp_path):
    """
    A stalled connection is counted as such
    """
    path = join(VAR_PATH, '00000005.ntx')
    with open(path, 'rb') as fp:
        content = fp.read()

    nntp_server.add_article('5', path, GROUP)
    nntp_server.on(
        r'^ARTICLE <5>$',
        Reply('220 0 <5>', raw=content[:500]), once=True)

    sock = connect_to(nntp_server, join_group=True, stall_timeout=0.5)
    assert sock.connect(timeout=5.0) is True
    article = sock.get('5', work_dir=str(tmp_path), group=GROUP)
    assert article.stats.stalled == 1
    assert article.stats.retries == 1
    sock.close()


def test_posting_records_statistics(nntp_server, tmp_path):
    """
    A post records what was sent on its response and the connection
    """
    article = NNTPArticle(
        subject='statistics', poster='tester <tester@example.com>',
        groups=GROUP, work_dir=str(tmp_path))
    article.add(join(VAR_PATH, 'joystick.jpg'))

    sock = connect_to(nntp_server)
    assert sock.connect(timeout=5.0) is True
    response = next(iter(sock.post(article)))

    assert response.stats.posted == 1
    assert response.stats.tx_bytes > 1000
    assert response.stats.commands == 1

    assert sock.stats.posted == 1
    assert sock.stats.commands == len(sent(nntp_server))
    sock.close()


def test_segmented_post_adds_up_its_articles():
    """
    A segmented post's statistics are those of its articles combined
    """
    post = NNTPSegmentedPost('file.rar')
    for n in (1, 2):
        article = NNTPArticle(id='%d@example.com' % n, no=n)
        article.stats = action(rx_bytes=100 * n, fetched=1)
        post.add(article)

    assert post.stats.fetched == 2
    assert post.stats.rx_bytes == 300


def test_cli_statistics_summary(tmp_path):
    """
    nr --statistics prints a summary when the command finishes
    """
    from newsreap.cli import cli
    from newsreap.NNTPSettings import create_config

    path = create_config(str(tmp_path / 'config.yaml'))
    result = CliRunner().invoke(
        cli, ['--config', path, '--statistics', 'db', 'init'], obj={})
    assert result.exit_code == 0
    assert 'Statistics' in result.output
    assert 'Retries       none' in result.output


def test_backup_server_fetch(nntp_server, tmp_path):
    """
    An article only the backup server has is fetched from it and counted
    """
    from tests.FakeNNTPServer import FakeNNTPServer

    path = join(VAR_PATH, '00000005.ntx')
    nntp_server.add_group(GROUP)

    with FakeNNTPServer() as backup_server:
        backup_server.add_article('5', path, GROUP)

        sock = connect_to(nntp_server, join_group=True)
        backup = connect_to(backup_server, join_group=True)
        sock.append(backup)

        article = sock.get('5', work_dir=str(tmp_path), group=GROUP)
        assert isinstance(article, NNTPArticle)
        assert next(iter(article.decoded)).is_valid() is True

        # The backup fetched it, so it isn't counted as missing
        assert article.stats.fetched == 1
        assert article.stats.missing == 0
        assert sock.stats.missing == 0
        assert backup.stats.fetched == 1

        # Both servers were asked
        assert 'ARTICLE <5>' in nntp_server.commands()
        assert 'ARTICLE <5>' in backup_server.commands()
        backup.close()
        sock.close()
