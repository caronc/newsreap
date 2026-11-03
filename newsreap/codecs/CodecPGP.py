# A codec for PGP blocks (signatures, keys and encrypted messages)
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
"""
PGP (and GnuPG) posts its data as "ASCII armor": a line like
'-----BEGIN PGP MESSAGE-----', a few "Name: value" lines, a blank line,
base64, a checksum line ('=' then 4 characters) and an END line.  A
signed message ('-----BEGIN PGP SIGNED MESSAGE-----') is readable text
followed by a signature block.

Each block is decoded as it is read: the binary it holds becomes an
NNTPBinaryContent and the text of a signed message an NNTPAsciiContent.
Both get a `pgp` attribute saying what was learned without needing any
key (see packets()): who it is encrypted for or signed by (key IDs and
fingerprints), the keys and names in a key block, when it was made, and
the name of the file inside when it isn't encrypted.  See RFC 9580
(https://www.rfc-editor.org/rfc/rfc9580) for the format.

Bad data never stops it: broken base64 is logged and left out, a block
that never ends is returned as far as it got, and anything packets()
can't make sense of is simply not reported.
"""

import re
import bz2
import zlib
from binascii import a2b_base64
from binascii import b2a_base64
from binascii import Error as BinAsciiError
from datetime import UTC
from datetime import datetime
from hashlib import sha1
from hashlib import sha256
from io import BytesIO
from os.path import basename

from newsreap.NNTPAsciiContent import NNTPAsciiContent
from newsreap.NNTPBinaryContent import NNTPBinaryContent
from newsreap.NNTPContent import NNTPContent
from newsreap.Utils import SEEK_END
from newsreap.codecs.CodecBase import CodecBase

# Logging
import logging
from newsreap.Logging import NEWSREAP_CODEC
logger = logging.getLogger(NEWSREAP_CODEC)

# Defines the new line delimiter
EOL = b'\r\n'

# The first and last line of a block (ie: -----BEGIN PGP MESSAGE-----)
ARMOR_RE = re.compile(
    br'^-----(?P<edge>BEGIN|END) PGP (?P<kind>[A-Z0-9][A-Z0-9 ,/]*?)-----'
    br'\s*$')

# The part of a message split over several posts (ie: MESSAGE, PART 1/3)
ARMOR_PART_RE = re.compile(r'PART (?P<part>\d+)(?:/(?P<total>\d+))?$')

# A "Name: value" line at the top of a block
ARMOR_HEADER_RE = re.compile(br'^(?P<key>[A-Za-z0-9-]+):\s?(?P<value>.*)$')

# The checksum line (= then the checksum in base64)
CHECKSUM_RE = re.compile(br'^=(?P<crc>[A-Za-z0-9+/]{4})\s*$')

# The kinds of block, what they hold, and the extension of the file
# decoded from them
SIGNED_MESSAGE = 'SIGNED MESSAGE'
SIGNATURE = 'SIGNATURE'
BLOCK_TYPES = {
    'MESSAGE': ('application/pgp-encrypted', '.gpg'),
    SIGNATURE: ('application/pgp-signature', '.sig'),
    'PUBLIC KEY BLOCK': ('application/pgp-keys', '.gpg'),
    'PRIVATE KEY BLOCK': ('application/pgp-keys', '.gpg'),
    'ARMORED FILE': ('application/octet-stream', '.gpg'),
}

# The characters base64 is written with
BASE64_ALPHABET = \
    b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/='

# Spaces and line endings (left out of a line of base64)
WHITESPACE = b' \t\r\n\x0b\x0c'

# How much of a block is kept in memory to read its packets from; what
# packets() reports is always near the start
SCAN_BYTES = 262144

# The most packets read (a key block has a few per key)
MAX_PACKETS = 256

# Compressed data inside compressed data is only opened this deep, and
# never to more than this many bytes
MAX_DEPTH = 4
MAX_INFLATE = 1048576

# How many bytes go on each base64 line when encoding (64 characters)
ARMOR_LINE_BYTES = 48

# The checksum (a CRC-24) used by ASCII armor
CRC24_INIT = 0xB704CE
CRC24_POLY = 0x1864CFB


def _crc24_table():
    """
    Returns the CRC-24 of every byte value (so the checksum can be worked
    out a byte at a time with a lookup)
    """
    table = []
    for byte in range(256):
        crc = byte << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= CRC24_POLY
        table.append(crc & 0xFFFFFF)
    return tuple(table)


CRC24_TABLE = _crc24_table()

# The public key methods (algorithms) by number
PUBLIC_KEY_ALGORITHMS = {
    1: 'RSA', 2: 'RSA', 3: 'RSA', 16: 'Elgamal', 17: 'DSA', 18: 'ECDH',
    19: 'ECDSA', 22: 'EdDSA', 25: 'X25519', 26: 'X448', 27: 'Ed25519',
    28: 'Ed448',
}

# The packet types (tags) by number
PACKET_PKESK = 1
PACKET_SIGNATURE = 2
PACKET_SKESK = 3
PACKET_ONE_PASS = 4
PACKET_SECRET_KEY = 5
PACKET_PUBLIC_KEY = 6
PACKET_SECRET_SUBKEY = 7
PACKET_COMPRESSED = 8
PACKET_ENCRYPTED = 9
PACKET_LITERAL = 11
PACKET_USER_ID = 13
PACKET_PUBLIC_SUBKEY = 14
PACKET_ENCRYPTED_MDC = 18
PACKET_AEAD = 20

# The packets that hold encrypted data
ENCRYPTED_PACKETS = (PACKET_ENCRYPTED, PACKET_ENCRYPTED_MDC, PACKET_AEAD)

# Signature details (subpackets) we read
SUBPACKET_CREATED = 2
SUBPACKET_ISSUER = 16
SUBPACKET_SIGNER_USER_ID = 28
SUBPACKET_ISSUER_FINGERPRINT = 33


class PGPState:
    """
    Where we are in a block
    """
    # The Name: value lines at the top of a block
    Header = 'header'

    # The base64 of a block
    Data = 'data'

    # The Hash: lines at the top of a signed message
    TextHeader = 'text-header'

    # The text of a signed message
    Text = 'text'


class CodecPGP(CodecBase):
    """
    Decodes PGP blocks (see the module description)
    """

    def __init__(self, work_dir=None, *args, **kwargs):
        super().__init__(work_dir=work_dir, *args, **kwargs)

        # What the last block finished held (see packets())
        self.meta = {}

        self._clear()

    def _clear(self):
        """
        Forgets the block we were reading
        """
        # Where we are (None outside a block)
        self._state = None

        # The block being read
        self._content = None
        self._finished = True
        self._kind = ''
        self._headers = {}

        # The start of the block's data (see SCAN_BYTES), its checksum so
        # far, and the checksum it gives
        self._scan = bytearray()
        self._crc24 = CRC24_INIT
        self._checksum = None

        # Base64 left over from the last line, and the line ending held
        # back from the last line of text
        self._carry = b''
        self._eol = b''

        # Part of a line (the rest hasn't arrived yet)
        self._pending = b''

        # How many blocks were found
        self._blocks = 0

    @staticmethod
    def crc24(data, crc=CRC24_INIT):
        """
        Returns the CRC-24 checksum of the data (carry on from an earlier
        one by passing it in)
        """
        table = CRC24_TABLE
        for byte in data:
            crc = ((crc << 8) & 0xFFFFFF) ^ table[(crc >> 16) ^ byte]
        return crc

    def detect(self, line, relative=True):
        """
        Returns a dictionary when the line is ours to decode, else None

        With relative, every line from a BEGIN line to its END line is
        ours.  Without it, only BEGIN and END lines are (with 'key' set to
        begin or end and 'kind' to what the block holds).
        """
        if isinstance(line, str):
            line = line.encode('utf-8', 'replace')

        if relative and self._state is not None:
            # Inside a block
            return {'key': self._state}

        found = ARMOR_RE.match(line)
        if not found:
            return None

        edge = found.group('edge').decode().lower()
        if relative and edge == 'end':
            # An end without a beginning
            return None

        return {'key': edge, 'kind': found.group('kind').decode()}

    def _start(self, kind):
        """
        Starts a block of the kind given (ie: MESSAGE)
        """
        self._blocks += 1
        self._kind = kind
        self._headers = {}
        self._scan = bytearray()
        self._crc24 = CRC24_INIT
        self._checksum = None
        self._carry = b''
        self._eol = b''
        self._finished = False

        if kind == SIGNED_MESSAGE:
            # Readable text (with its signature after it)
            self._content = NNTPAsciiContent(work_dir=self.work_dir)
            self._content.content_type = 'text/plain'
            self._state = PGPState.TextHeader

        else:
            content_type, extension = BLOCK_TYPES.get(
                ARMOR_PART_RE.sub('', kind).rstrip(', '),
                BLOCK_TYPES['ARMORED FILE'])
            self._content = NNTPBinaryContent(
                filepath='pgp%d%s' % (self._blocks, extension),
                work_dir=self.work_dir, unique=True)
            self._content.content_type = content_type
            self._state = PGPState.Header

        self._content.charset = ''
        self._content.pgp = {'kind': kind, 'headers': self._headers}
        self._content.open()

    def _finish(self):
        """
        Finishes the block being read; returns its content (None if there
        isn't one)
        """
        content = self._content
        if content is None or self._finished:
            return content

        if self._pending:
            # The last line never got its line ending
            line, self._pending = self._pending, b''
            self._write(line)

        if self._carry:
            # Base64 that wasn't padded out to 4 characters
            carry, self._carry = self._carry, b''
            self._write_base64(carry + b'=' * (-len(carry) % 4))

        self._finished = True
        content.close()

        meta = content.pgp
        if self._kind != SIGNED_MESSAGE:
            # What its packets say (see packets())
            meta.update(self.packets(bytes(self._scan)))

            # True or False when it gives a checksum (None when it doesn't;
            # it is optional)
            meta['checksum'] = None if self._checksum is None \
                else self._checksum == self._crc24

            if meta['checksum'] is False:
                logger.warning(
                    'PGP %s block failed its checksum.' % self._kind)

            if self._kind.startswith('MESSAGE') and not meta['encrypted']:
                # Only signed (anyone can read it)
                content.content_type = 'application/pgp'

            if meta.get('filename'):
                # Named after the file inside it
                content.filename = '%s%s' % (
                    meta['filename'], BLOCK_TYPES.get(
                        self._kind, BLOCK_TYPES['ARMORED FILE'])[1])

        part = ARMOR_PART_RE.search(self._kind)
        if part:
            # One part of a block split over several posts
            meta['part'] = int(part.group('part'))
            meta['total'] = int(part.group('total') or 0) or None

        content._is_valid = meta.get('checksum') is not False
        self.meta = meta
        return content

    @property
    def decoded(self):
        """
        What has been decoded so far (the block being read is finished
        first, so it can be used as it is)
        """
        return self._finish()

    @decoded.setter
    def decoded(self, value):
        """
        Sets what has been decoded (CodecBase clears it this way)
        """
        self._content = value
        self._finished = value is None

    def _write_base64(self, data):
        """
        Decodes base64 characters (a multiple of 4 of them)
        """
        try:
            decoded = a2b_base64(data)

        except BinAsciiError:
            # Not base64 after all; left out
            logger.warning(
                'PGP %s block: a line could not be decoded.' % self._kind)
            return

        if not decoded:
            return

        self._crc24 = self.crc24(decoded, self._crc24)
        if len(self._scan) < SCAN_BYTES:
            self._scan += decoded[:SCAN_BYTES - len(self._scan)]

        self._decoded += len(decoded)
        self._content.write(decoded)

    def _write(self, line):
        """
        Decodes one line of the block being read
        """
        self._lines += 1
        if self._state == PGPState.Data:
            found = CHECKSUM_RE.match(line)
            if found:
                # The checksum it gives
                try:
                    self._checksum = int.from_bytes(
                        a2b_base64(found.group('crc')), 'big')

                except BinAsciiError:
                    # Not one we can read
                    pass
                return

            line = line.translate(None, WHITESPACE)
            if line.translate(None, BASE64_ALPHABET):
                # Something other than base64 (ie: damage)
                logger.warning(
                    'PGP %s block: a line that is not base64 was left '
                    'out.' % self._kind)
                return

            data = self._carry + line
            usable = len(data) - len(data) % 4
            self._carry = data[usable:]
            if usable:
                self._write_base64(data[:usable])
            return

        # The text of a signed message: kept as it is, except a line
        # starting with '- ' has it removed (a line starting with a dash
        # gets one added when signed).  The line ending before the
        # signature isn't part of the text.
        if line.endswith(b'\r\n'):
            line, eol = line[:-2], b'\r\n'

        elif line.endswith(b'\n'):
            line, eol = line[:-1], b'\n'

        else:
            eol = b''

        if line[:2] == b'- ':
            line = line[2:]

        self._decoded += len(self._eol) + len(line)
        self._content.write(self._eol + line)
        self._eol = eol

    def decode(self, stream):
        """
        Decodes the lines of the stream that are ours

        - Returns a block's content once it ends (each block is returned on
          its own; we're asked again for the next one).
        - Returns True when the stream runs out with more to come.
        - Returns None when there is nothing for us to decode.
        """
        while self.decode_loop():
            line = stream.readline()
            if not line:
                # More to come
                return True

            if not line.endswith(b'\n'):
                # The rest of the line hasn't arrived yet
                self._pending += line
                continue

            if self._pending:
                line, self._pending = self._pending + line, b''

            self._total_lines += 1
            armor = ARMOR_RE.match(line)

            if self._state is None:
                if not armor or armor.group('edge') != b'BEGIN':
                    # Not the start of a block
                    return None

                self._start(armor.group('kind').decode())
                continue

            if armor and self._state != PGPState.Text or \
                    armor and armor.group('edge') == b'BEGIN':
                # The end of the block (a BEGIN line means it never ended,
                # or that the signature of a signed message starts)
                content = self._finish()
                self._content = None
                self._state = None

                if armor.group('edge') == b'BEGIN':
                    # The next block starts straight away
                    self._start(armor.group('kind').decode())

                return content

            if self._state in (PGPState.Header, PGPState.TextHeader):
                header = ARMOR_HEADER_RE.match(line.rstrip())
                if header:
                    key = header.group('key').decode('latin-1')
                    value = header.group('value').decode('utf-8', 'replace')
                    self._headers[key] = value
                    if key.lower() == 'charset':
                        self._content.charset = value.strip().lower()
                    continue

                # The data (or text) starts after the blank line (or
                # straight away when there isn't one)
                self._state = PGPState.Data \
                    if self._state == PGPState.Header else PGPState.Text

                if not line.strip():
                    continue

            self._write(line)

            if self._max_bytes > 0 and self._decoded >= self._max_bytes:
                # Only this much was wanted
                stream.seek(0, SEEK_END)
                content = self._finish()
                self._state = None
                return content

        return True

    def read(self, data):
        """
        Reads every block in the bytes given (ie: an article's body);
        returns (text, contents): the text outside of the blocks (bytes)
        and what each block held
        """
        self.reset()
        stream = BytesIO(data if data.endswith(b'\n') else data + b'\n')
        outside = []
        contents = []
        active = False
        while True:
            ptr = stream.tell()
            line = stream.readline()
            if not line:
                break

            if not active:
                if self.detect(line) is None:
                    outside.append(line)
                    continue

                stream.seek(ptr)

            result = self.decode(stream)
            active = self._state is not None
            if isinstance(result, NNTPContent):
                contents.append(result)

            elif result is not True:
                # Not a block after all
                outside.append(line)
                active = False

        if self._state is not None:
            # A block that never ended
            contents.append(self._finish())
            self._content = None
            self._state = None

        return b''.join(outside), contents

    @staticmethod
    def _length(data, pos):
        """
        Reads a packet's length (the newer format); returns (length,
        partial, position after it).  A partial length means more of the
        packet follows in chunks.
        """
        first = data[pos]
        if first < 192:
            return first, False, pos + 1

        if first < 224:
            return ((first - 192) << 8) + data[pos + 1] + 192, False, pos + 2

        if first == 255:
            return int.from_bytes(data[pos + 1:pos + 5], 'big'), False, \
                pos + 5

        return 1 << (first & 0x1F), True, pos + 1

    @staticmethod
    def _iter_packets(data):
        """
        Iterates over the packets in the data: (tag, body) pairs.  It stops
        at anything that isn't a packet.
        """
        for tag, body, _, _ in CodecPGP._packet_spans(data):
            yield tag, body

    @staticmethod
    def _packet_spans(data):
        """
        Iterates over the packets in the data: (tag, body, start, end), where
        start and end are where the whole packet sits in the data.  It stops
        at anything that isn't a packet.
        """
        pos = 0
        count = 0
        while pos < len(data) and count < MAX_PACKETS:
            count += 1
            start = pos
            first = data[pos]
            if not first & 0x80:
                # Not a packet
                return

            if first & 0x40:
                # The newer format
                tag = first & 0x3F
                length, partial, pos = CodecPGP._length(data, pos + 1)
                body = data[pos:pos + length]
                pos += length
                while partial and pos < len(data):
                    # Put the chunks back together
                    length, partial, pos = CodecPGP._length(data, pos)
                    body += data[pos:pos + length]
                    pos += length

            else:
                # The older format: the length is 1, 2 or 4 bytes (or runs
                # to the end)
                tag = (first >> 2) & 0x0F
                size = (1, 2, 4, 0)[first & 0x03]
                pos += 1
                if size:
                    length = int.from_bytes(data[pos:pos + size], 'big')
                    pos += size

                else:
                    length = len(data) - pos

                body = data[pos:pos + length]
                pos += length

            yield tag, body, start, min(pos, len(data))

    @staticmethod
    def _when(data):
        """
        Returns the time 4 bytes give (seconds since 1970, UTC); None for
        none
        """
        if len(data) != 4:
            return None

        seconds = int.from_bytes(data, 'big')
        if not seconds:
            return None

        return datetime.fromtimestamp(seconds, UTC).replace(tzinfo=None)

    @staticmethod
    def _subpackets(data, found):
        """
        Reads what a signature's details (subpackets) say into found
        """
        pos = 0
        while pos < len(data):
            length, _, pos = CodecPGP._length(data, pos)
            if not length or pos + length > len(data):
                return

            kind = data[pos] & 0x7F
            value = data[pos + 1:pos + length]
            pos += length

            if kind == SUBPACKET_CREATED and not found.get('created'):
                found['created'] = CodecPGP._when(value)

            elif kind == SUBPACKET_ISSUER and len(value) == 8:
                found['signers'].add(value.hex().upper())

            elif kind == SUBPACKET_ISSUER_FINGERPRINT and len(value) > 1:
                fingerprint = value[1:].hex().upper()
                found['fingerprints'].add(fingerprint)
                found['signers'].add(
                    fingerprint[-16:] if value[0] == 4 else fingerprint[:16])

            elif kind == SUBPACKET_SIGNER_USER_ID:
                found['user_ids'].append(value.decode('utf-8', 'replace'))

    @staticmethod
    def _read_packets(data, found, depth):
        """
        Reads the packets in the data into found (see packets())
        """
        for tag, body in CodecPGP._iter_packets(data):
            found['packets'].append(tag)
            if not body:
                continue

            if tag == PACKET_PKESK:
                found['encrypted'] = True
                if body[0] == 3 and len(body) >= 9:
                    key_id = body[1:9].hex().upper()
                    # All zeros means it doesn't say who it is for
                    found['recipients'].add(
                        key_id if key_id.strip('0') else 'hidden')

            elif tag == PACKET_SKESK:
                # Opened with a passphrase rather than a key
                found['encrypted'] = True
                found['symmetric'] = True

            elif tag in ENCRYPTED_PACKETS:
                found['encrypted'] = True

            elif tag == PACKET_ONE_PASS and len(body) >= 12 and body[0] == 3:
                found['signers'].add(body[4:12].hex().upper())

            elif tag == PACKET_SIGNATURE:
                if body[0] == 3 and len(body) >= 15:
                    found['created'] = found.get('created') or \
                        CodecPGP._when(body[3:7])
                    found['signers'].add(body[7:15].hex().upper())

                elif body[0] in (4, 6) and len(body) >= 6:
                    # Its details: some are signed (hashed), some aren't
                    size = 2 if body[0] == 4 else 4
                    pos = 4
                    for _ in range(2):
                        length = int.from_bytes(
                            body[pos:pos + size], 'big')
                        pos += size
                        CodecPGP._subpackets(
                            body[pos:pos + length], found)
                        pos += length

            elif tag in (PACKET_PUBLIC_KEY, PACKET_PUBLIC_SUBKEY,
                         PACKET_SECRET_KEY, PACKET_SECRET_SUBKEY) and \
                    len(body) >= 6:
                key = {
                    'subkey': tag in (
                        PACKET_PUBLIC_SUBKEY, PACKET_SECRET_SUBKEY),
                    'secret': tag in (
                        PACKET_SECRET_KEY, PACKET_SECRET_SUBKEY),
                    'version': body[0],
                    'created': CodecPGP._when(body[1:5]),
                    'algorithm': PUBLIC_KEY_ALGORITHMS.get(
                        body[5], str(body[5])),
                    'fingerprint': '',
                    'key_id': '',
                }

                if not key['secret']:
                    # A key's fingerprint is a hash of the key itself
                    # (secret keys hold more than that, so they're left
                    # alone)
                    if body[0] == 4:
                        digest = sha1(
                            b'\x99' + len(body).to_bytes(2, 'big') +
                            body).hexdigest().upper()
                        key['fingerprint'] = digest
                        key['key_id'] = digest[-16:]

                    elif body[0] in (5, 6):
                        digest = sha256(
                            bytes((0x94 + body[0], )) +
                            len(body).to_bytes(4, 'big') +
                            body).hexdigest().upper()
                        key['fingerprint'] = digest
                        key['key_id'] = digest[:16]

                if key['fingerprint']:
                    found['fingerprints'].add(key['fingerprint'])

                found['keys'].append(key)
                found['created'] = found.get('created') or key['created']

            elif tag == PACKET_USER_ID:
                found['user_ids'].append(body.decode('utf-8', 'replace'))

            elif tag == PACKET_LITERAL and len(body) >= 2:
                # The file itself (only seen when it isn't encrypted)
                size = body[1]
                name = body[2:2 + size].decode('utf-8', 'replace')
                name = basename(name.replace('\\', '/')).strip()
                if name and name != '_CONSOLE' and not found.get('filename'):
                    found['filename'] = name

                found['created'] = found.get('created') or \
                    CodecPGP._when(body[2 + size:6 + size])

            elif tag == PACKET_COMPRESSED and depth < MAX_DEPTH:
                inside = CodecPGP._inflate(body[0], body[1:])
                if inside:
                    CodecPGP._read_packets(inside, found, depth + 1)

    @staticmethod
    def _inflate(method, data):
        """
        Returns (at most MAX_INFLATE bytes of) compressed data opened up;
        b'' if it can't be
        """
        try:
            if method == 0:
                # Not compressed after all
                return data[:MAX_INFLATE]

            if method in (1, 2):
                # ZIP (raw deflate) or ZLIB
                return zlib.decompressobj(-15 if method == 1 else 15)\
                    .decompress(data, MAX_INFLATE)

            if method == 3:
                return bz2.BZ2Decompressor().decompress(data, MAX_INFLATE)

        except (zlib.error, OSError, ValueError, EOFError):
            # Damaged (or cut short before anything useful)
            pass

        return b''

    @staticmethod
    def packets(data):
        """
        Returns what the packets in a block's data say (no key needed):
        - encrypted: True when it is encrypted; symmetric when opened with
          a passphrase instead of a key
        - recipients: the key IDs it is encrypted for ('hidden' when it
          doesn't say)
        - signers: the key IDs it was signed with
        - fingerprints: every full key fingerprint seen
        - keys: each key in a key block (key_id, fingerprint, created,
          algorithm, subkey, secret)
        - user_ids: the names (ie: 'Name <email>') given
        - created: when it was signed or made (UTC)
        - filename: the name of the file inside (when not encrypted)
        - packets: the type of each packet seen, in order
        Lists are sorted so the result is the same each time.
        """
        found = {
            'encrypted': False,
            'symmetric': False,
            'recipients': set(),
            'signers': set(),
            'fingerprints': set(),
            'keys': [],
            'user_ids': [],
            'created': None,
            'filename': '',
            'packets': [],
        }

        try:
            CodecPGP._read_packets(bytes(data), found, 0)

        except (IndexError, ValueError, OverflowError):
            # Cut short or damaged; what was read so far is kept
            pass

        for key in ('recipients', 'signers', 'fingerprints'):
            found[key] = sorted(found[key])

        return found

    @staticmethod
    def split_keys(data):
        """
        Returns each public key in a key block's data (bytes) on its own:
        a key and everything after it (its names, signatures and subkeys)
        up to the next key.  Secret keys are left out.
        """
        data = bytes(data)
        keys = []
        current = None
        try:
            for tag, _, start, end in CodecPGP._packet_spans(data):
                if tag in (PACKET_PUBLIC_KEY, PACKET_SECRET_KEY):
                    # A new key starts here
                    current = bytearray() if tag == PACKET_PUBLIC_KEY \
                        else None
                    if current is not None:
                        keys.append(current)

                elif tag == PACKET_SECRET_SUBKEY:
                    # Never kept, even in a public key's block
                    continue

                if current is not None:
                    current += data[start:end]

        except (IndexError, ValueError, OverflowError):
            # Cut short or damaged; the keys read so far are kept
            pass

        return [bytes(key) for key in keys]

    @staticmethod
    def armor(data, kind='PUBLIC KEY BLOCK'):
        """
        Returns binary PGP data (bytes) wrapped in ASCII armor (as text)
        """
        lines = ['-----BEGIN PGP %s-----' % kind, '']
        for offset in range(0, len(data), ARMOR_LINE_BYTES):
            lines.append(b2a_base64(
                data[offset:offset + ARMOR_LINE_BYTES],
                newline=False).decode('ascii'))

        lines.append('=' + b2a_base64(
            CodecPGP.crc24(data).to_bytes(3, 'big'),
            newline=False).decode('ascii'))
        lines.append('-----END PGP %s-----' % kind)
        return '\n'.join(lines) + '\n'

    def encode(self, content, kind='MESSAGE', headers=None,
               mem_buf=1048576):
        """
        Wraps a binary PGP file (a path or an NNTPContent, ie: a .gpg or
        .sig file) in ASCII armor; returns an NNTPAsciiContent (None if the
        file can't be read)
        """
        if not isinstance(content, NNTPContent):
            content = NNTPContent(filepath=content, work_dir=self.work_dir)

        if not content.open():
            return None

        encoded = NNTPAsciiContent(
            filepath='%s.asc' % (content.filename or 'pgp'),
            work_dir=self.work_dir, unique=True)

        encoded.write(b'-----BEGIN PGP %s-----\r\n' % kind.encode())
        for key, value in (headers or {}).items():
            encoded.write(('%s: %s' % (key, value)).encode('utf-8') + EOL)
        encoded.write(EOL)

        # Read in blocks; each line holds 48 bytes (64 characters)
        mem_buf -= mem_buf % ARMOR_LINE_BYTES
        mem_buf = max(mem_buf, ARMOR_LINE_BYTES)
        crc = CRC24_INIT
        while True:
            data = content.stream.read(mem_buf)
            if not data:
                break

            crc = self.crc24(data, crc)
            for offset in range(0, len(data), ARMOR_LINE_BYTES):
                encoded.write(b2a_base64(
                    data[offset:offset + ARMOR_LINE_BYTES],
                    newline=False) + EOL)

        content.close()
        encoded.write(
            b'=' + b2a_base64(crc.to_bytes(3, 'big'), newline=False) + EOL)
        encoded.write(b'-----END PGP %s-----\r\n' % kind.encode())
        encoded.close()
        return encoded

    def reset(self):
        """
        Forgets the block we were decoding
        """
        super().reset()
        self._clear()

    def __lt__(self, other):
        """
        Sorts by name
        """
        return str(self) < str(other)

    def __str__(self):
        """
        A printable version of the codec
        """
        return getattr(self._content, 'filename', '') or 'PGP'

    def __repr__(self):
        """
        Return a printable object
        """
        return '<CodecPGP blocks=%d lines_processed=%d />' % (
            self._blocks, self._lines)
