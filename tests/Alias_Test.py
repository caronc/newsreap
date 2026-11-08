# Test aliases and the watch list from the command line
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


import pytest
from click.testing import CliRunner

from tests.FakeNNTPServer import FakeNNTPServer
from tests.conftest import config_for

TEST = 'alt.binaries.test'
OTHER = 'alt.binaries.other'


@pytest.fixture
def nr(tmp_path):
    """
    Runs nr against a test server that lists two groups (nothing is set
    up until 'db init' is run)
    """
    from newsreap.cli import cli

    group_list = tmp_path / 'group.list'
    group_list.write_text('%s 0000000002 0000000001 y\n'
                          '%s 0000000002 0000000001 y\n' % (TEST, OTHER))

    with FakeNNTPServer(group_list=str(group_list)) as server:
        config = config_for(server, tmp_path / 'config.yaml')
        runner = CliRunner()

        def run(*args, **kwargs):
            return runner.invoke(cli, ['--config', config, *args], obj={},
                                 **kwargs)

        yield run


def aliases(nr):
    """
    Returns {alias: [its groups]} as 'nr alias ls' shows them
    """
    found, alias = {}, None
    for line in nr('alias', 'ls').output.splitlines():
        if line.endswith(':'):
            alias = line[:-1]
            found[alias] = []
        elif line.strip().startswith('- '):
            found[alias].append(line.strip()[2:])
    return found


def test_listing_aliases_before_the_database_is_set_up(nr):
    """
    Says to set up the database (instead of failing with a traceback)
    """
    result = nr('alias', 'ls')
    assert result.exit_code == 1
    assert "run 'nr db init' first" in result.output
    assert 'Traceback' not in result.output


def test_removing_a_group_from_one_alias_leaves_the_others(nr):
    """
    'alias rm <alias> <group>' only takes the group out of that alias;
    a group that isn't in it, or an alias that doesn't exist, is an error
    """
    assert nr('db', 'init').exit_code == 0
    assert nr('group', 'refresh').exit_code == 0
    assert nr('alias', 'add', 'test', TEST).exit_code == 0
    assert nr('alias', 'add', 'both', TEST, OTHER).exit_code == 0

    assert nr('alias', 'rm', 'test', TEST).exit_code == 0
    assert aliases(nr) == {'both': [OTHER, TEST]}

    assert nr('alias', 'rm', 'both', 'alt.binaries.nosuch').exit_code == 1

    # What can be removed still is; the rest is an error
    assert nr('alias', 'add', 'two', TEST, OTHER).exit_code == 0
    result = nr('alias', 'rm', 'two', OTHER, 'alt.binaries.nosuch')
    assert result.exit_code == 1
    assert "'alt.binaries.nosuch'" in result.output
    assert aliases(nr)['two'] == [TEST]
    assert nr('alias', 'rm', 'two').exit_code == 0
    assert nr('alias', 'rm', 'nosuch').exit_code == 1

    assert nr('alias', 'add', 'one', TEST).exit_code == 0
    result = nr('alias', 'rm', 'one', OTHER)
    assert result.exit_code == 1
    assert "isn't part of the alias 'one'" in result.output
    assert aliases(nr)['one'] == [TEST]


def test_names_that_match_nothing_are_refused(nr):
    """
    A typo in a group name adds (or watches) nothing, and says so
    """
    assert nr('db', 'init').exit_code == 0
    assert nr('group', 'refresh').exit_code == 0

    result = nr('alias', 'add', 't', TEST, 'alt.binaries.typo')
    assert result.exit_code == 1
    assert "'alt.binaries.typo'" in result.output
    assert aliases(nr) == {}

    assert nr('group', 'watch', TEST, 'alt.binaries.typo').exit_code == 1


def test_unwatching_skips_a_typo(nr):
    """
    - 'group unwatch' still removes the groups it knows, and names the
      typo as an error
    - 'group ls' says to set up the database when it isn't
    """
    result = nr('group', 'ls')
    assert result.exit_code == 1
    assert "run 'nr db init' first" in result.output

    assert nr('db', 'init').exit_code == 0
    assert nr('group', 'refresh').exit_code == 0
    assert nr('group', 'watch', TEST, OTHER).exit_code == 0
    listed = nr('group', 'ls').output.splitlines()
    assert [line.split()[:2] for line in listed] == [['*', OTHER],
                                                     ['*', TEST]]

    result = nr('group', 'set', TEST, '--retention', '30', '--nzb', 'yes')
    assert result.exit_code == 0
    assert '%s: nzb, keeps 30 days' % TEST in result.output
    assert '(nzb, keeps 30 days)' in nr('group', 'ls').output

    result = nr('group', 'unwatch', TEST, 'alt.binaries.typo')
    assert result.exit_code == 1
    assert "'alt.binaries.typo'" in result.output
    listed = nr('group', 'ls').output
    assert TEST not in listed and OTHER in listed


def test_resetting_the_database_asks_first(nr):
    """
    'db reset' says what it removes and asks; no keeps everything
    """
    assert nr('db', 'init').exit_code == 0
    assert nr('group', 'refresh').exit_code == 0
    assert nr('alias', 'add', 'test', TEST).exit_code == 0

    result = nr('db', 'reset', input='n\n')
    assert 'Nothing was removed.' in result.output
    assert aliases(nr) == {'test': [TEST]}

    assert nr('-y', 'db', 'reset').exit_code == 0
    assert aliases(nr) == {}


def test_optimizing_compacts_every_sqlite_database(nr, tmp_path):
    """
    'db optimize' compacts the settings database and the header caches,
    and says how much it gave back
    """
    assert nr('db', 'init').exit_code == 0
    assert nr('group', 'refresh').exit_code == 0

    result = nr('db', 'optimize')
    assert result.exit_code == 0, result.output
    assert 'given back.' in result.output
    assert '->' in result.output
