# Test the NNTPGetFactory Object
#
# Copyright (C) 2026 Chris Caron <lead2gold@gmail.com>
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
from os.path import dirname
from os.path import abspath
from os.path import join

try:
    from tests.TestBase import TestBase

except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase
from os.path import isdir
from os.path import expanduser

from newsreap.NNTPManager import NNTPManager
from newsreap.NNTPGetFactory import NNTPGetFactory
from newsreap.decorators import hook


@hook(name='socket_read')
def custom_read_hook(**kwargs):
    return 'custom'


class NNTPGetFactory_Test(TestBase):
    """
    Tests the NNTPGetFactory (without connecting anywhere)
    """

    def test_download_path_is_expanded(self):
        """
        The path given to load() is expanded into an absolute path
        """
        mgr = NNTPManager(self.nntp_settings('get_factory'))
        gf = NNTPGetFactory(connection=mgr)

        path = join(self.tmp_dir, 'NNTPGetFactory', 'downloads')
        assert gf.load('<abcd@example.com>', path=path) is not False
        assert gf.base_path == path
        assert gf.name == 'abcd@example.com'
        assert isdir(path)

        # A user (~) path is expanded too
        gf.load('<abcd@example.com>', path='~/')
        assert gf.base_path == expanduser('~')

        mgr.close()

    def test_custom_hooks_keep_transfer_tracking(self):
        """
        Loading custom hooks keeps the factory's transfer progress hooks
        """
        mgr = NNTPManager(self.nntp_settings('get_factory_hooks'))
        gf = NNTPGetFactory(connection=mgr)

        path = join(self.tmp_dir, 'NNTPGetFactory', 'hooks')
        gf.load('<abcd@example.com>', hooks=custom_read_hook, path=path)

        results = [r['result'] for r in mgr.hooks.call(
            'socket_read', xfer_bytes=0, xfer_time=0.0)]
        assert 'custom' in results

        # Our transfer tracking hooks are still in place
        def functions(name):
            return [
                meta['function'].__name__
                for h in mgr.hooks for meta in (h[name] or [])]

        assert 'transfer_rates' in functions('socket_read')
        assert 'transfer_count' in functions('post_get')

        mgr.close()
