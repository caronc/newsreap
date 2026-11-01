# Tests creating a starting configuration (nr --init)
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

import os
import stat
from importlib import import_module

import pytest
from click.testing import CliRunner

from newsreap.NNTPSettings import NNTPSettings
from newsreap.NNTPSettings import create_config


@pytest.fixture
def config_paths(tmp_path, monkeypatch):
    """
    Points our standard configuration locations into a temporary directory
    """
    # The package shares its NNTPSettings name with the class, so we fetch
    # the modules themselves
    settings_module = import_module('newsreap.NNTPSettings')
    cli_module = import_module('newsreap.cli')

    paths = (
        str(tmp_path / 'config' / 'newsreap' / 'config.yaml'),
        str(tmp_path / '.newsreap' / 'config.yaml'),
    )
    monkeypatch.setattr(settings_module, 'DEFAULT_CONFIG_FILE_PATHS', paths)
    monkeypatch.setattr(cli_module, 'DEFAULT_CONFIG_FILE_PATHS', paths)
    return paths


def test_starting_configuration_is_usable(config_paths):
    """
    The configuration written loads, and its directories are ready
    """
    path = create_config()
    assert path == config_paths[0]

    base_dir = os.path.dirname(path)
    assert os.path.isdir(os.path.join(base_dir, 'var', 'tmp'))

    # It holds a password so only its owner may read it
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600

    settings = NNTPSettings(cfg_file=path)
    assert settings.is_valid() is True
    assert settings.base_dir == base_dir
    assert settings.work_dir == os.path.join(base_dir, 'var', 'tmp')
    assert settings.nntp_servers[0]['host'] == 'my.server.here'
    assert settings.nntp_servers[0]['secure'] is True
    assert settings.nntp_database['engine'] == \
        'sqlite:///%s' % os.path.join(base_dir, 'newsreap.db')

    # Only one server is set up; the backup is left as an example
    assert len(settings.nntp_servers) == 1
    settings.close()


def test_existing_configuration_is_never_replaced(config_paths):
    """
    Nothing changes when the configuration file is already there
    """
    path = config_paths[0]
    os.makedirs(os.path.dirname(path))
    with open(path, 'w') as fp:
        fp.write('mine')

    with pytest.raises(FileExistsError):
        create_config(path)

    with pytest.raises(FileExistsError):
        create_config()

    with open(path) as fp:
        assert fp.read() == 'mine'


def test_configuration_elsewhere_is_not_shadowed(config_paths):
    """
    A configuration found further down our search list is left in charge
    """
    other = config_paths[1]
    os.makedirs(os.path.dirname(other))
    with open(other, 'w') as fp:
        fp.write('mine')

    with pytest.raises(FileExistsError) as error:
        create_config()

    assert error.value.args[0] == other
    assert not os.path.exists(config_paths[0])


def test_existing_work_is_left_alone(tmp_path, config_paths):
    """
    Files already in the working directories are kept
    """
    path = str(tmp_path / 'usenet' / 'config.yaml')
    keep = tmp_path / 'usenet' / 'var' / 'tmp' / 'download.part'
    keep.parent.mkdir(parents=True)
    keep.write_bytes(b'half way there')

    assert create_config(path) == path
    assert keep.read_bytes() == b'half way there'


def test_init_from_the_command_line(tmp_path, config_paths):
    """
    nr --init creates the configuration once and refuses after that
    """
    from newsreap.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ['--init'], obj={})
    assert result.exit_code == 0
    assert 'nr db init' in result.output
    assert os.path.isfile(config_paths[0])

    result = runner.invoke(cli, ['--init'], obj={})
    assert result.exit_code == 1

    # A location of our own choosing has to be pointed to afterwards
    path = str(tmp_path / 'usenet' / 'config.yaml')
    result = runner.invoke(cli, ['--config', path, '--init'], obj={})
    assert result.exit_code == 0
    assert "nr --config '%s' db init" % path in result.output


def test_init_says_where_the_existing_configuration_is(config_paths, caplog):
    """
    Refusing to --init names the configuration file that was found
    """
    from newsreap.cli import cli

    other = config_paths[1]
    os.makedirs(os.path.dirname(other))
    with open(other, 'w') as fp:
        fp.write('mine')

    result = CliRunner().invoke(cli, ['--init'], obj={})
    assert result.exit_code == 1
    assert other in caplog.text
    assert 'NewsReap already uses that file' in caplog.text


def test_no_command_shows_help(config_paths):
    """
    Running nr with nothing to do shows how to use it
    """
    from newsreap.cli import cli

    result = CliRunner().invoke(cli, ['-v'], obj={})
    assert result.exit_code == 0
    assert '--init' in result.output


@pytest.mark.parametrize('option', ['--version', '-V'])
def test_version_is_shown(option):
    """
    nr --version (or -V) shows the version and exits without doing anything
    else
    """
    from newsreap import __version__
    from newsreap.cli import cli

    result = CliRunner().invoke(cli, [option, 'update', 'search'], obj={})
    assert result.exit_code == 0
    assert result.output.startswith('NewsReap v%s\n' % __version__)
    assert 'GPLv3' in result.output
