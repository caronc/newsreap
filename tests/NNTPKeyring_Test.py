# Test the PGP keys kept from archived posts (nr pgp)
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


from datetime import timedelta

import pytest
from click.testing import CliRunner

from newsreap.NNTPArchivePost import NNTPArchivePost
from newsreap.NNTPArchiver import NNTPArchiver
from newsreap.NNTPFilter import NNTPFilter
from newsreap.NNTPIndexer import NNTPIndexer
from newsreap.NNTPKeyring import NNTPKeyring
from newsreap.NNTPPurger import NNTPPurger
from newsreap.codecs.CodecPGP import CodecPGP
from newsreap.objects.index.PGPKey import PGPKey
from newsreap.objects.index.PGPKey import PGPKeySource
from newsreap.objects.index.PGPPost import HIDDEN_KEY_ID
from newsreap.objects.index.PGPPost import PGPPost
from newsreap.objects.index.PGPPost import PGPRole

from tests.CodecPGP_Test import FINGERPRINT
from tests.CodecPGP_Test import KEY_ID
from tests.CodecPGP_Test import MADE
from tests.CodecPGP_Test import SUBKEY_FINGERPRINT
from tests.CodecPGP_Test import SUBKEY_ID
from tests.CodecPGP_Test import USER_ID
from tests.CodecPGP_Test import sample
from tests.FakeNNTPServer import FakeNNTPServer
from tests.NNTPArchiver_Test import GROUP
from tests.NNTPArchiver_Test import POSTED
from tests.NNTPArchiver_Test import article
from tests.NNTPArchiver_Test import index_engine  # noqa: F401
from tests.conftest import config_for

# The second key in keyring.asc (an RSA key with no subkey)
SECOND_ID = '69D194EAD3F4BA40'
SECOND_FINGERPRINT = '18B485CB6C42E17AB9CA668B69D194EAD3F4BA40'
SECOND_USER_ID = 'Second Tester <second@newsreap.example>'

# Posts tied to the keys in different ways
POSTS = {
    'key@x': article(b'Here is my key\n' + sample('keyring.asc'),
                     Subject='My key'),
    'signed@x': article(sample('clearsigned.asc'), Subject='Signed'),
    'secret@x': article(sample('encrypted.asc'), Subject='For you'),
    'hidden@x': article(sample('hidden.asc'), Subject='For someone'),
    'plain@x': article('Nothing to see', Subject='Plain'),
}


def keep(index_engine, posts=POSTS, pgp=True):
    """
    Returns an archiver that has converted and kept the posts given
    (Message-ID -> article)
    """
    archiver = NNTPArchiver(index_engine, pgp=pgp)
    session = archiver.session(create=True)
    for no, (message_id, raw) in enumerate(posts.items()):
        archiver.store(session, archiver.convert(NNTPArchivePost(
            message_id, raw=raw, posted=POSTED + timedelta(minutes=no),
            groups={GROUP}, size=len(raw))))
    session.commit()
    return archiver


def ties(session):
    """
    Returns every (Message-ID, key ID, role) kept
    """
    return {(p.message_id, p.key_id, p.role)
            for p in session.query(PGPPost)}


def test_keys_are_read_from_key_blocks():
    """
    Each key in a block (with any text around it, or binary) is read with
    its names and subkeys, and its armored copy reads back the same
    """
    keys = NNTPKeyring.read_keys(b'Hello\n' + sample('keyring.asc'))
    assert [(k['key_id'], k['fingerprint'], k['algorithm'], k['user_ids'])
            for k in keys] == [
        (KEY_ID, FINGERPRINT, 'EdDSA', [USER_ID]),
        (SECOND_ID, SECOND_FINGERPRINT, 'RSA', [SECOND_USER_ID])]
    assert keys[0]['created'] == MADE
    assert [(s['key_id'], s['fingerprint']) for s in keys[0]['subkeys']] == [
        (SUBKEY_ID, SUBKEY_FINGERPRINT)]
    assert keys[1]['subkeys'] == []

    # Each one on its own
    again = NNTPKeyring.read_keys(keys[1]['armored'])
    assert [k['fingerprint'] for k in again] == [SECOND_FINGERPRINT]

    # Binary (and given as text)
    _, contents = CodecPGP().read(sample('public-key.asc'))
    assert [k['key_id'] for k in NNTPKeyring.read_keys(
        contents[0].getvalue())] == [KEY_ID]
    assert [k['key_id'] for k in NNTPKeyring.read_keys(
        sample('public-key.asc').decode())] == [KEY_ID]


def test_damaged_and_other_blocks_give_no_keys():
    """
    A block failing its checksum, a block that isn't a key, a secret key
    block and junk give no keys
    """
    data = bytearray(sample('public-key.asc'))
    data[data.index(b'\n\n') + 10] ^= 0x01
    assert NNTPKeyring.read_keys(bytes(data)) == []

    assert NNTPKeyring.read_keys(sample('detached.asc')) == []
    assert NNTPKeyring.read_keys(sample('public-key.asc').replace(
        b'PUBLIC KEY', b'PRIVATE KEY')) == []
    assert NNTPKeyring.read_keys(b'') == []
    assert NNTPKeyring.read_keys(b'\x99\x00') == []


def test_keys_are_found_by_id_fingerprint_or_name(index_engine):
    """
    Keys are found by their key ID (long or short), fingerprint (spaced or
    not), a subkey's ID, or part of a name (case doesn't matter)
    """
    archiver = keep(index_engine, {})
    session = archiver.session()
    assert NNTPKeyring.save_keys(
        session, NNTPKeyring.read_keys(sample('keyring.asc')),
        source=PGPKeySource.Imported) == 2
    session.commit()

    def found(text):
        return [k.key_id for k in NNTPKeyring.find(session, text)]

    assert found(None) == [KEY_ID, SECOND_ID]
    assert found(KEY_ID.lower()) == [KEY_ID]
    assert found('0x' + KEY_ID[-8:]) == [KEY_ID]
    assert found(NNTPKeyring.pretty(SECOND_FINGERPRINT)) == [SECOND_ID]
    assert found(SUBKEY_ID) == [KEY_ID]
    assert found(SUBKEY_FINGERPRINT) == [KEY_ID]
    assert found('SECOND@') == [SECOND_ID]
    assert found('newsreap.example') == [KEY_ID, SECOND_ID]
    assert found('%') == []
    assert found('DEADBEEF') == []

    # A key stands for its subkeys too; one we don't have for itself
    assert NNTPKeyring.resolve(session, USER_ID) == {KEY_ID, SUBKEY_ID}
    assert NNTPKeyring.resolve(session, 'nobody') == set()
    assert NNTPKeyring.resolve(session, '00' * 20) == {'00' * 8}
    assert NNTPKeyring.resolve(session, 'AB' * 32) == {'AB' * 8}
    archiver.close()


def test_keys_seen_again_gain_new_names(index_engine):
    """
    A key kept again isn't new, but keeps any name it didn't have
    """
    archiver = keep(index_engine, {})
    session = archiver.session()
    keys = NNTPKeyring.read_keys(sample('public-key.asc'))
    assert NNTPKeyring.save_keys(session, keys) == 1

    keys[0]['user_ids'] = ['Another <name@example.com>']
    assert NNTPKeyring.save_keys(session, keys) == 0
    session.commit()

    key = NNTPKeyring.find(session, KEY_ID)[0]
    assert key.user_ids.splitlines() == [
        USER_ID, 'Another <name@example.com>']
    assert session.query(PGPKey).count() == 2
    archiver.close()


def test_posts_are_tied_to_their_keys(index_engine):
    """
    With pgp on, archived posts are tied to the keys that signed them, the
    keys they were encrypted for and the keys they carry (a hidden
    recipient is noted as such), and those can be searched for
    """
    archiver = keep(index_engine)
    session = archiver.session()
    assert ties(session) == {
        ('key@x', KEY_ID, PGPRole.Carries),
        ('key@x', SECOND_ID, PGPRole.Carries),
        ('signed@x', KEY_ID, PGPRole.Signed),
        ('secret@x', SUBKEY_ID, PGPRole.EncryptedFor),
        ('hidden@x', HIDDEN_KEY_ID, PGPRole.EncryptedFor),
    }
    assert archiver.keys == 2
    assert archiver.find(key_ids=set()) == []
    assert str(archiver).endswith(', 2 new PGP key(s)')

    # Every post tied to the key (or its subkey), newest first
    key_ids = NNTPKeyring.resolve(session, USER_ID)
    assert [(p.message_id, role) for p, role in NNTPKeyring.posts(
        session, key_ids)] == [
        ('secret@x', PGPRole.EncryptedFor), ('signed@x', PGPRole.Signed),
        ('key@x', PGPRole.Carries)]
    assert [p.message_id for p in archiver.find(key_ids=key_ids)] == [
        'secret@x', 'signed@x', 'key@x']
    assert NNTPKeyring.counts(session) == (2, 3)

    # What a post is tied to (a subkey reads as its key)
    links = NNTPKeyring.links(session, 'secret@x')
    assert [(role, key_id, key.key_id) for role, key_id, _, key in
            links] == [(PGPRole.EncryptedFor, SUBKEY_ID, KEY_ID)]
    role, key_id, created, key = NNTPKeyring.links(session, 'signed@x')[0]
    assert created is not None and key.user_ids == USER_ID

    # Where the key was first seen
    key = NNTPKeyring.find(session, KEY_ID)[0]
    assert (key.message_id, key.source) == ('key@x', PGPKeySource.Post)
    archiver.close()


def test_nothing_is_kept_with_pgp_off(index_engine):
    """
    With pgp off (the default), no keys or ties are kept
    """
    archiver = keep(index_engine, pgp=False)
    session = archiver.session()
    assert ties(session) == set()
    assert session.query(PGPKey).count() == 0
    archiver.close()


def test_signed_mime_messages_are_tied_too(index_engine):
    """
    A MIME message with a PGP signature attached keeps its text (as the
    MIME hook reads it) and is tied to its signer
    """
    raw = article(
        b'--b1\r\nContent-Type: text/plain\r\n\r\nSigned hello\r\n'
        b'--b1\r\nContent-Type: application/pgp-signature; '
        b'name="signature.asc"\r\n\r\n' + sample('detached.asc') +
        b'--b1--\r\n',
        Content_Type='multipart/signed; protocol="application/'
        'pgp-signature"; boundary="b1"', MIME_Version='1.0')

    archiver = keep(index_engine, {'mime@x': raw})
    session = archiver.session()
    assert archiver.get('mime@x').text.startswith('Signed hello')
    assert ties(session) == {('mime@x', KEY_ID, PGPRole.Signed)}
    archiver.close()


def test_removed_posts_lose_their_ties(index_engine):
    """
    Posts purged or forgotten with their group take their ties with them
    (the keys stay)
    """
    archiver = keep(index_engine)
    session = archiver.session()

    purger = NNTPPurger(NNTPFilter(rules=[
        {'name': 'gone', 'subject': 'Signed'}]), index_db=index_engine)
    try:
        purger.purge_archive()
    finally:
        purger.close()

    session.expire_all()
    assert {m for m, _, _ in ties(session)} == {
        'key@x', 'secret@x', 'hidden@x'}

    indexer = NNTPIndexer(index_engine)
    try:
        indexer.forget_group(GROUP)
    finally:
        indexer.close()

    session.expire_all()
    assert ties(session) == set()
    assert session.query(PGPKey).filter(PGPKey.parent == '').count() == 2
    archiver.close()


@pytest.fixture
def server(tmp_path):
    """
    A server holding the posts in POSTS (in GROUP)
    """
    group_list = tmp_path / 'group.list'
    group_list.write_text('%s 0000000005 0000000001 y\n' % GROUP)

    with FakeNNTPServer(group_list=str(group_list),
                        join_group=False) as server:
        server.add_group(GROUP, 0, 1, len(POSTS))
        for no, (message_id, raw) in enumerate(POSTS.items(), 1):
            path = tmp_path / ('%d.msg' % no)
            path.write_bytes(raw)
            server.add_article(message_id, str(path), GROUP)
            server.add_overview(
                GROUP, no, 'Subject %d' % no, 'poster@example.com',
                POSTED + timedelta(minutes=no), message_id, size=len(raw))
        yield server


def test_pgp_from_the_command_line(tmp_path, server):
    """
    PGP keys are off until turned on; then a forced update reads the posts
    kept before, and keys can be listed, shown, searched by, exported,
    forgotten and imported
    """
    from newsreap.cli import cli

    config = config_for(server, tmp_path / 'config.yaml')
    runner = CliRunner()

    def nr(*args):
        return runner.invoke(cli, ['--config', config, *args], obj={})

    assert nr('db', 'init').exit_code == 0
    assert nr('group', 'refresh').exit_code == 0
    result = nr('update', GROUP, '--content')
    assert result.exit_code == 0, result.output
    assert '5 article(s) kept' in result.output

    # Off: nothing kept, and we're told how to turn it on
    result = nr('pgp', 'list')
    assert 'No PGP keys are kept.' in result.output
    assert "add 'pgp: yes' under processing:" in result.output
    assert 'not kept (set' in nr('status').output

    # Turned on: nothing new to fetch, until forced
    with open(config) as fp:
        text = fp.read()
    with open(config, 'w') as fp:
        fp.write(text.replace('# pgp: yes', 'pgp: yes'))

    fetched = len([c for c in server.commands() if c.startswith('ARTICLE')])
    result = nr('update', GROUP, '--content', '--cached', '--force')
    assert result.exit_code == 0, result.output
    assert '2 new PGP key(s)' in result.output
    assert len([c for c in server.commands()
                if c.startswith('ARTICLE')]) == fetched + 4

    result = nr('pgp', 'list')
    assert result.exit_code == 0
    lines = result.output.splitlines()
    assert lines[0].startswith('%s  2026-11-02  EdDSA' % KEY_ID)
    assert lines[0].endswith('3 post(s)  %s' % USER_ID)
    assert lines[1].endswith('1 post(s)  %s' % SECOND_USER_ID)
    assert nr('pgp', 'list', 'second').output.splitlines() == lines[1:]

    # Everything about a key (named by its subkey here)
    result = nr('pgp', 'show', SUBKEY_ID)
    assert result.exit_code == 0
    assert 'Fingerprint: %s' % NNTPKeyring.pretty(FINGERPRINT) in \
        result.output
    assert 'Name:        %s' % USER_ID in result.output
    assert 'Subkey:      %s (ECDH, 2026-11-02)' % SUBKEY_ID in result.output
    assert 'in <key@x>' in result.output
    assert 'encrypted for <secret@x>  For you' in result.output
    assert 'signed by     <signed@x>  Signed' in result.output

    # A key we don't have still lists the posts naming it
    assert nr('pgp', 'show', 'nobody').exit_code == 1

    # Searching by key
    result = nr('search', '--key', 'NewsReap Test')
    assert result.exit_code == 0
    assert {'<key@x>', '<signed@x>', '<secret@x>'} <= set(
        result.output.split())
    assert '<hidden@x>' not in result.output
    result = nr('search', '-k', 'nobody')
    assert "No PGP key matches 'nobody'" in result.output

    # A post shows what it is tied to
    result = nr('show', '--local', 'signed@x')
    assert 'PGP:        signed by %s (%s, 2026-11-02' % (KEY_ID, USER_ID) \
        in result.output
    assert 'PGP keys' in nr('status').output
    assert 'PGP:        encrypted for someone (who it is was left out)' in \
        nr('show', '--local', 'hidden@x').output

    # Handing a key on
    result = nr('pgp', 'export', 'second@')
    assert result.exit_code == 0
    assert [k['key_id'] for k in NNTPKeyring.read_keys(
        result.output)] == [SECOND_ID]

    # A name matching more than one key isn't forgotten
    result = nr('pgp', 'forget', 'newsreap.example')
    assert result.exit_code == 1
    result = nr('pgp', 'forget', SECOND_ID)
    assert result.exit_code == 0
    assert 'Forgot %s %s' % (SECOND_ID, SECOND_USER_ID) in result.output
    assert len(nr('pgp', 'list').output.splitlines()) == 1

    # The posts still name it
    result = nr('pgp', 'show', SECOND_ID)
    assert '(the key itself is not kept)' in result.output
    assert 'carries       <key@x>' in result.output

    # Back again by hand (the one we have isn't new)
    result = nr('pgp', 'import', str(tmp_path / 'missing.asc'))
    assert result.exit_code != 0
    junk = tmp_path / 'junk.asc'
    junk.write_bytes(b'nothing here')
    assert nr('pgp', 'import', str(junk)).exit_code == 1

    keyring = tmp_path / 'keyring.asc'
    keyring.write_bytes(sample('keyring.asc'))
    result = nr('pgp', 'import', str(keyring))
    assert result.exit_code == 0, result.output
    assert 'keyring.asc: 2 key(s), 1 new.' in result.output
    assert 'Imported:' in nr('pgp', 'show', SECOND_ID).output
