# -*- coding: utf-8 -*-
#
# A base testing class/library to test the Filters
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

import pytz
from os.path import abspath
from os.path import dirname
from datetime import datetime
from copy import deepcopy as copy

try:
    from tests.TestBase import TestBase

except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

from newsreap.Utils import strsize_to_bytes
from newsreap.NNTPSimpleFilter import NNTPSimpleFilter


class NNTPSimpleFilter_Test(TestBase):

    def setUp(self):
        """
        Grab a few more things from the config
        """
        super(NNTPSimpleFilter_Test, self).setUp()

        # Create a template entry we can clone from to make
        # thing easier to manipulate in each individual test
        self.template_entry = {
            'id': 'testuniqueid',
            'article_no': 1,
            'poster': 'Unknown Poster <strange@email.com>',
            'date': datetime(2000, 1, 1, 0, 0, 0, tzinfo=pytz.UTC),
            'subject': 'What.A.Great.Show (1/1) "what.a.great.show.mkv" Yenc (1/1)',
            'size': strsize_to_bytes('25M'),
            'lines': 3000,
            'group': 'alt.binaries.test',
            'xgroups': { 'alt.binaries.ohwell': 2, 'alt.binaries.ohwell2': 3, },
        }

    def test_blacklist_bad_files(self):
        """
        Blacklist testing of bad files
        """

        sf = NNTPSimpleFilter()
        entry = copy(self.template_entry)

        # hash table always starts empty and is populated on demand
        assert len(sf._regex_hash) == 0

        # Test against bad file extensions:
        for e in [ 'exe', 'pif', 'application', 'gadget', 'msi', 'msp', 'com',
                  'scr', 'hta', 'cpl', 'msc', 'jar', 'bat', 'vb', 'vbs',
                  # Encrypted VBE Script file
                  'vbe',
                  # Javascript (Windows can execute these outside of browsers)
                  # so treat it as bad
                  'js', 'jse',
                  # Windows Script File
                  'ws', 'wsf',
                  # Windows PowerShell Scripts
                  'ps1', 'ps1xml', 'ps2', 'ps2xml', 'psc1', 'psc2',
                  # Monad Scripts (later renamed to Powershell)
                  'msh', 'msh1', 'msh1xml', 'msh2', 'msh2xml',
                  # Windows Explorer Command file
                  'scf',
                  # A link to a program on your computer (usually
                  # populated with some malicious content)
                  'lnk',
                  # A text file used by AutoRun
                  'inf',
                  # A windows registry file
                  'reg',
                 ]:

            entry['subject'] = 'What.A.Great.Show (1/1) ' +\
                    '"what.a.great.show.%s" Yenc (1/1)' % e

            assert sf.blacklist(**entry) == True

    def test_scoring_video_files(self):
        """
        Test that we correctly score video files
        """

        sf = NNTPSimpleFilter()
        entry = copy(self.template_entry)

        # Expected Score
        score = 25

        # Test against video files:
        for e in [ 'avi', 'mpeg', 'mpg', 'mp4', 'mov', 'mkv', 'asf',
                  'ogg', 'iso', 'rm' ]:

            entry['subject'] = 'What.A.Great.Show (1/1) ' +\
                    '"what.a.great.show.%s" Yenc (1/1)' % e

            assert sf.score(**entry) == score

            # now test that we can support .??? extensions after
            # the initial one
            for i in range(1000):
                entry['subject'] = 'What.A.Great.Show (1/1) ' +\
                        '"what.a.great.show.%s.%.3d" Yenc (1/1)' % (e, i)
                assert sf.score(**entry) == score

    def test_scoring_image_files(self):
        """
        Test that we correctly score image files
        """

        sf = NNTPSimpleFilter()
        entry = copy(self.template_entry)

        # Expected Score
        score = 15

        # Test against video files:
        for e in [ 'jpg', 'jpeg', 'gif', 'png', 'bmp' ]:

            entry['subject'] = 'What.A.Great.Image (1/1) ' +\
                    '"what.a.great.image.%s" Yenc (1/1)' % e

            assert sf.score(**entry) == score

    def test_scoring_compressed_files(self):
        """
        Test that we correctly score compressed files
        """

        sf = NNTPSimpleFilter()
        entry = copy(self.template_entry)

        # Expected Score
        score = 25

        # Test against video files:
        for e in [ 'rar', '7z', 'zip', 'tgz', 'tar.gz']:

            entry['subject'] = 'What.A.Great.Archive (1/1) ' +\
                    '"what.a.great.archive.%s" Yenc (1/1)' % e

            assert sf.score(**entry) == score

            # now test that we can support .??? extensions after
            # the initial one
            for i in range(1000):
                entry['subject'] = 'What.A.Great.Archive (1/1) ' +\
                        '"what.a.great.archive.%s.%.3d" Yenc (1/1)' % (e, i)
                assert sf.score(**entry) == score

        # Test Sub Rar and Zip files (R?? and Z??)
        for e in [ 'r', 'z' ]:
            for i in range(100):
                entry['subject'] = 'What.A.Great.Archive (1/1) ' +\
                    '"what.a.great.archive.%s%.2d" Yenc (1/1)' % (e, i)

                assert sf.score(**entry) == score
                for ii in range(1000):
                    entry['subject'] = 'What.A.Great.Archive (1/1) ' +\
                        '"what.a.great.archive.%s%.2d.%.3d" Yenc (1/1)' % (
                            e, i, ii)
                    assert sf.score(**entry) == score

    def test_scoring_recovery_files(self):
        """
        Test that we correctly score image files
        """

        sf = NNTPSimpleFilter()
        entry = copy(self.template_entry)

        # Expected Score
        score = 10

        # Test against video files:
        for e in [ 'par', 'par2' ]:

            entry['subject'] = 'What.A.Great.Recovery (1/1) ' +\
                    '"what.a.great.recovery.%s" Yenc (1/1)' % e
            assert sf.score(**entry) == score
