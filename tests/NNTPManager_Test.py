#!/usr/bin/env python
#
# A base testing class/library to test the NNTP Manager class
#
# Copyright (C) 2017 Chris Caron <lead2gold@gmail.com>
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
import gevent
from os.path import join
from os.path import dirname
from os.path import abspath

try:
    from tests.TestBase import TestBase

except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

import pytest

from tests.FakeNNTPServer import FakeNNTPServer
from tests.conftest import config_for

from newsreap.NNTPSettings import NNTPSettings
from newsreap.NNTPManager import NNTPManager
from newsreap.decorators import hook
from newsreap.NNTPSettings import SERVER_LIST_KEY
from newsreap.NNTPSettings import PROCESSING_KEY


class NNTPManager_Test(TestBase):
    def setUp(self):
        """
        Grab a few more things from the config
        """
        super().setUp()

        # Secure NNTP Server
        self.nntps = FakeNNTPServer(secure=True)

        # Insecure NNTP Server
        self.nntp = FakeNNTPServer(secure=False)

        # Start our servers
        self.nntps.start()
        self.nntp.start()

        # Acquire our configuration
        self.nttp_ipaddr, self.nntp_portno = self.nntp.host, self.nntp.port
        self.nttps_ipaddr, self.nntps_portno = \
            self.nntps.host, self.nntps.port

    def tearDown(self):
        # Shutdown our test servers
        self.nntps.stop()
        self.nntp.stop()

        super().tearDown()

    def test_group_searching(self):
        """
        Test Group searching (LIST ACTIVE)

        """

        cfg_file = join(self.tmp_dir, 'NNTPManager.config.yaml')

        server = {
            'username': 'valid',
            'password': 'valid',
            'host': self.nttp_ipaddr,
            'port': self.nntp_portno,
            'secure': 'False',
            'compress': 'False',
            'priority': '1',
            'join_group': 'False',
        }

        processing = {
            # Our test server only supports one connection at this
            # time
            'threads': 1,
            'header_batch_size': 8000,
        }

        # Create a yaml configuration entry we can test with
        # The output is invalid (formatting)
        with open(cfg_file, 'w') as fp:
            fp.write('%s:\n' % PROCESSING_KEY)
            fp.write('   %s' % ('   '.join(['%s: %s\n' % (k, v) \
                for (k, v) in list(processing.items())])))

            fp.write('%s:\n' % SERVER_LIST_KEY)
            fp.write(' - %s' % ('   '.join(['%s: %s\n' % (k, v) \
                for (k, v) in list(server.items())])))

        # Settings Object
        setting = NNTPSettings(cfg_file=cfg_file)

        # Create our NNTP Manager Instance
        mgr = NNTPManager(setting)

        # Now we should be able to search our results; but default we block
        # until the fetch is complete.
        groups = mgr.groups()

        # This is the number of groups found in the var/group.list that
        # our NNTPServer connection references
        assert len(groups) == 38570
        assert isinstance(groups, list) is True

        # Filter our results
        groups = mgr.groups(filters='alt.binaries')
        assert len(groups) == 5270

        # Clean close
        mgr.close()

    def test_hooks_can_be_passed_in(self):
        """
        Hooks given to the manager are loaded and can be replaced
        """
        @hook(name='socket_read')
        def reader(**kwargs):
            return 'read'

        @hook(name='socket_write')
        def writer(**kwargs):
            return 'write'

        mgr = NNTPManager(self.nntp_settings('hooks'), hooks=reader)
        assert len(mgr.hooks.call('socket_read')) == 1

        mgr.set_hooks(writer)
        assert len(mgr.hooks.call('socket_read')) == 0
        assert len(mgr.hooks.call('socket_write')) == 1
        mgr.close()


def test_a_request_says_how_long_it_took(tmp_path):
    """
    A request is timed from when a connection takes it up until it is
    done; the time stays put once it is done
    """
    from newsreap.NNTPConnectionRequest import NNTPConnectionRequest

    with FakeNNTPServer(join_group=False) as server:
        settings = NNTPSettings(cfg_file=config_for(
            server, tmp_path / 'config.yaml'))
        manager = NNTPManager(settings)
        try:
            request = NNTPConnectionRequest(
                actions=[('stat', ('<nothing@example.com>', ), {})])
            assert request.elapsed() == 0.0

            manager.put(request)
            request.wait()
            took = request.elapsed()
            assert took > 0.0
            gevent.sleep(0.01)
            assert request.elapsed() == took

        finally:
            manager.close()
            settings.close()


def manager_for(server, tmp_path):
    """
    Returns (settings, manager) for a FakeNNTPServer
    """
    settings = NNTPSettings(cfg_file=config_for(
        server, tmp_path / 'config.yaml'))
    return settings, NNTPManager(settings)


def stored(server, tmp_path, *message_ids):
    """
    Makes a small article available for each Message-ID given
    """
    for message_id in message_ids:
        path = tmp_path / ('%s.eml' % message_id.strip('<>'))
        path.write_bytes(b'Subject: test\r\n\r\nhello there\r\n')
        server.add_article(message_id, str(path), 'alt.binaries.test')


def test_an_article_object_is_fetched_and_filled_in(tmp_path):
    """
    Handing the manager an NNTPArticle fills that article in with what
    was fetched
    """
    from newsreap.NNTPArticle import NNTPArticle

    with FakeNNTPServer(join_group=False) as server:
        stored(server, tmp_path, '<one@example.com>')
        settings, manager = manager_for(server, tmp_path)
        try:
            article = NNTPArticle(id='<one@example.com>')
            assert manager.get(article, str(tmp_path)) is not None
            assert article.body is not None

        finally:
            manager.close()
            settings.close()


def test_each_part_of_a_segmented_post_is_fetched(tmp_path):
    """
    Handing the manager an NNTPSegmentedPost fetches every article in it,
    and one that is missing doesn't stop the rest
    """
    from newsreap.NNTPArticle import NNTPArticle
    from newsreap.NNTPSegmentedPost import NNTPSegmentedPost

    with FakeNNTPServer(join_group=False) as server:
        stored(server, tmp_path, '<a@example.com>', '<b@example.com>')
        settings, manager = manager_for(server, tmp_path)
        try:
            post = NNTPSegmentedPost('file.bin')
            for no, message_id in enumerate(
                    ('<a@example.com>', '<gone@example.com>',
                     '<b@example.com>'), 1):
                post.add(NNTPArticle(id=message_id, no=no))

            responses = manager.get(post, str(tmp_path))
            assert len(responses) == 3
            assert [entry is not None for entry in responses] == [
                True, False, True]

        finally:
            manager.close()
            settings.close()


def test_a_request_that_breaks_leaves_the_worker_running(tmp_path):
    """
    A request whose run() raises is marked done, and the worker that ran
    it carries on with the next one
    """
    from newsreap.NNTPRequest import NNTPRequest
    from newsreap.NNTPConnectionRequest import NNTPConnectionRequest

    class Broken(NNTPRequest):
        def run(self, connection, *args, **kwargs):
            raise RuntimeError('broken')

    with FakeNNTPServer(join_group=False) as server:
        settings = NNTPSettings(cfg_file=config_for(
            server, tmp_path / 'config.yaml', connections=1))
        manager = NNTPManager(settings)
        try:
            broken = Broken()
            manager.put(broken)
            assert broken.wait(timeout=5)

            after = NNTPConnectionRequest(
                actions=[('stat', ('<nothing@example.com>', ), {})])
            manager.put(after)
            assert after.wait(timeout=5)
            assert len(after) == 1

        finally:
            manager.close()
            settings.close()


def test_an_unknown_action_is_answered_with_nothing(tmp_path):
    """
    An action the connection doesn't have gives None (like any other
    failed action) and the actions after it still run
    """
    from newsreap.NNTPConnectionRequest import NNTPConnectionRequest

    with FakeNNTPServer(join_group=False) as server:
        settings, manager = manager_for(server, tmp_path)
        try:
            request = NNTPConnectionRequest(actions=[
                ('no_such_thing', (), {}),
                ('stat', ('<nothing@example.com>', ), {})])
            manager.put(request)
            assert request.wait(timeout=5)
            assert len(request) == 2
            assert request.response[0] is None

            # What went wrong is kept, one entry per action like the answers
            assert isinstance(request.errors[0], AttributeError)
            assert request.errors[1] is None

        finally:
            manager.close()
            settings.close()


def test_a_worker_that_cannot_start_does_not_lock_the_manager(
        tmp_path, monkeypatch):
    """
    If making a worker fails, the next request can still be handed over
    """
    from newsreap.NNTPConnectionRequest import NNTPConnectionRequest

    with FakeNNTPServer(join_group=False) as server:
        settings, manager = manager_for(server, tmp_path)
        try:
            spawn = manager.spawn_workers

            def broken(count=1):
                raise OSError('no more connections')

            monkeypatch.setattr(manager, 'spawn_workers', broken)
            with pytest.raises(OSError):
                manager.put(NNTPConnectionRequest(actions=[]))

            monkeypatch.setattr(manager, 'spawn_workers', spawn)
            request = NNTPConnectionRequest(
                actions=[('stat', ('<nothing@example.com>', ), {})])
            with gevent.Timeout(5):
                manager.put(request)
            assert request.wait(timeout=5)

        finally:
            manager.close()
            settings.close()


def test_closing_wakes_whoever_waits_on_a_queued_request(tmp_path):
    """
    Requests still queued when the manager closes are answered with
    nothing instead of being waited on forever
    """
    from newsreap.NNTPConnectionRequest import NNTPConnectionRequest

    with FakeNNTPServer(join_group=False) as server:
        settings, manager = manager_for(server, tmp_path)
        try:
            # Queued, with no worker started to take it
            request = NNTPConnectionRequest(
                actions=[('stat', ('<nothing@example.com>', ), {})])
            manager._work_queue.put(request)
            manager.close()
            assert request.wait(timeout=1)
            assert request.response == [None]

        finally:
            settings.close()


def test_workers_share_the_connections_a_server_allows(tmp_path,
                                                       monkeypatch):
    """
    - two workers never use more of a server's connections together than
      it allows (connections: 3)
    - one with none waits for one (and uses one anyway after a while, so
      it is never stuck)
    - closing gives them back; without a database each worker uses them
      all
    """
    from newsreap.NNTPClaims import NNTPClaims

    # The package has a class of the same name
    module = sys.modules['newsreap.NNTPManager']

    monkeypatch.setattr(module, 'SLOT_RETRY', 0)
    monkeypatch.setattr(module, 'SLOT_WAIT', 0.05)

    with FakeNNTPServer(join_group=False) as server:
        settings = NNTPSettings(cfg_file=config_for(
            server, tmp_path / 'config.yaml', connections=3))

        # Without a database, nothing is shared
        alone = NNTPManager(settings, claims=NNTPClaims(settings))
        alone.spawn_workers(count=3)
        assert len(alone._pool) == 3
        alone.close()

        settings.open(reset=True)
        first = NNTPManager(settings, claims=NNTPClaims(settings))
        second = NNTPManager(settings, claims=NNTPClaims(settings))
        try:
            first.spawn_workers(count=2)
            assert len(first._pool) == 2

            second.spawn_workers(count=3)
            assert len(second._pool) == 1
            assert len(settings.claims('connection:')) == 3

            # The first can't take more until the second gives its back
            assert first.spawn_workers(count=1) is False
            assert len(first._pool) == 2
            second.close()
            first.spawn_workers(count=1)
            assert len(first._pool) == 3

            # With none free, the second waits, then uses one anyway
            second.spawn_workers(count=1)
            assert len(second._pool) == 1
            assert len(settings.claims('connection:')) == 3

        finally:
            first.close()
            second.close()
            settings.close()

        assert settings.claims() == {}


def test_a_connection_of_our_own_shares_the_servers_limit(tmp_path,
                                                         monkeypatch):
    """
    manager.connection() (ie: listing a server's groups) takes one of the
    connections the server allows, gives it back afterwards, and what it
    did is part of our statistics (under the server's key)
    """
    from newsreap.NNTPClaims import NNTPClaims
    from newsreap.Utils import server_key

    module = sys.modules['newsreap.NNTPManager']
    monkeypatch.setattr(module, 'SLOT_RETRY', 0)
    monkeypatch.setattr(module, 'SLOT_WAIT', 0.05)

    with FakeNNTPServer(join_group=False) as server:
        settings = NNTPSettings(cfg_file=config_for(
            server, tmp_path / 'config.yaml', connections=2))
        settings.open(reset=True)
        s = settings.nntp_servers[0]
        key = server_key(**s)

        manager = NNTPManager(settings, claims=NNTPClaims(settings))
        other = NNTPManager(settings, claims=NNTPClaims(settings))
        try:
            with manager.connection(s) as connection:
                assert connection.key == key
                assert connection.groups() is not None
                assert len(settings.claims('connection:')) == 1

                # Another worker only gets what is left
                other.spawn_workers(count=2)
                assert len(other._pool) == 1

            # Given back
            assert len(settings.claims('connection:')) == 1
            assert manager._slots == []
            assert manager.stats_by_host()[key].commands > 0

        finally:
            other.close()
            manager.close()
            settings.close()


def test_each_part_gets_decoders_of_its_own(tmp_path):
    """
    Decoders handed to the manager for a post are copied for each part
    (they keep what they decode, and the parts are fetched at once)
    """
    from newsreap.NNTPArticle import NNTPArticle
    from newsreap.NNTPSegmentedPost import NNTPSegmentedPost
    from newsreap.codecs.CodecHeader import CodecHeader

    with FakeNNTPServer(join_group=False) as server:
        stored(server, tmp_path, '<a@example.com>', '<b@example.com>')
        settings, manager = manager_for(server, tmp_path)
        try:
            post = NNTPSegmentedPost('file.bin')
            for no, message_id in enumerate(
                    ('<a@example.com>', '<b@example.com>'), 1):
                post.add(NNTPArticle(id=message_id, no=no))

            decoder = CodecHeader(work_dir=str(tmp_path))
            requests = manager.get(post, str(tmp_path), decoders=[decoder],
                                   block=False)
            given = [request.actions[0][2]['decoders'][0]
                     for request in requests]
            assert len({id(d) for d in given}) == 2
            assert decoder not in given
            assert all(isinstance(d, CodecHeader) for d in given)

            for request in requests:
                request.wait()

        finally:
            manager.close()
            settings.close()


def test_the_overview_format_and_any_command_go_through_the_pool(tmp_path):
    """
    overview_format() and send() are asked of one of the manager's
    connections; send() can join a group first (and says when it can't)
    """
    from newsreap.NNTPResponse import NNTPResponseCode

    with FakeNNTPServer(join_group=True) as server:
        server.add_group('alt.binaries.test')
        settings, manager = manager_for(server, tmp_path)
        try:
            assert isinstance(manager.overview_format(), tuple)

            request = manager.overview_format(block=False)
            request.wait()
            assert request.response[0] == manager.overview_format()

            assert manager.send('MODE READER').code == 200

            response = manager.send('MODE READER', group='alt.binaries.test')
            assert response.code == 200
            assert 'GROUP alt.binaries.test' in server.commands()

            response = manager.send('MODE READER', group='alt.no.such')
            assert response.code == NNTPResponseCode.INVALID_GROUP

        finally:
            manager.close()
            settings.close()


def test_a_server_with_no_connections_free_doesnt_hold_up_another(tmp_path):
    """
    Finding every connection of one server taken only waits on that
    server; another server's connection is still claimed right away
    """
    class Claims:
        def claim(self, names, limit=1):
            # The first server's are all taken
            return [] if 'busy.example.com' in names[0] else names[:limit]

        def release(self, names):
            pass

    with FakeNNTPServer(join_group=False) as server:
        settings = NNTPSettings(cfg_file=config_for(
            server, tmp_path / 'config.yaml'))
        manager = NNTPManager(settings, claims=Claims())
        try:
            assert manager._slot({'host': 'busy.example.com'}) is False
            assert manager._slot({'host': 'free.example.com'}) is True
        finally:
            manager.close()
            settings.close()


def test_a_command_isnt_sent_when_its_group_cant_be_joined(tmp_path):
    """
    send() with a group that doesn't exist answers INVALID_GROUP without
    sending the command (it would run in whatever group was used last)
    """
    from newsreap.NNTPResponse import NNTPResponseCode

    with FakeNNTPServer(join_group=True) as server:
        server.add_group('alt.binaries.test')
        server.on(r'^DATE$', '111 20260101000000')
        settings, manager = manager_for(server, tmp_path)
        try:
            assert manager.send('DATE', group='alt.binaries.test').code \
                == 111

            response = manager.send('DATE', group='alt.no.such')
            assert response.code == NNTPResponseCode.INVALID_GROUP
            assert server.commands().count('DATE') == 1

        finally:
            manager.close()
            settings.close()
