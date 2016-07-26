# -*- coding: utf-8 -*-
#
# A manager that can control multiple NNTP connections and
# orchastrate them together in a single class
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

import signal
from gevent import Greenlet
from gevent.event import Event
from gevent.queue import Queue
from gevent.queue import Empty as EmptyQueueException

from newsreap.NNTPConnection import NNTPConnection
from newsreap.NNTPConnection import XoverGrouping
from newsreap.NNTPConnectionRequest import NNTPConnectionRequest

# Logging
import logging
from newsreap.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)


class Worker(Greenlet):
    """
    This class actually performs all of the grunt work, a worker
    is a Greenlet or Thread which are spawned based on the number
    of connections defined.
    """

    def __init__(self, connection, work_queue):
        Greenlet.__init__(self, run=None)

        # Store NNTPConnection Object
        self._connection = connection

        # Our only communication to the outside world
        # event is a simple event handler of type Event()
        # we only unblock if this is set
        self._event = Event()

        # We always process as many queue entries as we can
        # This contains..
        #TODO: This should contain some sort of container object that
        #       allows us to process many Message-ID, but we need to know
        #       their group they belong into.  We also need their expected
        #       filename.  We want to allow the NZBFiles to force a filename
        #       optionally, otherwise the yenc= is used instead. A minimum
        #       of those 3 entries `must` be in this input queue.  Perhaps
        #       we should just use NNTPContent entries? (they don't have
        #       an article ID though)
        self._work_queue = work_queue

        # our exit flag, it is set externally
        self._exit = Event()


    def _run(self):
        """
        Read from the work_queue, process it using an NNTPRequest object.
        """
        # block until we have an event to handle.
        #print "Worker %s ready!" % self
        while not self._exit.is_set():
            #print "Worker %s loop!" % self

            #self._event.wait(timeout=1.0)
            #if self._exit.is_set():
            #    # We're done
            #    return

            try:
                request = self._work_queue.get()
                if request.is_set():
                    # Process has been aborted or is no longer needed
                    continue

            except StopIteration:
                # Got Exit
                return

            except EmptyQueueException:
                # Nothing available for us
                continue

            # If we reach here, we have a request to process
            request.run(connection=self._connection)


class NNTPManager(object):
    """
    Used to manage multiple NNTPConnections via worker threads.

    The intent is to accept the same actions an NNTPConnection()
    would take but then re-assing the request to an available worker.

    NNTPManager() requires an NNTPSettings() object to work correctly

    """

    def __init__(self, settings, *args, **kwargs):
        """
        Initialize the NNTPManager() based on the provided settings.
        it is presumed settings is a loaded NNTPSettings() object.
        """

        # A connection pool of NNTPConnections
        self._pool = []

        # A mapping of active worker threads
        self._workers = []

        # Queue Control
        self._work_queue = Queue()

        # Map signal
        gevent.signal(signal.SIGQUIT, gevent.kill)

        if not len(settings.nntp_servers):
            logger.warning("There were no NNTP Servers defined to load.")
            return

        for _ in range(settings.nntp_processing['threads']):
            con = NNTPConnection(**settings.nntp_servers[0])
            if len(settings.nntp_servers) > 1:
                # Append backup servers (if any defined)
                for idx in range(1, len(settings.nntp_servers)):
                    _con = NNTPConnection(**settings.nntp_servers[idx])
                    con.append(_con)

            # Appened connection to pool
            self._pool.append(con)

        logger.debug("Loaded %d pools with %d NNTP Servers" % (
            len(self._pool), len(settings.nntp_servers),
        ))
        return


    def get_connection(self):
        """
        Grabs a connection from the thread pool and returns it by reference.
        It is NOT safe to us this function if you have concurrent thread going
        on at the same time.

        The function attempts to find a connection that has already been
        established first.  If it fails to then it just returns the
        first connection in the queue.

        """

        if len(self._pool):
            # Find the first connected connection
            connection = next((c for c in self._pool \
                if c.connected == True), None)

            if connection:
                return connection
            # Return the first entry if nothing is already connected
            return self._pool[0]

        # Otherwise there is nothing to return
        return None


    def connect(self):
        """
        Sets up NNTP Workers and connections to server

        """
        if len(self._workers):
            return True

        for entry in self._pool:
            logger.debug("Spawning worker...")
            g = Worker(
                connection=entry,
                work_queue=self._work_queue,
            )
            g.start()

            # Track our worker
            self._workers.append(g)


    def close(self):
        """
        closes out any open threads and cleans up NNTPManager
        gracefully.
        """
        while not self._work_queue.empty():
            try:
                self._work_queue.get_nowait()
            except EmptyQueueException:
                # Nothing available for us
                break

        for worker in self._workers:
            # Toggle Exit
            worker._exit.set()
            self._work_queue.put(StopIteration)

        for entry in self._pool:
            entry.close()

        for worker in self._workers:
            logger.info("Waiting for workers to exit.")
            worker.join()

        del self._pool
        del self._workers
        self._workers = []
        self._pool = []


    def group(self, name, block=True):
        """
        Queue's an NNTPRequest for processing and returns a call
        to GROUP (fetching details on it specifically)

        If block is not set to true, then it is up to the calling
        application to monitor the request until it's complete.

        Since the Request Object is inherited from a gevent.Event()
        object, one can easily check the status with the ready()
        call or, wait() if they want to block until content is ready.

        See http://www.gevent.org/gevent.event.html#module-gevent.event
        for more details.

        To remain thread-safe; it's recommended that you do not change
        any of the response contents or articles contents prior to
        it's flag being set (marking completion)

        """
        if not len(self._workers):
            # Handle connections
            self.connect()

        # Push request to the queue
        request = NNTPConnectionRequest(actions=[
            # Append list of NNTPConnection requests in a list
            # ('function, (*args), (**kwargs) )
            ('group', (name, ), { }),
        ])

        # Append to Queue for processing
        self._work_queue.put(request)

        # We'll know when our request has been handled because the
        # request is included in the response.
        if block:
            request.wait()

            # Simplify things by returning just the response object
            # instead of the request
            return request.response[0]

        # We aren't blocking, so just return the request object
        return request


    def groups(self, filters=None, lazy=True, block=True):
        """
        Queue's an NNTPRequest for processing and returns the
        NNTP Group lists.

        If block is not set to true, then it is up to the calling
        application to monitor the request until it's complete.

        Since the Request Object is inherited from a gevent.Event()
        object, one can easily check the status with the ready()
        call or, wait() if they want to block until content is ready.

        See http://www.gevent.org/gevent.event.html#module-gevent.event
        for more details.

        To remain thread-safe; it's recommended that you do not change
        any of the response contents or articles contents prior to
        it's flag being set (marking completion)

        """
        if not len(self._workers):
            # Handle connections
            self.connect()

        # Push request to the queue
        request = NNTPConnectionRequest(actions=[
            # Append list of NNTPConnection requests in a list
            # ('function, (*args), (**kwargs) )
            ('groups', list(), {'filters': filters, 'lazy': lazy }),
        ])

        # Append to Queue for processing
        self._work_queue.put(request)

        # We'll know when our request has been handled because the
        # request is included in the response.
        if block:
            request.wait()

            # Simplify things by returning just the response object
            # instead of the request
            return request.response[0]

        # We aren't blocking, so just return the request object
        return request


    def stat(self, id, full=None, group=None, block=True):
        """
        Queue's an NNTPRequest for processing and returns it's
        response if block is set to True.

        If block is not set to true, then it is up to the calling
        application to monitor the request until it's complete.

        Since the Request Object is inherited from a gevent.Event()
        object, one can easily check the status with the ready()
        call or, wait() if they want to block until content is ready.

        See http://www.gevent.org/gevent.event.html#module-gevent.event
        for more details.

        To remain thread-safe; it's recommended that you do not change
        any of the response contents or articles contents prior to
        it's flag being set (marking completion)

        """
        if not len(self._workers):
            # Handle connections
            self.connect()

        # Push request to the queue
        request = NNTPConnectionRequest(actions=[
            # Append list of NNTPConnection requests in a list
            # ('function, (*args), (**kwargs) )
            ('stat', (id, ), {'group': group, 'full': full}),
        ])

        # Append to Queue for processing
        self._work_queue.put(request)

        # We'll know when our request has been handled because the
        # request is included in the response.
        if block:
            request.wait()

            # Simplify things by returning just the response object
            # instead of the request
            return request.response[0]

        # We aren't blocking, so just return the request object
        return request


    def get(self, id, tmp_dir, group=None, block=True):
        """
        Queue's an NNTPRequest for processing and returns it's
        response if block is set to True.

        If block is not set to true, then it is up to the calling
        application to monitor the request until it's complete.

        Since the Request Object is inherited from a gevent.Event()
        object, one can easily check the status with the ready()
        call or, wait() if they want to block until content is ready.

        See http://www.gevent.org/gevent.event.html#module-gevent.event
        for more details.

        To remain thread-safe; it's recommended that you do not change
        any of the response contents or articles contents prior to
        it's flag being set (marking completion)

        """
        if not len(self._workers):
            # Handle connections
            self.connect()

        # Push request to the queue
        request = NNTPConnectionRequest(actions=[
            # Append list of NNTPConnection requests in a list
            # ('function, (*args), (**kwargs) )
            ('get', (id, tmp_dir), {'group': group}),
        ])

        # Append to Queue for processing
        self._work_queue.put(request)

        # We'll know when our request has been handled because the
        # request is included in the response.
        if block:
            request.wait()

            # Simplify things by returning just the response object
            # instead of the request
            return request.response[0]

        # We aren't blocking, so just return the request object
        return request


    def xover(self, group, start=None, end=None, sort=XoverGrouping.BY_POSTER_TIME, block=True):
        """

        If the start or end time are set to `None` then they default to the
        low/high (head or tail) watermark respectively.

        overhead is performed to find these locations in the group identified.

        If the start or end time is a datetime variable, then additional
        overhead is performed to find these locations in the group identified.

        Scans Usenet Index and returns the following:
              {
                  id: u'the unique identifier',
                  article_no: 12345678,
                  poster: u'the poster's information',
                  date: datetime() object,
                  subject: u'a subject line in unicode',
                  size: 2135  // the message size in bytes
                  lines: 53   // the number of lines
                  group: u'alt.group.one'
                  xgroups : {
                      // references the Message-ID (id) per cross post
                       u'alt.group.two': 987654321,
                       u'alt.group.three': 12341234,
                  }
              }

        """
        if not len(self._workers):
            # Handle connections
            self.connect()

        # Push request to the queue
        request = NNTPConnectionRequest(actions=[
            # Append list of NNTPConnection requests in a list
            # ('function, (*args), (**kwargs) )
            ('xover', tuple(), {
                'start': start,
                'end': end,
                'group': group,
                'sort': sort,
            }),
        ])

        # Append to Queue for processing
        self._work_queue.put(request)

        # We'll know when our request has been handled because the
        # request is included in the response.
        if block:
            request.wait()

            # Simplify things by returning just the response object
            # instead of the request
            return request.response[0]

        # We aren't blocking, so just return the request object
        return request


    def seek_by_date(self, refdate, group=None, block=True):
        """
        Returns a pointer in the selected group identified
        by the date specified.

        If block is not set to true, then it is up to the calling
        application to monitor the request until it's complete.

        Since the Request Object is inherited from a gevent.Event()
        object, one can easily check the status with the ready()
        call or, wait() if they want to block until content is ready.

        See http://www.gevent.org/gevent.event.html#module-gevent.event
        for more details.

        To remain thread-safe; it's recommended that you do not change
        any of the response contents or articles contents prior to
        it's flag being set (marking completion)

        """
        if not len(self._workers):
            # Handle connections
            self.connect()

        # Push request to the queue
        request = NNTPConnectionRequest(actions=[
            # Append list of NNTPConnection requests in a list
            # ('function, (*args), (**kwargs) )
            ('seek_by_date', (refdate, ), {'group': group, }),
        ])

        # Append to Queue for processing
        self._work_queue.put(request)

        # We'll know when our request has been handled because the
        # request is included in the response.
        if block:
            request.wait()

            # Simplify things by returning just the response object
            # instead of the request
            return request.response[0]

        # We aren't blocking, so just return the request object
        return request
