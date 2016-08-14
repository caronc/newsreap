# -*- encoding: utf-8 -*-
#
# Test the SubProcess Object
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

from newsreap.SubProcess import SubProcess
from newsreap.SubProcess import ReturnCode


class SubProcess_Test(TestBase):
    """
    A Class for testing SubProcess

    """

    def test_simple_list(self):
        """
        SubProcess for listing files

        """
        # Set up our threaded process
        sp = SubProcess(['ls', '-1', '/tmp'])
        # Now run our process
        sp.start()
        # Wait for it to complete
        sp.join()

        assert sp.is_complete() is True
        assert sp.response_code() is 0
        assert sp.successful() is True
        assert sp.elapsed() > 0.0
        assert isinstance(sp.stdout(), list)
        assert isinstance(sp.stderr(), list)
        assert isinstance(sp.stdout(as_list=False), basestring)
        assert isinstance(sp.stderr(as_list=False), basestring)

        # Because under the hood stdout and stderr are stored into
        # Stream objects, we need to be able to check that multple
        # calls to the same object still return the same results
        assert len(sp.stdout()) > 1
        assert len(sp.stdout()) > 1
        assert len(sp.stderr()) > 0
        assert len(sp.stderr()) > 0

    def test_bad_execution(self):
        """
        SubProcess for listing files with bad arguments

        """
        # Set up our threaded process
        sp = SubProcess(['ls', '-wtf', '-not-a-valid-argument'])
        # Now run our process
        sp.start()
        # Wait for it to complete
        sp.join()

        assert sp.is_complete() is True
        assert sp.response_code() is not 0
        assert sp.successful() is False
        assert sp.elapsed() > 0.0

        # Because under the hood stdout and stderr are stored into
        # Stream objects, we need to be able to check that multple
        # calls to the same object still return the same results
        assert len(sp.stdout()) > 0
        assert len(sp.stdout()) > 0
        assert len(sp.stderr()) > 1
        assert len(sp.stderr()) > 1

    def test_program_that_will_not_die(self):
        """
        SubProcess for a process that will never stop

        """
        # Set up our threaded process to sleep for 1 day
        sp = SubProcess(['sleep', '1d'])
        # Now run our process
        sp.start()

        # Wait half a second (give thread time to start and run)
        sp.join(timeout=0.5)

        # PID is always None unless the process is still running
        assert sp.pid() is not None
        assert isinstance(sp.pid(), int)

        # The process is still running
        assert sp.is_complete() is False
        # The process is not successful, but we shouldn't be relying on this
        # value anyway because it's not even complete.
        assert sp.successful() is False
        # time should be elapsing
        assert sp.elapsed() > 0.0

        # No output to report at this time because one of the downsides of
        # using subprocess is you can't retrieve the pipes content until the
        # end.  Thus these functions will simply return nothing.
        assert len(sp.stdout()) > 0
        assert len(sp.stderr()) > 0

        # Currently the response code is still unknown
        assert sp.response_code() is ReturnCode.Unknown

        # We'll abort the process now
        sp.abort()

        # Wait for it to complete (because it will complete now)
        sp.join()

        # PID is always None when process is stopped
        assert sp.pid() is None

        # The response code should have change to Aborted
        assert sp.response_code() is ReturnCode.Aborted

        # The process is now complete
        assert sp.is_complete() is True

        # But we should not be successful at this point
        assert sp.successful() is False

    def test_program_timeout(self):
        """
        SubProcess for a process that will be killed simply because it took
        too long to execute.

        """
        # Set up our threaded process to sleep for 1 day
        # Bu
        sp = SubProcess(['sleep', '1d'], timeout=1.0)
        # Now run our process
        sp.start()

        # Wait 2 seconds (we'll die before then though
        sp.join(timeout=2.0)

        # The process is still running
        assert sp.is_complete() is True

        # PID is always None unless the process is still running
        assert sp.pid() is None

        # The process is not successful, but we shouldn't be relying on this
        # value anyway because it's not even complete.
        assert sp.successful() is False
        # time should be elapsing
        assert sp.elapsed() > 0.0

        # No output to report at this time because one of the downsides of
        # using subprocess is you can't retrieve the pipes content until the
        # end.  Thus these functions will simply return nothing.
        assert len(sp.stdout()) > 0
        assert len(sp.stderr()) > 0

        # A Timeout
        assert sp.response_code() is ReturnCode.Timeout

    def test_process_dies_with_object(self):
        """
        SubProcess for a process that will never stop

        """
        # Set up our threaded process to sleep for 1 day
        sp = SubProcess(['sleep', '1d'])
        # Now run our process
        sp.start()

        # Wait half a second (give thread time to start and run)
        sp.join(timeout=0.5)

        # We'll get the pid number of our process
        assert sp.pid() is not None
        pid = sp.pid()

        # Test that the process is indeed running
        assert self.pid_exists(pid) is True

        # now we'll flat out destroy the object (always abort it first)
        sp.abort()
        del sp

        # Test that the process is now dead as a result of the previous action
        assert self.pid_exists(pid) is False
