# -*- coding: utf-8 -*-
#
# A manager that can control multiple NNTP connections and
# orchastrate them together in a single class
#
# Copyright (C) 2015-2017 Chris Caron <lead2gold@gmail.com>
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
from gevent.lock import Semaphore
from gevent.queue import Queue
from gevent.queue import Empty as EmptyQueueException

from newsreap.NNTPConnection import NNTPConnection
from newsreap.NNTPnzb import NNTPnzb
from newsreap.NNTPSegmentedPost import NNTPSegmentedPost
from newsreap.NNTPArticle import NNTPArticle
from newsreap.NNTPConnection import XoverGrouping
from newsreap.NNTPConnectionRequest import NNTPConnectionRequest
from newsreap.NNTPSettings import NNTPSettings

# Logging
import logging
from newsreap.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)


class WorkTracker(object):
    """
    Our Work Tracking object we use to determine who's working and who isn't.

    By keeping it in it's own object, it allows us to wrap communication to
    and from it with a mutex (supporting threading).

    The idea is that we want to consult the tracker to see who's available
    before creating work. If there isn't enough workers available, then we
    spin another worker before assigning the task.

    Without this logic, a new connection to the NNTP Provier is made with
    each request up until the total number of max threads have been met. The
    goal of this is to re-use threads that area already connected before going
    through the painful overhead of creating another connection.

    """

    def __init__(self):
        """
        Initialize our object
        """
        super(WorkTracker, self).__init__()

        self.lock = Semaphore(value=1)

        # Track our available workers
        self.available = set()

        # Track our busy workers
        self.busy = set()

    def mark_available(self, worker):
        """
        Marks a worker available
        """
        try:
            self.lock.acquire(blocking=True)
            # Store our worker
            self.available.add(worker)
            self.busy.discard(worker)
            return True

        finally:
            self.lock.release()

        return False

    def mark_busy(self, worker):
        """
        Marks a worker unavailable
        """
        try:
            self.lock.acquire(blocking=True)
            self.busy.add(worker)
            self.available.discard(worker)
            return True

        finally:
            self.lock.release()

        return False

    def __len__(self):
        """
        Return our hash list (not thread safe)
        """
        return len(self.available) + len(self.busy)


class Worker(Greenlet):
    """
    This class actually performs all of the grunt work, a worker
    is a Greenlet or Thread which are spawned based on the number
    of connections defined.
    """

    def __init__(self, connection, work_queue, work_tracker):
        Greenlet.__init__(self, run=None)

        # Store NNTPConnection Object
        self._connection = connection

        # Our only communication to the outside world
        # event is a simple event handler of type Event()
        # we only unblock if this is set
        self._event = Event()

        # We always process as many queue entries as we can
        # TODO: This should contain some sort of container object that
        #       allows us to process many Message-ID, but we need to know
        #       their group they belong into.  We also need their expected
        #       filename.  We want to allow the NZBFiles to force a filename
        #       optionally, otherwise the yenc= is used instead. A minimum
        #       of those 3 entries `must` be in this input queue.  Perhaps
        #       we should just use NNTPContent entries? (they don't have
        #       an article ID though)
        self._work_queue = work_queue

        # Store our work tracker
        self._work_tracker = work_tracker

        # our exit flag, it is set externally
        self._exit = Event()

    def _run(self):
        """
        Read from the work_queue, process it using an NNTPRequest object.
        """
        # block until we have an event to handle.
        # print "Worker %s ready!" % self
        while not self._exit.is_set():
            # Begin our loop
            try:
                request = self._work_queue.get()
                if request is StopIteration:
                    # during a close() call (defined below) we force
                    # a StopIteration into the queue to force an exit
                    # from a program level
                    return

                if request.is_set():
                    # Process has been aborted or is no longer needed
                    continue

            except StopIteration:
                # Got Exit
                return

            except EmptyQueueException:
                # Nothing available for us
                continue

            # Mark ourselves busy
            self._work_tracker.mark_busy(self)

            # If we reach here, we have a request to process
            request.run(connection=self._connection)

            # Mark ourselves available again
            self._work_tracker.mark_available(self)

        # Ensure our connection is closed before we exit
        self._connection.close()

    def __del__(self):
        # If Ctrl-C is pressed or we're forced to break earlier then we may
        # end up here. Ensure our connection is closed before we exit
        self._connection.close()


class NNTPManager(object):
    """
    Used to manage multiple NNTPConnections via worker threads.

    The intent is to accept the same actions an NNTPConnection()
    would take but then re-assing the request to an available worker.

    NNTPManager() requires an NNTPSettings() object to work correctly

    """

    def __init__(self, settings=None, *args, **kwargs):
        """
        Initialize the NNTPManager() based on the provided settings.
        it is presumed settings is a loaded NNTPSettings() object.
        """

        # A connection pool of NNTPConnections
        self._pool = []

        # A mapping of active worker threads
        self._workers = []

        # Keep track of the workers available for processing
        # we will use this value to determine if we need to spin
        # up another process or not.
        self._work_tracker = WorkTracker()

        # Queue Control
        self._work_queue = Queue()

        # Map signal
        gevent.signal(signal.SIGQUIT, gevent.kill)

        if settings is None:
            # Use defaults
            settings = NNTPSettings()

        if not len(settings.nntp_servers):
            logger.warning("There were no NNTP Servers defined to load.")
            raise AttributeError('No NNTP Servers Defined')

        # Store our defined settings
        self._settings = settings

        return

    def spawn_workers(self, count=1):
        """
        Spawns X workers (but never more then the total allowed)
        """
        _count = 0
        while len(self._pool) < self._settings.nntp_processing['threads']:
            # First we build our connection object
            connection = NNTPConnection(**self._settings.nntp_servers[0])
            if len(self._settings.nntp_servers) > 1:
                # Append backup servers (if any defined)
                for idx in range(1, len(self._settings.nntp_servers)):
                    _connection = NNTPConnection(
                            **self._settings.nntp_servers[idx])
                    connection.append(_connection)

            # Appened connection object to a pool
            self._pool.append(connection)

            logger.debug("Spawning worker...")
            g = Worker(
                connection=connection,
                work_queue=self._work_queue,
                work_tracker=self._work_tracker,
            )
            g.start()

            # Track our worker
            self._workers.append(g)

            _count += 1
            if _count >= count:
                # Stop spawning
                break

        if _count > 0:
            logger.info("Loaded %d new worker(s)." % (_count))
            return True

        return False

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
            connection = next(
                    (c for c in self._pool if c.connected is True), None)

            if connection:
                return connection

            # Return the first entry if nothing is already connected
            return self._pool[0]

        # Otherwise there is nothing to return
        return None

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

    def put(self, request):
        """
        Handles the adding to the worker queue

        """

        # Determine if we need to spin a worker or not
        self._work_tracker.lock.acquire(blocking=True)

        if len(self._work_tracker.available) == 0:
            if len(self._work_tracker) < self._settings\
                                            .nntp_processing['threads']:
                # Spin up more work
                self.spawn_workers(count=1)

        # Append to Queue for processing
        self._work_queue.put(request)

        # Release our lock
        self._work_tracker.lock.release()

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
        # Push request to the queue
        request = NNTPConnectionRequest(actions=[
            # Append list of NNTPConnection requests in a list
            # ('function, (*args), (**kwargs) )
            ('group', (name, ), {}),
        ])

        # Append to Queue for processing
        self.put(request)

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
        # Push request to the queue
        request = NNTPConnectionRequest(actions=[
            # Append list of NNTPConnection requests in a list
            # ('function, (*args), (**kwargs) )
            ('groups', list(), {'filters': filters, 'lazy': lazy}),
        ])

        # Append to Queue for processing
        self.put(request)

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
        # Push request to the queue
        request = NNTPConnectionRequest(actions=[
            # Append list of NNTPConnection requests in a list
            # ('function, (*args), (**kwargs) )
            ('stat', (id, ), {'group': group, 'full': full}),
        ])

        # Append to Queue for processing
        self.put(request)

        # We'll know when our request has been handled because the
        # request is included in the response.
        if block:
            request.wait()

            # Simplify things by returning just the response object
            # instead of the request
            return request.response[0]

        # We aren't blocking, so just return the request object
        return request

    def post(self, payload, update_headers=True, success_only=False,
             block=True):
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
        # A list of results
        requests = []

        if isinstance(payload, NNTPnzb):
            # We're dealing with an NZB-File
            if not payload.is_valid():
                return None

            # Pre-Spawn workers based on the number of segments found in our
            # NZB-File.
            self.spawn_workers(payload.segcount())

            # We iterate over each segment defined int our NZBFile and merge
            # them into 1 file. We do this until we've processed all the
            # segments and we return a list of articles
            for segpost in payload:
                for article in segpost:

                    # Push request to the queue
                    request = NNTPConnectionRequest(actions=[(
                        # Append list of NNTPConnection requests in a list
                        # ('function, (*args), (**kwargs) )
                        'post', (article, ), {
                            'update_headers': update_headers,
                            'success_only': success_only,
                            },
                        ),
                    ])

                    # Append to Queue for processing
                    self.put(request)

                    # Store our request
                    requests.append(request)

        elif isinstance(payload, NNTPSegmentedPost):
            # Pre-Spawn workers based on the number of segments we find.
            self.spawn_workers(len(payload))

            # We iterate over each segment defined int our NZBFile and merge
            # them into 1 file. We do this until we've processed all the
            # segments and we return a list of articles
            for article in payload:

                # Push request to the queue
                request = NNTPConnectionRequest(actions=[(
                    # Append list of NNTPConnection requests in a list
                    # ('function, (*args), (**kwargs) )
                    'post', (article, ), {
                        'update_headers': update_headers,
                        'success_only': success_only,
                        },
                    ),
                ])

                # Append to Queue for processing
                self.put(request)

                # Store our request
                requests.append(request)

        elif isinstance(payload, NNTPArticle):
            # We're dealing with a single Article

            # Push request to the queue
            request = NNTPConnectionRequest(actions=[(
                # Append list of NNTPConnection requests in a list
                # ('function, (*args), (**kwargs) )
                'post', (payload, ), {
                    'update_headers': update_headers,
                    'success_only': success_only,
                    },
                ),
            ])

            # Append to Queue for processing
            self.put(request)

            # Store our request
            requests.append(request)

        # We'll know when our request has been handled because the
        # request is included in the response.
        if not block:
            # We aren't blocking, so just return the request objects
            return requests

        # Block indefinitely on all pending requests
        [req.wait() for req in iter(requests)]

        # Simplify things by returning just the response object
        # instead of the request
        responses = [req.response[0] for req in iter(requests)]

        if isinstance(payload, NNTPArticle):
            # A single Article, we can just go ahead and return a single
            # response
            return responses[0]

        # Return our responses
        return responses

    def get(self, id, work_dir, group=None, max_bytes=0, block=True,
            force=False):
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

        max_bytes is set to the number of bytes you want to have received
        before you automatically abort the connection and flag the object as
        having only been partially complete  Use this option when you need to
        inspect the first bytes of a binary file. Set this to zero to download
        the entire thing (this is the default value)

        The force flag when set to true forces the download of content even
        if it has previously already been retrieved.
        """

        # A list of results
        requests = []

        if isinstance(id, NNTPnzb):
            # We're dealing with an NZB-File
            if not id.is_valid():
                return None

            # Pre-Spawn workers based on the number of segments found in our
            # NZB-File.
            self.spawn_workers(id.segcount())

            # We iterate over each segment defined int our NZBFile and merge
            # them into 1 file. We do this until we've processed all the
            # segments and we return a list of articles
            for segpost_no, segpost in enumerate(id):
                for article_no, article in enumerate(segpost):

                    # Push request to the queue
                    request = NNTPConnectionRequest(actions=[
                        # Append list of NNTPConnection requests in a list
                        # ('function, (*args), (**kwargs) )
                        ('get', (article, work_dir), {
                            'group': group,
                            'max_bytes': max_bytes,
                        }),
                    ])

                    # Append to Queue for processing
                    self.put(request)

                    # Store our request
                    requests.append(request)

        elif isinstance(id, NNTPSegmentedPost):
            # Pre-Spawn workers based on the number of segments we find.
            self.spawn_workers(len(id))

            # We iterate over each segment defined int our NZBFile and merge
            # them into 1 file. We do this until we've processed all the
            # segments and we return a list of articles
            for article_no, article in enumerate(id):

                # Push request to the queue
                request = NNTPConnectionRequest(actions=[
                    # Append list of NNTPConnection requests in a list
                    # ('function, (*args), (**kwargs) )
                    ('get', (article, work_dir), {
                        'group': group,
                        'max_bytes': max_bytes,
                    }),
                ])

                # Append to Queue for processing
                self.put(request)

                # Store our request
                requests.append(request)

        elif isinstance(id, NNTPArticle):
            # We're dealing with a single Article

            # Push request to the queue
            request = NNTPConnectionRequest(actions=[
                # Append list of NNTPConnection requests in a list
                # ('function, (*args), (**kwargs) )
                ('get', (id, work_dir), {
                    'group': group,
                    'max_bytes': max_bytes,
                }),
            ])

            # Append to Queue for processing
            self.put(request)

            # Store our request
            requests.append(request)

        if isinstance(id, basestring):
            # We're dealing a Message-ID (Article-ID)

            # Push request to the queue
            request = NNTPConnectionRequest(actions=[
                # Append list of NNTPConnection requests in a list
                # ('function, (*args), (**kwargs) )
                ('get', (id, work_dir), {
                    'group': group,
                    'max_bytes': max_bytes,
                }),
            ])

            # Append to Queue for processing
            self.put(request)

            # Store our request
            requests.append(request)

        # We'll know when our request has been handled because the
        # request is included in the response.
        if not block:
            # We aren't blocking, so just return the request objects
            return requests

        # Block indefinitely on all pending requests
        [req.wait() for req in iter(requests)]

        # Simplify things by returning just the response object
        # instead of the request
        responses = [req.response[0] for req in iter(requests)]

        if isinstance(id, NNTPnzb):
            # NZB-File; Assign our Article Responses with their respected
            # locations in the NZB-File. This is done in addition to returning
            # our response objects
            resp_iter = iter(responses)

            # Iterate over our NZB-File (SegmentedPost) Entries
            for segpost in iter(id):
                # Iterate over our Segmented Post Entries
                for article in iter(segpost):
                    # For each segment in our list
                    resp = resp_iter.next()

                    # This should always equate since our response list
                    # was generated from our post
                    assert(article.id == resp.id)

                    # Load our response back to our NNTPnzb object
                    article.load(resp)

        elif isinstance(id, NNTPSegmentedPost):
            # Support NNTPSegmentedPost() Objects
            resp_iter = iter(responses)

            # Iterate over our Segmented Post Entries
            for article in iter(segpost):
                # For each segment in our list
                resp = resp_iter.next()

                # This should always equate since our response list
                # was generated from our post
                assert(article.id == resp.id)

                # Load our response back to our NNTPnzb object
                article.load(resp)

        elif isinstance(id, NNTPArticle):
            # This should always equate since our response list
            # was generated from our post
            assert(id.id == responses[0].id)

            # Load our response back to our NNTPnzb object
            article.load(responses[0])

            # Return our single article
            return responses[0]

        elif isinstance(id, basestring):
            # Message-ID: Just return our response directly
            return responses[0]

        # Return our responses
        return responses

    def xover(self, group, start=None, end=None,
              sort=XoverGrouping.BY_POSTER_TIME, block=True):
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
        self.put(request)

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
        # Push request to the queue
        request = NNTPConnectionRequest(actions=[
            # Append list of NNTPConnection requests in a list
            # ('function, (*args), (**kwargs) )
            ('seek_by_date', (refdate, ), {'group': group, }),
        ])

        # Append to Queue for processing
        self.put(request)

        # We'll know when our request has been handled because the
        # request is included in the response.
        if block:
            request.wait()

            # Simplify things by returning just the response object
            # instead of the request
            return request.response[0]

        # We aren't blocking, so just return the request object
        return request

    def __del__(self):
        """
        Gracefully clean up any lingering connections
        """
        self.close()
