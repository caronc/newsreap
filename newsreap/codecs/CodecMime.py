# A codec for MIME articles (attachments, base64 and quoted-printable)
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
MIME is how email (and some Usenet posts) carry attachments and text that
isn't plain ASCII.  The article's header says how its body is laid out:

- Content-Type: multipart/mixed; boundary="xyz" means the body is split
  into parts, each starting with a line '--xyz' and having a small header
  of its own; '--xyz--' ends the last one.
- Content-Transfer-Encoding says how a part (or the whole body) is stored:
  base64, quoted-printable (=E9 for an accented letter), uuencode, or as
  it is (7bit, 8bit, binary).

Every part is decoded as the article is read: text parts become an
NNTPAsciiContent (with a charset attribute), everything else an
NNTPBinaryContent named as the part says (with a content_type attribute).
The article's own header is read from the CodecHeader decoding it (see
`header`), since that is where the boundary is given.  Bad data never
stops it: a line that can't be decoded is logged and left out, and a part
that never ends is returned as far as it got.
"""

import re
from binascii import a2b_base64
from binascii import a2b_qp
from binascii import a2b_uu
from binascii import b2a_base64
from binascii import Error as BinAsciiError
from email.header import decode_header
from email.header import make_header
from email.message import Message
from email.parser import BytesHeaderParser
from email import policy
from mimetypes import guess_extension
from mimetypes import guess_type
from os.path import basename

from newsreap.NNTPAsciiContent import NNTPAsciiContent
from newsreap.NNTPBinaryContent import NNTPBinaryContent
from newsreap.NNTPContent import NNTPContent
from newsreap.Utils import SEEK_END
from newsreap.Utils import random_str
from newsreap.codecs.CodecBase import CodecBase

# Logging
import logging
from newsreap.Logging import NEWSREAP_CODEC
logger = logging.getLogger(NEWSREAP_CODEC)

# Defines the new line delimiter
EOL = b'\r\n'

# A boundary line ('--' then 1 to 70 of the characters RFC 2046 allows,
# never ending with a space); '--' at the end closes the last part
BOUNDARY_RE = re.compile(
    br"^--(?P<boundary>[0-9A-Za-z'()+_,./:=?-]"
    br"(?:[ 0-9A-Za-z'()+_,./:=?-]{0,68}[0-9A-Za-z'()+_,./:=?-])?)"
    br'\s*$')

# How the body of an article (or one of its parts) can be stored; the
# others (7bit, 8bit, binary or none at all) are stored as they are
BASE64 = 'base64'
QUOTED_PRINTABLE = 'quoted-printable'
UUENCODE = ('x-uuencode', 'uuencode', 'x-uue', 'uue')

# The encodings worth decoding a body that isn't split into parts for
SINGLE_PART_ENCODINGS = (BASE64, QUOTED_PRINTABLE, *UUENCODE)

# The characters base64 is written with
BASE64_ALPHABET = \
    b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/='

# Spaces and line endings (left out of a line of base64)
WHITESPACE = b' \t\r\n\x0b\x0c'

# A part's header bigger than this isn't a header (the rest is read as
# its body)
MAX_PART_HEADER = 65536

# Parts can hold parts (ie: a message with an attachment, sent with a
# copy as HTML); deeper than this is left as it is
MAX_DEPTH = 16

# How many bytes go on each base64 line when encoding (76 characters)
BASE64_LINE_BYTES = 57


class MimeState:
    """
    Where we are in a MIME article
    """
    # Before the first boundary (ie: 'This is a multi-part message')
    Preamble = 'preamble'

    # Reading a part's own header
    Header = 'header'

    # Reading a part's content
    Body = 'body'

    # A body that isn't split into parts
    Single = 'single'

    # After the last boundary
    Epilogue = 'epilogue'


class CodecMime(CodecBase):
    """
    Decodes MIME articles (see the module description)
    """

    def __init__(self, work_dir=None, header=None, boundary=None,
                 *args, **kwargs):
        """
        - header: where the article's header is read from: the CodecHeader
          decoding it, an NNTPHeader or a dict (None when only the body is
          fetched)
        - boundary: the boundary to look for, when it is already known
        """
        super().__init__(work_dir=work_dir, *args, **kwargs)

        # Where the article's header comes from
        self.header = header

        # A boundary given to us (kept across a reset())
        self.boundary = boundary

        # The headers to post with what encode() made
        self.headers = {}

        self._clear()

    def _clear(self):
        """
        Forgets where we are in an article
        """
        # The boundaries of the parts we're in (innermost last)
        self._boundaries = []
        if self.boundary:
            self._boundaries.append(
                self.boundary.encode('utf-8')
                if isinstance(self.boundary, str) else self.boundary)

        # Where we are; None until the article's header has been read
        self._state = MimeState.Preamble if self._boundaries else None

        # The part being read: its content and how it is stored
        self._content = None
        self._encoding = ''
        self._finished = True

        # A part's header as it is read
        self._head = b''

        # Base64 characters left over from the last line (they're decoded
        # 4 at a time)
        self._carry = b''

        # The line ending held back from the last line (the one before a
        # boundary belongs to the boundary)
        self._eol = b''

        # Part of a line (the rest hasn't arrived yet)
        self._pending = b''

        # How many parts were found
        self._parts = 0

    def _article(self):
        """
        Returns the article's header as an email Message (None if it
        hasn't been read yet)
        """
        header = self.header
        if header is None:
            # Only the body (nothing to go by)
            return Message()

        if getattr(header, 'header_parsed', True) is False:
            # The CodecHeader hasn't finished reading it
            return None

        # A CodecHeader holds its NNTPHeader as what it decoded
        header = getattr(header, 'decoded', header)

        message = Message()
        for key, value in header.items():
            key = str(key)
            if key.lower().startswith('x-content-'):
                # Some headers are kept with X- in front of them
                key = key[2:]
                if key in message:
                    continue

            if key.lower().startswith('content-'):
                message[key] = str(value)

        return message

    def _prepare(self):
        """
        Works out how the article is laid out once its header is read;
        returns False until it has been
        """
        if self._state is not None:
            return True

        message = self._article()
        if message is None:
            return False

        # A body made of parts
        boundary = message.get_param('boundary') \
            if message.get_content_maintype() == 'multipart' else None

        if isinstance(boundary, str) and boundary.strip():
            self._boundaries.append(boundary.strip().encode('utf-8'))
            self._state = MimeState.Preamble
            return True

        encoding = str(message.get(
            'Content-Transfer-Encoding', '')).strip().lower()
        if encoding in SINGLE_PART_ENCODINGS:
            # One encoded body
            self._state = MimeState.Single
            self._begin(message)
            return True

        # Nothing for us; the body is read as it is
        self._state = MimeState.Epilogue
        return True

    def _boundary(self, line):
        """
        Returns 'next' or 'last' when the line is one of our boundaries
        (with the boundary), otherwise (None, None)
        """
        if line[:2] != b'--':
            return None, None

        line = line.rstrip()
        for boundary in reversed(self._boundaries):
            if line == b'--' + boundary:
                return 'next', boundary

            if line == b'--' + boundary + b'--':
                return 'last', boundary

        return None, None

    def detect(self, line, relative=True):
        """
        Returns a dictionary when the line is ours to decode, else None

        With relative, it goes by the article: every line from the first
        boundary to the last (or every line of a body encoded as a whole)
        is ours.  Without it, only a line that looks like a boundary is.
        """
        if isinstance(line, str):
            line = line.encode('utf-8', 'replace')

        if not relative:
            found = BOUNDARY_RE.match(line.rstrip())
            if not found:
                return None

            boundary = found.group('boundary')
            return {
                'key': 'last' if boundary.endswith(b'--') else 'next',
                'boundary': boundary.decode('latin-1'),
            }

        if not self._prepare() or self._state == MimeState.Epilogue:
            return None

        if self._state == MimeState.Preamble:
            # Only the boundary starts the parts
            key, boundary = self._boundary(line)
            if key is None:
                return None

            return {'key': key, 'boundary': boundary.decode('latin-1')}

        # Inside the parts (or an encoded body)
        return {'key': self._state}

    def _begin(self, message):
        """
        Starts a part described by its header (an email Message)
        """
        content_type = message.get_content_type()
        if message.get_content_maintype() == 'multipart':
            # Parts inside this part
            boundary = message.get_param('boundary')
            if isinstance(boundary, str) and boundary.strip() and \
                    len(self._boundaries) < MAX_DEPTH:
                self._boundaries.append(boundary.strip().encode('utf-8'))
                self._state = MimeState.Preamble
                return

            # Not a boundary we can use; read it as it is
            content_type = 'application/octet-stream'

        try:
            filename = message.get_filename() or ''

        except (TypeError, ValueError, LookupError):
            # A name we can't read
            filename = ''

        if '=?' in filename:
            # Written as encoded words (ie: =?utf-8?q?caf=C3=A9.jpg?=);
            # not the standard way, but common
            try:
                filename = str(make_header(decode_header(filename)))

            except (UnicodeError, LookupError, ValueError, TypeError):
                # Kept as it is
                pass

        # Only the name (never a path)
        filename = basename(str(filename).replace('\\', '/')).strip()

        disposition = str(message.get(
            'Content-Disposition', '')).strip().lower()

        try:
            charset = message.get_content_charset()

        except (TypeError, ValueError, LookupError):
            # A character set name we can't read
            charset = None

        self._parts += 1
        self._encoding = str(message.get(
            'Content-Transfer-Encoding', '')).strip().lower()
        self._carry = b''
        self._eol = b''
        self._crc = 0xffffffff
        self._finished = False

        if content_type == 'text/plain' and not filename and \
                not disposition.startswith('attachment'):
            # Text to read
            self._content = NNTPAsciiContent(work_dir=self.work_dir)

        else:
            if not filename:
                # Named after the part and what it holds
                filename = 'part%d%s' % (
                    self._parts, guess_extension(content_type) or '.bin')

            elif content_type == 'application/octet-stream':
                # The name usually says more
                content_type = guess_type(filename)[0] or content_type

            self._content = NNTPBinaryContent(
                filepath=filename, work_dir=self.work_dir, unique=True)

        self._content.content_type = content_type
        self._content.charset = charset or ''

        # Opened for writing now (so an empty part is still a file)
        self._content.open()
        self._state = MimeState.Body \
            if self._state != MimeState.Single else MimeState.Single

    def _finish(self):
        """
        Finishes the part being read; returns its content (None if there
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
        content._is_valid = True
        content.close()
        return content

    @property
    def decoded(self):
        """
        What has been decoded so far (the part being read is finished
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
                'MIME part %d: a line of base64 could not be decoded.' %
                self._parts)
            return

        self._store(decoded)

    def _store(self, decoded):
        """
        Writes decoded bytes to the part being read
        """
        if not decoded:
            return

        self._calc_crc(decoded)
        self._decoded += len(decoded)
        self._content.write(decoded)

    def _write(self, line):
        """
        Decodes one line of the part being read
        """
        self._lines += 1
        if self._encoding == BASE64:
            line = line.translate(None, WHITESPACE)
            if line.translate(None, BASE64_ALPHABET):
                # Something other than base64 (ie: a note or damage)
                logger.warning(
                    'MIME part %d: a line that is not base64 was left '
                    'out.' % self._parts)
                return

            data = self._carry + line
            usable = len(data) - len(data) % 4
            self._carry = data[usable:]
            if usable:
                self._write_base64(data[:usable])
            return

        if self._encoding in UUENCODE:
            stripped = line.strip()
            if not stripped or stripped == b'`' or \
                    stripped.lower() == b'end' or \
                    stripped[:6].lower() == b'begin ':
                # Its first and last lines (and blank ones) hold nothing
                return

            try:
                decoded = a2b_uu(stripped)

            except BinAsciiError:
                # Some encoders write lines longer than they say; only the
                # length the line gives is read
                length = (((stripped[0] - 32) & 63) * 4 + 5) // 3
                try:
                    decoded = a2b_uu(stripped[:length])

                except BinAsciiError:
                    logger.warning(
                        'MIME part %d: a uuencoded line could not be '
                        'decoded.' % self._parts)
                    return

            self._store(decoded)
            return

        # Lines are kept as they are (quoted-printable is decoded); the
        # line ending is held back since the one before a boundary isn't
        # part of the content
        if line.endswith(b'\r\n'):
            line, eol = line[:-2], b'\r\n'

        elif line.endswith(b'\n'):
            line, eol = line[:-1], b'\n'

        else:
            eol = b''

        if self._encoding == QUOTED_PRINTABLE:
            if line.rstrip().endswith(b'='):
                # A soft line break: the line goes on (no line ending)
                line, eol = line.rstrip()[:-1], b''

            try:
                line = a2b_qp(line)

            except BinAsciiError:
                # Kept as it is
                pass

        self._store(self._eol + line)
        self._eol = eol

    def decode(self, stream):
        """
        Decodes the lines of the stream that are ours

        - Returns a part's content once it ends (each part is returned on
          its own; we're asked again for the next one).
        - Returns True when the stream runs out with more to come.
        - Returns None once the last part ends with nothing to return.
        """
        if not self._prepare():
            # Nothing can be done without the article's header
            return None

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

            if self._state == MimeState.Single:
                # Every line is part of the body
                self._write(line)

            elif self._state == MimeState.Header:
                if line.strip():
                    self._head += line
                    if len(self._head) <= MAX_PART_HEADER:
                        continue

                    logger.warning(
                        'MIME part %d has a header that never ends.' % (
                            self._parts + 1))

                # The end of the part's header
                head, self._head = self._head, b''
                self._begin(BytesHeaderParser(
                    policy=policy.compat32).parsebytes(head))

            else:
                key, boundary = self._boundary(line)
                if key is None:
                    if self._state == MimeState.Body:
                        self._write(line)

                    # Otherwise text before the first part (or after the
                    # last); nothing to keep
                    continue

                # The part before it (if any) is done
                content = self._finish()
                self._content = None

                if key == 'next':
                    # Drop the parts inside the part this boundary is for
                    while self._boundaries[-1] != boundary:
                        self._boundaries.pop()

                    self._state = MimeState.Header

                else:
                    # The last part of these parts
                    while self._boundaries and \
                            self._boundaries.pop() != boundary:
                        pass

                    self._state = MimeState.Preamble \
                        if self._boundaries else MimeState.Epilogue

                if content is not None:
                    return content

                if self._state == MimeState.Epilogue:
                    # All done
                    return None

                continue

            if self._max_bytes > 0 and self._decoded >= self._max_bytes:
                # Only this much was wanted
                stream.seek(0, SEEK_END)
                return self._finish()

        return True

    def encode(self, content, name=None, mem_buf=1048576):
        """
        Encodes a file (a path or an NNTPContent) as a MIME attachment:
        returns the body (an NNTPAsciiContent) and sets `headers` to the
        headers it must be posted with; None if the file can't be read
        """
        if not isinstance(content, NNTPContent):
            content = NNTPContent(filepath=content, work_dir=self.work_dir)

        name = name or content.filename or 'attachment.bin'
        boundary = '=_newsreap_%s' % random_str(24)
        content_type = guess_type(name)[0] or 'application/octet-stream'

        if not content.open():
            return None

        encoded = NNTPAsciiContent(
            filepath=name, work_dir=self.work_dir, unique=True)

        # The part's header (the name in quotes, with any quotes in it
        # dropped)
        name = name.replace('"', '').replace('\r', '').replace('\n', '')
        encoded.write(
            b'--%s\r\n'
            b'Content-Type: %s; name="%s"\r\n'
            b'Content-Transfer-Encoding: base64\r\n'
            b'Content-Disposition: attachment; filename="%s"\r\n\r\n' % (
                boundary.encode(), content_type.encode(),
                name.encode('utf-8'), name.encode('utf-8')))

        # Read in blocks; each line holds 57 bytes (76 characters)
        mem_buf -= mem_buf % BASE64_LINE_BYTES
        mem_buf = max(mem_buf, BASE64_LINE_BYTES)
        while True:
            data = content.stream.read(mem_buf)
            if not data:
                break

            for offset in range(0, len(data), BASE64_LINE_BYTES):
                encoded.write(b2a_base64(
                    data[offset:offset + BASE64_LINE_BYTES],
                    newline=False) + EOL)

        content.close()
        encoded.write(b'--%s--\r\n' % boundary.encode())
        encoded.close()

        self.headers = {
            'MIME-Version': '1.0',
            'Content-Type': 'multipart/mixed; boundary="%s"' % boundary,
        }
        return encoded

    def reset(self):
        """
        Forgets the article we were decoding (the header we read from and
        any boundary we were given are kept)
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
        return getattr(self._content, 'filename', '') or 'MIME'

    def __repr__(self):
        """
        Return a printable object
        """
        return '<CodecMime parts=%d lines_processed=%d />' % (
            self._parts, self._lines)
