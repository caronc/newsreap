# -*- coding: utf-8 -*-
#
# A base testing class/library to help set some common testing vars
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

import unittest
import yaml

from os import chmod
from os import kill
from os import urandom
from os.path import join
from os.path import exists
from os.path import isdir
from os.path import dirname
from os.path import abspath
from tempfile import gettempdir
from getpass import getuser
from shutil import rmtree
from os import utime

try:
    from newsreap.codecs import CodecBase

except ImportError:
    sys.path.insert(0, join(dirname(dirname(abspath(__file__))), 'newsreap'))
    from newsreap.codecs import CodecBase

from newsreap.Utils import strsize_to_bytes
from newsreap.Utils import mkdir

# Logging
import logging
from newsreap.Logging import add_handler
from newsreap.Logging import NEWSREAP_ENGINE
from newsreap.Logging import SQLALCHEMY_ENGINE

# Silence Logging for Tests (uncomment and/or comment as needed)
# By default having them off is nice because then you can run
# $> nosetests -s
#
#add_handler(logging.getLogger(NEWSREAP_ENGINE), sendto=None)
#add_handler(logging.getLogger(SQLALCHEMY_ENGINE), sendto=None)


class TestBase(unittest.TestCase):

    def setUp(self):
        """Prepare some workable files to make the rest of testing easier"""
        self.config_file = join(
            dirname(abspath(__file__)),
            'var',
            'config.yaml',
        )
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

        if isdir(self.test_dir):
            try:
                rmtree(self.test_dir)

            except OSError, e:
                # An exception occured; get this resolved before
                # doing any more testing
                raise

            except:
                pass

        mkdir(self.test_dir, 0700)
        mkdir(self.tmp_dir, 0700)
        mkdir(self.out_dir, 0700)

        ## Configure Logging
        #handler = logging.StreamHandler(sys.stdout)
        #handler.setLevel(logging.DEBUG)

        ## create a logging format
        #formatter = logging.Formatter(
        #    '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        #handler.setFormatter(formatter)

        ## add the handlers to the logger
        #logger.addHandler(handler)


    def pid_exists(self, pid):
        """A simple function that tests if a PID is running.

        See: http://stackoverflow.com/questions/568271/\
                how-to-check-if-there-exists-a-process-with-a-given-pid
        """
        try:
            # Sending signal 0 to a pid will raise an OSError exception if the
            # pid is not running, and do nothing otherwise.
            kill(pid, 0)

        except OSError:
            return False

        return True

    def touch(self, path, size=None, random=False, perm=None, time=None):
        """Simplify the dynamic creation of files or the updating of their
        modified time.  If a size is specified, then a file of that size
        will be created on the disk. If the file already exists, then the
        size= attribute is ignored (for safey reasons).

        if random is set to true, then the file created is actually
        created using tons of randomly generated content.  This is MUCH
        slower but nessisary for certain tests.

        """

        path = abspath(path)
        if not isdir(dirname(path)):
            mkdir(dirname(path), 0700)

        if not exists(path):
            size = strsize_to_bytes(size)

            if not random:
                f = open(path, "wb")
                if isinstance(size, int) and size > 0:
                    f.seek(size-1)
                    f.write("\0")
                f.close()

            else: # fill our file with randomly generaed content
                with open(path, 'wb') as f:
                    # Fill our file with garbage
                    f.write(urandom(size))

        # Update our path
        utime(path, time)

        if perm is not None:
            # Adjust permissions
            chmod(path, perm)

        # Return True
        return True

    def cleanup(self):
        """Remove the temporary directory"""
        try:
            rmtree(self.test_dir)
        except:
            pass

if __name__ == '__main__':
    unittest.main()
