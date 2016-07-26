# -*- coding: utf-8 -*-
#
# An NNTPRequest Object used by the NNTPManagaer
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

import gevent.monkey
gevent.monkey.patch_all()

from datetime import datetime

from gevent.event import Event

class NNTPRequest(Event):
    """
    This is used with the NNTPManager class; specificially the query()
    function.

    you feed NNTPManager.query() NNTPRequest() objects and get NNTPResponse()
    objects in return.
    """

    def __init__(self, *args, **kwargs):
        """
        Initializes a request object and the 'action' must be a function
        name that exists in the NNTPConnection(), you can optionally specify
        it the args and kwargs too.
        """
        Event.__init__(self)

        # Contains a list of objects returned by request made
        self.response = []

        # A Simple timer
        self._time_start = None
        self._time_finish = None
        self._time_elapsed = None

        # For iterating over decoded items
        self._iter = None


    def timer_start(self):
        """
        Starts internal timer useful for tracking how long the request
        took to perform.
        """
        self._time_start = datetime.now()


    def timer_stop(self):
        """
        Stops the timer and populates the elapsed time.
        """
        self._time_finish = datetime.now()

        # Calculate Processing Time
        time_elapsed = self._time_finish - self._time_start
        self._time_elapsed = (time_elapsed.days * 86400) + time_elapsed.seconds \
                     + (time_elapsed.microseconds/1e6)

        return self._time_elapsed


    def elapsed(self):
        """
        Dynamically Calculates the elapsed time if it hasn't been calculated
        yet otherwise it just returns the current elapsed period
        """
        if self._time_elapsed:
            return self.elapsed

        if not self._time_start:
            return 0

        # Calculate Processing Time
        time_elapsed = datetime.now() - self._time_start
        return (time_elapsed.days * 86400) + time_elapsed.seconds \
                     + (time_elapsed.microseconds/1e6)


    def abort(self):
        """
        A way of aborting the running processes.
        """
        # The set flag is checked and content is not processed
        # if we've done so already
        self.set()


    def run(self, connection, *args, **kwargs):
        """
        Executes actions and returns response object

        You'll want to over-ride this class and populate the response
        object.

        When complete, you want to trip the completion flag so the
        calling process knows when the data has arrived:

            self.set()

        This function should not process anymore if content is already set
        The function returns True if it processed the content okay and
        False if it did not.

        All results should be appended to the response object
        """
        # Ideally you want to run you're code here and override this class

        if self.is_set():
            return False

        # Set our completion flag; this flags any blocking
        # services waiting for us to complete to resume
        self.set()
        return True


    def append(self, result):
        """
        Append function (emulating list)
        """
        self.response.append(result)


    def next(self):
        """
        Python 2 support
        Support stream type functions and iterations
        """
        if not self._iter:
            self._iter = iter(self.response)

        return next(self._iter)


    def __next__(self):
        """
        Python 3 support
        Support iterating through list
        """
        if not self._iter:
            self._iter = iter(self.response)

        return next(self._iter)


    def __iter__(self):
        """
        Mimic iter()
        """
        return iter(self.response)


    def __len__(self):
        """
        support the len() function
        """
        return len(self.response)


    def __repr__(self):
        """
        Return a printable version of the file being read
        """
        return '<NNTPRequest complete=%s elapsed=%ss />' % (
            self.is_set(),
            self.elapsed(),
        )
