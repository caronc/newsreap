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
from newsreap.NNTPSettings import SERVER_VARIABLES
from newsreap.NNTPSettings import SERVER_LIST_KEY
from newsreap.NNTPSettings import DATABASE_LIST_KEY
from newsreap.NNTPSettings import PROCESSING_VARIABLES
from newsreap.NNTPSettings import PROCESSING_LIST_KEY
from newsreap.Database import MEMORY_DATABASE_ENGINE
from newsreap.objects.nntp.Server import Server

# The defaults assigned to a Settings object
DEFAULTS = {
    DATABASE_LIST_KEY: {},
    SERVER_LIST_KEY: [],
    PROCESSING_LIST_KEY: {},
}

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
        try:
            unlink(cfg_file)
        except:
            pass

        settings = NNTPSettings(
            cfg_file=cfg_file,
            cfg_path=dirname(cfg_file),
            engine=MEMORY_DATABASE_ENGINE,
            # Make sure the existing database is reset
            reset=True,
        )

        assert settings.cfg_path is not None
        assert len(settings.cfg_files) == 0
        assert len(settings.nntp_servers) == 0
        assert settings.is_valid() is False

        # now create it and populate it with nothing
        fd = open(cfg_file, 'w')
        fd.close()

        assert settings.read(cfg_file) is False
        assert len(settings.cfg_files) == 1
        assert basename(settings.cfg_files[0]) == basename(cfg_file)
        assert len(settings.cfg_data) == len(DEFAULTS)
        print str(settings.cfg_data)
        for k in DEFAULTS.iterkeys():
            print str(k)
            print type(settings.cfg_data[k])
            assert settings.cfg_data[k] == DEFAULTS[k]

        assert settings.is_valid() is False

        # change the permission so it's inaccessible and try again
        # Note: this test doesn't work in Windows based machines
        chmod(cfg_file, 0)
        assert settings.read(cfg_file) is False
        assert len(settings.cfg_files) == 1
        assert basename(settings.cfg_files[0]) == basename(cfg_file)
        for k in DEFAULTS.iterkeys():
            assert settings.cfg_data[k] == DEFAULTS[k]
        assert settings.is_valid() is False

        # cleanup
        unlink(cfg_file)

        # Create a poorly formatted (unparseable file)
        with open(cfg_file, 'w') as fp:
            fp.write('%s\n' % SERVER_LIST_KEY)
            fp.write(' - test: apple\n')

        settings = NNTPSettings(
            cfg_file=cfg_file,
            cfg_path=dirname(cfg_file),
            # Make sure the existing database is reset
            reset=True,
        )

        assert settings.cfg_path is not None
        assert len(settings.cfg_files) == 0

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
            'compress': 'False',
            'join_group': 'True',
            'priority': '1',
            # We won't crap out on unsupported entries
            'invalid_entry': 'safely_ignored',
        })

        # Create a yaml configuration entry we can test with
        with open(cfg_file, 'w') as fp:
            fp.write('%s:\n' % SERVER_LIST_KEY)
            for server in servers:
                fp.write('  - %s' % ('    '.join(['%s: %s\n' % (k, v) \
                                for (k, v) in server.items()])))

        # Now We test it out
        settings = NNTPSettings(
            cfg_file=cfg_file,
            cfg_path=dirname(cfg_file),
            engine=MEMORY_DATABASE_ENGINE,
            # Make sure the existing database is reset
            reset=True,
        )

        # our configuration should be valid
        assert settings.is_valid() is True
        assert len(settings.nntp_servers) == 2
        for i in range(0, len(servers)):
            assert len(settings.nntp_servers[i]) == len(SERVER_VARIABLES)
            # Confirm we don't worry about invalid entries allowing future
            # configuration files to be backwards compatible with the old
            assert invalid_entry not in settings.nntp_servers[i]

        # The second entry has a higher priority than the first, so it
        # should be used.
        for (k, v) in servers[1].items():
            if k == invalid_entry:
                # We already established the length above, therefore
                # we can't check for this item or we'll have a problem
                continue

            assert v == str(settings.nntp_servers[0][k])

        # Now just check that our first entry was successfully stored second
        # since it's priority was lower
        for (k, v) in servers[0].items():
            if k == invalid_entry:
                # We already established the length above, therefore
                # we can't check for this item or we'll have a problem
                #
                continue

            assert v == str(settings.nntp_servers[1][k])

        # make sure the file doesn't exist already
        try:
            unlink(cfg_file)
        except:
            pass


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
            fp.write('%s:\n' % PROCESSING_LIST_KEY)
            fp.write('  %s' % ('  '.join(['%s: %s\n' % (k, v) \
                for (k, v) in processing.items()])))

        # Now We test it out
        settings = NNTPSettings(
            cfg_file=cfg_file,
            cfg_path=dirname(cfg_file),
            engine=MEMORY_DATABASE_ENGINE,
            # Make sure the existing database is reset
            reset=True,
        )

        # processing information alone isn't enough to be a valid
        # setting load;  We need to have servers; so this will fail
        assert settings.is_valid() is False

        # However, we should have still been able to load our configuration
        assert len(settings.nntp_processing) == len(PROCESSING_VARIABLES)

        # Confirm we don't worry about invalid entries allowing future
        # configuration files to be backwards compatible with the old
        assert invalid_entry not in settings.nntp_processing

        for (k, v) in processing.items():
            if k == invalid_entry:
                # We already established the length above, therefore
                # we can't check for this item or we'll have a problem
                continue

            assert v == settings.nntp_processing[k]



    def test_sql_database_settings_01(self):
        """
        Test sql database configuration passed in and defaults
        """
        # We'll create a file now but it will be empty
        # so that will still make it invalid
        cfg_file = join(
            self.tmp_dir,
            'NNTPSettings.test_database_cfg.yaml',
        )

        # Memory Only Configuration
        database = {
            'engine': MEMORY_DATABASE_ENGINE,
        }

        # make sure the file doesn't exist already
        try:
            unlink(cfg_file)
        except:
            pass

        # Create a yaml configuration entry we can test with
        with open(cfg_file, 'w') as fp:
            fp.write('database:\n')
            fp.write('  %s' % ('  '.join(['%s: "%s"\n' % (k, v) \
                                for (k, v) in database.items()])))


        # Now We test it out
        settings = NNTPSettings(
            cfg_file=cfg_file,
            cfg_path=dirname(cfg_file),
            engine=MEMORY_DATABASE_ENGINE,
            # Make sure the existing database is reset
            reset=True,
        )

        # our configuration should not be valid because even though
        # we successfully read the file, we had no nntp servers configured
        assert settings.is_valid() is False

        # If we insert a record into our database and try again, we'll
        # be fine though.
        s = settings.session()
        s.add(Server("low", priority=2))
        s.add(Server("high", priority=1))
        s.commit()

        # Now if we re-load our configuration, we should be good to go
        settings.read()

        assert settings.is_valid() is True

        # We should have 2 entries now (read from our database)
        assert len(settings.nntp_servers) == 2

        # Test that our servers are ordered by priority
        assert settings.nntp_servers[0]['host'] == 'high'
        assert settings.nntp_servers[1]['host'] == 'low'

        # make sure the file doesn't exist already
        try:
            unlink(cfg_file)
        except:
            pass


    def test_sql_database_settings_02(self):
        """
        Similar to the test above except we test that the defined
        database with the highest priority trumps it's duplicate reguardless
        if it was found in the database or the flat file.

        Entries are only considered unique by their hostname (port is
        intentionally not considered). Newsreap already has the ability
        to support multiple threads per server.  So there is no reason
        to set up a backup server being the same as the first. If the
        priorities found are the same, the flat file always trumps

        """
        # We'll create a file now but it will be empty
        # so that will still make it invalid
        cfg_file = join(
            self.tmp_dir,
            'NNTPSettings.test_database_cfg.yaml',
        )

        # Test File Trumping
        file_priority = 2
        dbase_priority = 4

        # Define the name of our common host
        common_server = {
            'username': 'foo',
            'password': 'bar',
            'host': 'foo.bar.net',
            'port': '563',
            'secure': 'True',
            'compress': 'True',
            'priority': file_priority,
            'join_group': 'False',
        }

        servers = []
        servers.append(common_server)

        # Memory Only Configuration
        database = {
            'engine': MEMORY_DATABASE_ENGINE,
        }

        # Create a yaml configuration entry we can test with
        with open(cfg_file, 'w') as fp:
            fp.write('database:\n')
            fp.write('  %s' % ('  '.join(['%s: "%s"\n' % (k, v) \
                                for (k, v) in database.items()])))
            fp.write('%s:\n' % SERVER_LIST_KEY)
            for server in servers:
                fp.write('  - %s' % ('    '.join(['%s: %s\n' % (k, v) \
                                for (k, v) in server.items()])))


        # Now We test it out
        settings = NNTPSettings(
            cfg_file=cfg_file,
            cfg_path=dirname(cfg_file),
            engine=MEMORY_DATABASE_ENGINE,
            # Make sure the existing database is reset
            reset=True,
        )

        # We have a flat file, so our configuration will be valid since
        # there are 2 servers defined in it.
        assert settings.is_valid() is True

        # Create a session
        s = settings.session()

        s.add(Server(
            name="common_server",
            host=common_server.get('host'),
            port=int(common_server.get('port')),
            username=common_server.get('username'),
            password=common_server.get('password'),
            secure=bool(common_server.get('secure')),
            join_group=bool(common_server.get('join_group')),
            # Set our database priority
            priority=dbase_priority,
        ))
        s.commit()

        common_server = {
            'username': 'foo',
            'password': 'bar',
            'host': 'foo.bar.net',
            'port': '563',
            'secure': 'True',
            'compress': 'True',
            'priority': '2',
            'join_group': 'False',
        }

        # Now if we re-load our configuration, we should be good to go
        settings.read()

        assert settings.is_valid() is True

        # We will only have 1 entry (not 2) because the datab
        assert len(settings.nntp_servers) == 1

        # remove the configuration file
        unlink(cfg_file)

        assert not isfile(cfg_file)


    def test_multifile_server_settings(self):
        """
        Test server settings read from multiple files
        """
        # We'll create a file now but it will be empty
        # so that will still make it invalid
        cfg_file_01 = join(self.tmp_dir, 'NNTPSettings.test_multi01.yaml')
        cfg_file_02 = join(self.tmp_dir, 'NNTPSettings.test_multi02.yaml')

        servers_01 = []
        servers_02 = []

        invalid_entry = 'invalid_entry'

        servers_01.append({
            'username': 'foo',
            'password': 'bar',
            'host': 'foo.bar.net',
            'port': '563',
            'secure': 'True',
            'compress': 'True',
            'priority': '2',
            'join_group': 'False',
        })

        servers_01.append({
            'username': 'bar',
            'password': 'foo',
            'host': 'bar.foo.net',
            'port': '119',
            'secure': 'False',
            'compress': 'False',
            'join_group': 'True',
            'priority': '1',
            # We won't crap out on unsupported entries
            'invalid_entry': 'safely_ignored',
        })

        servers_02.append({
            'username': 'foobar',
            'password': 'foofoo',
            'host': 'barfoo.foobar.net',
            'port': '119',
            'secure': 'False',
            'compress': 'False',
            'join_group': 'True',
            'priority': '3',
        })

        # Create a yaml configuration entry we can test with (cfg_01)
        with open(cfg_file_01, 'w') as fp:
            fp.write('%s:\n' % SERVER_LIST_KEY)
            for server in servers_01:
                fp.write('  - %s' % ('    '.join(['%s: %s\n' % (k, v) \
                                for (k, v) in server.items()])))

        # Create a yaml configuration entry we can test with (cfg_02)
        with open(cfg_file_02, 'w') as fp:
            fp.write('%s:\n' % SERVER_LIST_KEY)
            for server in servers_02:
                fp.write('  - %s' % ('    '.join(['%s: %s\n' % (k, v) \
                                for (k, v) in server.items()])))

        # Now We test it out
        settings = NNTPSettings(
            cfg_file=[cfg_file_01, cfg_file_02],
            cfg_path=dirname(cfg_file_01),
            engine=MEMORY_DATABASE_ENGINE,
            # Make sure the existing database is reset
            reset=True,
        )

        assert settings.is_valid() is True

        # All servers from all files will be loaded
        assert len(settings.nntp_servers) == 3

        for i in range(0, len(servers_01)):
            assert len(settings.nntp_servers[i]) == len(SERVER_VARIABLES)
            # Confirm we don't worry about invalid entries allowing future
            # configuration files to be backwards compatible with the old
            assert invalid_entry not in settings.nntp_servers[i]

        for i in range(0, len(servers_02)):
            assert len(settings.nntp_servers[i]) == len(SERVER_VARIABLES)
            # Confirm we don't worry about invalid entries allowing future
            # configuration files to be backwards compatible with the old
            assert invalid_entry not in settings.nntp_servers[i]

        # We want to be sure host entries are stored in order of their
        # defined priority
        assert settings.nntp_servers[0]['host'] == servers_01[1]['host']
        assert settings.nntp_servers[1]['host'] == servers_01[0]['host']
        assert settings.nntp_servers[2]['host'] == servers_02[0]['host']


    def test_merged_server_settings_01(self):
        """
        Test server settings read from a file and then later has
        a database entry that over-rides it.
        """

        # Common Hostname to use
        hostname = 'foobar.localhost'
        port = 9000

        cfg_file = join(self.tmp_dir, 'NNTPSettings.test_merge.yaml')
        # We identify a server and intentionally 'do not' specify a
        # priority.  It will default to being priority 1 since it's
        # the first item in the list anyway, but because it lacks
        # the priority entry, it's values can be over-ridden by a
        # matched database entry
        servers = []
        servers.append({
            'username': 'foobar',
            'password': 'barfoo',
            'host': hostname,
            'port': str(port),
            'secure': 'False',
            'compress': 'False',
            'join_group': 'True',
        })

        # Create a yaml configuration entry we can test with (cfg_02)
        with open(cfg_file, 'w') as fp:
            fp.write('%s:\n' % SERVER_LIST_KEY)
            for server in servers:
                fp.write('  - %s' % ('    '.join(['%s: %s\n' % (k, v) \
                                for (k, v) in server.items()])))

        # Create our Settings Object
        settings = NNTPSettings(
            cfg_file=cfg_file,
            cfg_path=dirname(cfg_file),
            engine=MEMORY_DATABASE_ENGINE,
            # Make sure the existing database is reset
            reset=True,
        )

        assert len(settings.nntp_servers) == 1
        # Our hostname should be set correctly
        assert settings.nntp_servers[0]['host'] == hostname
        # Our port should be set correctly
        assert settings.nntp_servers[0]['port'] == port
        # Defaults to priority of 1
        assert settings.nntp_servers[0]['priority'] == 1

        # Now create a record that will over-ride some of the settings
        s = settings.session()
        s.add(Server(hostname, port=(port+1), priority=2))
        s.commit()

        # Re-read our configuration
        settings.read()

        # Since the database entry has the same servername, it
        # should not create a second entry
        assert len(settings.nntp_servers) == 1

        # Our hostname should be set correctly
        assert settings.nntp_servers[0]['host'] == hostname
        # Our port should be the over-ridden value
        assert settings.nntp_servers[0]['port'] == port+1
        # Priority was set to 2 above, so that should be it's value now
        assert settings.nntp_servers[0]['priority'] == 2


    def test_merged_server_settings_02(self):
        """
        Test server settings read from a file and then later has
        a database entry that over-rides it. However this test
        tests that entries where the priority is defined in the
        configuration file will over-ride the database entry
        """

        # Common Hostname to use
        hostname = 'foobar.localhost'
        port = 9000

        cfg_file = join(self.tmp_dir, 'NNTPSettings.test_merge.yaml')
        # We identify a server and intentionally 'do not' specify a
        # priority.  It will default to being priority 1 since it's
        # the first item in the list anyway, but because it lacks
        # the priority entry, it's values can be over-ridden by a
        # matched database entry
        servers = []
        servers.append({
            'username': 'foobar',
            'password': 'barfoo',
            'host': hostname,
            'port': str(port),
            'secure': 'False',
            'compress': 'False',
            'join_group': 'True',
            'priority': '10',
        })

        # Create a yaml configuration entry we can test with (cfg_02)
        with open(cfg_file, 'w') as fp:
            fp.write('%s:\n' % SERVER_LIST_KEY)
            for server in servers:
                fp.write('  - %s' % ('    '.join(['%s: %s\n' % (k, v) \
                                for (k, v) in server.items()])))

        # Create our Settings Object
        settings = NNTPSettings(
            cfg_file=cfg_file,
            cfg_path=dirname(cfg_file),
            engine=MEMORY_DATABASE_ENGINE,
            # Make sure the existing database is reset
            reset=True,
        )

        assert len(settings.nntp_servers) == 1
        # Our hostname should be set correctly
        assert settings.nntp_servers[0]['host'] == hostname
        # Our port should be set correctly
        assert settings.nntp_servers[0]['port'] == port
        # Defaults to priority of 1
        assert settings.nntp_servers[0]['priority'] == 10

        # Now create a record that will over-ride some of the settings
        s = settings.session()
        s.add(Server(hostname, port=(port+1), priority=2))
        s.commit()

        # Re-read our configuration
        settings.read()

        # Since the database entry has the same servername, it
        # should not create a second entry
        assert len(settings.nntp_servers) == 1

        # Our hostname should be set correctly
        assert settings.nntp_servers[0]['host'] == hostname
        # Our port should not be over-ridden because of our priority in
        # the configuration file
        assert settings.nntp_servers[0]['port'] == port
        # Priority was set to 2 above, but because a priority was identified
        # in the configuration file; that takes priority
        assert settings.nntp_servers[0]['priority'] == 10
