# Test how many connections are made, and how busy they are kept
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


import logging
from datetime import datetime

from newsreap.Logging import NEWSREAP_ENGINE
from newsreap.NNTPArchiver import NNTPArchiver
from newsreap.NNTPGroupDatabase import NNTPGroupDatabase
from newsreap.NNTPManager import NNTPManager
from newsreap.NNTPSettings import NNTPSettings
from newsreap.objects.group.Article import Article

from tests.FakeNNTPServer import FakeNNTPServer
from tests.conftest import config_for

GROUP = 'alt.binaries.test'

# A backup server added to a configuration (see with_servers())
BACKUP = """
  - host: backup.example.com
    port: 119
    username: someone
    password: secret
    secure: False
%s
"""


def settings_for(server, tmp_path, name='config.yaml', backup=None,
                 own=None, old=None, **processing):
    """
    Returns the settings of a configuration pointing at the test server

    - backup: the connections line of a backup server to add (ie:
      '    connections: 2'); no backup when None
    - own: the connections the main server allows (its own setting)
    - old: a threads setting in place of connections (as older
      configurations have)
    - processing: settings to change under processing:
    """
    path = config_for(server, tmp_path / name, **processing)
    with open(path) as fp:
        content = fp.read()

    if old is not None:
        content = content.replace(
            '\n   connections: 4\n', '\n   threads: %d\n' % old, 1)

    if own is not None:
        content = content.replace(
            '    # connections: 10', '    connections: %d' % own, 1)

    if backup is not None:
        content = content.replace(
            '\n# A backup server', BACKUP % backup + '\n# A backup server', 1)

    with open(path, 'w') as fp:
        fp.write(content)

    return NNTPSettings(cfg_file=path)


def test_connections_default_to_four(tmp_path):
    """
    Four connections are made unless something says otherwise; the old
    threads name still works, and connections wins over it
    """
    with FakeNNTPServer() as server:
        settings = settings_for(server, tmp_path)
        assert settings.connections() == 4
        assert settings.nntp_processing['threads'] == 4

        settings = settings_for(server, tmp_path, 'old.yaml', old=7)
        assert settings.connections() == 7

        settings = settings_for(
            server, tmp_path, 'both.yaml', threads=7, connections=3)
        assert settings.connections() == 3


def test_unusable_connection_counts_fall_back(tmp_path, caplog):
    """
    A count that isn't a whole number of 1 or more is said, and the
    default is used
    """
    caplog.set_level(logging.WARNING, logger=NEWSREAP_ENGINE)
    with FakeNNTPServer() as server:
        settings = settings_for(server, tmp_path, connections="'lots'")
        assert settings.connections() == 4
        assert "connections: 'lots' isn't a number" in caplog.text

        settings = settings_for(server, tmp_path, 'zero.yaml', connections=0)
        assert settings.connections() == 4


def test_each_server_can_allow_its_own_connections(tmp_path):
    """
    A server's own connections setting wins over the one under
    processing:; a backup only gets as many connections as it allows
    """
    with FakeNNTPServer() as server:
        settings = settings_for(
            server, tmp_path, own=6, backup='    connections: 2',
            connections=3)
        main, backup = settings.nntp_servers
        assert settings.connections(main) == 6
        assert settings.connections(backup) == 2

        manager = NNTPManager(settings)
        try:
            assert manager.window() == 12

            manager.spawn_workers(count=6)
            assert len(manager._pool) == 6
            assert [len(c._backups) for c in manager._pool] == [
                1, 1, 0, 0, 0, 0]

        finally:
            manager.close()


def test_content_is_fetched_with_every_connection_busy(tmp_path):
    """
    Keeping the small articles of a group asks for two articles for each
    connection at once (not a fixed number)
    """
    with FakeNNTPServer() as server:
        settings = settings_for(server, tmp_path, connections=20)
        manager = NNTPManager(settings)
        assert manager.window() == 40

        # One article cached for the group
        db_file = settings.group_database(GROUP)
        cache = NNTPGroupDatabase.from_file(db_file)
        session = cache.session()
        session.add(Article(
            message_id='a@x', article_no=1, subject='Hello',
            poster='me@example.com', size=10, lines=1,
            posted_date=datetime(2020, 1, 1)))
        session.commit()
        cache.close()

        # The window each fetch was asked for
        windows = []

        def fetch(manager, wanted, work_dir, group, window):
            windows.append(window)
            return iter(())

        archiver = NNTPArchiver(str(tmp_path / 'index.db'))
        archiver._fetch = fetch
        try:
            archiver.archive(GROUP, db_file, manager)

        finally:
            archiver.close()
            manager.close()

        assert windows == [40]
