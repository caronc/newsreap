# -*- coding: utf-8 -*-
#
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

import gevent.monkey
gevent.monkey.patch_all()

import signal
import subprocess
import re
from os import kill
from os import SEEK_SET

from StringIO import StringIO

from gevent import Greenlet
from gevent import sleep
from gevent.event import Event

from datetime import datetime
from datetime import timedelta

# Logging
import logging
from newsreap.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)

# Regular Expression for the extraction of data
NEW_LINE_RE = re.compile(r'\r*\n')

class ReturnCode():
    """
    Return codes when errors occur
    """
    Unknown = -10
    Timeout = -20
    Aborted = -30


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
            elapsed_time = datetime.utcnow() - self._execution_begin

        elapsed_time = (elapsed_time.days * 86400) \
                         + elapsed_time.seconds \
                         + (elapsed_time.microseconds/1e6)

        return elapsed_time

    def _run(self):
        """
        Read from the work_queue, process it using an NNTPRequest object.

        """

        # Make sure our done flag is not set
        self._done.clear()

        # Execute our Process
        p1 = subprocess.Popen(
            self._cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Calculate Current Time
        self._execution_begin = datetime.utcnow()

        # Store some information
        self._pid = p1.pid

        # Calculate Wait Time
        max_wait_time = self._execution_begin + \
                        timedelta(seconds=self._timeout)

        while p1.poll() == None and not self._abort.is_set():
            # Head of Poll Loop

            if self._timeout and \
               datetime.utcnow() >= max_wait_time:
                # Process aborted (took too long)
                try:
                    kill(self._pid, signal.SIGKILL)
                except:
                    pass

                # Force bad return code
                self._returncode = ReturnCode.Timeout

                # Set our elapsed time to now
                self._execution_finish = datetime.utcnow()

                # Retrieve stdout/stderr
                self._stdout = StringIO(p1.stdout.read())
                self._stderr = StringIO(p1.stderr.read())

                # Make sure no one uses the PID anymore
                self._pid = None

                # Set our done flag
                self._done.set()
                return

            # CPU Throttle
            self._abort.wait(self._throttle)

        if p1.poll() == None or self._abort.is_set():
            ## Safety
            try:
                kill(self._pid, signal.SIGKILL)
            except:
                pass

            # Force bad return code
            self._returncode = ReturnCode.Aborted

        else:
            # Store return code
            self._returncode = p1.returncode

        # Execution Completion Time
        self._execution_finish = datetime.utcnow()

        # Retrieve stdout/stderr
        self._stdout = StringIO(p1.stdout.read())
        self._stderr = StringIO(p1.stderr.read())

        # Make sure no one uses the PID anymore
        self._pid = None

        # Set our done flag
        self._done.set()

        # We're done!
        return

    def is_complete(self, timeout=None):
        """
        Returns True if the process has completed its execution
        if timeout is set to a time, then the function blocks up until that
        period of time elapses or the call completes.

        Times should be specified as float values (in seconds).

        """
        if timeout is not None:
            self._done.wait(timeout)

        return self._execution_finish is not None

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
        return self._returncode is 0

    def stdout(self, as_list=True):
        """
        if as_list is set to True, then the stdout results are split on new
        lines into a list object
        """
        # Ensure we're at the head of our buffer
        self._stdout.seek(0L, SEEK_SET)

        if as_list:
            return NEW_LINE_RE.split(self._stdout.read())
        return self._stdout.read()

    def stderr(self, as_list=True):
        """
        if as_list is set to True, then the stdout results are split on new
        lines into a list object

        """
        # Ensure we're at the head of our buffer
        self._stderr.seek(0L, SEEK_SET)

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
