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
if 'threading' in sys.modules:
    #  gevent patching since pytests import
    #  the sys library before we do.
    del sys.modules['threading']

import gevent.monkey
gevent.monkey.patch_all()

# Import threading after monkey patching
# see: http://stackoverflow.com/questions/8774958/\
#        keyerror-in-module-threading-after-a-successful-py-test-run
import threading

from os.path import join
from os.path import dirname
from os.path import abspath

try:
    from tests.TestBase import TestBase

except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

from tests.FakeNNTPServer import FakeNNTPServer

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
    from tests.conftest import config_for

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
