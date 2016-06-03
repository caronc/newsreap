# -*- encoding: utf-8 -*-
#
# A base testing class/library to help set some common testing vars
#
# Copyright (C) 2015 Chris Caron <lead2gold@gmail.com>
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

import unittest
import yaml

from os import makedirs
from os.path import join
from os.path import dirname
from os.path import abspath
from tempfile import gettempdir
from getpass import getuser
from shutil import rmtree

#print 'here %s' % dirname(dirname(abspath(__file__)))
try:
    from lib.codecs import CodecBase

except ImportError:
    print 'importing %s' % dirname(dirname(abspath(__file__)))
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from lib.codecs import CodecBase

# Logging
import logging
from lib.Logging import add_handler
from lib.Logging import NEWSREAP_ENGINE
from lib.Logging import SQLALCHEMY_ENGINE

# Silence Logging for Tests
add_handler(logging.getLogger(NEWSREAP_ENGINE), sendto=None)
add_handler(logging.getLogger(SQLALCHEMY_ENGINE), sendto=None)


class TestBase(unittest.TestCase):

    def setUp(self):
        """Prepare some workable files to make the rest of testing easier"""
        self.config_file = join(dirname(
            dirname(dirname(abspath(__file__)))), 'config.yaml')
        stream = file(self.config_file, 'r')
        self.config = yaml.load(stream)
        self.test_dir = join(
            gettempdir(),
            'nntp-test-%s' % getuser(),
        )

        self.out_dir = join(self.test_dir, 'out')
        self.tmp_dir = join(self.test_dir, 'tmp')

        # Monkey Patch a few paths
        CodecBase.DEFAULT_TMP_DIR = self.tmp_dir
        CodecBase.DEFAULT_OUT_DIR = self.out_dir

        # Prepare our variable path
        self.var_dir = join(abspath(dirname(__file__)), 'var')

        try:
            rmtree(self.test_dir)
        except:
            pass
        makedirs(self.test_dir, 0700)
        makedirs(self.tmp_dir, 0700)
        makedirs(self.out_dir, 0700)

        ## Configure Logging
        #handler = logging.StreamHandler(sys.stdout)
        #handler.setLevel(logging.DEBUG)

        ## create a logging format
        #formatter = logging.Formatter(
        #    '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        #handler.setFormatter(formatter)

        ## add the handlers to the logger
        #logger.addHandler(handler)


    def cleanup(self):
        """Remove the temporary directory"""
        try:
            rmtree(self.test_dir)
        except:
            pass

if __name__ == '__main__':
    unittest.main()
