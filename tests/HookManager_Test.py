# Test the HookManager Object
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

from newsreap.Hook import Hook
from newsreap.HookManager import HookManager
from newsreap.decorators import hook


class HookManager_Test(TestBase):
    """
    A Class for testing the HookManager which allows us to easily manage many
    Hooks.

    """

    def test_general_features(self):
        """
        Test general functionality

        """
        # path to our post hooks
        path = join(
            dirname(dirname(abspath(__file__))),
            'newsreap', 'hooks', 'post',
        )

        # Create our Hook Manager
        hookm = HookManager()
        hookn = HookManager()

        # we support repr() since it makes our objects print friendly
        assert(repr(hookm) == '<HookManager hooks=0 />')

        # Our Length (tracks the number of hooks loaded)
        assert(len(hookm) == 0)

        # Add our module by absolute filepath
        assert(hookn.add(join(path, 'debug.py'), path, 400) is True)
        assert(len(hookn) == 1)
        # Duplicate (by file)
        assert(hookn.add(join(path, 'debug.py'), path, 400) is False)
        assert(len(hookn) == 1)

        # Support iterators
        hook = next(iter(hookn))
        assert(isinstance(hook, Hook))
        assert(hook == 'debug')

        # pyc files aren't so happy
        assert(hookn.add(join(path, 'debug.pyc'), path, 400) is False)
        assert(len(hookn) == 1)

        # Add our module
        assert(hookm.add('debug', path, 400) is True)
        assert(len(hookm) == 1)

        # We can use the 'in' keyword
        assert('debug' in hookm)

        # Adding the same element won't work
        assert(hookm.add('debug', path, 400) is False)

        # We'll still be left with 1 item
        assert(len(hookm) == 1)

        # Create a bad entry
        def bad_entry(*args, **kwargs):
            # Throw an exception
            raise ValueError

        def another_entry(*args, **kwargs):
            # Throw an exception
            return 1

        # Assignments work
        hookm['debug']['bad_entry'] = bad_entry

        # We have one entry in our list
        assert(len(hookm['debug']['bad_entry']) == 1)

        # we can't add it again; it won't work
        assert(hookm['debug'].add(
            bad_entry, name='bad_entry') is False)

        # Store our object that will throw
        assert(hookm['debug'].add(
            bad_entry, name='test_function') is True)
        assert(hookm['debug'].add(
            another_entry, name='test_function', priority=1) is True)

        # We have to entries stored in test_function
        assert(len(hookm['debug']['test_function']) == 2)

        # It doesn't overwrite the last, it actually appends another function
        assert(len(hookm['debug']['test_function']) == 2)

        # Add another hook
        assert(hookm.add(Hook(name="debug2")) is True)
        assert(hookm['debug2'].add(
            another_entry, name='test_function', priority=1) is True)

        results = hookm.call('test_function')
        # One call would have thrown an exception
        assert(len(results) == 1)

        assert('debug' in hookm)

        it = next(iter(hookm.keys()))
        assert('debug' == it)

        hookm.call('pre_upload')
        # We can directly hash into our item
        assert(isinstance(hookm[0], Hook))

        hookm.reset()
        assert(len(hookm) == 0)

    def test_managers_can_be_merged(self):
        """
        Adding one HookManager to another merges their hooks
        """
        @hook(name='my_hook')
        def first(**kwargs):
            return 'first'

        @hook(name='my_hook', priority=5)
        def second(**kwargs):
            return 'second'

        hookm = HookManager()
        assert hookm.add(first) is True

        hookn = HookManager()
        assert hookn.add(second) is True

        # Merge; both functions now live in our second manager
        assert hookn.add(hookm) is True
        assert [r['result'] for r in hookn.call('my_hook')] == \
            ['second', 'first']

        # Merging again adds nothing new
        assert hookn.add(hookm) is False

        # The first manager is left as it was
        assert [r['result'] for r in hookm.call('my_hook')] == ['first']

        # Brand new hooks are added as they are
        hooko = HookManager()
        assert hooko.add(Hook(name='other')) is True
        assert hookn.add(hooko) is True
        assert 'other' in hookn

    def test_calling_leaves_hooks_unchanged(self):
        """
        Calling a hook spread across several modules doesn't change them
        """
        @hook(name='my_hook')
        def first(**kwargs):
            return 'first'

        @hook(name='my_hook')
        def second(**kwargs):
            return 'second'

        hook_a = Hook(name='a')
        hook_a.add(first)
        hook_b = Hook(name='b')
        hook_b.add(second)

        hookm = HookManager()
        hookm.add(hook_a)
        hookm.add(hook_b)

        assert len(hookm.call('my_hook')) == 2
        assert len(hookm.call('my_hook')) == 2

        # Each hook still only holds its own function
        assert len(hook_a['my_hook']) == 1
        assert len(hook_b['my_hook']) == 1

    def test_hook_names_can_be_iterated(self):
        """
        iterkeys() walks the hook names; 'in' checks them
        """
        hookm = HookManager()
        hookm.add(Hook(name='first'))
        hookm.add(Hook(name='second'))

        assert sorted(hookm.iterkeys()) == ['first', 'second']
        assert sorted(hookm.keys()) == ['first', 'second']
        assert 'first' in hookm
        assert 'third' not in hookm
