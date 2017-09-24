#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# This is a simple dumb NNTP Server that is useful for unit testing.
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
import sys
if 'threading' in sys.modules:
    #  gevent patching since pytests import
    #  the sys library before we do.
    del sys.modules['threading']

import gevent
import gevent.monkey
from gevent import ssl
from gevent import socket

gevent.monkey.patch_all()

# Import threading after monkey patching
# see: http://stackoverflow.com/questions/8774958/\
#        keyerror-in-module-threading-after-a-successful-py-test-run
import threading

import re
from io import BytesIO
from zlib import compress
from os.path import dirname
from os.path import join
from os.path import abspath

try:
    from newsreap.SocketBase import SocketBase
    from newsreap.SocketBase import SocketException
    from newsreap.SocketBase import DEFAULT_BIND_ADDR

except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from newsreap.SocketBase import SocketBase
    from newsreap.SocketBase import SocketException
    from newsreap.SocketBase import DEFAULT_BIND_ADDR

# Our internal server is only used for testing, therefore we can get away with
# having a really low timeout
socket.setdefaulttimeout(10.0)

# The directory containing all of the variable data used
# for the NNTPConnection Testing
NNTP_TEST_VAR_PATH = join(dirname(abspath(__file__)), 'var')

# Empty File
DEFAULT_EMPTY_FILE = join(NNTP_TEST_VAR_PATH, 'emptyfile.msg')

# Article ID
ARTICLE_ID_RE = re.compile(r'\s*<*\s*(?P<id>[^>]+)>?.*')

# All of the default NNTP Responses are defined here by their
# compiled regular expression
NNTP_DEFAULT_MAP = {
    re.compile('AUTHINFO USER valid'): {
        'response': '381 PASS required',
    },
    re.compile('AUTHINFO PASS valid'): {
        'response': '281 Ok'
    },
    re.compile('XFEATURE COMPRESS GZIP'): {
        'response': '290 GZIP Feature enabled',
    },
    re.compile('LIST ACTIVE'): {
        'response': '215 Newsgroups in form "group high low flags".',
        'file': join(NNTP_TEST_VAR_PATH, 'group.list'),
    },
    re.compile('GROUP alt.binaries.l2g.znb'): {
        'response': '211 709278590 69039573 778318162 alt.binaries.l2g.znb',
    },
    re.compile('HEAD (?P<id>[^ \t\r\n]+).*$'): {
        'stat': 'id',
    },
    re.compile('STAT (?P<id>[^ \t\r\n]+).*$'): {
        'stat': 'id',
    },
    re.compile('ARTICLE (?P<id>[^ \t\r\n]+).*$'): {
        'article': 'id',
    },
    re.compile('BODY (?P<id>[^ \t\r\n]+).*$'): {
        'article': 'id',
    },
    re.compile('GROUP (?P<id>[^ \t\r\n]+).*$'): {
        'group': 'id',
    },
    re.compile('^QUIT'): {
        'response': '200 See you later!',
        # Reset our current state of the map
        'reset': True,
    },
}

# Delimiters
NNTP_EOL = '\r\n'
NNTP_EOD = '.\r\n'


class NNTPClient(SocketBase):
    """
    An NNTPClient is produced from a NNTPSocketServer by simply calling
    get_client().

    With an NNTPClient() you call send() to push your commands and
    will get the results returned to you

    """
    def __init__(self, *args, **kwargs):
        """
        NNTPClient initialization
        """
        # Initialize the Socket Base Class
        super(NNTPClient, self).__init__(*args, **kwargs)

    def put(self, line, eol=True):
        """
        A Simple put() script to simplify transmitting data to the fake server

        You can directly interface through here, or you can use the
        get_client() to spin an actual a TCP/IP Client
        """

        if eol:
            line = line + NNTP_EOL

        # print("DEBUG: CLIENT TX: %s" % line.strip())
        self.send(line)
        response = self.read()
        # print("DEBUG: CLIENT RX: %s" % response.strip())
        # print('')

        return response

    def close(self):
        """
        Gracefully disconnects from the server
        """
        try:
            # Prevent Recursion by calling parent send()
            super(NNTPClient, self).send('QUIT' + NNTP_EOL)
        except:
            # well.. we tried at least
            pass

        try:
            # close the port
            self.socket.close()
        except:
            pass


class NNTPSocketServer(threading.Thread):
    def __init__(self, join_group=True, host='localhost', port=0,
                 secure=None, *args, **kwargs):

        # Handle Threading
        threading.Thread.__init__(self)

        self._can_post = True
        self._has_yenc = True

        self._active = threading.Event()
        self._io_wait = threading.Event()
        self._maplock = threading.Lock()

        # If set to True, then a group join is required before the article
        # in question can be fetched.
        self._join_group = join_group

        # Server (self-signed) Certificates for SSL Testing
        kwargs['certfile'] = abspath(
            join(dirname(__file__), 'var', 'ssl', 'localhost.crt'))
        kwargs['keyfile'] = abspath(
            join(dirname(__file__), 'var', 'ssl', 'localhost.key'))

        # sent welcome
        self.sent_welcome = False

        # Override Map
        self.override_map = {}

        # A map of id's to filenames (stored locally on disk)
        # if a fetch is made to an item that contains a map, then
        # it is retrieved from disk and delivered
        self.fetch_map = {}

        # A mapping of groups and their group details
        self.group_map = {}

        # The default current group
        self.current_group = None

        # Set this to a default file to deliver content to when
        # data is fetched from the fetch_map and doesn't exist
        # If you set this to None, then nothing is returned.
        self.default_fetch = DEFAULT_EMPTY_FILE

        # Initialize the Socket Base Class
        self.socket = SocketBase(
            host=host, port=port, secure=secure, *args, **kwargs)

    def local_connection_info(self, timeout=3.0):
        """
        Returns local configuration (listening info)
        """

        if self.socket is None:
            return None

        # Block until server is active
        if not self._active.wait(timeout):
            return None

        connection_info = self.socket.local_connection_info()
        if connection_info:
            _ipaddr, _portno = connection_info
        else:
            return None

        if _ipaddr == DEFAULT_BIND_ADDR:
            _ipaddr = '127.0.0.1'

        return (_ipaddr, _portno)

    def get_client(self, timeout=3.0):
        """
        Returns a client after establishing a connection to the server.

        """
        connection_info = self.local_connection_info(timeout=timeout)
        _ipaddr, _portno = connection_info

        # create a socket
        sock = NNTPClient(
            host=_ipaddr,
            port=_portno,
            secure=self.socket.secure,
        )

        # connect
        sock.connect(5.0)

        # return the socket
        return sock

    def put(self, line, eol=True):
        """
        Pushes directly to the NNTPSocketServer without need of a remote
        connection and acquires the response
        """
        # print('Scanning Against: "%s"' % line)

        # cur_thread = threading.current_thread()
        # response = "{}: {}".format(cur_thread.name, data)
        # self.socket.send(response)

        # Process over-ride map
        self._maplock.acquire()
        override = self.override_map.items()
        self._maplock.release()

        response = None
        for k, v in override + NNTP_DEFAULT_MAP.items():
            result = k.search(line)
            if result:
                # we matched
                if 'response' in v:
                    response = v['response']

                if 'reset' in v:
                    # Reset our current state
                    self.reset()

                if 'stat' in v:
                    entry = str(result.group(v['stat']))
                    if not self.current_group:
                        response = '412 No newsgroup selected'

                    elif not entry:
                        response = '423 No article with that number'

                    else:
                        response = '223 %s Article exists' % entry

                    break

                if 'group' in v:
                    entry = str(result.group(v['group']))
                    if not entry:
                        response = '423 No such article in this group'
                        self.current_group = None

                    elif entry not in self.group_map:
                        response = '423 No such article in this group'
                        self.current_group = None

                    else:
                        response = '211 %d %d %d %s' % (
                            self.group_map[entry][0],
                            self.group_map[entry][1],
                            self.group_map[entry][2],
                            self.group_map[entry][3],
                        )

                        # Set Group
                        self.current_group = entry

                    # We're done handling GROUP command
                    break

                    # checking that we're good to go that way
                if 'article' in v:
                    # Tidy up our article id
                    _result = ARTICLE_ID_RE.match(
                        str(result.group(v['article'])),
                    )
                    if not _result:
                        response = '423 No article with that number'
                        break

                    if self._join_group:
                        # A group join is required; perform some overhead
                        if not self.current_group:
                            response = '412 No newsgroup selected'
                            break

                        elif self.current_group \
                                not in self.fetch_map:
                            # Not found
                            response = '423 No article with that number'
                            break

                        # create a file from our fetch map
                        entry = self.fetch_map[self.current_group].get(
                            str(_result.group('id')),
                            self.default_fetch,
                        )

                    else:
                        try:
                            # If we are in a group, test it first
                            entry = self.fetch_map[self.current_group].get(
                                str(_result.group('id')),
                                self.default_fetch,
                            )

                        except KeyError:
                            # Otherwise, iterate through our list and find
                            # match since there is no join_group
                            # requirement
                            found = False
                            for g in self.fetch_map.iterkeys():
                                entry = self.fetch_map[g].get(
                                    str(_result.group('id')),
                                    False,
                                )
                                if entry is False:
                                    # No match
                                    continue

                                # Toggle found flag
                                found = True

                                if entry is None:
                                    # Assign Default
                                    entry = self.default_fetch

                                # We're done
                                break

                            if not found:
                                # Not found
                                response = '423 No article with that number'
                                break

                    if isinstance(entry, basestring):
                        response = '230 Retrieving article.'
                        # Store our file we mapped to
                        v['file'] = entry

                        # Fall through to handle file
                    else:
                        # Not found
                        response = '423 No article with that number'
                        break

                if 'file' in v:
                    try:
                        # Read in a file and send it
                        fd = open(v['file'], 'rb')
                        response += NNTP_EOL + fd.read()
                        fd.close()

                    except IOError:
                        response = "501 file '%s' is missing." % v['file']
                        break

                elif 'gzip' in v:
                    # If the file isn't gzipped and you 'want' to gzip it
                    # then you use this
                    try:
                        # Read in a gzipped file and send it
                        fd = open(v['gzip'], 'rb')
                        if response is None:
                            response = '230 Retrieving article.'

                        response += " [COMPRESS=GZIP]" + NNTP_EOL + \
                            compress(fd.read())
                        fd.close()

                    except IOError:
                        response = "502 file '%s' is missing." % v['file']
                        break

                # We're done
                break

        if response is None:
            response = "503 No handler for the request"

        return response

    def is_ready(self, timeout=5.0):
        """
        A thread safe way of finding out if we're up and listen for a
        connection
        """
        return self._active.wait(timeout)

    def nntp_server(self):
        """
        A fake nntp server that generates responses like a real one

        It lets us test the protocol by simulating different responses.
        """

        # Set io_wait flag
        self._io_wait.set()

        # Send Welcome Message
        if not self.sent_welcome:
            welcome_str = "200 l2g.caronc.dummy NNRP Service Ready"
            if self._can_post:
                welcome_str += " (posting ok)"
            if self._has_yenc:
                welcome_str += " (yEnc enabled)"
            try:
                self.socket.send(welcome_str + NNTP_EOD)
            except:
                # connection lost
                # print('DEBUG: SOCKET ERROR DURING SEND (EXITING)....')
                return

            self.sent_welcome = True

        data = BytesIO()
        d_len = data.tell()

        while self._active.is_set() and self.socket.connected:
            # print('DEBUG: SERVER LOOP')

            # ptr manipulation
            d_ptr = data.tell()
            if d_ptr > 32768:
                # Truncate
                data = BytesIO(data.read())
                d_ptr = 0
                data.seek(d_ptr)

            try:
                # print('DEBUG: SERVER BLOCKING FOR DATA')
                pending = self.socket.can_read(0.8)
                if pending is None:
                    # No more data
                    continue

                if not pending:
                    # nothing pending; back to io_wait
                    continue

                while self.socket.can_read():
                    # print('DEBUG: SERVER BLOCKING FOR DATA....')
                    _data = self.socket.read()
                    if not _data:
                        # print('DEBUG: SERVER NO DATA (EXITING)....')
                        # Reset our settings to prepare for another connection
                        self.reset()
                        return
                    # print('DEBUG: SERVER READ DATA: %s' % _data.rstrip())

                    # Buffer response
                    data.write(_data)
                    d_len = data.tell()

            except (socket.error, SocketException):
                # Socket Issue
                # print('DEBUG: SOCKET ERROR (EXITING)....')
                # print('DEBUG: ERROR %s' % str(e))
                # Reset our sent_welcome flag
                self.sent_welcome = False
                return

            # Seek End for size
            if d_ptr == d_len:
                continue
            data.seek(d_ptr)

            # Acquire our line
            line = data.readline()

            # Build our response
            response = self.put(line)

            # Return it on the socket
            try:
                self.socket.send(response + NNTP_EOD)
            except:
                # connection lost
                # print('DEBUG: SOCKET ERROR DURING SEND (EXITING)....')
                return

        # print('DEBUG: handle() (EXITING)....')

    def run(self):
        """
        Run thread
        """
        # Enable Server
        self._active.set()

        while self._active.is_set():
            # Thread Main Loop
            try:
                if not self.socket.listen():
                    # Wait for a connection
                    continue

            except SocketException:
                # Lost the connection; loop
                continue

            # print('DEBUG: SERVERSIDE CONNECTION ESTABLISHED!')

            # If we reach we have a connection
            self.nntp_server()

            # We're probably here because we lost our connection
            self.reset()

        # We're finished (close our socket if not already done so)
        self.socket.close()

    def shutdown(self):
        """
        Handle shutdown
        """
        # Clear active flag
        self._active.clear()
        # print('DEBUG: SERVER GOT SHUTDOWN')
        try:
            self.socket.close()
        except:
            pass

        # Reset welcome flag
        self.sent_welcome = False

        # We're done
        return True

    def set_override(self, override=None):
        """
        Sets an override map (or resets it to nothing)
        """

        # Clear io_wait flag
        self._io_wait.clear()

        if not override:
            override = {}

        # Store copy of passed in override
        self._maplock.acquire()
        self.override_map = dict(override)
        self._maplock.release()

    def reset(self):
        """
        This function is called to let the server handle disconnects faster
        """
        # Clear io_wait flag
        self._io_wait.clear()

        # Reset the current group
        self.current_group = None

        # sent welcome
        self.sent_welcome = False

    def map(self, article_id, groups, filepath=None):
        """
        Maps an article (and groups) article_id to a filepath
        """
        if isinstance(groups, basestring):
            # expect a list of groups, but allow single
            # entries too; just convert them before
            # moving on
            groups = [groups, ]

        self._maplock.acquire()
        for group in groups:
            if group not in self.group_map:
                # Create Group Entry
                self.group_map[group] = [0, 0, 0, group]
                # Create fetch_map entry (empty)
                self.fetch_map[group] = {}

            if filepath:
                # If a file was specified, update our details
                self.fetch_map[group][str(article_id)] = str(filepath)
                # Increment tail
                self.group_map[group][1] += 1
                # Increment count
                self.group_map[group][2] += 1

        self._maplock.release()


if __name__ == "__main__":

    # SSL Checking
    nntp_server = NNTPSocketServer(
        secure=ssl.PROTOCOL_TLSv1,
        # secure=False,
    )
    # Launch thread
    # nntp_server.daemon = True
    nntp_server.start()

    # Acquire a client connection
    socket = nntp_server.get_client()

    socket.put("AUTHINFO USER valid")
    nntp_server.shutdown()

    # NON SSL
    nntp_server = NNTPSocketServer(
        secure=False,
    )

    # Append file to map
    nntp_server.map(
        '3', 'alt.bin.test',
        join(NNTP_TEST_VAR_PATH, '00000005.ntx'),
    )

    # Exit the server thread when the main thread terminates
    # nntp_server.daemon = True
    nntp_server.start()

    # Acquire a client connection
    socket = nntp_server.get_client()

    socket.put("AUTHINFO USER valid")
    socket.put("AUTHINFO PASS user")
    socket.put("READ FILE 3")
    socket.put("GROUP alt.bin.test")
    socket.put("ARTICLE 3")
    socket.put("Hello World 3")

    # Close our client
    socket.close()

    # Shutdown our server
    nntp_server.shutdown()
