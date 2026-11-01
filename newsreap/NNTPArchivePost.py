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

from .objects.index.ArchivedPost import ArchiveKind

# The character sets tried (in order) when an article doesn't say which one
# it uses (or says one that doesn't work); latin-1 always works, so it is
# the last resort
FALLBACK_CHARSETS = ('utf-8', 'cp1252', 'latin-1')

# The first line of a full article (as ARTICLE returns it) is a header
# ("Name: value"); a body on its own (as BODY returns it) usually isn't
HEADER_LINE_RE = re.compile(br'^[A-Za-z0-9-]+:[ \t]')

# Where the header ends and the body starts
HEADER_END_RE = re.compile(br'\r?\n\r?\n')


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

        # What the hooks decide
        self.kind = ArchiveKind.Missing if raw is None else ArchiveKind.Text
        self.text = None
        self.encoding = []
        self.charset = ''
        self.files = []
        self.done = raw is None

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

    @staticmethod
    def decode_text(data, charset=None):
        """
        Returns (text, charset) for the bytes given: decoded with the
        character set given if it works, otherwise the first of
        FALLBACK_CHARSETS that does; line endings become '\\n'
        """
        for name in (charset, *FALLBACK_CHARSETS):
            if not name:
                continue

            try:
                text = data.decode(name)

            except (LookupError, UnicodeDecodeError):
                # Not a character set we know, or not this one
                continue

            return text.replace('\r\n', '\n').replace('\r', '\n'), \
                name.lower()

        # Never reached; latin-1 decodes anything
        return data.decode('latin-1'), 'latin-1'

    def __str__(self):
        return '<%s>' % self.message_id

    def __repr__(self):
        return '<NNTPArchivePost message_id="%s" kind=%s />' % (
            self.message_id, self.kind)
