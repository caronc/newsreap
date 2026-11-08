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


from contextlib import contextmanager
from copy import deepcopy

from gevent import Greenlet
from gevent import sleep
from time import time
from gevent.event import Event
from gevent.lock import Semaphore
from gevent.queue import Queue
from gevent.queue import Empty as EmptyQueueException

from .Database import DatabaseUnavailable
from .codecs.CodecBase import CodecBase
from .HookManager import HookManager
from .NNTPConnection import NNTPConnection
from .NNTPnzb import NNTPnzb
from .NNTPSegmentedPost import NNTPSegmentedPost
from .NNTPArticle import NNTPArticle
from .NNTPConnection import XoverGrouping
from .NNTPConnectionRequest import NNTPConnectionRequest
from .NNTPRequest import NNTPRequest
from .NNTPSettings import NNTPSettings
from .NNTPStatistics import NNTPStatistics
from .Utils import server_key

# Logging
import logging
from newsreap.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)

# What a connection to a server is claimed as (see NNTPClaims); a server
# allowing 10 connections has 10 of them, shared by every worker
CONNECTION_CLAIM = 'connection:'

# When other workers have every connection to a server: how often (seconds)
# we ask again, and how long we wait at most for our first one (then one is
# used anyway, so the work is never stuck)
SLOT_RETRY = 10
SLOT_WAIT = 600


def own_decoders(decoders):
    """
    Returns decoders one request can use on its own: a copy of the codecs
    given (a codec keeps what it is decoding, so two connections can't
    share one); None and False are passed on as they are
    """
    if isinstance(decoders, (CodecBase, list, tuple)):
        return deepcopy(decoders)
    return decoders


class WorkTracker:
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
        super().__init__()

        self.lock = Semaphore(value=1)

        # Track our available workers
        self.available = set()

        # Track our busy workers
        self.busy = set()

    def mark_available(self, worker):
        """
        Marks a worker available
        """
        with self.lock:
            self.available.add(worker)
            self.busy.discard(worker)
        return True

    def mark_busy(self, worker):
        """
        Marks a worker unavailable
        """
        with self.lock:
            self.busy.add(worker)
            self.available.discard(worker)
        return True

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
        # print("Worker %s ready!" % self)
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

            # If we reach here, we have a request to process; we are free
            # again afterwards, however it went
            request.timer_start()
            try:
                request.run(connection=self._connection)

            except Exception:
                # A broken request mustn't stop the worker (it would still
                # look available and nothing would take the next request);
                # anything waiting on it is woken up
                logger.exception('A request failed: %r' % request)
                if not request.is_set():
                    request.abort()

            finally:
                request.timer_stop()
                self._work_tracker.mark_available(self)

        # Ensure our connection is closed before we exit
        self._connection.close()

    def __del__(self):
        # If Ctrl-C is pressed or we're forced to break earlier then we may
        # end up here. Ensure our connection is closed before we exit
        self._connection.close()


class NNTPManager:
    """
    Used to manage multiple NNTPConnections via worker threads.

    The intent is to accept the same actions an NNTPConnection()
    would take but then re-assing the request to an available worker.

    NNTPManager() requires an NNTPSettings() object to work correctly

    """
    def __init__(self, settings=None, hooks=None, claims=None, *args,
                 **kwargs):
        """
        Initialize the NNTPManager() based on the provided settings.
        it is presumed settings is a loaded NNTPSettings() object.

        - claims: an NNTPClaims; each server's connections setting is then
          shared with the other workers using the same database (several
          'nr update' running at once never go over it together)
        """
        # The connections we claimed (see _slot()), and when asking for one
        # last failed
        self._claims = claims
        self._slots = []
        self._slot_failed = {}

        # A connection pool of NNTPConnections
        self._pool = []

        # Statistics of the connections we've already closed (all added
        # together, and by the host each one talked to)
        self._closed_stats = NNTPStatistics()
        self._closed_by_host = {}

        # A mapping of active worker threads
        self._workers = []

        # Keep track of the workers available for processing
        # we will use this value to determine if we need to spin
        # up another process or not.
        self._work_tracker = WorkTracker()

        # Queue Control
        self._work_queue = Queue()

        # Define hook(s) allowing us to over-ride sections of the process.
        # This grants the user to ability to manipulate content prior to it
        # being posted and/or staged
        self.hooks = HookManager()
        if hooks:
            self.hooks.add(hooks)

        if settings is None:
            # Use defaults
            settings = NNTPSettings()

        if not len(settings.nntp_servers):
            logger.warning("There were no NNTP Servers defined to load.")
            raise AttributeError('No NNTP Servers Defined')

        # Store our defined settings
        self._settings = settings

        return

    def set_hooks(self, hooks, reset=True):
        """
        Sets hooks into our connection object(s)

        - hooks can be a HookManager, a Hook, a function or the name/path
          of a hook module.
        - reset removes any hooks already loaded first.
        """
        if hooks is self.hooks:
            # These are already our hooks
            return

        if reset:
            # Reset our loaded hooks
            self.hooks.reset()

        self.hooks.add(hooks)

    def servers(self):
        """
        Returns the servers articles are asked of: our main server first,
        then the backups (each a dict with its host and port, as the
        configuration has it)
        """
        return list(self._settings.nntp_servers)

    def window(self):
        """
        Returns how many requests are worth keeping queued at once: two for
        each worker (connection to our main server), so none of them waits
        for work
        """
        return max(2, 2 * self._settings.connections())

    def spawn_workers(self, count=1):
        """
        Spawns X workers (but never more then the total allowed)
        """
        _count = 0
        while len(self._pool) < self._settings.connections():
            # One of the server's connections has to be ours; without any
            # yet, we wait for one
            if not self._slot(self._settings.nntp_servers[0],
                              wait=not self._pool):
                break

            # First we build our connection object
            connection = NNTPConnection(**self._settings.nntp_servers[0])

            # Directly map the connection's hooks to the ones here in the
            # NNTPManager() object
            connection.hooks = self.hooks

            if len(self._settings.nntp_servers) > 1:
                # Append backup servers (if any defined); only as many
                # workers as a backup allows connections get one
                for idx in range(1, len(self._settings.nntp_servers)):
                    backup = self._settings.nntp_servers[idx]
                    if len(self._pool) >= self._settings.connections(backup) \
                            or not self._slot(backup):
                        continue

                    _connection = NNTPConnection(**backup)

                    # Directly map the connection's hooks to the ones here in
                    # the NNTPManager() object
                    _connection.hooks = self.hooks

                    connection.append(_connection)

            # Append connection object to a pool
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

    def _slot(self, server, wait=False):
        """
        Claims one of a server's connections for us (see CONNECTION_CLAIM);
        returns True if we may make one

        - wait: when other workers have them all, wait for one (up to
          SLOT_WAIT seconds); otherwise we ask again at most every
          SLOT_RETRY seconds
        - without claims (or a database to keep them in) we always may
        """
        if self._claims is None:
            return True

        host = server_key(**server)
        if not wait and time() - self._slot_failed.get(host, 0.0) < \
                SLOT_RETRY:
            # We asked this server a moment ago
            return False

        names = ['%s%s:%d' % (CONNECTION_CLAIM, host, no)
                 for no in range(self._settings.connections(server))]
        started = time()
        while True:
            try:
                found = self._claims.claim(
                    [name for name in names if name not in self._slots],
                    limit=1)

            except DatabaseUnavailable:
                # Nobody to share them with
                self._claims = None
                return True

            if found:
                self._slots.extend(found)
                return True

            self._slot_failed[host] = time()
            if not wait:
                return False

            if time() - started >= SLOT_WAIT:
                logger.warning(
                    'Other workers kept every connection to %s for %d '
                    'seconds; using one anyway.' % (host, SLOT_WAIT))
                return True

            if time() - started < SLOT_RETRY:
                logger.info('Other workers have every connection to %s; '
                            'waiting for one.' % host)
            sleep(SLOT_RETRY)

    def close(self):
        """
        closes out any open threads and cleans up NNTPManager
        gracefully.
        """
        while not self._work_queue.empty():
            try:
                request = self._work_queue.get_nowait()

            except EmptyQueueException:
                # Nothing available for us
                break

            # Nothing will run it now; wake up anything waiting on it
            if isinstance(request, NNTPRequest):
                request.abort()

        for worker in self._workers:
            # Toggle Exit
            worker._exit.set()
            self._work_queue.put(StopIteration)

        for entry in self._pool:
            entry.close()

        for worker in self._workers:
            logger.info("Waiting for workers to exit.")
            worker.join()

        # Keep what our connections did before letting them go
        self._closed_stats = self.stats
        self._closed_by_host = self.stats_by_host()

        del self._pool
        del self._workers
        self._workers = []
        self._pool = []

        if self._slots and self._claims is not None:
            # Our connections are free for other workers again
            self._claims.release(self._slots)
            self._slots = []

    @property
    def stats(self):
        """
        The statistics of all of our connections (backups included)
        added together
        """
        total = self._closed_stats.copy()
        for connection in self._pool:
            total += connection.stats

            for backup in connection._backups:
                total += backup.stats

        return total

    def stats_by_host(self):
        """
        Returns {server key: NNTPStatistics} of all of our connections
        (backups included), by the server each one talked to (see
        Utils.server_key(); for a plain server it is its host)
        """
        found = {host: stats.copy()
                 for host, stats in self._closed_by_host.items()}
        for connection in self._pool:
            for each in (connection, *connection._backups):
                found.setdefault(each.key, NNTPStatistics())
                found[each.key] += each.stats

        return found

    @contextmanager
    def connection(self, server):
        """
        A connection of our own to one of our servers (its settings, as in
        NNTPSettings.nntp_servers), ie: to list its groups:

            with manager.connection(server) as connection:
                connection.groups()

        - it counts against how many connections the server allows, with
          every other worker's (it waits for one to be free, as our
          workers do)
        - it is closed afterwards, and what it did is part of our
          statistics
        """
        before = set(self._slots)
        self._slot(server, wait=True)
        ours = [name for name in self._slots if name not in before]

        connection = NNTPConnection(**server)
        try:
            yield connection

        finally:
            connection.close()
            self.add_stats(connection.stats, host=connection.key)
            if ours:
                # Free for the others again
                self._slots = [
                    name for name in self._slots if name not in ours]
                if self._claims is not None:
                    self._claims.release(ours)

    def add_stats(self, stats, host=None):
        """
        Adds statistics gathered elsewhere (ie: by a connection of your own)
        to ours, so they are part of our total (and of the server's, when
        its key is given; see stats_by_host())
        """
        self._closed_stats += stats
        if host is not None:
            self._closed_by_host.setdefault(host, NNTPStatistics())
            self._closed_by_host[host] += stats

    def put(self, request):
        """
        Handles the adding to the worker queue

        """

        # Determine if we need to spin a worker or not (the lock is let go
        # of even if starting one fails)
        with self._work_tracker.lock:
            if len(self._work_tracker.available) == 0:
                if len(self._work_tracker) < self._settings.connections():
                    # Spin up more work
                    self.spawn_workers(count=1)

            # Append to Queue for processing
            self._work_queue.put(request)

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

    def overview_format(self, block=True):
        """
        Returns what the fields after the first seven of an overview (XOVER)
        line are, asked of one of our connections (see
        NNTPConnection.overview_format()); with block=False the request is
        returned instead (its response[0] holds the answer once it is done)
        """
        request = NNTPConnectionRequest(actions=[
            ('overview_format', (), {}),
        ])
        self.put(request)

        if block:
            request.wait()
            return request.response[0]

        return request

    def send(self, command, group=None, decoders=None, timeout=None,
             retries=0, block=True):
        """
        Sends a command (ie: 'DATE' or 'HELP') on one of our connections
        and returns its NNTPResponse (see NNTPConnection.send()); with
        block=False the request is returned instead (its response[0] holds
        the answer once it is done)

        - group: join this group first, for commands that depend on it; a
          connection is otherwise in whatever group it was used for last.
          When it can't be joined, the command isn't sent and an
          INVALID_GROUP response is returned
        - decoders: what the answer is decoded with (a copy is used)
        - retries: how many more times it is sent if the connection drops;
          only for commands safe to send twice (not POST)
        """
        request = NNTPConnectionRequest(actions=[('send', (command, ), {
            'decoders': own_decoders(decoders), 'timeout': timeout,
            'retries': retries, 'group': group or None})])
        self.put(request)

        if block:
            request.wait()
            return request.response[0] if request.response else None

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
                        }),
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
                    }),
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

        if isinstance(payload, NNTPArticle):
            # A single Article, we can just go ahead and return a single
            # response
            return responses[0]

        # Return our responses
        return responses

    def get(self, id, work_dir, decoders=None, group=None, max_bytes=0,
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

        max_bytes is set to the number of bytes you want to have received
        before you automatically abort the connection and flag the object as
        having only been partially complete  Use this option when you need to
        inspect the first bytes of a binary file. Set this to zero to download
        the entire thing (this is the default value)
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
                            'decoders': own_decoders(decoders),
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
                        'decoders': own_decoders(decoders),
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
                    'decoders': decoders,
                    'group': group,
                    'max_bytes': max_bytes,
                }),
            ])

            # Append to Queue for processing
            self.put(request)

            # Store our request
            requests.append(request)

        if isinstance(id, str):
            # We're dealing a Message-ID (Article-ID)

            # Push request to the queue
            request = NNTPConnectionRequest(actions=[
                # Append list of NNTPConnection requests in a list
                # ('function, (*args), (**kwargs) )
                ('get', (id, work_dir), {
                    'decoders': decoders,
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
                    # Load what was fetched back into our NNTPnzb object (a
                    # missing article has nothing to load)
                    resp = next(resp_iter)
                    if resp is not None:
                        article.load(resp)

        elif isinstance(id, NNTPSegmentedPost):
            # Support NNTPSegmentedPost() Objects
            resp_iter = iter(responses)

            # Iterate over our Segmented Post Entries
            for article in iter(id):
                # Load what was fetched back into our post (a missing
                # article has nothing to load)
                resp = next(resp_iter)
                if resp is not None:
                    article.load(resp)

        elif isinstance(id, NNTPArticle):
            # Load what was fetched back into the article given
            if responses[0] is not None:
                id.load(responses[0])

            # Return our single article
            return responses[0]

        elif isinstance(id, str):
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
                  subject: 'a subject line',
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
        try:
            self.close()

        except Exception:
            # Python is shutting down (our greenlets may already be gone);
            # there is nothing left to clean up gracefully
            pass
