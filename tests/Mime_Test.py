# -*- coding: utf-8 -*-
#
# A testing class/library for the Mime Object
#
# Copyright (C) 2017 Chris Caron <lead2gold@gmail.com>
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


import sys
if 'threading' in sys.modules:
    #  gevent patching since pytests import
    #  the sys library before we do.
    del sys.modules['threading']

import gevent.monkey
gevent.monkey.patch_all()
# Import threading after monkey patching
# see: http://stackoverflow.com/questions/8774958/\
#        keyerror-in-module-threading-after-a-successful-py-test-run

from os.path import dirname
from os.path import abspath
from os.path import join
from os.path import isfile

try:
    from tests.TestBase import TestBase

except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

from newsreap.Mime import MIME_TYPES
from newsreap.Mime import Mime
from newsreap.Mime import MimeResponse
from newsreap.NNTPContent import NNTPContent


class Mime_Test(TestBase):

    def test_from_content(self):
        """
        Tests the from_content() function of the Mime Object
        """

        # Prepare our Mime Object
        m = Mime()

        response = m.from_content(None)
        assert(isinstance(response, MimeResponse))
        response = m.from_content("")
        assert(isinstance(response, MimeResponse))
        response = m.from_content(u"")
        assert(isinstance(response, MimeResponse))

        # First we take a binary file
        binary_filepath = join(self.var_dir, 'joystick.jpg')
        assert isfile(binary_filepath)
        with open(binary_filepath, 'rb') as f:
            buf = f.read()

        response = m.from_content(buf)
        assert(isinstance(response, MimeResponse))
        assert(response.type() == 'image/jpeg')
        assert(response.encoding() == 'binary')

    def test_from_file(self):
        """
        Tests the from_file() function of the Mime Object
        """

        # Prepare our Mime Object
        m = Mime()

        response = m.from_file(None)
        assert(response is None)
        response = m.from_file("")
        assert(response is None)
        response = m.from_file(u"")
        assert(response is None)

        # First we take a binary file
        binary_filepath = join(self.var_dir, 'joystick.jpg')

        response = m.from_file(binary_filepath)
        assert(isinstance(response, MimeResponse))
        assert(response.type() == 'image/jpeg')
        assert(response.encoding() == 'binary')

        response = m.from_file(binary_filepath, fullscan=True)
        assert(isinstance(response, MimeResponse))
        assert(response.type() == 'image/jpeg')
        assert(response.encoding() == 'binary')

    def test_from_filename_01(self):
        """
        Attempt to lookup a filetype by it's extension
        """
        # Prepare our Mime Object
        m = Mime()

        response = m.from_filename(None)
        assert(response is None)
        response = m.from_filename("")
        assert(response is None)
        response = m.from_filename(u"")
        assert(response is None)

        # Find RAR Files
        response = m.from_filename('test.rar')
        assert(isinstance(response, MimeResponse))
        assert(response.type() == 'application/x-rar-compressed')
        assert(response.encoding() == 'binary')

        # Support other rar types (r00, r01, ..., r99):
        for inc in range(0, 100):
            response = m.from_filename('test.r%.2d' % inc)
            assert(isinstance(response, MimeResponse))
            assert(response.type() == 'application/x-rar-compressed')
            assert(response.encoding() == 'binary')

        # Find Zip Files
        response = m.from_filename('test.zip')
        assert(isinstance(response, MimeResponse))
        assert(response.type() == 'application/zip')
        assert(response.encoding() == 'binary')

        # Support other zip types (z00, z01, ..., z99):
        for inc in range(0, 100):
            response = m.from_filename('test.z%.2d' % inc)
            assert(isinstance(response, MimeResponse))
            assert(response.type() == 'application/zip')
            assert(response.encoding() == 'binary')

        # Find 7-zip Files
        response = m.from_filename('test.7z')
        assert(isinstance(response, MimeResponse))
        assert(response.type() == 'application/x-7z-compressed')
        assert(response.encoding() == 'binary')

        response = m.from_filename('test.7za')
        assert(isinstance(response, MimeResponse))
        assert(response.type() == 'application/x-7z-compressed')
        assert(response.encoding() == 'binary')

        # Support variations of par files
        response = m.from_filename('test.par')
        assert(isinstance(response, MimeResponse))
        assert(response.type() == 'application/x-par2')
        assert(response.encoding() == 'binary')

        response = m.from_filename('test.par2')
        assert(isinstance(response, MimeResponse))
        assert(response.type() == 'application/x-par2')
        assert(response.encoding() == 'binary')

        response = m.from_filename('test.pdf.vol03+4.par2')
        assert(isinstance(response, MimeResponse))
        assert(response.type() == 'application/x-par2')
        assert(response.encoding() == 'binary')

        for inc in range(0, 100):
            response = m.from_filename('test.part%.2d.7z' % inc)
            assert(isinstance(response, MimeResponse))
            assert(response.type() == 'application/x-7z-compressed')
            assert(response.encoding() == 'binary')

        for inc in range(0, 1000):
            response = m.from_filename('test.7z.%.3d' % inc)
            assert(isinstance(response, MimeResponse))
            assert(response.type() == 'application/x-7z-compressed')
            assert(response.encoding() == 'binary')

            response = m.from_filename('test.7za.%.3d' % inc)
            assert(isinstance(response, MimeResponse))
            assert(response.type() == 'application/x-7z-compressed')
            assert(response.encoding() == 'binary')

        for inc in range(0, 10):
            response = m.from_filename('test.7z%d' % inc)
            assert(isinstance(response, MimeResponse))
            assert(response.type() == 'application/x-7z-compressed')
            assert(response.encoding() == 'binary')

    def test_from_filename_02(self):
        """
        Perform a quick check on all of the extension types we mapped
        """

        # Prepare a quick reverse lookup of extensions to check that
        # we can match against them.
        m = Mime()
        for x in MIME_TYPES:
            if not x[3]:
                # No extension; so don't process these
                continue
            response = m.from_filename('fake_file%s' % x[3])
            assert(isinstance(response, MimeResponse))
            assert(response.encoding() == x[2])
            assert(response.type() == x[0])

    def test_extension_from_mime(self):
        """
        Tests extension matching
        """

        # Initialize our mime object
        m = Mime()

        # Empty content just gives us an empty response
        assert(m.extension_from_mime(None) == '')
        assert(m.extension_from_mime("") == '')
        assert(m.extension_from_mime(u"") == '')

        # there is no lookkup either if the mime simply doesn't exist
        assert(m.extension_from_mime("invalid/mime") == '')

        # But if we know it, we'll pass it back
        assert(m.extension_from_mime('application/x-7z-compressed') == '.7z')

    def test_extension_from_filename(self):
        """
        Test extension lookups by their filename.

        An advanced os.path.splittext() if you will.
        """

        # Initialize our mime object
        m = Mime()

        # Empty content just gives us an empty response
        assert(m.extension_from_filename(None) == '')
        assert(m.extension_from_filename("") == '')
        assert(m.extension_from_filename(u"") == '')

        # NZB File
        assert(m.extension_from_filename("a.longer.name.test.nzb") == '.nzb')

        # Has no extension:
        assert(m.extension_from_filename("test") == '')

        # tar.gz files:
        assert(m.extension_from_filename("test.tar.gz") == '.tar.gz')
        assert(m.extension_from_filename("test.tgz") == '.tgz')

        # tar.bz files
        assert(m.extension_from_filename("test.tar.bz") == '.tar.bz')
        assert(m.extension_from_filename("test.tar.bz2") == '.tar.bz2')
        assert(m.extension_from_filename("test.tbz2") == '.tbz2')
        assert(m.extension_from_filename("test.tbz") == '.tbz')

        # tar.xz files
        assert(m.extension_from_filename("test.tar.xz") == '.tar.xz')
        assert(m.extension_from_filename("test.txz") == '.txz')

        # par file without another extension in front
        assert(m.extension_from_filename(
            "b2d3075d94af88c6742b1fdfc34b225e.vol000+01.par2") ==
            ".vol000+01.par2")

        # Numeric Extension
        assert(m.extension_from_filename(
            "b2d3075d94af88c6742b1fdfc34b225e.32") == ".32")

        # Par Files; we still grab the extension in front
        assert(m.extension_from_filename("test.pdf.vol03+4.par2") ==
               '.pdf.vol03+4.par2')

        # Par Files; can look back at our extensions like tar.gz too!
        assert(m.extension_from_filename("test.tar.gz.vol03+4.par2") ==
               '.tar.gz.vol03+4.par2')

        # pdf.par2 file
        assert(m.extension_from_filename(
            "A.Great.File-We.All.Want.2017.pdf.par2") == '.pdf.par2')

        # handle parts
        assert(m.extension_from_filename("test.part00.7z") == '.part00.7z')

        # handle parts with par2 entries as well
        assert(m.extension_from_filename("test.part00.7z.par2") ==
               '.part00.7z.par2')

        assert(m.extension_from_filename("test.part00.7z.vol03+4.par2") ==
               '.part00.7z.vol03+4.par2')

    def test_from_bestguess(self):
        """
        test from_bestguess()

        bestguess() does the best of both worlds: from_file() and
        from_filename().  It never returns None unless you give it
        bad data.
        """

        # Initialize our mime object
        m = Mime()

        # Empty content just gives us an empty response
        assert(m.from_bestguess(None) is None)
        assert(m.from_bestguess("") is None)
        assert(m.from_bestguess(u"") is None)

        # First we take a binary file
        image = join(self.var_dir, 'joystick.jpg')
        c = NNTPContent(image, work_dir=self.tmp_dir)
        copy = c.copy()

        # since we have a filename, we can pick it up from that
        assert(m.from_bestguess(copy.filename).type() == 'image/jpeg')
        # We can also get it from_file() because even though our temporary
        # file does not have an extension at all, we can still
        assert(m.from_bestguess(copy.path()).type() == 'image/jpeg')
