# Test clearing out what is no longer needed (nr prune)
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


import fcntl
import os
import time
from datetime import timedelta

from click.testing import CliRunner

from tests.FakeNNTPServer import FakeNNTPServer
from tests.NNTPIndexer_Test import GROUP
from tests.NNTPIndexer_Test import POSTED
from tests.NNTPIndexer_Test import release
from tests.conftest import config_for

# Older than anything we keep around
LONG_AGO = time.time() - 30 * 86400


def aged(path):
    """
    Makes a file or directory (and everything in it) look long untouched
    """
    for root, folders, files in os.walk(str(path)):
        for name in (*folders, *files):
            os.utime(os.path.join(root, name), (LONG_AGO, LONG_AGO))
    os.utime(str(path), (LONG_AGO, LONG_AGO))


def old_statistics(config, days=40):
    """
    Adds a day of statistics for our (first) server, `days` days ago
    """
    from newsreap.NNTPSettings import NNTPSettings
    from newsreap.NNTPStatistics import NNTPStatistics
    from newsreap.Utils import server_key
    from newsreap.Utils import utcnow

    settings = NNTPSettings(cfg_file=config)
    try:
        key = server_key(**settings.nntp_servers[0])
        assert settings.record_stats(
            {key: NNTPStatistics(rx_bytes=1, commands=1)},
            day=utcnow().date() - timedelta(days=days)) == [key]

    finally:
        settings.close()


def test_what_is_no_longer_needed_is_cleared_out(tmp_path):
    """
    Each kind is cleared out only when asked for: old indexed headers,
    downloads that didn't finish and leftover work files are shown (and
    kept with --dry-run or a 'no'), then cleared out; a download being
    run, and anything recent, is left alone
    """
    from newsreap.cli import cli

    group_list = tmp_path / 'group.list'
    group_list.write_text('%s 0000000002 0000000001 y\n' % GROUP)

    with FakeNNTPServer(group_list=str(group_list)) as server:
        for no, (subject, poster, message_id) in enumerate(
                release('Holiday', files=1, segments=2), 1):
            server.add_overview(
                GROUP, no, subject, poster, POSTED + timedelta(minutes=no),
                message_id, size=100)

        config = config_for(server, tmp_path / 'config.yaml')
        runner = CliRunner()

        def nr(*args, **kwargs):
            return runner.invoke(cli, ['--config', config, *args], obj={},
                                 **kwargs)

        assert nr('db', 'init').exit_code == 0
        assert nr('group', 'refresh').exit_code == 0
        assert nr('update', GROUP).exit_code == 0

        assert nr('prune', '--only', 'work').output.strip() == \
            'Nothing to clear out.'

        # Downloads that didn't finish: one stale, one being run, one recent
        staging = tmp_path / 'cache' / 'downloads'
        for name in ('stale-1', 'running-2', 'recent-3'):
            (staging / name / 'parts').mkdir(parents=True)
            (staging / name / 'parts' / 'a.part').write_bytes(b'x' * 100)
            (staging / name / '.lock').write_text('')
        aged(staging / 'stale-1')
        aged(staging / 'running-2')

        # A leftover work file, and one still in use
        work = tmp_path / 'cache' / 'tmp'
        work.mkdir(parents=True, exist_ok=True)
        (work / 'left.tmp').write_bytes(b'y')
        aged(work / 'left.tmp')
        (work / 'busy.tmp').write_bytes(b'z')

        # On its own, prune does what the configuration says (a dry run
        # here, so nothing goes yet)
        result = nr('prune', '-n')
        assert result.exit_code == 0, result.output
        assert '1 leftover work file(s)' in result.output
        assert 'Dry run: nothing was changed.' in result.output
        assert (staging / 'stale-1').exists()
        assert (work / 'left.tmp').exists()

        # Downloads and headers are kept until asked for
        result = nr('-y', 'prune', '--only', 'downloads')
        assert result.exit_code == 1
        assert 'prune_downloads is 0' in result.output
        assert nr('-y', 'prune', '--only', 'headers').exit_code == 1
        assert (staging / 'stale-1').exists()

        # --only can't be added to the watched groups (it would be passed
        # over)
        result = nr('-y', 'prune', '-w', '--only', 'downloads', '--days', '1')
        assert result.exit_code == 1
        assert 'not both' in result.output
        assert (staging / 'stale-1').exists()

        with open(str(staging / 'running-2' / '.lock')) as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

            result = nr('prune', '--only', 'downloads', '--days', '7',
                        '--dry-run')
            assert result.exit_code == 0, result.output
            assert 'stale-1' in result.output
            assert 'running-2' not in result.output
            assert 'recent-3' not in result.output
            assert 'Dry run: nothing was changed.' in result.output

            result = nr('prune', '--only', 'downloads', '--days', '7',
                        input='n\n')
            assert 'Nothing was removed.' in result.output
            assert (staging / 'stale-1').exists()

            result = nr('-y', 'prune', '--only', 'downloads', '--days', '7')
            assert result.exit_code == 0, result.output
            assert 'Cleared out.' in result.output

        assert not (staging / 'stale-1').exists()
        assert (staging / 'running-2').exists()
        assert (staging / 'recent-3').exists()
        assert (work / 'left.tmp').exists()

        # Transfer statistics older than 31 days (today's stay)
        old_statistics(config)
        result = nr('prune', '--only', 'statistics', '--dry-run')
        assert '1 day(s) of server statistics older than 31 day(s)' in \
            result.output
        assert nr('prune', '--only', 'statistics', '--days',
                  '50').output.strip() == 'Nothing to clear out.'

        # All of them, as configured: only work files and statistics go
        # out of the box
        result = nr('-y', 'prune')
        assert result.exit_code == 0, result.output
        assert 'raw_retention is 0' in result.output
        assert 'prune_downloads is 0' in result.output
        assert '1 leftover work file(s)' in result.output
        assert '1 day(s) of server statistics' in result.output
        assert not (work / 'left.tmp').exists()
        assert (work / 'busy.tmp').exists()
        assert 'statistics' not in nr('prune', '-n').output
        assert 'Today ' in nr('db', 'status').output

        result = nr('prune', '--only', 'headers', '--days', '30',
                    '--dry-run')
        assert result.exit_code == 0, result.output
        assert '%s: 2 header(s) older than 30 day(s)' % GROUP in \
            result.output

        assert nr('-y', 'prune', '--only', 'headers', '--days',
                  '30').exit_code == 0

        # The headers went, and the collection they made is still there
        assert nr('search', '--articles', '-g', GROUP, '-q').output == ''
        assert nr('search', 'holiday', '-q').output.strip()


def test_prune_works_before_the_database_is_set_up(tmp_path):
    """
    Work files and downloads can be cleared before 'nr db init'; only
    asking for groups needs the database
    """
    from newsreap.cli import cli

    with FakeNNTPServer() as server:
        config = config_for(server, tmp_path / 'config.yaml')

    work = tmp_path / 'cache' / 'tmp'
    work.mkdir(parents=True, exist_ok=True)
    (work / 'left.tmp').write_bytes(b'y')
    aged(work / 'left.tmp')

    def nr(*args):
        return CliRunner().invoke(cli, ['--config', config, *args], obj={})

    result = nr('-y', 'prune')
    assert result.exit_code == 0, result.output
    assert '1 leftover work file(s)' in result.output
    assert not (work / 'left.tmp').exists()

    result = nr('prune', '--only', 'groups')
    assert result.exit_code == 1
    assert "run 'nr db init' first" in result.output


def test_prune_days_come_from_the_configuration(tmp_path):
    """
    prune_downloads and prune_work set how long each is kept; 0 keeps
    them, and a value that isn't a number falls back to the default
    """
    from newsreap.NNTPPruner import NNTPPruner
    from newsreap.NNTPSettings import NNTPSettings

    with FakeNNTPServer() as server:
        config = config_for(server, tmp_path / 'config.yaml')

    settings = NNTPSettings(cfg_file=config)
    pruner = NNTPPruner(settings)
    assert (pruner.headers, pruner.downloads, pruner.work) == (0, 0, 1)
    assert pruner.statistics == 31

    text = open(config).read()
    text = text.replace('# prune_downloads: 0', 'prune_downloads: 14')
    text = text.replace('# prune_work: 1', "prune_work: 'soon'")
    text = text.replace('statistics: 31', 'statistics: 0')
    open(config, 'w').write(text)

    settings = NNTPSettings(cfg_file=config)
    pruner = NNTPPruner(settings)
    assert (pruner.downloads, pruner.work, pruner.statistics) == (14, 1, 0)
    assert pruner.find().statistics == 0

    # What is given wins over the configuration
    pruner = NNTPPruner(settings, downloads=0, work=3)
    assert (pruner.downloads, pruner.work) == (0, 3)
    assert pruner.find().downloads == []


def test_a_download_started_after_finding_is_left_alone(tmp_path):
    """
    A staging directory found to be stale is still left alone if a run
    takes it up before it is cleared (ie: while you answer the question)
    """
    from newsreap.NNTPDownloader import NNTPDownloader
    from newsreap.NNTPPruner import NNTPPruner
    from newsreap.NNTPSettings import NNTPSettings

    with FakeNNTPServer() as server:
        config = config_for(server, tmp_path / 'config.yaml')

    settings = NNTPSettings(cfg_file=config)
    staging = tmp_path / 'cache' / 'downloads' / 'job-1'
    (staging / 'parts').mkdir(parents=True)
    (staging / '.lock').write_text('')
    aged(staging)

    pruner = NNTPPruner(settings, downloads=1)
    found = pruner.find()
    assert [path for path, _ in found.downloads] == [str(staging)]

    # A run takes it up now
    run = NNTPDownloader(None, str(staging.parent), str(tmp_path))
    run.staging = str(staging)
    lock = run._lock()
    assert lock is not None

    try:
        pruner.clear(found)
        assert staging.is_dir()

    finally:
        lock.close()

    # Once nothing uses it, it goes
    pruner.clear(found)
    assert not staging.exists()
    settings.close()


def test_work_files_are_only_cleared_inside_our_cache(tmp_path):
    """
    Work files are only cleared from a folder inside our cache directory;
    one next to it with a similar name (ie: cache-old), or the cache
    directory itself, is left alone
    """
    from newsreap.NNTPPruner import NNTPPruner
    from newsreap.NNTPSettings import NNTPSettings

    with FakeNNTPServer() as server:
        config = config_for(server, tmp_path / 'config.yaml')

    settings = NNTPSettings(cfg_file=config)
    cache = settings.cache_dir
    for folder in (cache + '-old', cache, os.path.join(cache, 'tmp')):
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, 'left.tmp')
        open(path, 'wb').close()
        aged(path)

    found = {}
    for folder in (cache + '-old', cache, os.path.join(cache, 'tmp')):
        settings.work_dir = folder
        found[folder] = list(NNTPPruner(settings, work=1)._work_files())

    assert found[cache + '-old'] == []
    assert found[cache] == []
    assert found[os.path.join(cache, 'tmp')] == [
        os.path.join(cache, 'tmp', 'left.tmp')]
    settings.close()
