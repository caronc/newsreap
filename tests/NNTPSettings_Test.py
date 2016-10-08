# -*- encoding: utf-8 -*-
#
# Test the NNTPSettings Object
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
if 'threading' in sys.modules:
    #  gevent patching since pytests import
    #  the sys library before we do.
    del sys.modules['threading']

import gevent.monkey
gevent.monkey.patch_all()

from os.path import join
from os.path import dirname
from os.path import abspath
from os.path import basename
from os.path import isfile
from os import unlink
from os import chmod

try:
    from tests.TestBase import TestBase

except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

from newsreap.NNTPSettings import NNTPSettings
from newsreap.NNTPSettings import SERVER_LIST_KEY
from newsreap.NNTPSettings import DEFAULT_SERVER_VARIABLES
from newsreap.NNTPSettings import DEFAULT_PROCESSING_VARIABLES
from newsreap.NNTPSettings import PROCESSING_KEY
from newsreap.NNTPSettings import VALID_SETTINGS_ENTRY
from newsreap.NNTPIOStream import NNTPIOStream


class NNTPSettings_Test(TestBase):
    """
    Test the configuration and interaction to and from
    the database configured.
    """

    def test_bad_file(self):
        """
        Test that we can't load bad files
        """
        # We'll create a file now but it will be empty
        # so that will still make it invalid
        cfg_file = join(
            self.tmp_dir,
            'NNTPSettings.test_bad_file.yaml'
        )

        # Make sure the file doesn't exist
        assert isfile(cfg_file) is False

        settings = NNTPSettings(cfg_file=cfg_file)
        assert settings.is_valid() is False
        assert len(settings.nntp_servers) == 0
        assert settings.is_valid() is False

        # now create it and populate it with nothing
        fd = open(cfg_file, 'w')
        fd.close()
        # File exists now
        assert isfile(cfg_file) is True

        assert settings.read(cfg_file) is False
        assert basename(settings.cfg_file) == basename(cfg_file)
        assert len(settings.cfg_data) == len(VALID_SETTINGS_ENTRY)
        for k in VALID_SETTINGS_ENTRY.iterkeys():
            assert k in settings.cfg_data

        assert settings.is_valid() is False

        # eliminate the file
        unlink(cfg_file)
        # File is gone again
        assert isfile(cfg_file) is False

        # Create a poorly formatted (unparseable file)
        with open(cfg_file, 'w') as fp:
            fp.write('%s\n' % SERVER_LIST_KEY)
            fp.write(' - test: apple\n')

        settings = NNTPSettings(cfg_file=cfg_file)

        assert settings.is_valid() is False

        # cleanup
        unlink(cfg_file)

    def test_server_settings(self):
        """
        Test server configuration passed in and defaults
        """
        # We'll create a file now but it will be empty
        # so that will still make it invalid
        cfg_file = join(self.tmp_dir, 'NNTPSettings.test_server_cfg.yaml')

        invalid_entry = 'invalid_entry'

        servers = []
        servers.append({
            'username': 'foo',
            'password': 'bar',
            'host': 'foo.bar.net',
            'port': '563',
            'secure': 'True',
            # compress = True sets our IOStream to gzip.rfc3977
            'compress': 'True',
            'priority': '2',
            'join_group': 'False',
        })

        servers.append({
            'username': 'bar',
            'password': 'foo',
            'host': 'bar.foo.net',
            'port': '119',
            'secure': 'False',
            # compress = False sets our IOStream to rfc3977
            'compress': 'False',
            'join_group': 'True',
            'priority': '1',
            # We won't crap out on unsupported entries
            'invalid_entry': 'safely_ignored',
        })

        # Create a yaml configuration entry we can test with
        # This one has a valid parseable format
        assert isfile(cfg_file) is False
        with open(cfg_file, 'w') as fp:
            fp.write('%s:\n' % SERVER_LIST_KEY)
            for server in servers:
                fp.write('  - %s' % ('    '.join(['%s: %s\n' % (k, v) \
                                for (k, v) in server.items()])))
        assert isfile(cfg_file) is True

        # Now We test it out
        settings = NNTPSettings(cfg_file=cfg_file)

        # our configuration should be valid
        assert settings.is_valid() is True

        # 2 Servers Identified
        assert len(settings.nntp_servers) == 2

        # Test that our priorities were assigned correctly even though they
        # were inserted out of order
        assert settings.nntp_servers[0]['priority'] == 1
        assert settings.nntp_servers[1]['priority'] == 2

        # Test that our compression flags correctly set the iostream
        assert settings.nntp_servers[0]['iostream'] == \
                NNTPIOStream.RFC3977
        assert settings.nntp_servers[1]['iostream'] == \
                NNTPIOStream.RFC3977_GZIP

        for i in range(0, len(servers)):
            assert len(settings.nntp_servers[i]) == \
                    len(DEFAULT_SERVER_VARIABLES)
            # Confirm we don't worry about invalid entries allowing future
            # configuration files to be backwards compatible with the old
            assert invalid_entry not in settings.nntp_servers[i]

        # The second entry has a higher priority than the first, so it
        # should be used.
        for (k, v) in servers[1].items():
            if k in (invalid_entry, 'compress'):
                # We already established the length above, therefore
                # we can't check for this item or we'll have a problem

                # It should be observed that even though 'compress' was
                # specified as an argument, it's only there for user/cfg
                # friendliness.  It actually sets the iostream variable
                # instead.
                continue

            assert v == str(settings.nntp_servers[0][k])

        # Now just check that our first entry was successfully stored second
        # since it's priority was lower
        for (k, v) in servers[0].items():
            if k in (invalid_entry, 'compress'):
                # We already established the length above, therefore
                # we can't check for this item or we'll have a problem

                # It should be observed that even though 'compress' was
                # specified as an argument, it's only there for user/cfg
                # friendliness.  It actually sets the iostream variable
                # instead.
                continue

            assert v == str(settings.nntp_servers[1][k])

        # make sure the file doesn't exist already
        try:
            unlink(cfg_file)
        except:
            pass

        assert isfile(cfg_file) is False

    def test_processing_settings(self):
        """
        Test Processing Settings
        """
        cfg_file = join(self.tmp_dir, 'NNTPSettings.test_processing_cfg.yaml')

        invalid_entry = 'invalid_entry'

        processing = {
            'threads': 10,
            'header_batch_size': 8000,
            invalid_entry: 'garbage'
        }

        # Create a yaml configuration entry we can test with
        with open(cfg_file, 'w') as fp:
            fp.write('%s:\n' % PROCESSING_KEY)
            fp.write('    %s' % ('    '.join(['%s: %s\n' % (k, v) \
                for (k, v) in processing.items()])))

        # Now We test it out
        settings = NNTPSettings(cfg_file=cfg_file)

        # processing information alone isn't enough to be a valid
        # setting load;  We need to have servers; so this will fail
        assert settings.is_valid() is False

        # However, we should have still been able to load our configuration
        assert len(settings.nntp_processing) == \
                len(DEFAULT_PROCESSING_VARIABLES)

        # Confirm we don't worry about invalid entries allowing future
        # configuration files to be backwards compatible with the old
        assert invalid_entry not in settings.nntp_processing

        for (k, v) in processing.items():
            if k == invalid_entry:
                # We already established the length above, therefore
                # we can't check for this item or we'll have a problem
                continue

            assert v == settings.nntp_processing[k]

    def test_scanner_error(self):
        """
        Test YAML Scanner Settings
        """
        cfg_file = join(self.tmp_dir, 'NNTPSettings.test_scanner_error.yaml')

        server = {
            'username': '%foo',
            'password': 'bar',
            'host': 'foo.bar.net',
            'port': '563',
            'secure': 'True',
            'compress': 'True',
            'priority': '2',
            'join_group': 'False',
        }

        with open(cfg_file, 'w') as fp:
            fp.write('%s:\n' % SERVER_LIST_KEY)
            fp.write(' - %s' % ('  '.join(['%s: %s\n' % (k, v) \
                for (k, v) in server.items()])))

        # Now we test it out
        settings = NNTPSettings(cfg_file=cfg_file)

        # We fail because of the YAML does not accept % entries
        # It throws a Scanner exception
        assert settings.is_valid() is False

    def test_parse_error(self):
        """
        Test YAML Parse Settings
        """
        cfg_file = join(self.tmp_dir, 'NNTPSettings.test_parse_error.yaml')

        invalid_entry = 'invalid_entry'

        server = {
            'username': 'foo',
            'password': 'bar',
            'host': 'foo.bar.net',
            'port': '563',
            'secure': 'True',
            'compress': 'True',
            'priority': '2',
            'join_group': 'False',
            invalid_entry: 'garbage',
        }

        processing = {
            'threads': 10,
            'header_batch_size': 8000,
            invalid_entry: 'garbage',
        }

        # Create a yaml configuration entry we can test with
        # The output is invalid (formatting)
        with open(cfg_file, 'w') as fp:
            fp.write('%s:\n' % PROCESSING_KEY)
            fp.write('  %s' % ('  '.join(['%s: %s\n' % (k, v) \
                for (k, v) in processing.items()])))

            fp.write('%s:\n' % SERVER_LIST_KEY)
            fp.write(' - %s' % ('  '.join(['%s: %s\n' % (k, v) \
                for (k, v) in server.items()])))

        # Now we test it out
        settings = NNTPSettings(cfg_file=cfg_file)

        # We fail because of the YAML formatting
        assert settings.is_valid() is False

    def test_writing_settings(self):
        """
        Test Writing Settings
        """
        cfg_file = join(self.tmp_dir, 'NNTPSettings.test_writing_cfg.yaml')

        invalid_entry = 'invalid_entry'

        server = {
            'username': 'foo',
            'password': 'bar',
            'host': 'foo.bar.net',
            'port': '563',
            'secure': 'True',
            'compress': 'True',
            'priority': '2',
            'join_group': 'False',
            invalid_entry: 'garbage',
        }

        processing = {
            'threads': 10,
            'header_batch_size': 8000,
            invalid_entry: 'garbage',
        }

        # Create a yaml configuration entry we can test with
        with open(cfg_file, 'w') as fp:
            fp.write('%s:\n' % PROCESSING_KEY)
            fp.write('    %s' % ('    '.join(['%s: %s\n' % (k, v) \
                for (k, v) in processing.items()])))

            fp.write('%s:\n' % SERVER_LIST_KEY)
            fp.write('  - %s' % ('    '.join(['%s: %s\n' % (k, v) \
                for (k, v) in server.items()])))

        # Now We test it out
        settings = NNTPSettings(cfg_file=cfg_file)

        # processing information alone isn't enough to be a valid
        # setting load;  We need to have servers; so this will fail
        assert settings.is_valid() is True

        # 1 Server Identified
        assert len(settings.nntp_servers) == 1

        # However, we should have still been able to load our configuration
        assert len(settings.nntp_processing) == \
                len(DEFAULT_PROCESSING_VARIABLES)

        # Confirm we don't worry about invalid entries allowing future
        # configuration files to be backwards compatible with the old
        assert invalid_entry not in settings.nntp_processing

        new_cfg_file = join(
            self.tmp_dir,
            'NNTPSettings.test_new_cfg.yaml'
        )

        assert isfile(new_cfg_file) is False
        assert settings.save(new_cfg_file) is True
        # Save should have been sucessfull
        assert isfile(new_cfg_file) is True

        # Load configuration
        new_settings = NNTPSettings(cfg_file=new_cfg_file)

        # Our new settings should look like the old ones
        assert new_settings.is_valid()
        assert new_settings.base_dir == settings.base_dir
        assert new_settings.work_dir == settings.work_dir
        assert new_settings.nntp_servers == settings.nntp_servers
        assert new_settings.nntp_processing == settings.nntp_processing
        assert new_settings.nntp_processing == settings.nntp_processing

        # change the permission so it's inaccessible and try again
        # Note: this test doesn't work in Windows based machines
        chmod(new_cfg_file, 0000)
        assert settings.read(new_cfg_file) is False
        assert basename(settings.cfg_file) == basename(new_cfg_file)
        for k in VALID_SETTINGS_ENTRY.iterkeys():
            assert k in settings.cfg_data

        assert settings.is_valid() is False

        # Load back our old settings
        new_settings = NNTPSettings(cfg_file=new_cfg_file)

        # Note that we can't write to a unwritable file
        assert settings.save(new_cfg_file) is False

    def test_single_server_support(self):
        """
        Test the ability to support a single server
        """
        cfg_file = join(self.tmp_dir, 'NNTPSettings.test_single_server.yaml')

        invalid_entry = 'invalid_entry'

        server = {
            'username': 'foo',
            'password': 'bar',
            'host': 'foo.bar.net',
            'port': '563',
            'secure': 'True',
            'compress': 'True',
            'priority': '2',
            'join_group': 'False',
            invalid_entry: 'garbage',
        }

        # Simply create a config script where the server is identified
        # as a single entry (not multiple). This is just to prove
        # we support this style too for people with just 1 server
        with open(cfg_file, 'w') as fp:
            fp.write('%s:\n' % SERVER_LIST_KEY)
            fp.write('  %s' % ('  '.join(['%s: %s\n' % (k, v) \
                for (k, v) in server.items()])))

        # Now we test it out
        settings = NNTPSettings(cfg_file=cfg_file)

        # We should be valid
        assert settings.is_valid() is True
        # ... with 1 server identified
        assert len(settings.nntp_servers) == 1
