# Test decrypting PGP posts with gpg (nr pgp import/passphrase/decrypt)
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
import stat
import time
from datetime import timedelta
from os import stat as os_stat
from shutil import which

import pytest
from click.testing import CliRunner

from newsreap.Logging import NEWSREAP_ENGINE
from newsreap.NNTPArchivePost import NNTPArchivePost
from newsreap.NNTPArchiver import NNTPArchiver
from newsreap.NNTPGnuPG import NNTPGnuPG
from newsreap.NNTPKeyring import NNTPKeyring
from newsreap.objects.index.ArchivedPost import ArchiveKind
from newsreap.objects.index.ArchiveFile import ArchiveFile
from newsreap.objects.index.PGPPost import HIDDEN_KEY_ID
from newsreap.objects.index.PGPPost import PGPRole
from newsreap.objects.index.PGPWait import WaitReason

from tests.CodecPGP_Test import sample
from tests.FakeNNTPServer import FakeNNTPServer
from tests.NNTPArchiver_Test import GROUP
from tests.NNTPArchiver_Test import POSTED
from tests.NNTPArchiver_Test import article
from tests.NNTPArchiver_Test import index_engine  # noqa: F401
from tests.NNTPKeyring_Test import ties
from tests.conftest import config_for
from tests.conftest import run_program

pytestmark = pytest.mark.skipif(
    not which('gpg'), reason='gpg is not installed')

# The test key whose secret half is kept (reader-secret.asc)
READER_KEY_ID = '798B51E20D4081A0'
READER_FINGERPRINT = 'D2DA79DA100A3A8A71217655798B51E20D4081A0'
READER_SUBKEY_ID = '6D4022DDC7182E36'
READER_USER_ID = 'NewsReap Reader <reader@newsreap.example>'
READER_PASSPHRASE = 'open sesame'

# What the posts for it say
READER_TEXT = 'Meet at the usual place at noon.'
HIDDEN_TEXT = 'Nobody else may know.'

# Posts for it (and one for someone else)
POSTS = {
    'reader@x': article(sample('for-reader.asc'), Subject='For the reader'),
    'hidden@x': article(sample('for-reader-hidden.asc'), Subject='Hidden'),
    'file@x': article(sample('for-reader-file.asc'), Subject='A file'),
    'other@x': article(sample('encrypted.asc'), Subject='For another'),
}


def stop_agent(home):
    """
    Stops the gpg-agent gpg started for a home folder
    """
    run_program(['gpgconf', '--homedir', str(home), '--kill', 'gpg-agent'])


@pytest.fixture
def gpg(tmp_path):
    """
    A gpg (with its own folder) holding the reader's secret key; no
    passphrase is kept for it
    """
    gpg = NNTPGnuPG(str(tmp_path / 'gpg'))
    assert gpg.import_keys(sample('reader-secret.asc')) == [
        READER_FINGERPRINT]
    yield gpg
    stop_agent(gpg.home)


def keep(index_engine, gpg, posts=POSTS):
    """
    Returns an archiver (with pgp on) that has converted and kept the posts
    given (Message-ID -> article)
    """
    archiver = NNTPArchiver(index_engine, pgp=True, gpg=gpg)
    session = archiver.session(create=True)
    for no, (message_id, raw) in enumerate(posts.items()):
        post = archiver.convert(NNTPArchivePost(
            message_id, raw=raw, posted=POSTED + timedelta(minutes=no),
            groups={GROUP}, size=len(raw)))
        archiver.store(session, post)
        archiver._count(post)
    session.commit()
    return archiver


def test_secret_keys_are_held_by_their_ids(gpg):
    """
    The key and its subkey both lead to the key's fingerprint; the public
    half can be handed on
    """
    assert gpg.secret_keys() == {
        READER_KEY_ID: READER_FINGERPRINT,
        READER_SUBKEY_ID: READER_FINGERPRINT}
    assert gpg.holds([READER_SUBKEY_ID, 'AAAAAAAAAAAAAAAA']) == [
        READER_FINGERPRINT]
    assert gpg.holds([HIDDEN_KEY_ID]) == [READER_FINGERPRINT]
    assert gpg.holds(['AAAAAAAAAAAAAAAA']) == []

    keys = NNTPKeyring.read_keys(gpg.export(READER_FINGERPRINT))
    assert [(k['key_id'], k['user_ids']) for k in keys] == [
        (READER_KEY_ID, [READER_USER_ID])]


def test_posts_for_our_key_are_decrypted_as_they_are_kept(
        index_engine, gpg):
    """
    With the passphrase kept, a post for our key keeps what it says (and
    is tied to who signed it inside), a hidden recipient is tried with our
    key, and a file inside is noted by its real name
    """
    gpg.save_passphrase(READER_FINGERPRINT, READER_PASSPHRASE)
    archiver = keep(index_engine, gpg)
    session = archiver.session()

    post = archiver.get('reader@x')
    assert post.text == READER_TEXT
    assert post.kind == ArchiveKind.Text
    assert post.encoding == 'pgp'
    assert archiver.get('hidden@x').text == HIDDEN_TEXT

    files = archiver.files('file@x')
    assert [(f.filename, f.size) for f in files] == [('secret-plan.bin', 16)]
    assert archiver.get('file@x').kind == ArchiveKind.Binary

    # The one for someone else stays as it was
    assert archiver.get('other@x').text is None
    assert [f.content_type for f in archiver.files('other@x')] == [
        'application/pgp-encrypted']

    assert ('reader@x', READER_KEY_ID, PGPRole.Signed) in ties(session)
    assert ('hidden@x', HIDDEN_KEY_ID, PGPRole.EncryptedFor) in ties(session)
    assert (archiver.decrypted, archiver.locked) == (3, 0)
    assert NNTPKeyring.locked(session, gpg.secret_keys()) == set()

    # Searchable by what it says
    assert [p.message_id for p in archiver.find(['usual place'])] == [
        'reader@x']
    archiver.close()


def test_posts_wait_when_the_passphrase_is_not_known(
        index_engine, gpg, caplog):
    """
    Without the passphrase gpg isn't left waiting for one: the posts are
    kept as they were (still encrypted), noted as waiting and logged
    (shown with -v)
    """
    caplog.set_level(logging.WARNING, logger=NEWSREAP_ENGINE)
    started = time.monotonic()
    archiver = keep(index_engine, gpg)
    assert time.monotonic() - started < gpg.timeout
    assert "<reader@x> could not be decrypted (passphrase); the next 'nr " \
        "update' tries it again." in caplog.text

    session = archiver.session()
    assert archiver.get('reader@x').text is None
    assert (archiver.decrypted, archiver.locked) == (0, 3)
    assert NNTPKeyring.locked(session, gpg.secret_keys()) == {
        'reader@x', 'hidden@x', 'file@x'}
    assert 'still locked' in str(archiver)

    # Only what is asked about
    assert NNTPKeyring.locked(
        session, gpg.secret_keys(), ['reader@x', 'other@x']) == {'reader@x'}

    # A wrong passphrase doesn't help
    gpg.save_passphrase(READER_FINGERPRINT, 'not it')
    result = gpg.decrypt(sample('for-reader.asc'), [READER_SUBKEY_ID])
    assert not result
    assert result.reason == 'bad passphrase'
    archiver.close()


def test_without_gpg_nothing_is_decrypted(index_engine, tmp_path):
    """
    A gpg that isn't installed decrypts nothing (and nothing waits on it)
    """
    gpg = NNTPGnuPG(
        str(tmp_path / 'gpg'), binary=str(tmp_path / 'no-such-gpg'))
    assert not gpg.available()
    archiver = keep(index_engine, gpg)
    assert archiver.get('reader@x').text is None
    assert (archiver.decrypted, archiver.locked) == (0, 0)
    archiver.close()


def test_passphrases_are_checked_and_kept_privately(gpg):
    """
    A passphrase is only right if it unlocks the key, even while gpg-agent
    remembers the right one; it is kept in a file only we can read
    """
    assert not gpg.check_passphrase(READER_FINGERPRINT, 'not it')
    assert gpg.check_passphrase(READER_FINGERPRINT, READER_PASSPHRASE)

    # gpg-agent remembers the right one now
    assert not gpg.check_passphrase(READER_FINGERPRINT, 'not it')

    assert gpg.passphrase_file(READER_FINGERPRINT) is None
    gpg.save_passphrase(READER_FINGERPRINT, READER_PASSPHRASE)
    path = gpg.passphrase_file(READER_FINGERPRINT)
    assert stat.S_IMODE(os_stat(path).st_mode) == 0o600
    assert gpg.forget_passphrase(READER_FINGERPRINT)
    assert not gpg.forget_passphrase(READER_FINGERPRINT)


def test_gpg_that_stops_answering_is_stopped(tmp_path):
    """
    A gpg that never finishes is stopped after the timeout, and one that
    writes too much is stopped once it passes the limit
    """
    sleeper = tmp_path / 'gpg'
    sleeper.write_text('#!/bin/sh\nexec sleep 60\n')
    sleeper.chmod(0o700)

    gpg = NNTPGnuPG(str(tmp_path / 'home'), binary=str(sleeper), timeout=1)
    started = time.monotonic()
    code, _, _ = gpg._run(['--decrypt'], data=b'data')
    assert code == 'timeout'
    assert time.monotonic() - started < 10

    flood = tmp_path / 'flood'
    flood.write_text('#!/bin/sh\nexec yes\n')
    flood.chmod(0o700)

    gpg = NNTPGnuPG(str(tmp_path / 'home'), binary=str(flood))
    code, output, _ = gpg._run(['--decrypt'], limit=1000)
    assert code is None
    assert len(output) == 1001

    assert NNTPGnuPG._read('timeout', b'', [], False).reason == 'timeout'
    assert NNTPGnuPG._read(None, b'', [], False).reason == 'too big'


@pytest.fixture
def server(tmp_path):
    """
    A test server whose group holds the posts for the reader
    """
    group_list = tmp_path / 'group.list'
    group_list.write_text('%s 0000000004 0000000001 y\n' % GROUP)

    with FakeNNTPServer(group_list=str(group_list),
                        join_group=False) as server:
        server.add_group(GROUP, 0, 1, len(POSTS))
        for no, (message_id, raw) in enumerate(POSTS.items(), start=1):
            path = tmp_path / ('%d.msg' % no)
            path.write_bytes(raw)
            server.add_article(message_id, str(path), GROUP)
            server.add_overview(
                GROUP, no, 'Subject %d' % no, 'poster@example.com',
                POSTED + timedelta(minutes=no), message_id, size=len(raw))
        yield server


def test_decrypting_from_the_command_line(tmp_path, server):
    """
    A secret key is imported, the posts for it wait until its passphrase
    is given, and 'nr pgp decrypt' then reads them
    """
    from newsreap.cli import cli

    config = config_for(server, tmp_path / 'config.yaml', pgp='yes')
    runner = CliRunner()

    def nr(*args, **kwargs):
        return runner.invoke(cli, ['--config', config, *args], obj={},
                             **kwargs)

    secret = tmp_path / 'secret.asc'
    secret.write_bytes(sample('reader-secret.asc'))
    home = tmp_path / 'pgp'
    try:
        assert nr('db', 'init').exit_code == 0
        assert nr('group', 'refresh').exit_code == 0

        result = nr('pgp', 'import', str(secret))
        assert result.exit_code == 0, result.output
        assert 'Kept %s %s (secret key)' % (
            READER_KEY_ID, READER_USER_ID) in result.output
        assert 'secret key held' in nr('pgp', 'list').output

        # No passphrase yet: kept, but waiting
        result = nr('update', GROUP, '--content')
        assert result.exit_code == 0, result.output
        assert '3 still locked' in result.output
        assert 'Not decrypted yet' in nr('db', 'status').output
        assert 'no passphrase kept; 3 post(s) not decrypted yet' in nr(
            'pgp', 'show', READER_KEY_ID).output

        # A wrong one isn't kept
        result = nr('pgp', 'passphrase', 'reader', input='not it\n')
        assert result.exit_code == 1
        assert "doesn't unlock" in result.output

        result = nr('pgp', 'passphrase', 'reader',
                    input=READER_PASSPHRASE + '\n')
        assert result.exit_code == 0, result.output
        assert '3 kept post(s) are waiting for it' in result.output

        start = len(server.commands())
        result = nr('pgp', 'decrypt')
        assert result.exit_code == 0, result.output
        assert '3 post(s) waiting: 3 decrypted, 0 still locked.' in \
            result.output
        assert asked(server, start) == {'reader@x', 'hidden@x', 'file@x'}

        assert 'No kept post is waiting' in nr('pgp', 'decrypt').output
        result = nr('search', '--posts', 'usual place')
        assert '<reader@x>' in result.output

        assert 'Forgot the passphrase' in nr(
            'pgp', 'passphrase', READER_KEY_ID, '--forget').output

    finally:
        stop_agent(home)


def command_line(tmp_path, server, **processing):
    """
    Returns a function running nr (with pgp on) against the test server,
    with the reader's secret key imported
    """
    from newsreap.cli import cli

    config = config_for(
        server, tmp_path / 'config.yaml', pgp='yes', **processing)
    runner = CliRunner()

    def nr(*args, **kwargs):
        return runner.invoke(cli, ['--config', config, *args], obj={},
                             **kwargs)

    secret = tmp_path / 'secret.asc'
    secret.write_bytes(sample('reader-secret.asc'))
    assert nr('db', 'init').exit_code == 0
    assert nr('group', 'refresh').exit_code == 0
    assert nr('pgp', 'import', str(secret)).exit_code == 0
    return nr


def asked(server, start=0):
    """
    Returns the articles asked of the server since its `start`th command
    (a slow answer can make a connection ask for one twice)
    """
    return {c.split('<', 1)[1].rstrip('>') for c in server.commands()[start:]
            if c.startswith('ARTICLE <')}


def waits(tmp_path):
    """
    Returns why each waiting post still waits, and how often it was tried
    again: Message-ID -> (reason, tries)
    """
    archiver = NNTPArchiver(str(tmp_path / 'index.db'), pgp=True)
    try:
        return {m: (w.reason, w.tries) for m, w in NNTPKeyring.waits(
            archiver.session(), ['reader@x', 'hidden@x', 'file@x']).items()}

    finally:
        archiver.close()


def snapshot(tmp_path):
    """
    Returns what the index holds of the test posts (to understand a failed
    retry test): every key tie, the posts waiting and why, and which of
    the group's cached articles are marked as kept
    """
    from newsreap.NNTPGroupDatabase import NNTPGroupDatabase
    from newsreap.objects.group.Article import Article
    from newsreap.objects.index.PGPPost import PGPPost
    gpg = NNTPGnuPG(str(tmp_path / 'pgp'))
    archiver = NNTPArchiver(str(tmp_path / 'index.db'), pgp=True)
    cache = NNTPGroupDatabase.from_file(
        str(tmp_path / 'cache' / 'search' / ('%s.db' % GROUP)))
    try:
        session = archiver.session()
        return {
            'secret': gpg.secret_keys(),
            'ties': sorted((t.message_id, t.key_id, t.role)
                           for t in session.query(PGPPost)),
            'locked': sorted(NNTPKeyring.locked(
                session, gpg.secret_keys())),
            'waits': waits(tmp_path),
            'archived': sorted((a.message_id, a.archived)
                               for a in cache.session().query(Article)),
        }

    finally:
        cache.close()
        archiver.close()


def recorded(nr, server, tmp_path):
    """
    Returns (update, history): update() runs 'nr -vv update <group>
    --content' and notes what it said, what was asked of the server and
    what the index held after it (to understand a failed retry test); the
    -vv logs what each fetch got back and what was kept
    """
    history = []

    def update():
        start = len(server.commands())
        result = nr('-vv', 'update', GROUP, '--content')
        history.append((result.output, server.commands()[start:],
                        snapshot(tmp_path)))
        return result

    return update, history


def explained(result, history):
    """
    Fails the test, showing the last output and every step before it
    """
    from pprint import pformat
    pytest.fail('%s\n%s' % (result.output, pformat(history)))


def test_locked_posts_are_tried_again_a_limited_number_of_times(
        tmp_path, server):
    """
    The next 'nr update' fetches the posts that couldn't be decrypted
    again, up to pgp_retries times; a new passphrase gives them their
    tries back
    """
    nr = command_line(tmp_path, server, pgp_retries=2)
    update, history = recorded(nr, server, tmp_path)
    try:
        result = update()
        assert result.exit_code == 0, result.output
        assert '3 still locked' in result.output
        assert 'tried again' not in result.output
        assert set(waits(tmp_path).values()) == {(WaitReason.Passphrase, 0)}

        # Tried again twice (nothing has changed, so still locked)
        for tries in (1, 2):
            start = len(server.commands())
            result = update()
            assert result.exit_code == 0, result.output
            assert '3 locked post(s) tried again' in result.output
            assert asked(server, start) == {'reader@x', 'hidden@x', 'file@x'}
            assert set(waits(tmp_path).values()) == {
                (WaitReason.Passphrase, tries)}

        assert '3 no longer tried on their own' in result.output
        assert '3 no longer tried by nr update' in nr('db', 'status').output

        # Then left alone
        start = len(server.commands())
        result = update()
        assert 'tried again' not in result.output
        assert asked(server, start) == set()

        # A passphrase gives them another go
        result = nr('pgp', 'passphrase', 'reader',
                    input=READER_PASSPHRASE + '\n')
        assert result.exit_code == 0, result.output
        assert 'nr update tries them again' in nr('db', 'status').output

        history.append(('after the passphrase', [], snapshot(tmp_path)))
        start = len(server.commands())
        result = update()
        assert result.exit_code == 0, result.output
        if '3 locked post(s) tried again, 3 decrypted' not in result.output:
            explained(result, history)
        assert asked(server, start) == {'reader@x', 'hidden@x', 'file@x'}
        assert waits(tmp_path) == {}
        assert '<reader@x>' in nr('search', '--posts', 'usual place').output

    finally:
        stop_agent(tmp_path / 'pgp')


def test_answers_that_cannot_change_are_given_up_on(tmp_path, server):
    """
    A post the server no longer has (or won't let us have) isn't fetched
    again on its own, even after a new passphrase; 'nr pgp decrypt --all'
    still tries it
    """
    nr = command_line(tmp_path, server, pgp_retries=5)
    update, history = recorded(nr, server, tmp_path)

    try:
        assert '3 still locked' in update().output

        server.on(r'^ARTICLE <reader@x>$', '430 No such article')
        server.on(r'^ARTICLE <file@x>$', '502 Access denied')

        result = update()
        assert result.exit_code == 0, result.output
        assert '3 locked post(s) tried again' in result.output, history
        assert '2 no longer tried on their own' in result.output
        assert waits(tmp_path) == {
            'reader@x': (WaitReason.Gone, 1),
            'file@x': (WaitReason.Denied, 1),
            'hidden@x': (WaitReason.Passphrase, 1)}

        # Only the one a passphrase can help is tried again
        assert nr('pgp', 'passphrase', 'reader',
                  input=READER_PASSPHRASE + '\n').exit_code == 0
        history.append(('after the passphrase', [], snapshot(tmp_path)))
        start = len(server.commands())
        result = update()
        if '1 locked post(s) tried again, 1 decrypted' not in result.output:
            explained(result, history)
        assert asked(server, start) == {'hidden@x'}

        start = len(server.commands())
        result = nr('pgp', 'decrypt')
        assert '2 post(s) given up on are left out' in result.output
        assert 'No kept post is waiting' in result.output
        assert asked(server, start) == set()

        result = nr('pgp', 'decrypt', '--all')
        assert '2 post(s) waiting: 0 decrypted, 0 still locked, 2 could ' \
            'not be fetched.' in result.output
        assert asked(server, start) == {'reader@x', 'file@x'}

    finally:
        stop_agent(tmp_path / 'pgp')


def test_hidden_posts_for_someone_else_are_given_up_on(
        index_engine, gpg, tmp_path):
    """
    A post whose recipient is hidden is tried with our keys; when none of
    them open it, it wasn't for us and isn't tried again
    """
    # Someone else's key, and a message to them that hides who it is for
    home = tmp_path / 'other'
    home.mkdir(mode=0o700)
    other = ['gpg', '--homedir', str(home), '--batch', '--quiet',
             '--pinentry-mode', 'loopback']
    run_program(
        [*other, '--passphrase', '', '--quick-gen-key',
         'Other <other@newsreap.example>', 'default', 'default', 'never'],
        check=True)
    message = run_program(
        [*other, '--armor', '--trust-model', 'always', '--encrypt',
         '--hidden-recipient', 'other@newsreap.example'],
        input=b'Not for the reader.', check=True).stdout
    stop_agent(home)

    # Without our passphrase, gpg can't tell; it waits like any other
    posts = {'elsewhere@x': article(message, Subject='Hidden for another')}
    archiver = keep(index_engine, gpg, posts)
    assert NNTPKeyring.waits(archiver.session(), ['elsewhere@x'])[
        'elsewhere@x'].reason == WaitReason.Passphrase
    archiver.close()

    # With it, our key opens but the message isn't for it
    gpg.save_passphrase(READER_FINGERPRINT, READER_PASSPHRASE)
    archiver = keep(index_engine, gpg, posts)
    session = archiver.session()
    assert (archiver.locked, archiver.given_up) == (0, 0)
    assert NNTPKeyring.waits(session, ['elsewhere@x'])[
        'elsewhere@x'].reason == WaitReason.NoKey
    assert NNTPKeyring.locked(session, gpg.secret_keys(), final=False) == set()
    assert NNTPKeyring.locked(session, gpg.secret_keys()) == {'elsewhere@x'}

    # A new key could be the one it is for, so it is given another go
    assert NNTPKeyring.renew(session, ['elsewhere@x']) == 1
    session.commit()
    assert NNTPKeyring.locked(
        session, gpg.secret_keys(), retries=1, final=False,
        group=GROUP) == {'elsewhere@x'}
    assert NNTPKeyring.locked(
        session, gpg.secret_keys(), group='alt.elsewhere') == set()
    archiver.close()


def test_secret_keys_need_pgp_on(tmp_path, server):
    """
    With pgp off, a secret key is refused (and says how to turn it on)
    """
    from newsreap.cli import cli

    config = config_for(server, tmp_path / 'config.yaml')
    secret = tmp_path / 'secret.asc'
    secret.write_bytes(sample('reader-secret.asc'))
    result = CliRunner().invoke(
        cli, ['--config', config, 'pgp', 'import', str(secret)], obj={})
    assert result.exit_code == 1
    assert "only used with 'pgp: yes'" in result.output
    assert not (tmp_path / 'pgp').exists()


def test_still_encrypted_files_are_noted(index_engine, gpg):
    """
    A post that is still encrypted is noted by an encrypted file (that is
    what marks it as waiting)
    """
    archiver = keep(index_engine, gpg, {'reader@x': POSTS['reader@x']})
    session = archiver.session()
    assert [(f.codec, f.content_type) for f in session.query(ArchiveFile)
            .filter(ArchiveFile.message_id == 'reader@x')] == [
        ('pgp', 'application/pgp-encrypted')]
    archiver.close()


def test_programs_are_started_without_forking_us(tmp_path, monkeypatch):
    """
    gpg (and every other program) is started without a copy of our own
    process being made first: in such a copy our other connections could
    run for a moment and send their commands again on the sockets they
    share with us (mixing one article's reply into the next)
    """
    import gevent.os
    import gevent.subprocess
    import os
    from newsreap.SubProcess import SubProcess
    from newsreap.Utils import Popen
    from newsreap.Utils import start_program
    from newsreap.Utils import wait_program

    def forked(*args, **kwargs):
        raise AssertionError('our process was forked to start a program')

    for module, name in ((os, 'fork'), (gevent.os, 'fork'),
                         (gevent.os, 'fork_and_watch'),
                         (gevent.os, 'fork_gevent'),
                         (gevent.subprocess, 'fork_and_watch')):
        monkeypatch.setattr(module, name, forked)

    # Python's own fork (and exec) in C, which our copy of subprocess falls
    # back on when it doesn't use posix_spawn (ie: on musl)
    monkeypatch.setitem(
        Popen._execute_child.__globals__, '_fork_exec', forked)

    gpg = NNTPGnuPG(str(tmp_path / 'gpg'))
    assert gpg.import_keys(sample('reader-secret.asc')) == [
        READER_FINGERPRINT]
    assert READER_KEY_ID in gpg.secret_keys(refresh=True)

    assert wait_program(start_program(['true'])) == 0
    with pytest.raises(FileNotFoundError):
        start_program(['newsreap-no-such-program'])

    process = SubProcess(['true'])
    process.start()
    process.join()
    assert process.successful()

    # (the test's own clean up may fork)
    monkeypatch.undo()
    stop_agent(tmp_path / 'gpg')
