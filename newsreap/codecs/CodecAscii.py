# A dummy decoder to handle just raw writes used to represent a message body
#
# Copyright (C) 2015-2016 Chris Caron <lead2gold@gmail.com>
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

import codecs
from os.path import splitext

from newsreap.NNTPAsciiContent import NNTPAsciiContent
from newsreap.codecs.CodecBase import CodecBase

# Logging
import logging
from newsreap.Logging import NEWSREAP_CODEC
logger = logging.getLogger(NEWSREAP_CODEC)

# Defines the new line delimiter
EOL = b'\r\n'

# The following is used to determine we're not dealing with any
# binary characters on the line
ASCII_CHARACTERS = bytearray([7, 8, 9, 10, 12, 13, 27]) + \
        bytearray(range(0x20, 0x7f)) + \
        bytearray(range(0x80, 0x100))

# The character sets tried (in order) on text that doesn't say which one it
# uses (or says one that doesn't work).  latin-1 reads anything, so it is
# the last resort.
FALLBACK_CHARSETS = ('utf-8', 'cp1252', 'latin-1')

# What text files from DOS days (ie: .nfo) are written in: the box drawing
# and accented letters of the original IBM PC
DOS_CHARSET = 'cp437'

# The kinds of file written in DOS_CHARSET
DOS_TEXT_FILES = frozenset(('.nfo', '.diz'))

# Character sets newsreaders give when they don't really know (ie: they
# say us-ascii while sending accented letters); these are ignored when the
# text isn't plain ASCII
UNSURE_CHARSETS = frozenset((
    'ascii', 'us-ascii', 'ansi_x3.4-1968', 'us', 'default', 'none',
    'unknown', 'unknown-8bit', 'x-unknown', 'x-user-defined', '7bit',
    '8bit', 'binary', 'iso646-us',
))

# Character sets where one letter can take several bytes (so they are
# tried before UTF-8 when an article says it uses one of them)
MULTIBYTE_CHARSETS = (
    'shift_jis', 'cp932', 'euc_j', 'iso2022', 'gb2312', 'gbk', 'gb18030',
    'big5', 'euc_kr', 'cp949', 'johab', 'cp950', 'hz', 'utf_16', 'utf_32',
    'utf_7',
)

# Byte order marks: a few bytes at the start of a file saying which
# Unicode encoding it uses (the longest are checked first)
BYTE_ORDER_MARKS = (
    (codecs.BOM_UTF32_LE, 'utf-32'),
    (codecs.BOM_UTF32_BE, 'utf-32'),
    (codecs.BOM_UTF8, 'utf-8-sig'),
    (codecs.BOM_UTF16_LE, 'utf-16'),
    (codecs.BOM_UTF16_BE, 'utf-16'),
)


class CodecAscii(CodecBase):
    """
    This is the codec used to store general content parsed that is not encoded
    on an NNTP Server. It doesn't do much but store/track ascii data
    """

    def __init__(self, descriptor=None, work_dir=None, *args, **kwargs):
        super().__init__(descriptor=descriptor,
            work_dir=work_dir, *args, **kwargs)

        # Our Ascii Object we can reference while we store our
        # text content
        self.decoded = NNTPAsciiContent(
            filepath='.message',
            work_dir=self.work_dir,
        )

        # The character set the text says it uses (if it does); see text()
        self.charset = kwargs.get('charset')


    def detect(self, line, relative=True):
        """
        A Simple function that can be used to determine if there is
        ascii on the line.

        It returns None if there is no ascii characters on the line otherwise
        it returns an empty dictionary since there are no meta-keys to extract
        # from a common line

        """
        if isinstance(line, str):
            line = line.encode('utf-8')

        # Anything left over after removing the printable characters means
        # the line holds binary data
        if line.translate(None, ASCII_CHARACTERS):
            # We're dealing with binary data
            return None

        # We always match this type, but we also always return
        # an empty dictionary
        return {}


    def decode(self, stream):
        """ Decode body decoding always stops at the end
            of the line.
        """

        # Read in our data
        data = stream.readline()
        if not data:
            # We're Done; returns the number of bytes decoded
            return self._decoded

        # Convert lines to separated by cr & lf
        decoded = data.rstrip() + EOL

        # Line Tracking
        self._lines += 1

        # Track the number of bytes decoded
        self._decoded += len(decoded)

        # Write data to out stream
        self.decoded.write(decoded)

        # Returns the number of bytes decoded
        return self._decoded


    def text(self):
        """
        Returns (text, charset): what was decoded so far, read with
        to_text()
        """
        return self.to_text(self.decoded.getvalue(), self.charset)

    @staticmethod
    def charset_name(charset):
        """
        Returns the Python name of a character set (ie: 'ISO-8859-1' gives
        'iso8859-1'); None if it isn't one we know
        """
        if isinstance(charset, bytes):
            charset = charset.decode('latin-1')

        if not isinstance(charset, str):
            return None

        charset = charset.strip().strip('"\'').lower()
        if not charset:
            return None

        try:
            return codecs.lookup(charset).name

        except (LookupError, ValueError):
            # Not a character set Python knows
            return None

    @staticmethod
    def to_text(data, charset=None, filename=None):
        """
        Returns (text, charset) for the bytes given, coping with the many
        ways Usenet gets this wrong:
        - a byte order mark (BOM) at the start says which Unicode encoding
          is used
        - otherwise UTF-8 is used whenever the bytes are valid UTF-8, even
          if the article says otherwise (newsreaders often claim latin-1
          or us-ascii while sending UTF-8)
        - otherwise the character set given, then cp1252 and latin-1;
          text mixing them (ie: a UTF-8 reply quoting latin-1) is read a
          line at a time, so each line comes out right
        - files from DOS days (.nfo, .diz) are read as cp437
        Line endings become '\\n', and NUL characters are removed (some
        databases can't store them).  The charset returned is the one
        given (lower case) when it was used, else the one that worked.
        """
        if isinstance(charset, bytes):
            charset = charset.decode('latin-1')

        # The name Python knows it by, and the name it was given as
        known = CodecAscii.charset_name(charset)
        given = charset.strip().strip('"\'').lower() if known else None

        if not data:
            return '', given or FALLBACK_CHARSETS[0]

        if given in UNSURE_CHARSETS and not data.isascii():
            # It doesn't really know
            given = known = None

        if filename and splitext(filename)[1].lower() in DOS_TEXT_FILES \
                and not known:
            # Written for DOS (unless it is UTF-8 or says otherwise)
            known = given = DOS_CHARSET

        # The result, before it is tidied
        text = None

        for mark, name in BYTE_ORDER_MARKS:
            if data.startswith(mark):
                try:
                    text = data.decode(name)
                    given = name.replace('-sig', '')

                except UnicodeDecodeError:
                    # Only looked like one
                    pass

                break

        if text is None and data.isascii():
            # Plain ASCII reads the same in nearly every character set (the
            # ones that don't, ie: iso-2022-jp, say so)
            try:
                text = data.decode(
                    'ascii' if not known or known.startswith(
                        ('utf-16', 'utf-32')) else known)

            except UnicodeDecodeError:
                # Not what it said it was
                text = data.decode('ascii')
                known = given = None

            given = given or FALLBACK_CHARSETS[0]

        multibyte = known is not None and \
            known.replace('-', '_').startswith(MULTIBYTE_CHARSETS)

        if text is None and multibyte and (
                not known.startswith(('utf-16', 'utf-32')) or b'\x00' in data):
            # A character set where letters take several bytes; it is
            # tried first (UTF-8 could read some of these too).  UTF-16
            # and UTF-32 always have NUL bytes in them.
            try:
                text = data.decode(known)

            except UnicodeDecodeError:
                # Not what it said it was
                pass

        if text is None:
            try:
                text = data.decode('utf-8')
                given = 'utf-8'

            except UnicodeDecodeError:
                # Not UTF-8 (or only some of it is)
                pass

        if text is None:
            # A line at a time: UTF-8, the one given, cp1252, latin-1.
            # Any line that is valid UTF-8 is read as UTF-8 (the others
            # with the character set given), since the one given can't be
            # trusted with text that mixes them.
            order = [name for name in (
                FALLBACK_CHARSETS[0], known, *FALLBACK_CHARSETS[1:])
                if name]

            lines = []
            used = {}
            for line in data.split(b'\n'):
                for name in order:
                    try:
                        lines.append(line.decode(name))

                    except UnicodeDecodeError:
                        # Try the next one
                        continue

                    if not line.isascii():
                        used[name] = used.get(name, 0) + len(line)
                    break

            text = '\n'.join(lines)

            # Named after the one most of the text used
            name = max(used, key=used.get) if used else FALLBACK_CHARSETS[0]
            given = given if name == known else name

        # Tidy the line endings, and drop NUL characters
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        if '\x00' in text:
            text = text.replace('\x00', '')

        return text, given

    def reset(self):
        """
        Reset our decoded content
        """
        super().reset()

        # Reset our decoded content
        self.decoded = NNTPAsciiContent(
            filepath='.message',
            work_dir=self.work_dir,
        )


    def __str__(self):
        """
        Return a printable version of the codec
        """
        return repr(self)


    def __repr__(self):
        """
        Return a printable object
        """
        return '<CodecAscii lines_processed=%d />' % (
            self._lines,
        )
