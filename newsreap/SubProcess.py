# A threaded wrapper to subprocess to prevent blocking
#
# Copyright (C) 2016 Chris Caron <lead2gold@gmail.com>
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


import signal
import re
from os import kill
from os import fsdecode
from os import SEEK_SET

from io import StringIO
from tempfile import TemporaryFile

from gevent import Greenlet
from gevent.event import Event

from datetime import timedelta

from newsreap.Utils import start_program
from newsreap.Utils import utcnow

# Logging
import logging
from newsreap.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)

# Regular Expression for the extraction of data
NEW_LINE_RE = re.compile(r'\r*\n', re.ASCII)


class ReturnCode:
    """
    Return codes when errors occur
    """
    Unknown = -10
    Timeout = -20
    Aborted = -30
    # The program couldn't be started (ie: it isn't installed)
    NotStarted = -40


class SubProcess(Greenlet):
    """
    Threaded execution of a command being called.
    """

    def __init__(self, command, timeout=None):
        """
        Initialize the function

        """
        Greenlet.__init__(self, run=None)

        # we abort if this is set
        self._abort = Event()

        # this is set when an command has completed execution
        self._done = Event()

        # Tracks the PID file of item being executed
        self._pid = None

        # The return code is set after the programs execution
        self._returncode = ReturnCode.Unknown

        # The command itself should a list() identifing the executable as the
        # first entry followed by all of the arguments you wish to pass into
        # it.
        self._cmd = command

        # Since we need to poll until the execution of the process is
        # complete, we need to set a poll time.
        self._throttle = 0.5

        # Track when the execution started
        self._execution_begin = None

        # Track when the execution completed
        self._execution_finish = None

        # The number of seconds at most we will allow the execution of the
        # process to run for before we force it to abort it's operation.

        # Setting this to zero disables this timeout restriction
        self._timeout = 0.0

        if timeout:
            self._timeout = timeout

        # These are populated with the output of the stdout and
        # stderr stream.
        self._stdout = StringIO()
        self._stderr = StringIO()

    def elapsed(self):
        """
        Returns the elapsed time (as a float) of the threaded execution which
        includes the number of microseconds.

        """
        if self._execution_begin is None:
            # No elapsed time has taken place yet
            return 0.0

        if self._execution_finish is not None:
            # Execution has completed, we only want to calculate
            # the execution time.
            elapsed_time = self._execution_finish - self._execution_begin

        else:
            # Calculate Elapsed Time
            elapsed_time = utcnow() - self._execution_begin

        elapsed_time = (elapsed_time.days * 86400) \
                         + elapsed_time.seconds \
                         + (elapsed_time.microseconds/1e6)

        return elapsed_time

    def _run(self):
        """
        Runs the command; whatever happens, the done flag is set at the end
        (so nothing waits on it forever)

        What it writes goes into temporary files rather than pipes: a pipe
        only holds so much, and a program writing a lot (ie: rar or par2)
        would stop and wait for it to be read.
        """

        # Make sure our done flag is not set
        self._done.clear()

        try:
            with TemporaryFile() as out, TemporaryFile() as err:
                try:
                    p1 = start_program(self._cmd, stdout=out, stderr=err)

                except (OSError, ValueError) as e:
                    logger.error('Could not run %s (%s).' % (
                        self._cmd[0] if self._cmd else 'nothing', e))
                    self._returncode = ReturnCode.NotStarted
                    self._execution_begin = self._execution_finish = \
                        utcnow()
                    return

                self._wait(p1)

                # Retrieve stdout/stderr
                out.seek(0)
                err.seek(0)
                self._stdout = StringIO(fsdecode(out.read()))
                self._stderr = StringIO(fsdecode(err.read()))

        finally:
            # Make sure no one uses the PID anymore
            self._pid = None

            # Set our done flag
            self._done.set()

    def _wait(self, p1):
        """
        Waits for the program to finish (stopping it once our timeout is
        up, or when we are aborted) and keeps its return code
        """
        # Calculate Current Time
        self._execution_begin = utcnow()

        # Store some information
        self._pid = p1.pid

        # Calculate Wait Time
        max_wait_time = self._execution_begin + \
            timedelta(seconds=self._timeout or 0)

        stopped = None
        while p1.poll() is None:
            if self._abort.is_set():
                stopped = ReturnCode.Aborted
                break

            if self._timeout and utcnow() >= max_wait_time:
                # Process aborted (took too long)
                stopped = ReturnCode.Timeout
                break

            # CPU Throttle
            self._abort.wait(self._throttle)

        if stopped is not None:
            try:
                kill(self._pid, signal.SIGKILL)

            except OSError:
                # It finished on its own meanwhile
                pass

            # Collect the stopped process so it isn't left behind
            p1.wait()
            self._returncode = stopped

        else:
            # Store return code
            self._returncode = p1.returncode

        # Execution Completion Time
        self._execution_finish = utcnow()

    def is_complete(self, timeout=None):
        """
        Returns True if the process has completed its execution
        if timeout is set to a time, then the function blocks up until that
        period of time elapses or the call completes.

        Times should be specified as float values (in seconds).

        """
        if timeout is not None:
            self._done.wait(timeout)

        return self._done.is_set()

    def response_code(self):
        """
        Returns the result

        """
        return self._returncode

    def successful(self):
        """
        Returns True if the calling action was successful or not.  This call
        can be subjective because it bases it's response simply on whether or
        not a zero (0) was returned by the program called. Usually a non-zero
        value means there was a failure.

        """
        return self._returncode == 0

    def stdout(self, as_list=True):
        """
        if as_list is set to True, then the stdout results are split on new
        lines into a list object
        """
        # Ensure we're at the head of our buffer
        self._stdout.seek(0, SEEK_SET)

        if as_list:
            return NEW_LINE_RE.split(self._stdout.read())
        return self._stdout.read()

    def stderr(self, as_list=True):
        """
        if as_list is set to True, then the stdout results are split on new
        lines into a list object

        """
        # Ensure we're at the head of our buffer
        self._stderr.seek(0, SEEK_SET)

        if as_list:
            return NEW_LINE_RE.split(self._stderr.read())
        return self._stderr.read()

    def pid(self):
        """
        returns the pid number of the running process, but returns None if
        the process is no longer running.
        """
        return self._pid

    def abort(self):
        """
        Abort the executing command

        """
        self._abort.set()
        try:
            kill(self._pid, signal.SIGKILL)
        except:
            pass

        if self._pid:
            self.join(timeout=10.0)

    def __str__(self):
        """
        returns the command being executed

        """
        return ' '.join(self._cmd)

    def __repr__(self):
        """
        Return a printable version of the file being read

        """
        return '<SubProcess cmd=%s execution_time=%ds return_code=%d />' % (
            self._cmd[0],
            self.elapsed(),
            self._returncode,
        )
