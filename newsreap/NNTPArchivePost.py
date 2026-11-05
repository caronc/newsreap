# One article being archived (what the archive hooks work on)
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

import re
from email import message_from_bytes
from email import policy
from email.header import decode_header
from email.header import make_header
from os.path import splitext

from .codecs.CodecAscii import CodecAscii

# The character sets tried (in order) when text doesn't say which one it
# uses (still importable from here)
from .codecs.CodecAscii import FALLBACK_CHARSETS  # noqa: F401
from .objects.index.ArchivedPost import ArchiveKind

# The first line of a full article (as ARTICLE returns it) is a header
# ("Name: value"); a body on its own (as BODY returns it) usually isn't
HEADER_LINE_RE = re.compile(br'^[A-Za-z0-9-]+:[ \t]')

# Where the header ends and the body starts
HEADER_END_RE = re.compile(br'\r?\n\r?\n')

# The kinds of file whose text is kept when one is posted (small and
# readable: release notes, checksum lists, ...)
TEXT_FILES = frozenset((
    '.nfo', '.diz', '.txt', '.sfv', '.md5', '.sha1', '.sha256',
))

# The biggest text file kept
MAX_TEXT_FILE = 65536


def sent(raw):
    """
    Returns an article as the server sent it: every line it sent ended
    with a line ending, but the last one's is taken off with the end of
    the article
    """
    if raw and not raw.endswith(b'\n'):
        return raw + b'\r\n'
    return raw


class NNTPArchivePost:
    """
    One article being archived.

    It holds what the server said about it (its subject, poster, ...) and
    the article itself, as fetched.  The archive hooks (see NNTPArchiver)
    read it and fill in what gets kept:
    - kind: text or binary (see ArchiveKind)
    - text: what it says, decoded so it can be searched (None for binaries)
    - encoding / charset: how its text was stored
    - files: what the files it holds or carries say about themselves
    - pgp / pgp_keys: the PGP keys it names and the key blocks it carries
      (only kept when the pgp setting is on; see NNTPKeyring)
    - done: set by a hook that has dealt with the article, so the hooks
      after it leave it alone
    """

    def __init__(self, message_id, raw=None, subject='', poster='',
                 posted=None, size=0, groups=None, server=''):
        """
        Prepares an article for archiving

        - raw: the article as fetched (bytes); None if it couldn't be
        - groups: the groups it was found in
        - server: the server it came from
        """
        self.message_id = message_id.strip().strip('<>')
        self.raw = raw
        self.subject = subject or ''
        self.poster = poster or ''
        self.posted = posted
        self.size = size or 0
        self.groups = set(groups or ())
        self.server = server or ''

        # Its score and tags (see NNTPFilter)
        self.score = 0
        self.tags = frozenset()

        # What the hooks decide
        self.kind = ArchiveKind.Missing if raw is None else ArchiveKind.Text
        self.text = None
        self.encoding = []
        self.charset = ''
        self.files = []
        self.pgp = []
        self.pgp_keys = []
        self.done = raw is None

        # Why a message encrypted for one of our keys couldn't be decrypted
        # (see WaitReason); empty if nothing failed
        self.locked = ''

        # The parsed article (worked out when first needed)
        self._message = None

        # Split the header from the body
        self.head = b''
        self.body = raw or b''
        if raw and HEADER_LINE_RE.match(raw):
            found = HEADER_END_RE.search(raw)
            if found:
                self.head = raw[:found.start()]
                self.body = raw[found.end():]

            else:
                # Nothing but a header
                self.head = raw
                self.body = b''

    @property
    def message(self):
        """
        The article parsed as an email message (see Python's email
        package), which is how MIME parts are read; None without a header
        """
        if self._message is None and self.head:
            # The lenient (compat32) policy copes with the broken headers
            # Usenet is full of
            self._message = message_from_bytes(
                self.head + b'\r\n\r\n' + self.body, policy=policy.compat32)

        return self._message

    def header(self, name, default=''):
        """
        Returns a header's value (with any encoded words decoded), or the
        default if the article doesn't have it
        """
        if self.message is None:
            return default

        value = self.message.get(name)
        if value is None:
            return default

        try:
            return str(make_header(decode_header(str(value)))).strip()

        except (UnicodeError, LookupError, ValueError, TypeError):
            # Badly encoded; use it as it is
            return str(value).strip()

    def lines(self):
        """
        Iterates over the lines of the body (as bytes, without their line
        endings)
        """
        return iter(self.body.splitlines())

    def add_file(self, filename='', codec='', content_type='', size=None,
                 part=None, total=None, crc32=''):
        """
        Notes a file the article holds or carries (the file itself is never
        kept)
        """
        self.files.append({
            'filename': (filename or '').strip()[:256],
            'codec': codec[:16],
            'content_type': (content_type or '')[:128],
            'size': size,
            'part': part,
            'total': total,
            'crc32': (crc32 or '').lower()[:8],
        })

    def add_pgp(self, key_id, role, created=None):
        """
        Notes a PGP key the article names (see PGPRole: signed by it,
        encrypted for it, ...); key_id is its 16 character hex key ID, or
        'hidden' when the sender left it out (see HIDDEN_KEY_ID)
        """
        if key_id:
            self.pgp.append((key_id, role, created))

    def add_text(self, filename, data):
        """
        Keeps the text of a small text file the article holds (see
        TEXT_FILES), after any text it has already; returns True if it was
        kept
        """
        if not filename or not data or len(data) > MAX_TEXT_FILE or \
                splitext(filename)[1].lower() not in TEXT_FILES:
            return False

        text = self.decode_text(data, filename=filename)[0].strip()
        if not text:
            return False

        self.text = '%s\n\n%s' % (self.text, text) if self.text else text
        return True

    @staticmethod
    def decode_text(data, charset=None, filename=None):
        """
        Returns (text, charset) for the bytes given; see
        CodecAscii.to_text() for how the character set is worked out
        - filename: the file the text came from (if any); .nfo files are
          read as DOS text
        """
        return CodecAscii.to_text(data, charset, filename)

    def __str__(self):
        return '<%s>' % self.message_id

    def __repr__(self):
        return '<NNTPArchivePost message_id="%s" kind=%s />' % (
            self.message_id, self.kind)
