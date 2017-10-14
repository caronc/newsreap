# -*- coding: utf-8 -*-
#
# A container for handling mime manipulation
#
# Copyright (C) 2015-2017 Chris Caron <lead2gold@gmail.com>
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
This module works best when it has access to the libmagic library.

This was created because there are just to many variations of python-magic
when told to be installed in various distributions.
There are so many sources to wrapping the libmagic library. This wasn't
intended to be 'yet another one', but it was the only way I could guarentee
consistency.  Having looked through all of the sources, i put together
this module here which is a generic combination of them all that only
attempts to achieve the results we need for the newsreap program. Sources:
 - https://github.com/file/file (official)
       * see python/magic.py
 - https://github.com/threatstack/libmagic (fork)
       * see python/magic.py
 - https://github.com/ahupp/python-magic
       * for some reason has the most likes but based on wrapping the
         official sources identified above.
"""

import gevent.monkey
gevent.monkey.patch_all()

import re
import ctypes
from ctypes.util import find_library
from ctypes import c_char_p
from ctypes import c_int
from ctypes import c_size_t
from ctypes import c_void_p
from gevent.lock import Semaphore

# Logging
import logging
from newsreap.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)

# Our libmagic flag
_libmagic = ctypes.cdll.LoadLibrary(
    find_library('magic') or
    find_library('magic1') or
    find_library('cygmagic-1')
)

# Define a dictionary that we will use to store our library references within
_magic = {}
if not _libmagic or not _libmagic._name:
    # Toggle our HAS_LIBMAGIC Flag
    HAS_LIBMAGIC = False

else:
    # LIBMAGIC loaded successfully

    HAS_LIBMAGIC = True

    # Define our magic pointer type
    magic_t = c_void_p

    # The buffer is where content is looked up in; this is not thread safe!
    _magic['buffer'] = _libmagic.magic_buffer
    _magic['buffer'].restype = c_char_p
    _magic['buffer'].argtypes = [magic_t, c_void_p, c_size_t]

    _magic['open'] = _libmagic.magic_open
    _magic['open'].restype = magic_t
    _magic['open'].argtypes = [c_int]

    _magic['close'] = _libmagic.magic_close
    _magic['close'].restype = None
    _magic['close'].argtypes = [magic_t]

    # This gets set after a from_buffer() call
    _magic['error'] = _libmagic.magic_error
    _magic['error'].restype = c_char_p
    _magic['error'].argtypes = [magic_t]

    _magic['errno'] = _libmagic.magic_errno
    _magic['errno'].restype = c_int
    _magic['errno'].argtypes = [magic_t]

    _magic['file'] = _libmagic.magic_file
    _magic['file'].restype = c_char_p
    _magic['file'].argtypes = [magic_t, c_char_p]

    _magic['load'] = _libmagic.magic_load
    _magic['load'].restype = c_int
    _magic['load'].argtypes = [magic_t, c_char_p]

# Flag constants for open and setflags
MAGIC_NONE = 0
MAGIC_DEBUG = 1
MAGIC_SYMLINK = 2
MAGIC_COMPRESS = 4
MAGIC_DEVICES = 8
MAGIC_MIME_TYPE = 16
MAGIC_CONTINUE = 32
MAGIC_CHECK = 64
MAGIC_PRESERVE_ATIME = 128
MAGIC_RAW = 256
MAGIC_ERROR = 512
MAGIC_MIME_ENCODING = 1024
MAGIC_MIME = 1040
MAGIC_APPLE = 2048

# The delimiters we use to divy up our results
MAGIC_LIST_RE = re.compile('\s*[;]+\s*')

# Used to detect encoding type
ENCODING_PARSE_RE = re.compile(r'^(charset=)?(?P<encoding>.*)$', re.I)

# Support deep scan of files
TYPE_PARSE_RE = re.compile(
    r'([\\]+(?P<offset>[0-9]+)?-\s+)?(?P<mtype>[a-z0-9_-]+/[a-z0-9_-]+)\s*',
    re.I,
)

# servers. This table is used for matching based on filename in addition to
# performing reverse lookups if we know the type. This list is intentionally
# ordered from the most likely match from a usenet download to the least.

# The Mime Type Regular Expression
MT_PREFIX_RE = r'^\s*(.+[\\/])?(?P<fname>[^\\/]+)?'

# The syntax is as follows:
# ('mime type', regex, 'single .extension if available')
MIME_TYPES = (
    # rar, r00, r01, etc
    ('application/x-rar-compressed', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.(r(ar|[0-9]{2})))\s*$', flags=re.I), 'binary', '.rar'),

    # zip, z00, z01, etc
    ('application/zip', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.(z(ip|[0-9]{2})))\s*$', flags=re.I), 'binary', '.zip'),

    # 7z,
    #  - supports .7z, 7za
    #  - supports .7z0, .7z1, .7z2, etc
    #  - supports .part00.7z, .part01.7z, etc
    #  - supports .7z.000, .7z.001, etc
    #  - supports .7za.000, .7za.001, etc
    ('application/x-7z-compressed', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.(part(?P<part>[0-9]+)\.7z|7za?\.?(?P<part0>[0-9]+)?))\s*$',
                                               flags=re.I), 'binary', '.7z'),

    # B-Zipped (v2) Tape Archive  .tar.bz2
    ('application/tar+bzip2', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.t(ar\.bz2|bz2))\s*$', flags=re.I), 'binary', '.tar.bz2'),

    # B-Zipped (v1) Tape Archive  .tar.bz
    ('application/tar+bzip', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.t(ar\.bz|bz))\s*$', flags=re.I), 'binary', '.tar.bz'),

    # GZipped Tape Archive (TAR)
    ('application/tar+gzip', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.t(ar\.gz|gz))\s*$', flags=re.I), 'binary', '.tar.gz'),

    # Tape Archive (TAR)
    ('application/x-tar', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.tar)\s*$', flags=re.I), 'binary', '.tar'),

    # GZip
    ('application/x-gzip', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.gz)\s*$', flags=re.I), 'binary', '.gz'),

    # B-Zip 2
    ('application/x-bzip2', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.bz2)\s*$', flags=re.I), 'binary', '.bz2'),

    # B-Zip
    ('application/x-bzip', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.bz)\s*$', flags=re.I), 'binary', '.bz'),

    # LZH Compressed .lza, .lha
    ('application/x-lzh-compressed', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.l[zh]a)\s*$', flags=re.I), 'binary', '.lza'),

    # .avi
    ('video/x-msvideo', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.avi)\s*$', flags=re.I), 'binary', '.avi'),

    # .mkv
    ('video/x-matroska', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.mkv)\s*$', flags=re.I), 'binary', '.mkv'),

    # mpg, mpeg, mpe, mpg mpeg2, mpga
    ('video/mpg', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.mp(e|e?g2?|a))\s*$', flags=re.I), 'binary', '.mpg'),

    # mp4, mpeg4, mpe4, mpg4
    ('video/mp4', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.mpe?g?4)\s*$', flags=re.I), 'binary', '.mp4'),

    # .ogv
    ('video/ogg', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.ogg)\s*$', flags=re.I), 'binary', '.ogg'),

    # .3gp, .3gpp
    ('video/3gpp', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.3gpp?)\s*$', flags=re.I), 'binary', '.3gp'),

    # .3g2
    ('video/3gpp2', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.3g2)\s*$', flags=re.I), 'binary', '.3g2'),

    # .ts
    ('video/mp2t', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.ts)\s*$', flags=re.I), 'binary', '.ts'),

    # .mov, .qt
    ('video/quicktime', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.(mov|qt))\s*$', flags=re.I), 'binary', '.mov'),

    # .divx
    ('video/divx', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.divx)\s*$', flags=re.I), 'binary', '.divx'),

    # .webm
    ('video/webm', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.webm)\s*$', flags=re.I), 'binary', '.webm'),

    # .flv
    ('video/x-flv', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.flv)\s*$', flags=re.I), 'binary', '.flv'),

    # .m4v
    ('video/x-m4v', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.m4v)\s*$', flags=re.I), 'binary', '.m4v'),

    # .mng
    ('video/x-mng', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.mng)\s*$', flags=re.I), 'binary', '.mng'),

    # .asf, asx
    ('video/x-ms-asf', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.as[xf])\s*$', flags=re.I), 'binary', '.asf'),

    # .wmv
    ('video/x-ms-wmv', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.wmv)\s*$', flags=re.I), 'binary', '.wmv'),

    # .mid, midi, .kar
    ('audio/midi', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.(midi?|kar))\s*$', flags=re.I), 'binary', '.midi'),

    # .mp3
    ('audio/mpeg', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.mp3)\s*$', flags=re.I), 'binary', '.mp3'),

    # .wav
    ('audio/x-wav', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.wav)\s*$', flags=re.I), 'binary', '.wav'),

    # .oga
    ('audio/oga', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.oga)\s*$', flags=re.I), 'binary', '.oga'),

    # .m4a
    ('audio/x-m4a', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.m4a)\s*$', flags=re.I), 'binary', '.m4a'),

    # .ra, .ram
    ('audio/x-realaudio', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.ram?)\s*$', flags=re.I), 'binary', '.ra'),

    # gif file (.gif)
    ('image/gif', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.gif)\s*$', flags=re.I), 'binary', '.gif'),

    # Jpeg file (.jpeg, .jpg)
    ('image/jpeg', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.jpe?g)\s*$', flags=re.I), 'binary', '.jpeg'),

    # PNG Image (.png)
    ('image/png', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.png)\s*$', flags=re.I), 'binary', '.png'),

    # TIFF File (.tif, .tiff)
    ('image/tiff', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.tiff?)\s*$', flags=re.I), 'binary', '.tiff'),

    # VND Bitmap (.wbmp)
    ('image/vnd.wap.wbmp', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.wbmp)\s*$', flags=re.I), 'binary', '.wbmp'),

    # Windows BMP (.bmp)
    ('image/x-ms-bmp', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.bmp)\s*$', flags=re.I), 'binary', '.bmp'),

    # Quicktime Image (.qti, .qtif)
    ('image/x-quicktime', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.qtif?)\s*$', flags=re.I), 'binary', '.qti'),

    # Scalar Vector Graphics (.svg, svgz)
    ('image/image/svg+xml', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.svgz?)\s*$', flags=re.I), 'binary', '.svg'),

    # JNG Format (.jng)
    ('image/image/x-jng', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.jng)\s*$', flags=re.I), 'binary', '.jng'),

    # Icon Format (.ico)
    ('image/image/x-icon', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.ico)\s*$', flags=re.I), 'binary', '.ico'),

    # Support image files (.iso and .img)
    ('application/x-iso9660-image', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.(iso|img))\s*$', flags=re.I), 'binary', '.iso'),

    # Support PAR files (.par, .par2)
    ('application/x-par2', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.par2?)\s*$', flags=re.I), 'binary', '.par'),

    # text files
    ('text/plain', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.(txt|diz|nfo))\s*$', flags=re.I), 'ascii', '.txt'),

    # Rich Text files
    ('application/rtf', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.rtf)\s*$', flags=re.I), 'ascii', '.rtf'),

    # Microsoft Document
    ('application/msword', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.doc)\s*$', flags=re.I), 'binary', '.doc'),

    # NZB-File
    ('text/x-nzb', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.nzb)\s*$', flags=re.I), 'ascii', '.nzb'),

    # Windows Binary/Executable Files
    ('application/x-dosexec', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.(msi|bin|exe|com|scr|dll))\s*$', flags=re.I),
     'binary', '.exe'),

    # Debian Packages (.deb)
    ('application/vnd.debian.binary-package', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.(deb))\s*$', flags=re.I), 'binary', '.deb'),

    # RPM Package (.rpm and .drpm)
    ('application/x-redhat-package-manager', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.(d?rpm))\s*$', flags=re.I), 'binary', '.rpm'),

    # html, htm, and shtml
    ('text/html', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.s?html?)\s*$', flags=re.I), 'ascii', '.html'),

    # Shockwave Flash
    ('application/x-shockwave-flash', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.swf)\s*$', flags=re.I), 'binary', '.swf'),

    # sql
    ('application/x-sql', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.sql)\s*$', flags=re.I), 'ascii', '.sql'),

    # css
    ('text/css', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.css)\s*$', flags=re.I), 'ascii', '.css'),

    # xml
    ('text/xml', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.xml)\s*$', flags=re.I), 'ascii', '.xml'),

    # Perl (.pl, .pm)
    ('application/x-perl', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.pl)\s*$', flags=re.I), 'ascii', '.pl'),

    # Python ByteCode (.pyo, .pyc)
    ('application/x-python', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.py(o|c))\s*$', flags=re.I), 'binary', '.pyc'),

    # Python .py
    ('application/x-python', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.py)\s*$', flags=re.I), 'ascii', '.py'),

    # javascript (.js)
    ('application/javascript', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.js)\s*$', flags=re.I), 'ascii', '.js'),

    # atom (.atom)
    ('application/atom+xml', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.atom)\s*$', flags=re.I), 'ascii', '.atom'),

    # rss (.rss)
    ('application/rss+xml', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.rss)\s*$', flags=re.I), 'ascii', '.rss'),

    # java archive (.jar, .war, .ear)
    ('application/java-archive', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.(jar|war|ear))\s*$', flags=re.I), 'binary', '.jar'),

    # PDF (.pdf)
    ('application/pdf', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.pdf)\s*$', flags=re.I), 'binary', '.pdf'),

    # EPub (E-Reader Format) (.epub)
    ('application/epub+zip', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.epub)\s*$', flags=re.I), 'binary', '.epub'),

    # Mobi (Amazon E-Reader Format) (.mobi, .prc)
    ('application/x-mobipocket-ebook', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.(mobi|prc))\s*$', flags=re.I), 'binary', '.mobi'),

    # Postscript (.ps, .eps, .ai)
    ('application/postscript', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.(e?ps|ai))\s*$', flags=re.I), 'binary', '.ps'),

    # (Ascii based) Certificates (.crt, .pem)
    ('application/x-x509-ca-cert', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.(crt|pem))\s*$', flags=re.I), 'ascii', '.crt'),

    # (binary based) Certificates (.crt, .pem)
    ('application/x-x509-ca-cert', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.der)\s*$', flags=re.I), 'binary', '.der'),

    # ====================================================================== #

    # The second last entry must always catch all that has an extension
    ('application/octet-stream', re.compile(MT_PREFIX_RE +
     r'(?P<ext>\.[^ \t]+)\s*$'), '', ''),

    # The last entry must always be the empty - catch all
    ('application/octet-stream', re.compile(MT_PREFIX_RE +
     r'(?P<ext>)\s+$'), '', ''),
)


class MimeResponse(object):
    """
    Our mime result
    """
    def __init__(self, mime_type='application/x-empty',
                 mime_encoding='binary'):
        """
        Initializes our MimeResponse object
        """

        # e.g. text/x-python
        self._mime_type = mime_type
        if isinstance(self._mime_type, basestring):
            # The mime type is stored as (index, detected_type)
            # since it's possible to do a deep scan, you can match
            # on different parts of the file to acquire different
            # content.  The default is just index 0 (head of the file)
            # followed by the defined mime-type
            self._mime_type = [(0, self._mime_type)]

        # e.g. binary, us-ascii
        self._mime_encoding = mime_encoding

    def type(self):
        """
        Always return the first element in our array, and just return
        the mime-type itself
        """
        return self._mime_type[0][1]

    def encoding(self):
        """
        Returns the encoding of our file (binary/ascii)
        """
        return self._mime_encoding

    def extension(self):
        # TODO: using type, return the extension using our map above
        return ''

    def __str__(self):
        return self.type()

    def __unicode__(self):
        return unicode(self.type())


class Mime(object):
    """
    Our mime response object.
    """
    def __init__(self, magic_file=None):
        """
        Create a new libmagic wrapper.
        magic_file - Another magic file other then the default
        """

        # The lock allows us to be thread-safe
        self.lock = Semaphore(value=1)

        # Tracks the errno/errstr set after each call
        self.errno = 0
        self.errstr = ''

        # Load our magic file
        self.magic_file = magic_file

        # Initialize our flags
        # our flags
        self.flags = (MAGIC_MIME | MAGIC_MIME_ENCODING)

    def from_content(self, content, uncompress=False, fullscan=False):
        """
        detect the type based on the content specified.

          content - the content to process
          uncompress - Try to look inside compressed files.
          fullscan - Scan entire file and extract as much details as possible

        """
        if not HAS_LIBMAGIC:
            return None

        if not content:
            return MimeResponse()

        # our pointer to our libmagic object
        ptr = None

        # our flags
        flags = self.flags

        if fullscan:
            flags |= MAGIC_CONTINUE

        if uncompress:
            flags |= MAGIC_COMPRESS

        try:
            self.lock.acquire(blocking=True)
            # 'application/octet-stream; charset=binary'

            # Acquire a pointer
            ptr = _magic['open'](flags)

            # Acquire a pointer
            _magic['load'](ptr, self.magic_file)
            self.errno = _magic['errno'](ptr)
            if self.errno == 0:

                # Achieve our results as a list
                _typ, _enc = MAGIC_LIST_RE.split(self._tostr(_magic['buffer'](
                    ptr,
                    self._tobytes(content),
                    len(content),
                )))

                # Acquire our errorstr (if one exists)
                self.errstr = _magic['error'](ptr)

                _typs = []
                for n, _typ_re in enumerate(TYPE_PARSE_RE.finditer(_typ)):
                    if n is 0:
                        _typs.append((0, _typ_re.group('mtype')))
                    else:
                        _typs.append((
                                int(_typ_re.group('offset')),
                                _typ_re.group('mtype')
                            ),
                        )

                _enc_re = ENCODING_PARSE_RE.match(_enc)
                if _enc_re:
                    _enc = _enc_re.group('encoding')

                mr = MimeResponse(mime_type=_typs, mime_encoding=_enc)

                # return our object
                return mr

        except TypeError:
            # This occurs if buffer check returns None
            # Acquire our errorstr (if one exists)
            self.errstr = _magic['error'](ptr)
            if self.errstr:
                # an error occured; return None
                return None

            # If we get here, we didn't even get an error
            return MimeResponse(
                mime_type='application/octet-stream',
                mime_encoding='binary',
            )

        finally:
            if ptr is not None:
                # Release our pointer
                _magic['close'](ptr)

            # Release our lock
            self.lock.release()

        # We failed if we got here, return nothing
        return None

    def from_file(self, path, uncompress=False, fullscan=False):
        """
        detect the type based on the content specified.

          path - the file to process
          uncompress - Try to look inside compressed files.
          fullscan - Scan entire file and extract as much details as possible

        """
        if not HAS_LIBMAGIC:
            return None

        if not path:
            return None

        # our pointer to our libmagic object
        ptr = None

        # our flags
        flags = self.flags

        if fullscan:
            flags |= MAGIC_CONTINUE

        if uncompress:
            flags |= MAGIC_COMPRESS

        try:
            self.lock.acquire(blocking=True)
            # 'application/octet-stream; charset=binary'

            # Acquire a pointer
            ptr = _magic['open'](flags)

            # Acquire a pointer
            _magic['load'](ptr, self.magic_file)
            self.errno = _magic['errno'](ptr)
            if self.errno == 0:

                # Achieve our results as a list
                _typ, _enc = MAGIC_LIST_RE.split(self._tostr(_magic['file'](
                    ptr,
                    self._tobytes(path),
                )))

                # Acquire our errorstr (if one exists)
                self.errstr = _magic['error'](ptr)

                _typs = []
                for n, _typ_re in enumerate(TYPE_PARSE_RE.finditer(_typ)):
                    if n is 0:
                        _typs.append((0, _typ_re.group('mtype')))
                    else:
                        _typs.append((
                                int(_typ_re.group('offset')),
                                _typ_re.group('mtype')
                            ),
                        )

                _enc_re = ENCODING_PARSE_RE.match(_enc)
                if _enc_re:
                    _enc = _enc_re.group('encoding')

                mr = MimeResponse(mime_type=_typs, mime_encoding=_enc)

                # return our object
                return mr

        except TypeError:
            # This occurs if buffer check returns None
            # Acquire our errorstr (if one exists)
            self.errstr = _magic['error'](ptr)
            if self.errstr:
                # an error occured; return None
                return None

            # If we get here, we didn't even get an error
            return MimeResponse(
                mime_type='application/octet-stream',
                mime_encoding='binary',
            )

        finally:
            if ptr is not None:
                # Release our pointer
                _magic['close'](ptr)

            # Release our lock
            self.lock.release()

        # We failed if we got here, return nothing
        return None

    def from_filename(self, filename):
        """
        detect the type based on the filename (and/or extension) specified.

        """

        if not filename:
            # Invalid
            return None

        mime_type = next((m for m in MIME_TYPES if m[1].match(filename)), None)

        if mime_type:
            return MimeResponse(
                mime_type=mime_type[0],
                mime_encoding=mime_type[2],
            )

        # No match
        return None

    def get_extension(self, mime_type):
        """
        takes a mime type and returns the file extension that bests matches
        it. This function returns an empty string if the mime type can't
        be looked up correctly, otherwise it returns the matching extension.
        """

        if not mime_type:
            # Invalid; but return an empty string
            return ''

        # iterate over our list and return on our first match
        return next((m[3] for m in MIME_TYPES if mime_type == m[0]), '')

    def _tostr(self, s, encoding='utf-8'):
        if s is None:
            return None
        if isinstance(s, str):
            return s
        try:  # keep Python 2 compatibility
            return str(s, encoding)
        except TypeError:
            return str(s)

    def _tobytes(self, b, encoding='utf-8'):
        if b is None:
            return None
        if isinstance(b, bytes):
            return b
        try:  # keep Python 2 compatibility
            return bytes(b, encoding)
        except TypeError:
            return bytes(b)
