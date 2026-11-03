# Test the PGP codec (signatures, keys and encrypted messages)
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

import random
from datetime import datetime
from os.path import join

from newsreap.NNTPAsciiContent import NNTPAsciiContent
from newsreap.NNTPBinaryContent import NNTPBinaryContent
from newsreap.codecs.CodecPGP import CodecPGP

from tests.CodecMime_Test import decoders
from tests.CodecMime_Test import run_decoders
from tests.FakeNNTPServer import VAR_PATH
from tests.conftest import connect_to

# Blocks made with GnuPG (a test key made for this, with no passphrase)
PGP_PATH = join(VAR_PATH, 'pgp')

# The test key: its key ID, fingerprint and encryption subkey
KEY_ID = '11349D4561E2FDA5'
FINGERPRINT = '934C032AD2AD6EC90C1E8C1211349D4561E2FDA5'
SUBKEY_ID = '87E519AC03047C0D'
SUBKEY_FINGERPRINT = '7F4DDD097FCFCFA4608A594487E519AC03047C0D'
USER_ID = 'NewsReap Test <test@newsreap.example>'
MADE = datetime(2026, 11, 2, 12, 55, 23)


def sample(name):
    """
    Returns the bytes of one of the sample blocks
    """
    with open(join(PGP_PATH, name), 'rb') as fp:
        return fp.read()


def read(tmp_path, name):
    """
    Returns (text outside the blocks, contents) for a sample
    """
    return CodecPGP(work_dir=str(tmp_path)).read(sample(name))


def test_signatures_name_who_signed_them(tmp_path):
    """
    A signature block gives the key that made it and when
    """
    outside, (signature, ) = read(tmp_path, 'detached.asc')
    assert outside == b''
    assert isinstance(signature, NNTPBinaryContent)
    assert signature.content_type == 'application/pgp-signature'
    assert signature.filename == 'pgp1.sig'
    assert signature.pgp['signers'] == [KEY_ID]
    assert signature.pgp['fingerprints'] == [FINGERPRINT]
    assert signature.pgp['created'] == MADE
    assert signature.pgp['checksum'] is True
    assert signature.pgp['encrypted'] is False
    assert signature.is_valid()


def test_signed_messages_keep_their_text(tmp_path):
    """
    The text of a signed message is kept (lines starting with a dash lose
    the '- ' added when it was signed) and its signature read after it
    """
    _, (text, signature) = read(tmp_path, 'clearsigned.asc')
    assert isinstance(text, NNTPAsciiContent)
    assert text.content_type == 'text/plain'
    assert text.getvalue() == b'Hello Usenet.\n- a dash line\nFrom here'
    assert text.pgp['headers'] == {'Hash': 'SHA512'}
    assert signature.pgp['signers'] == [KEY_ID]


def test_encrypted_messages_name_who_can_read_them(tmp_path):
    """
    An encrypted message gives the key it is for (or says a passphrase
    opens it)
    """
    _, (message, ) = read(tmp_path, 'encrypted.asc')
    assert message.content_type == 'application/pgp-encrypted'
    assert message.pgp['encrypted'] is True
    assert message.pgp['symmetric'] is False
    assert message.pgp['recipients'] == [SUBKEY_ID]
    assert message.pgp['filename'] == ''

    _, (message, ) = read(tmp_path, 'symmetric.asc')
    assert message.pgp['encrypted'] is True
    assert message.pgp['symmetric'] is True
    assert message.pgp['recipients'] == []


def test_signed_files_give_their_name(tmp_path):
    """
    A signed (not encrypted) message gives the name of the file in it,
    which also names what was decoded
    """
    _, (message, ) = read(tmp_path, 'signed.asc')
    assert message.content_type == 'application/pgp'
    assert message.pgp['encrypted'] is False
    assert message.pgp['filename'] == 'note.txt'
    assert message.filename == 'note.txt.gpg'
    assert message.pgp['signers'] == [KEY_ID]


def test_key_blocks_list_their_keys(tmp_path):
    """
    A public key block lists each key (with its fingerprint) and the names
    on it
    """
    _, (keys, ) = read(tmp_path, 'public-key.asc')
    assert keys.content_type == 'application/pgp-keys'
    assert [(k['key_id'], k['fingerprint'], k['subkey'], k['algorithm'],
             k['created']) for k in keys.pgp['keys']] == [
        (KEY_ID, FINGERPRINT, False, 'EdDSA', MADE),
        (SUBKEY_ID, SUBKEY_FINGERPRINT, True, 'ECDH', MADE)]
    assert keys.pgp['user_ids'] == [USER_ID]
    assert keys.pgp['fingerprints'] == sorted(
        [FINGERPRINT, SUBKEY_FINGERPRINT])


def test_damaged_blocks_are_flagged(tmp_path):
    """
    A block whose checksum doesn't match is flagged as damaged
    """
    data = sample('detached.asc')
    lines = data.split(b'\n')

    # Change one character of the base64
    line = lines[2]
    lines[2] = line[:10] + (b'A' if line[10:11] != b'A' else b'B') + \
        line[11:]
    _, (signature, ) = CodecPGP(work_dir=str(tmp_path)).read(
        b'\n'.join(lines))
    assert signature.pgp['checksum'] is False
    assert not signature.is_valid()

    # The checksum is optional
    _, (signature, ) = CodecPGP(work_dir=str(tmp_path)).read(b'\n'.join(
        line for line in data.split(b'\n') if not line.startswith(b'=')))
    assert signature.pgp['checksum'] is None
    assert signature.is_valid()
    assert signature.pgp['signers'] == [KEY_ID]


def test_bad_data_never_stops_decoding(tmp_path):
    """
    Blocks that never end, lines that aren't base64, headers without a
    blank line after them, and blocks one after another are all read
    """
    data = sample('detached.asc')

    # Never ends (cut before its END line)
    _, (signature, ) = CodecPGP(work_dir=str(tmp_path)).read(
        data[:data.index(b'-----END')])
    assert signature.pgp['signers'] == [KEY_ID]

    # Damage inside it, and no blank line after its BEGIN line
    lines = data.split(b'\n')
    lines.insert(3, b'!! this is not base64 !!')
    lines.insert(1, b'Comment: made for a test')
    _, (signature, ) = CodecPGP(work_dir=str(tmp_path)).read(
        b'\n'.join(lines).replace(b'\n\n', b'\n'))
    assert signature.pgp['headers'] == {'Comment': 'made for a test'}
    assert signature.pgp['signers'] == [KEY_ID]

    # A new block starts before the last one ended
    outside, contents = CodecPGP(work_dir=str(tmp_path)).read(
        b'Before\n' + data[:data.index(b'-----END')] +
        sample('encrypted.asc') + b'After\n')
    assert outside == b'Before\nAfter\n'
    assert [c.content_type for c in contents] == [
        'application/pgp-signature', 'application/pgp-encrypted']

    # An END line on its own is just text
    outside, contents = CodecPGP(work_dir=str(tmp_path)).read(
        b'-----END PGP SIGNATURE-----\n')
    assert contents == []


def test_packets_never_fail_on_garbage():
    """
    Random bytes, and real blocks cut short anywhere, never raise; what
    can be read is reported
    """
    rng = random.Random(1)
    for _ in range(3000):
        found = CodecPGP.packets(
            bytes(rng.randrange(256) for _ in range(rng.randrange(64))))
        assert isinstance(found['packets'], list)

    # Every length of every sample
    for name in ('detached.asc', 'encrypted.asc', 'public-key.asc',
                 'signed.asc', 'symmetric.asc'):
        _, (content, ) = CodecPGP().read(sample(name))
        data = content.getvalue()
        for size in range(len(data) + 1):
            CodecPGP.packets(data[:size])

        # Bytes changed at random
        for _ in range(200):
            damaged = bytearray(data)
            for _ in range(3):
                damaged[rng.randrange(len(damaged))] = rng.randrange(256)
            CodecPGP.packets(bytes(damaged))


def test_checksums_match_the_openpgp_standard():
    """
    The CRC-24 used by ASCII armor gives the standard's check value
    """
    assert CodecPGP.crc24(b'') == 0xB704CE
    assert CodecPGP.crc24(b'123456789') == 0x21CF02

    # Worked out in pieces, the same
    assert CodecPGP.crc24(b'6789', CodecPGP.crc24(b'12345')) == 0x21CF02


def test_files_wrapped_in_armor_decode_back(tmp_path):
    """
    encode() wraps a binary PGP file in ASCII armor that decodes back to
    the same bytes (with a checksum that matches)
    """
    _, (signature, ) = read(tmp_path, 'detached.asc')
    data = signature.getvalue()
    path = tmp_path / 'note.sig'
    path.write_bytes(data * 3)

    encoded = CodecPGP(work_dir=str(tmp_path)).encode(
        str(path), kind='SIGNATURE', headers={'Comment': 'test'},
        mem_buf=100)
    _, (decoded, ) = CodecPGP(work_dir=str(tmp_path)).read(
        encoded.getvalue())
    assert decoded.getvalue() == data * 3
    assert decoded.pgp['checksum'] is True
    assert decoded.pgp['headers'] == {'Comment': 'test'}


def test_block_lines_can_be_spotted_on_their_own():
    """
    Without the article to go by, only BEGIN and END lines are recognised
    """
    codec = CodecPGP()
    assert codec.detect(b'-----BEGIN PGP MESSAGE-----\r\n',
                        relative=False) == {
        'key': 'begin', 'kind': 'MESSAGE'}
    assert codec.detect(b'-----END PGP SIGNATURE-----', relative=False) == {
        'key': 'end', 'kind': 'SIGNATURE'}
    assert codec.detect(b'-----BEGIN PGP MESSAGE, PART 1/2-----',
                        relative=False)['kind'] == 'MESSAGE, PART 1/2'
    assert codec.detect(b'just text', relative=False) is None

    # An end with no beginning isn't ours
    assert codec.detect(b'-----END PGP MESSAGE-----') is None


def test_messages_split_over_posts_say_which_part(tmp_path):
    """
    A message split over several posts says which part each one is
    """
    data = sample('encrypted.asc').replace(
        b'PGP MESSAGE-----', b'PGP MESSAGE, PART 2/3-----')
    _, (message, ) = CodecPGP(work_dir=str(tmp_path)).read(data)
    assert (message.pgp['part'], message.pgp['total']) == (2, 3)
    assert message.content_type == 'application/pgp-encrypted'


def test_signed_articles_are_decoded_as_they_are_fetched(
        nntp_server, tmp_path):
    """
    A signed article fetched from a server comes back as its text plus its
    signature; text around them stays in the body
    """
    path = tmp_path / 'signed.msg'
    path.write_bytes(
        b'From: someone@example.com\r\nSubject: signed\r\n\r\n'
        b'Before it\r\n' +
        sample('clearsigned.asc').replace(b'\n', b'\r\n') +
        b'After it\r\n')
    nntp_server.add_article('signed@x', str(path), 'alt.test')

    # With the decoders a connection uses (as they read it)
    body, contents = run_decoders(
        path.read_bytes(), [*decoders(tmp_path), CodecPGP(
            work_dir=str(tmp_path))])
    assert body == b'Before it\r\nAfter it\r\n'
    text, signature = contents[1:]
    assert text.getvalue() == \
        b'Hello Usenet.\r\n- a dash line\r\nFrom here'
    assert signature.pgp['signers'] == [KEY_ID]

    # Fetched
    sock = connect_to(nntp_server)
    article = sock.get('signed@x', work_dir=str(tmp_path / 'work'),
                       group='alt.test')
    sock.close()

    found = {c.content_type: c for c in article.decoded
             if hasattr(c, 'pgp')}
    assert sorted(found) == ['application/pgp-signature', 'text/plain']
    assert found['application/pgp-signature'].pgp['signers'] == [KEY_ID]
    assert b'From here' in found['text/plain'].getvalue()


def test_key_blocks_split_into_their_keys():
    """
    A block holding several keys splits into one piece per key (with its
    names and subkeys); secret keys are left out
    """
    _, contents = CodecPGP().read(sample('keyring.asc'))
    keys = CodecPGP.split_keys(contents[0].getvalue())
    assert len(keys) == 2

    first = CodecPGP.packets(keys[0])
    assert [k['key_id'] for k in first['keys']] == [KEY_ID, SUBKEY_ID]
    assert first['user_ids'] == [USER_ID]

    second = CodecPGP.packets(keys[1])
    assert [k['key_id'] for k in second['keys']] == ['69D194EAD3F4BA40']
    assert second['user_ids'] == ['Second Tester <second@newsreap.example>']

    # The same key marked secret (tag 5) is left out, and so is junk
    body = next(CodecPGP._iter_packets(keys[0]))[1]
    secret = bytes((0xC5, len(body))) + body
    assert CodecPGP.split_keys(secret + keys[1]) == [keys[1]]
    assert CodecPGP.split_keys(b'not a key') == []
    assert CodecPGP.split_keys(keys[0][:20]) == [keys[0][:20]]


def test_binary_data_is_armored():
    """
    Binary PGP data wrapped in armor reads back the same (checksum and
    all)
    """
    data = bytes(range(256)) * 3
    armored = CodecPGP.armor(data)
    assert armored.startswith('-----BEGIN PGP PUBLIC KEY BLOCK-----\n\n')
    assert armored.endswith('-----END PGP PUBLIC KEY BLOCK-----\n')

    _, contents = CodecPGP().read(armored.encode())
    assert contents[0].getvalue() == data
    assert contents[0].pgp['checksum'] is True
    assert CodecPGP.armor(b'', 'SIGNATURE').splitlines()[-2] == '=twTO'
