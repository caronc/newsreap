# -*- encoding: utf-8 -*-
#
# Test the NNTPnzb Object
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

import sys
if 'threading' in sys.modules:
    #  gevent patching since pytests import
    #  the sys library before we do.
    del sys.modules['threading']

import gevent.monkey
gevent.monkey.patch_all()

from os.path import join
from os.path import dirname
from os.path import isfile
from os.path import abspath

try:
    from tests.TestBase import TestBase

except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

from newsreap.NNTPnzb import NNTPnzb
from newsreap.NNTPSegmentedFile import NNTPSegmentedFile


class NNTPnzb_Test(TestBase):
    """
    A Class for testing NNTPnzb which handles all the XML
    parsing and simple iterations over our XML files.

    """

    def test_general_features(self):
        """
        Open a valid nzb file and make sure we can parse it
        """
        # No parameters should create a file
        nzbfile = join(self.var_dir, 'Ubuntu-16.04.1-Server-i386.nzb')
        assert isfile(nzbfile)
        # create an object containing our nzbfile
        nzbobj = NNTPnzb(nzbfile=nzbfile)

        # Test iterations
        for article in nzbobj:
            assert isinstance(article, NNTPSegmentedFile)

        # Test Length (this particular file we know has 55 entries
        # If we don't hardcode this check, we could get it wrong below
        assert len(nzbobj) == 55

        # We should be able to iterate over each entry and get
        # the same count
        assert len(nzbobj) == sum(1 for c in nzbobj)

        assert nzbobj.is_valid() is True

        assert nzbobj.gid() == '8c6b3a3bc8d925cd63125f7bea31a5c9'

    def test_gid_retrievals(self):
        """
        Test different variations of gid retrievals based on what
        defines a valid GID entry in an NZB File.
        """
        # No parameters should create a file
        nzbfile = join(
            self.var_dir,
            'Ubuntu-16.04.1-Server-i386-nofirstsegment.nzb',
        )
        assert isfile(nzbfile)

        nzbobj = NNTPnzb(nzbfile=nzbfile)
        assert nzbobj.is_valid() is True

        # GID Is not retrievable
        assert nzbobj.gid() is None

        # No parameters should create a file
        nzbfile = join(
            self.var_dir,
            'Ubuntu-16.04.1-Server-i386-nofile.nzb',
        )
        assert isfile(nzbfile)

        nzbobj = NNTPnzb(nzbfile=nzbfile)
        assert nzbobj.is_valid() is True

        # GID Is not retrievable
        assert nzbobj.gid() is None


    def test_bad_files(self):
        """
        Test different variations of bad file inputs
        """
        # No parameters should create a file
        nzbfile = join(self.var_dir, 'missing.file.nzb')
        assert not isfile(nzbfile)

        nzbobj = NNTPnzb(nzbfile=nzbfile)
        assert nzbobj.is_valid() is False
        assert nzbobj.gid() is None

        # Test Length
        assert len(nzbobj) == 0
