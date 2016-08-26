# -*- encoding: utf-8 -*-
#
# Test the NNTP Header Object
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

from os.path import dirname
from os.path import abspath

try:
    from tests.TestBase import TestBase
except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

from newsreap.NNTPHeader import NNTPHeader


class NNTPHeader_Test(TestBase):

    def test_key_formatting(self):
        """
        Make sure we can support proper key indexing/manip
        """
        # Initialize Codec
        hdr = NNTPHeader()
        hdr['message-id'] = 'Test'

        # One item in the queue
        assert len(hdr) == 1

        # Confirm that our key is infact stored as Message-ID
        assert hdr.keys()[0] == 'Message-ID'

        # Multiple ways of accessing the same key since each NNTP Server can
        # tend to store header information slightly different then another
        assert hdr['Message-id'] == 'Test'
        assert hdr['message-id'] == 'Test'
        assert hdr['  message-id'] == 'Test'
        assert hdr['message-id   '] == 'Test'
        assert hdr['  message-id   '] == 'Test'

        # No new key was inserted by accident with above commands
        assert len(hdr) == 1

        # Confirm that our key is still 'just' Message-ID
        assert hdr.keys()[0] == 'Message-ID'

        # Now since we actually look for -id and make sure we change it
        # to -ID, we just want to verfy different variations of this won't
        # get changed
        hdr['my-identifier'] = 'Test2'

        # Now we have 2 header entries
        assert len(hdr) == 2

        # This should still remove the Message-ID entry
        del hdr['mESSAGE-id']

        # We only have 1 entry in our header table now
        assert len(hdr) == 1

        # This simple check just makes sure we don't create a key called
        # My-IDentifier
        assert hdr.keys()[0] == 'My-Identifier'
