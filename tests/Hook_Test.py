# -*- coding: utf-8 -*-
#
# Test the Hook Object
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

from blist import sortedset
from os.path import dirname
from os.path import abspath

try:
    from tests.TestBase import TestBase

except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

from newsreap.Hook import Hook
from newsreap.hooks.post import debug


class Hook_Test(TestBase):
    """
    A Class for testing the Hook class which allows us to call functions we
    hook to.

    """

    def test_general_features(self):
        """
        Test general functionality
        """

        # Create 2 hooks
        hookx = Hook(name='debugX', module=debug, priority=1000)
        hooka = Hook(name='debugA', module=debug, priority=0)

        # Default priority
        hookb = Hook(name='debugX', module=debug, priority=None)
        assert(hookb.priority == Hook.priority)

        # Shares the same debug name
        assert(hookb == hookx)
        assert(hash(hookb) == hash(hookx))
        assert(repr(hookb) == repr(hookx))

        # Test our less than operator
        assert(str(hooka) == 'debugA')
        assert(unicode(hooka) == u'debugA')

        # Test our less than operator
        assert(hooka < hookx)
        assert(hooka != hookx)
        assert(hooka == 'debugA')
        assert(hooka != 'debugX')

        # Call some valid and invalid entries (at this point our wrapped
        # functios all return None)
        assert('pre_upload' in hooka)

        results = hooka.call('pre_upload')
        assert(isinstance(results, sortedset))
        assert(len(results) == 1)
        assert(results[0]['result'] is None)

        # force a bad function into our hook
        assert('bad_entry' not in hooka)

        def bad_entry(*args, **kwargs):
            # Throw an exception
            raise ValueError

        def good_entry(*args, **kwargs):
            # Return the meaning of life
            return 42

        # call our functions (it will fail because they have not been loaded
        # yet)
        results = hooka.call('bad_entry')
        assert(isinstance(results, sortedset))
        assert(len(results) == 0)

        results = hooka.call('good_entry')
        assert(isinstance(results, sortedset))
        assert(len(results) == 0)

        # Assign our new bad function
        hooka['bad_entry'] = bad_entry
        # Assign our new good function
        hooka['good_entry'] = good_entry

        # Test that we notice their presense
        assert('bad_entry' in hooka)
        assert('good_entry' in hooka)

        # Now they're loaded so we can call them
        results = hooka.call('bad_entry')
        # Nothing changes here since we throw an exception;
        # we don't record it's value
        assert(isinstance(results, sortedset))
        assert(len(results) == 0)

        results = hooka.call('good_entry')
        assert(isinstance(results, sortedset))
        assert(len(results) == 1)
        assert(results[0]['result'] == 42)

        assert(hooka['invalid_function'] is None)

        # We can't assign invalid (non-callable types)
        try:
            hooka['invalid_function'] = 4
            # we should never get here
            assert(False)

        except ValueError:
            # We should have thrown an exception and landed here
            assert(True)

        # It's still not in our list
        assert(hooka['invalid_function'] is None)

        for function in iter(hookx):
            # iterates over our functions
            assert(callable(function))

        # Test our length
        assert(len(hookx) == 19)

        # We can delete too
        del hooka['good_entry']
