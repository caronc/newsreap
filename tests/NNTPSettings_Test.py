# Test the NNTPSettings Object
#
# Copyright (C) 2015-2017 Chris Caron <lead2gold@gmail.com>
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
        for k in VALID_SETTINGS_ENTRY.keys():
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
                fp.write('  - %s' % ('    '.join(
                    ['%s: %s\n' % (k, v) for (k, v) in list(server.items())])))

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
            # Every setting with a default is there (and nothing else)
            assert set(settings.nntp_servers[i]) <= \
                set(DEFAULT_SERVER_VARIABLES)
            assert {k for k, v in DEFAULT_SERVER_VARIABLES.items()
                    if v is not None} <= set(settings.nntp_servers[i])
            # Confirm we don't worry about invalid entries allowing future
            # configuration files to be backwards compatible with the old
            assert invalid_entry not in settings.nntp_servers[i]

        # The second entry has a higher priority than the first, so it
        # should be used.
        for (k, v) in list(servers[1].items()):
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
        for (k, v) in list(servers[0].items()):
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
            fp.write('    %s' % ('    '.join(
                ['%s: %s\n' % (k, v) for (k, v) in list(processing.items())])))

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

        for (k, v) in list(processing.items()):
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
            fp.write(' - %s' % ('  '.join(
                ['%s: %s\n' % (k, v) for (k, v) in list(server.items())])))

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
            fp.write('  %s' % ('  '.join(
                ['%s: %s\n' % (k, v) for (k, v) in list(processing.items())])))

            fp.write('%s:\n' % SERVER_LIST_KEY)
            fp.write(' - %s' % ('  '.join(
                ['%s: %s\n' % (k, v) for (k, v) in list(server.items())])))

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
            fp.write('    %s' % ('    '.join(
                ['%s: %s\n' % (k, v) for (k, v) in list(processing.items())])))

            fp.write('%s:\n' % SERVER_LIST_KEY)
            fp.write('  - %s' % ('    '.join(
                ['%s: %s\n' % (k, v) for (k, v) in list(server.items())])))

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
        for k in VALID_SETTINGS_ENTRY.keys():
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
            fp.write('  %s' % ('  '.join(
                ['%s: %s\n' % (k, v) for (k, v) in list(server.items())])))

        # Now we test it out
        settings = NNTPSettings(cfg_file=cfg_file)

        # We should be valid
        assert settings.is_valid() is True
        # ... with 1 server identified
        assert len(settings.nntp_servers) == 1

    def test_settings_masking(self):
        """
        Test our masking capabilities
        """

        # Create a settings object
        settings = NNTPSettings()

        # Define a base_dir
        settings.base_dir = self.tmp_dir

        # Define a work_dir
        settings.work_dir = '%{base_dir}/blah'

        # Perform some basic lookups
        assert(settings.apply_mask('%{base_dir}', lazy=False) == self.tmp_dir)
        assert(settings.apply_mask('%{work_dir}', lazy=True) ==
               join(self.tmp_dir, 'blah'))

        # The current values are always used
        settings.work_dir = '%{base_dir}/new_dir'
        assert(settings.apply_mask('%{work_dir}', lazy=True) ==
               join(self.tmp_dir, 'new_dir'))

        # We can do custom mapping too
        assert(settings.apply_mask(
                '%{work_dir}/%{custom0}-%{test}',
                mask_map={'%{custom0}': 'boo', '%{test}': 'hoo'}) ==
               join(self.tmp_dir, 'new_dir', '{0}-{1}'.format('boo', 'hoo')))

        # A mask we don't know (or one naming itself) is left as it is
        assert settings.apply_mask('%{work_dir}/%{invalid}/') == \
            join(self.tmp_dir, 'new_dir', '%{invalid}/')
        settings.work_dir = '%{work_dir}/x'
        assert settings.apply_mask('%{work_dir}').startswith('%{work_dir}/x')


def test_sections_that_are_not_settings_are_ignored(tmp_path):
    """
    A section (or a server) that isn't a list of settings is reported and
    left out; the rest of the configuration still loads
    """
    from tests.FakeNNTPServer import FakeNNTPServer
    from tests.conftest import config_for

    with FakeNNTPServer() as server:
        path = config_for(server, tmp_path / 'config.yaml')
        with open(path) as fp:
            content = fp.read()

        # A server given as a bare name, and broken sections
        content = content.replace(
            'servers:\n', 'servers:\n  - just-a-host-name\n', 1)
        with open(path, 'w') as fp:
            fp.write(content + '\nposting: 5\ndownloads: [1, 2]\n')

        settings = NNTPSettings(cfg_file=path)
        try:
            assert settings.is_valid()
            assert len(settings.nntp_servers) == 1
            assert settings.nntp_servers[0]['port'] == server.port

        finally:
            settings.close()


def test_servers_are_told_apart_by_user_host_and_port():
    """
    server_key() names a server by its user, host and port (the usual port
    is left out)
    """
    from newsreap.Utils import server_key

    assert server_key('News.Example.com') == 'news.example.com'
    assert server_key('news.example.com', 119) == 'news.example.com'
    assert server_key('news.example.com', 563, secure=True) == \
        'news.example.com'
    assert server_key('news.example.com', 563) == 'news.example.com:563'
    assert server_key('news.example.com', '8119', 'me') == \
        'me@news.example.com:8119'
    assert server_key(**{'host': 'news.example.com', 'username': 'me',
                         'port': 563, 'secure': True, 'priority': 1}) == \
        'me@news.example.com'


def test_two_accounts_on_one_server_are_both_used(tmp_path, caplog):
    """
    Two accounts on the same host are two servers; one listed twice is
    only used once (and said so)
    """
    from newsreap.Utils import server_key

    path = tmp_path / 'config.yaml'
    path.write_text(
        'servers:\n'
        '  - host: news.example.com\n'
        '    username: first\n'
        '  - host: news.example.com\n'
        '    username: second\n'
        '  - host: News.Example.com\n'
        '    username: first\n')

    settings = NNTPSettings(cfg_file=str(path))
    assert [server_key(**s) for s in settings.nntp_servers] == [
        'first@news.example.com', 'second@news.example.com']
    assert 'first@news.example.com is listed more than once' in caplog.text
    settings.close()


def test_the_download_batch_size_falls_back_when_it_isnt_a_count(
        tmp_path, caplog):
    """
    batch_size is 10,000 unless set; one that isn't a count of articles
    (ie: 0 or a word) is an error and 10,000 is used
    """
    cfg_file = tmp_path / 'config.yaml'
    for value, expected, error in (
            (None, 10000, False), (500, 500, False), ("'250'", 250, False),
            (0, 10000, True), ('lots', 10000, True)):
        cfg_file.write_text('' if value is None else
                            'downloads:\n  batch_size: %s\n' % value)
        settings = NNTPSettings(cfg_file=str(cfg_file))
        caplog.clear()
        assert settings.batch_size() == expected
        assert any(r.levelname == 'ERROR' for r in caplog.records) is error


def test_a_mistyped_mask_is_reported_not_a_crash(tmp_path, caplog):
    """
    A configuration with a mask we don't know (ie: %{datadir}) loads (the
    mask is said to be wrong), a %{ that isn't a mask is left alone, and
    %{ramdisk} is the processing: ramdisk setting
    """
    path = tmp_path / 'config.yaml'
    path.write_text(
        'global:\n'
        '   base_dir: %s\n'
        '   work_dir: "%%{datadir}/tmp"\n'
        'processing:\n'
        '   ramdisk: /dev/shm/nr\n'
        'database:\n'
        "   engine: 'sqlite:///%%{base_dir}/a%%{b.db'\n"
        'servers:\n'
        '  - host: localhost\n'
        '    username: me\n'
        '    password: "p%%{x"\n' % tmp_path)

    settings = NNTPSettings(cfg_file=str(path))
    assert settings.is_valid()
    assert settings.work_dir.endswith('%{datadir}/tmp')
    assert '%{datadir}' in caplog.text
    assert settings.nntp_database['engine'] == \
        'sqlite:///%s/a%%{b.db' % tmp_path
    assert settings.apply_mask('%{ramdisk}/x') == '/dev/shm/nr/x'


def write_config(tmp_path, text):
    """
    Writes a configuration holding one server (and `text`); returns its path
    """
    path = tmp_path / 'config.yaml'
    path.write_text(
        'global:\n'
        '   base_dir: %s\n'
        '%s'
        'servers:\n'
        '  - host: localhost\n'
        '    username: me\n' % (tmp_path, text))
    return str(path)


@pytest.mark.parametrize('text', [
    # An alias that was never defined
    'processing:\n   threads: *nothing\n',
    # A tag YAML can't build
    'processing:\n   threads: !!python/object:os.system x\n',
])
def test_a_broken_configuration_is_refused_not_a_crash(tmp_path, text,
                                                       caplog):
    """
    Whatever the YAML reader can't make sense of is an error saying so
    """
    settings = NNTPSettings(cfg_file=write_config(tmp_path, text))
    assert not settings.is_valid()
    assert 'Failed to interpret YAML configuration' in caplog.text


@pytest.mark.parametrize('engine', [
    'not a url at all', 'nosuchdatabase://x/y', 'postgresql+nosuchdriver://x/y'])
def test_a_database_that_cant_be_used_is_an_error(tmp_path, engine, caplog):
    """
    A database URL that isn't one (or whose driver isn't installed) is an
    error saying so, never a crash
    """
    settings = NNTPSettings(cfg_file=write_config(
        tmp_path, "database:\n   engine: '%s'\n" % engine))
    assert settings.session() is None
    assert "can't be used" in caplog.text


def test_the_main_database_is_a_file_unless_one_is_named(tmp_path):
    """
    Without a database engine, the main database is newsreap.db in our data
    directory (never one in memory), and a server's host can be a number
    """
    path = tmp_path / 'config.yaml'
    path.write_text(
        'global:\n   base_dir: %s\n'
        'servers:\n  - host: 1234\n    username: me\n' % tmp_path)
    settings = NNTPSettings(cfg_file=str(path))
    assert settings.is_valid()
    assert settings.nntp_servers[0]['host'] == '1234'
    assert settings.nntp_database['engine'] == \
        'sqlite:///%s' % (tmp_path / 'newsreap.db')
