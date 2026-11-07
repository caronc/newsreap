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


import os
import stat
import logging
from importlib import import_module

import pytest
from click.testing import CliRunner

from newsreap.Logging import NEWSREAP_ENGINE
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

    # And where the data and caches go (so nothing lands in our home)
    monkeypatch.setattr(
        settings_module, 'DEFAULT_DATA_DIR', str(tmp_path / 'share'))
    monkeypatch.setattr(
        settings_module, 'DEFAULT_CACHE_DIR', str(tmp_path / 'cache'))
    return paths


def test_starting_configuration_is_usable(config_paths, tmp_path):
    """
    The configuration written loads, and its directories are ready: in
    the usual place, each kind of file goes where the system expects it
    """
    path = create_config()
    assert path == config_paths[0]

    config_dir = os.path.dirname(path)
    data_dir = str(tmp_path / 'share')
    cache_dir = str(tmp_path / 'cache')
    assert os.path.isdir(data_dir)
    assert os.path.isdir(os.path.join(cache_dir, 'tmp'))

    # It holds a password so only its owner may read it
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600

    settings = NNTPSettings(cfg_file=path)
    assert settings.is_valid() is True
    assert (settings.config_dir, settings.data_dir, settings.cache_dir) == (
        config_dir, data_dir, cache_dir)
    assert settings.base_dir == data_dir
    assert settings.work_dir == os.path.join(cache_dir, 'tmp')
    assert settings.nntp_servers[0]['host'] == 'my.server.here'
    assert settings.nntp_servers[0]['secure'] is True
    assert settings.nntp_database['engine'] == \
        'sqlite:///%s' % os.path.join(data_dir, 'newsreap.db')
    assert settings.index_database() == os.path.join(data_dir, 'index.db')
    assert settings.group_database('a.b.test') == os.path.join(
        cache_dir, 'search', 'a.b.test.db')
    assert settings.filters_file() == os.path.join(config_dir, 'filters')

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


def test_configuration_elsewhere_keeps_everything_with_it(tmp_path):
    """
    A configuration written outside the usual place keeps what NewsReap
    stores in the same directory (its base_dir)
    """
    usenet = str(tmp_path / 'usenet')
    path = create_config(os.path.join(usenet, 'config.yaml'))
    with open(path) as fp:
        assert "   base_dir: '%s'\n" % usenet in fp.read()

    settings = NNTPSettings(cfg_file=path)
    assert (settings.base_dir, settings.data_dir, settings.cache_dir) == (
        usenet, usenet, os.path.join(usenet, 'cache'))
    assert settings.index_database() == os.path.join(usenet, 'index.db')
    assert settings.work_dir == os.path.join(usenet, 'cache', 'tmp')
    settings.close()


def test_each_kind_of_file_can_be_moved(tmp_path):
    """
    data_dir and cache_dir move what can't and what can be fetched again,
    on their own
    """
    path = create_config(str(tmp_path / 'conf' / 'config.yaml'))
    with open(path) as fp:
        content = fp.read()

    content = content.replace(
        "   # data_dir: '~/.local/share/newsreap'",
        "   data_dir: '%s'" % (tmp_path / 'keep')).replace(
        "   # cache_dir: '~/.cache/newsreap'",
        "   cache_dir: '%{config_dir}/caches'")
    with open(path, 'w') as fp:
        fp.write(content)

    settings = NNTPSettings(cfg_file=path)
    assert settings.data_dir == str(tmp_path / 'keep')
    assert settings.cache_dir == str(tmp_path / 'conf' / 'caches')
    assert settings.nntp_database['engine'] == \
        'sqlite:///%s' % (tmp_path / 'keep' / 'newsreap.db')
    settings.close()


def test_folders_follow_the_xdg_settings(tmp_path, monkeypatch):
    """
    XDG_CONFIG_HOME, XDG_DATA_HOME and XDG_CACHE_HOME are used when set
    (a relative one is ignored, as the specification says)
    """
    settings_module = import_module('newsreap.NNTPSettings')
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data'))
    monkeypatch.setenv('XDG_CACHE_HOME', 'relative/cache')
    monkeypatch.delenv('XDG_CONFIG_HOME', raising=False)

    assert settings_module.user_dir('data') == str(
        tmp_path / 'data' / 'newsreap')
    assert settings_module.user_dir('cache') == os.path.join(
        os.path.expanduser('~'), '.cache', 'newsreap')
    assert settings_module.user_dir('config') == os.path.join(
        os.path.expanduser('~'), '.config', 'newsreap')


def test_reading_another_configuration_uses_its_directories(tmp_path):
    """
    Settings read a second time take the new configuration's directories
    (in every setting that names one)
    """
    first = create_config(str(tmp_path / 'one' / 'config.yaml'))
    second = create_config(str(tmp_path / 'two' / 'config.yaml'))

    settings = NNTPSettings(cfg_file=first)
    assert settings.work_dir == str(tmp_path / 'one' / 'cache' / 'tmp')

    settings.read(second)
    assert settings.work_dir == str(tmp_path / 'two' / 'cache' / 'tmp')
    assert settings.nntp_database['engine'] == \
        'sqlite:///%s' % (tmp_path / 'two' / 'newsreap.db')
    settings.close()


def test_init_writes_an_example_filters_file(tmp_path):
    """
    nr --init makes the filters directory with an example of every rule
    setting; the example isn't read, and each rule in it is valid once
    its '# ' is removed
    """
    import yaml
    from newsreap.NNTPFilter import NNTPFilter

    path = create_config(str(tmp_path / 'conf' / 'config.yaml'))
    example = tmp_path / 'conf' / 'filters' / 'example.yaml.template'
    assert example.is_file()

    settings = NNTPSettings(cfg_file=path)
    assert settings.filters_file() == str(tmp_path / 'conf' / 'filters')
    assert len(settings.filters()) == 0
    settings.close()

    # Its rules, uncommented ('##' lines are only explanations)
    text = '\n'.join(
        line[2:] if line.startswith('# ') else ''
        for line in example.read_text().splitlines()
        if not line.startswith('##'))
    rules = yaml.safe_load(text)['filters']
    filters = NNTPFilter(rules=rules)
    assert len(filters) == len(rules) > 5
    assert {rule.action for rule in filters.values()} == {
        'score', 'keep', 'drop'}


def test_init_carries_on_without_a_filters_directory(tmp_path, caplog):
    """
    A filters directory that can't be made (ie: a file is in the way) is
    only warned about
    """
    caplog.set_level(logging.WARNING, logger=NEWSREAP_ENGINE)
    (tmp_path / 'conf').mkdir()
    (tmp_path / 'conf' / 'filters').write_text('in the way')
    path = create_config(str(tmp_path / 'conf' / 'config.yaml'))
    assert os.path.isfile(path)
    assert 'Could not prepare the filters directory' in caplog.text


def test_a_yml_configuration_is_found_too(tmp_path, monkeypatch):
    """
    Each standard place is checked for config.yaml, then config.yml
    """
    settings_module = import_module('newsreap.NNTPSettings')
    found = [path for path in settings_module.DEFAULT_CONFIG_FILE_PATHS
             if path.startswith(settings_module.DEFAULT_BASE_DIR)]
    assert found == [
        os.path.join(settings_module.DEFAULT_BASE_DIR, 'config.yaml'),
        os.path.join(settings_module.DEFAULT_BASE_DIR, 'config.yml')]

    # One found that way is used
    path = create_config(str(tmp_path / 'one' / 'config.yaml'))
    yml = str(tmp_path / 'one' / 'config.yml')
    os.rename(path, yml)
    monkeypatch.setattr(
        settings_module, 'DEFAULT_CONFIG_FILE_PATHS',
        (path, yml))
    settings = NNTPSettings()
    assert settings.cfg_file == yml
    settings.close()


def test_a_yml_filters_file_is_used_too(tmp_path):
    """
    filters.yml next to the configuration works as filters.yaml does
    """
    path = create_config(str(tmp_path / 'conf' / 'config.yaml'))
    (tmp_path / 'conf' / 'filters.yml').write_text(
        'filters:\n  - name: old\n    subject: x\n')
    settings = NNTPSettings(cfg_file=path)
    assert settings.filters_file() == str(tmp_path / 'conf' / 'filters.yml')
    assert list(settings.filters().keys()) == ['old']
    settings.close()


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


def test_init_names_the_download_directory(tmp_path, monkeypatch):
    """
    The configuration written names the Downloads directory found (so it
    is easy to see and change), and it is the one used
    """
    downloads = tmp_path / 'My Downloads'
    (tmp_path / 'xdg').mkdir()
    (tmp_path / 'xdg' / 'user-dirs.dirs').write_text(
        'XDG_DOWNLOAD_DIR="%s"\n' % downloads)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'xdg'))

    path = create_config(str(tmp_path / 'usenet' / 'config.yaml'))
    with open(path) as fp:
        assert "   download_dir: '%s'\n" % downloads in fp.read()

    settings = NNTPSettings(cfg_file=path)
    assert settings.download_dir() == str(downloads)
    settings.close()


def test_the_shipped_template_is_a_working_configuration():
    """
    The template 'nr --init' copies is valid YAML on its own (someone may
    copy it by hand), and both lines it fills in are there to find
    """
    import yaml
    from newsreap.NNTPSettings import CONFIG_TEMPLATE
    from newsreap.NNTPSettings import TEMPLATE_BASE_DIR
    from newsreap.NNTPSettings import TEMPLATE_DOWNLOAD_DIR

    with open(CONFIG_TEMPLATE) as fp:
        content = fp.read()

    assert isinstance(yaml.safe_load(content), dict)
    assert content.count(TEMPLATE_BASE_DIR) == 1
    assert content.count(TEMPLATE_DOWNLOAD_DIR) == 1


def test_masks_can_be_written_in_any_case(tmp_path):
    """
    %{CACHE_DIR} is the same as %{cache_dir}
    """
    settings = NNTPSettings(cfg_file=create_config(
        str(tmp_path / 'usenet' / 'config.yaml')))
    assert settings.apply_mask('%{CACHE_DIR}/x') == \
        settings.apply_mask('%{cache_dir}/x') == \
        os.path.join(settings.cache_dir, 'x')
    assert settings.apply_mask('%{Mine}', mask_map={'%{mine}': 'it'}) == 'it'
    settings.close()
