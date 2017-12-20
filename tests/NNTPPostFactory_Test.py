# -*- coding: utf-8 -*-
#
# Test the NNTPPostFactory Object
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

from os.path import dirname
from os.path import abspath
from os.path import join

try:
    from tests.TestBase import TestBase

except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

from newsreap.NNTPSettings import SERVER_LIST_KEY
from newsreap.NNTPSettings import PROCESSING_KEY
from newsreap.NNTPSettings import NNTPSettings
from newsreap.NNTPManager import NNTPManager
from newsreap.NNTPPostFactory import NNTPPostFactory
from newsreap.Utils import strsize_to_bytes


class NNTPPostFactory_Test(TestBase):
    """
    A Class for testing NNTPPostFactory

    """

    def test_detect_split_size(self):
        """
        Test detect_split_size()

        """
        cfg_file = join(self.tmp_dir, 'NNTPManager.config.yaml')

        server = {
            'username': 'valid',
            'password': 'valid',
            'host': 'localhost',
            'port': 119,
            'secure': 'False',
            'compress': 'False',
            'priority': '1',
            'join_group': 'False',
        }

        processing = {
            # Our test server only supports one connection at this
            # time
            'threads': 1,
            'header_batch_size': 8000,
        }

        # Create a yaml configuration entry we can test with
        # The output is invalid (formatting)
        with open(cfg_file, 'w') as fp:
            fp.write('%s:\n' % PROCESSING_KEY)
            fp.write('   %s' % ('   '.join(
                ['%s: %s\n' % (k, v) for (k, v) in processing.items()])))

            fp.write('%s:\n' % SERVER_LIST_KEY)
            fp.write(' - %s' % ('   '.join(
                ['%s: %s\n' % (k, v) for (k, v) in server.items()])))

        # Settings Object
        setting = NNTPSettings(cfg_file=cfg_file)

        # Create our NNTP Manager Instance
        mgr = NNTPManager(setting)

        pf = NNTPPostFactory(connection=mgr)
        assert(pf.detect_split_size(None) is False)
        assert(pf.detect_split_size('') is False)
        assert(pf.detect_split_size('garbage') is False)

        # 0-100MB     ->   5MB/archive
        assert(pf.detect_split_size(0) == strsize_to_bytes('5MB'))
        assert(pf.detect_split_size(strsize_to_bytes('5MB') - 1) ==
               strsize_to_bytes('5MB'))

        # 100MB-1GB   ->  15MB/archive
        assert(pf.detect_split_size('100M') == strsize_to_bytes('15MB'))
        assert(pf.detect_split_size(strsize_to_bytes('1GB') - 1) ==
               strsize_to_bytes('15MB'))

        # 1GB-5GB     ->  50MB/archive
        assert(pf.detect_split_size('1G') == strsize_to_bytes('50MB'))
        assert(pf.detect_split_size(strsize_to_bytes('5GB') - 1) ==
               strsize_to_bytes('50MB'))

        # 5GB-15GB    ->  100MB/archive
        assert(pf.detect_split_size('5G') == strsize_to_bytes('100MB'))
        assert(pf.detect_split_size(strsize_to_bytes('15GB') - 1) ==
               strsize_to_bytes('100MB'))

        # 15GB-25GB   ->  200MB/archive
        assert(pf.detect_split_size('15G') == strsize_to_bytes('200MB'))
        assert(pf.detect_split_size(strsize_to_bytes('25GB') - 1) ==
               strsize_to_bytes('200MB'))

        # 25GB+       ->  400MB/archive
        assert(pf.detect_split_size('25G') == strsize_to_bytes('400MB'))
        assert(pf.detect_split_size('50G') == strsize_to_bytes('400MB'))
        assert(pf.detect_split_size('100G') == strsize_to_bytes('400MB'))
