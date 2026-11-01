# Test the passwords posts are protected with
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

import json
import os
import shutil
import subprocess
import stat
import string

import pytest
from click.testing import CliRunner

from newsreap.HookManager import HookManager
from newsreap.NNTPPostFactory import NNTPPostFactory
from newsreap.NNTPPostFactory import PASSWORD_LENGTH
from newsreap.NNTPPostFactory import make_password
from newsreap.NNTPnzb import NNTPnzb
from tests.FakeNNTPServer import FakeNNTPServer
from tests.conftest import config_for


class Offline:
    """
    Stands in for a connection where nothing is sent
    """
    def __init__(self):
        self.hooks = HookManager()


def content(tmp_path):
    """
    Returns the path of something to post (a directory with a file in it)
    """
    path = tmp_path / 'Holiday.Photos'
    path.mkdir()
    (path / 'photo.jpg').write_bytes(os.urandom(2048))
    return str(path)


def test_made_up_passwords():
    """
    Made up passwords are 24 characters of upper and lower case letters,
    numbers and symbols (never whitespace), and differ every time
    """
    allowed = set(
        string.ascii_letters + string.digits + string.punctuation)

    # Short ones can't hold every kind of character
    for length in (0, 1, 2, 3):
        assert len(make_password(length)) == length

    passwords = {make_password() for _ in range(200)}
    assert len(passwords) == 200

    for password in passwords:
        assert len(password) == PASSWORD_LENGTH == 24
        assert set(password) <= allowed
        assert not any(c.isspace() for c in password)
        for kind in (string.ascii_uppercase, string.ascii_lowercase,
                     string.digits, string.punctuation):
            assert any(c in kind for c in password)


def test_passwords_are_kept_exactly(tmp_path):
    """
    The password is kept (exactly, spaces too) for a later upload, and only
    the user posting can read it
    """
    pf = NNTPPostFactory(connection=Offline())
    assert pf.load(content(tmp_path)) is True
    assert pf.load_password() is None

    assert pf.save_password(' my secret\tpass ') is True
    assert pf.load_password() == ' my secret\tpass '
    assert stat.S_IMODE(os.stat(pf.password_path).st_mode) == 0o600

    # Loading the same content again (ie: to upload later) still knows it
    again = NNTPPostFactory(connection=Offline())
    assert again.load(pf.path) is True
    assert again.load_password() == ' my secret\tpass '

    # Posting without a password forgets it
    assert pf.save_password(None) is True
    assert pf.load_password() is None


@pytest.mark.parametrize('args, configured, expected', [
    # Nothing asked for
    ([], None, None),
    # The password given
    (['--password', 'given pass'], None, 'given pass'),
    # From the configuration file
    ([], 'from config', 'from config'),
    # Asked not to (even though the configuration has one)
    (['--no-password'], 'from config', None),
    (['--password', 'given'], 'from config', 'given'),
    # An empty password is the same as none
    (['--password', ''], 'from config', None),
    ([], '', None),
    # A length only changes the passwords we make up
    ([], {'password': 'mine', 'password_length': 0}, 'mine'),
    (['--password', 'given'], {'password_length': 3}, 'given'),
    # A length of 0 is the same as no password
    ([], {'password': 'auto', 'password_length': 0}, None),
    (['--generate-password'], {'password_length': 0}, None),
])
def test_where_the_password_comes_from(
        tmp_path, monkeypatch, args, configured, expected):
    """
    nr post uses the password asked for, or else the one in the
    configuration file
    """
    used = run_prepare(tmp_path, monkeypatch, args, configured)
    assert used == [expected]


@pytest.mark.parametrize('args, configured, length', [
    (['--generate-password'], None, 24),
    ([], 'auto', 24),
    ([], ' AUTO ', 24),
    # Their length can be changed
    ([], {'password': 'auto', 'password_length': 10}, 10),
    (['-G'], {'password_length': '127'}, 127),
    (['-G'], {'password_length': 1}, 1),
    (['-G'], {'password_length': None}, 24),
])
def test_passwords_can_be_made_up(tmp_path, monkeypatch, args, configured,
                                  length):
    """
    nr post makes up a password when asked to (or when the configuration
    says 'auto'), password_length characters long
    """
    used = run_prepare(tmp_path, monkeypatch, args, configured)
    assert len(used) == 1
    assert len(used[0]) == length


@pytest.mark.parametrize('length', [-1, -24, 'abc', '1.5', 128, True])
@pytest.mark.parametrize('args', [[], ['--no-password']])
def test_bad_password_lengths_are_refused(tmp_path, monkeypatch, length,
                                         args):
    """
    A password_length that isn't a number from 0 to 127 stops nr post
    before anything is done
    """
    assert run_prepare(
        tmp_path, monkeypatch, args, {'password_length': length},
        exit_code=1) == []


@pytest.mark.parametrize('args', [
    ['--password', 'a', '--no-password'],
    ['--password', 'a', '--generate-password'],
    ['--generate-password', '--no-password'],
])
def test_conflicting_password_options_are_refused(tmp_path, monkeypatch,
                                                  args):
    """
    Only one way of choosing the password can be used (and never an empty
    one)
    """
    assert run_prepare(
        tmp_path, monkeypatch, args, None, exit_code=1) == []


def run_prepare(tmp_path, monkeypatch, args, configured, exit_code=0):
    """
    Runs 'nr post --prep' and returns the passwords it prepared with

    - configured: the posting password of the configuration file, or a
      dict of posting settings
    """
    from newsreap.cli import cli

    used = []
    monkeypatch.setattr(
        NNTPPostFactory, 'prepare',
        lambda self, archive_size=None, password=None:
        used.append(password) or True)

    with FakeNNTPServer() as server:
        config = config_for(server, tmp_path / 'config.yaml')
        if configured is not None:
            # Just a password, or any posting settings
            settings = configured if isinstance(configured, dict) \
                else {'password': configured}
            with open(config, 'a') as f:
                f.write('\nposting:\n%s' % ''.join(
                    '    %s: %s\n' % (key, json.dumps(value))
                    for key, value in settings.items()))

        runner = CliRunner()
        assert runner.invoke(
            cli, ['--config', config, 'db', 'init'], obj={}).exit_code == 0
        result = runner.invoke(
            cli, ['--config', config, 'post', '--prep', *args,
                  content(tmp_path)], obj={})

    assert result.exit_code == exit_code, result.output
    return used


def test_the_password_goes_into_the_nzb_file(tmp_path):
    """
    The password the archives were made with is written into the NZB file
    when the post is uploaded
    """
    from newsreap.cli import cli

    path = content(tmp_path)

    # As the prep step leaves it
    pf = NNTPPostFactory(connection=Offline())
    assert pf.load(path) is True
    assert pf.save_password('sec ret!') is True

    with FakeNNTPServer() as server:
        config = config_for(server, tmp_path / 'config.yaml')
        runner = CliRunner()
        assert runner.invoke(
            cli, ['--config', config, 'db', 'init'], obj={}).exit_code == 0
        result = runner.invoke(
            cli, ['--config', config, 'post', '--stage', '--upload', '-g',
                  'alt.binaries.test', path], obj={})
        assert result.exit_code == 0, result.output

    nzb = NNTPnzb('%s.nzb' % path)
    assert len(list(nzb)) == 1
    assert nzb.meta == {'password': 'sec ret!'}


def test_failed_archiving_stops_cleanly(tmp_path, monkeypatch):
    """
    If the archive can't be made (ie: rar is missing), preparing stops
    with an error instead of crashing (and the password is still kept)
    """
    from newsreap.codecs.CodecRar import CodecRar

    monkeypatch.setattr(CodecRar, 'encode', lambda self, *a, **kw: None)
    pf = NNTPPostFactory(connection=Offline())
    assert pf.load(content(tmp_path)) is True
    assert pf.prepare(password='kept') is False
    assert pf.load_password() == 'kept'


@pytest.mark.skipif(
    not (shutil.which('rar') and shutil.which('unrar')),
    reason='rar and/or unrar are not installed')
def test_archives_need_the_password(tmp_path):
    """
    The archives made for a post only open with its password (spaces and
    symbols included)
    """
    password = 'with space %s' % make_password()
    pf = NNTPPostFactory(connection=Offline())
    assert pf.load(content(tmp_path)) is True
    assert pf.prepare(password=password) is True

    archives = sorted(
        name for name in os.listdir(pf.prep_path) if name.endswith('.rar'))
    assert archives
    archive = os.path.join(pf.prep_path, archives[0])

    def opens(*args):
        return subprocess.run(
            ['unrar', 't', '-y', *args, archive],
            capture_output=True).returncode == 0

    assert opens('-p%s' % password) is True
    assert opens('-pwrong') is False
