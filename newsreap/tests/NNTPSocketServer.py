#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# This is a simple dumb NNTP Server that is useful for unit testing.
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

import sys
if 'threading' in sys.modules:
    #  gevent patching since pytests import
    #  the sys library before we do.
    del sys.modules['threading']

import gevent
import gevent.monkey
from gevent import ssl
from gevent import socket

from gevent.select import select
gevent.monkey.patch_all()

#import socket
# Import threading after monkey patching
# see: http://stackoverflow.com/questions/8774958/\
#        keyerror-in-module-threading-after-a-successful-py-test-run
import threading

import re
import SocketServer
from pprint import pformat
from io import BytesIO
from zlib import compress
from os.path import dirname
from os.path import join
from os.path import isfile
from os.path import abspath

# Our internal server is only used for testing, therefore we can get away with
# having a really low timeout
socket.setdefaulttimeout(10.0)

# The directory containing all of the variable data used
# for the NNTPConnection Testing
NNTP_TEST_VAR_PATH = join(dirname(abspath(__file__)), 'var')

# Empty File
DEFAULT_EMPTY_FILE = join(
    NNTP_TEST_VAR_PATH,
    'NNTPConnection',
    'emptyfile.msg',
)

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
        'file': join(NNTP_TEST_VAR_PATH, 'grouplist'),
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
    },
}

# Delimiters
NNTP_EOL = '\r\n'
NNTP_EOD = '.\r\n'


class NNTPSocketServer(SocketServer.TCPServer):
    def __init__(self, server_address, RequestHandlerClass,
                 bind_and_activate=True, secure=True, join_group=True):

        # Hostname is used for SSL Verification (if set)
        self.hostname = server_address[0]

        self._can_post = True
        self._has_yenc = True

        self._secure = secure
        self._io_wait = threading.Event()
        self._maplock = threading.Lock()

        # If set to True, then a group join is required before the article
        # in question can be fetched.
        self._join_group = join_group

        # Server (self-signed) Certificates for SSL Testing
        self.certfile = abspath(
            join(dirname(__file__), 'var', 'ssl','localhost.crt'))
        self.keyfile = abspath(
            join(dirname(__file__), 'var', 'ssl','localhost.key'))

        # These checks are very nessisary; you'll get strange errors like:
        # _ssl.c:341: error:140B0002:SSL \
        #               routines:SSL_CTX_use_PrivateKey_file:system lib
        #
        # The error itself will surface during the call to wrap_socket() which
        # will throw the exception ssl.SSLError
        #
        # it doesn't hurt to just check ahead of time.
        if not isfile(self.certfile):
            raise ValueError(
                'Could not locate Certificate: %s' % self.certfile)
        if not isfile(self.keyfile):
            raise ValueError(
                'Could not locate Private Key: %s' % self.keyfile)

        # Secure Protocol to use
        try:
            # Python v2.7+
            self.ssl_version = ssl.PROTOCOL_TLSv1_2
        except AttributeError:
            # Python v2.6+
            self.ssl_version = ssl.PROTOCOL_TLSv1

        # sent welcome
        self.sent_welcome = False

        # Override Map
        self.override_map = {}

        # Monkey Patch so that we can toggle the reuse_address
        SocketServer.TCPServer.allow_reuse_address = True

        # Initialize Server
        SocketServer.TCPServer.__init__(
            self, server_address=server_address,
            RequestHandlerClass=RequestHandlerClass,
            bind_and_activate=bind_and_activate,
        )

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


    def shutdown(self):
        """
        Handle shutdown
        """
        # Clear io wait flag
        #print('DEBUG: SERVER GOT SHUTDOWN')
        self._io_wait.clear()
        try:
            self.socket.close()
        except:
            pass

        # Reset welcome flag
        self.sent_welcome = False
        return SocketServer.TCPServer.shutdown(self)


    def set_override(self, override=None):
        """
        Sets an override map (or resets it to nothing)
        """

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
        self._io_wait.clear()

        # Reset the current group
        self.current_group = None

        # sent welcome
        self.sent_welcome = False


    def map(self, id, groups, filepath=None):
        """
        Maps an article (and groups) id to a filepath
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
                self.fetch_map[group][str(id)] = str(filepath)
                # Increment tail
                self.group_map[group][1] += 1
                # Increment count
                self.group_map[group][2] += 1

        self._maplock.release()


class NNTPBaseRequestHandler(SocketServer.BaseRequestHandler):
    """
    The RequestHandler class for our server.

    It is instantiated once per connection to the server, and must
    override the handle() method to implement communication to the
    client.
    """

    def pending_data(self, timeout=0.0):
        """
        Checks if there is data that can be read from the
        socket (if open). Returns True if there is data and
        False if not.
        """
        # rs = Read Sockets
        # ws = Write Sockets
        # es = Error Sockets
        if self.request:
            rs, _, _ = select([self.request] , [], [self.request], timeout)
            return len(rs) > 0
        return None


    def apply_ssl(self):
        """
        Wraps connection with SSL
        """

        # Swap out old socket
        self._request = self.request

        try:
            # Python 2.7.9
            context = ssl.SSLContext(self.server.ssl_version)
            #context.check_hostname = True
            context.check_hostname = False
            #context.load_verify_locations(ca_cert)
            #context.load_default_certs()
            context.load_cert_chain(
                certfile=self.server.certfile,
                keyfile=self.server.keyfile,
            )

            # Save new SSL Socket
            self.request = context.wrap_socket(
                self.request,
                server_side=True,
            )
            self.request.connect(self.server.server_address)

        except (ValueError, AttributeError, TypeError):
            try:
                # <=Python 2.7.8
                self.request = ssl.wrap_socket(
                    self.request,
                    server_side=True,
                    certfile=self.server.certfile,
                    keyfile=self.server.keyfile,
                    ssl_version=self.server.ssl_version,
                )
            #except ssl.SSLError, e:
            except ssl.SSLError:
                #print 'DEBUG: SERVER DENIED CLIENT SSL (wrong version)'
                #print str(e)
                return False

        #except ssl.SSLError, e:
        except ssl.SSLError:
            #print 'DEBUG: SERVER DENIED CLIENT SSL (wrong version)'
            #print str(e)
            return False

        return True


    def handle(self):
        # self.request is the TCP socket connected to the client

        #print 'DEBUG: HANDLE IN'
        if self.server._secure:
            #print 'DEBUG: SECURING CONNECTION'
            # Applying SSL
            if not self.apply_ssl():
                self.server._io_wait.clear()
                return

        # Send Welcome Message
        if not self.server.sent_welcome:
            welcome_str = "200 l2g.caronc.dummy NNRP Service Ready"
            if self.server._can_post:
                welcome_str += " (posting ok)"
            if self.server._has_yenc:
                welcome_str += " (yEnc enabled)"
            try:
                self.request.sendall(welcome_str + NNTP_EOD)
            except:
                # connection lost
                #print 'DEBUG: SOCKET ERROR DURING SEND (EXITING)....'
                return

            self.server.sent_welcome = True

        data = BytesIO()
        # Set the io_wait() flag for we're waiting on data now
        self.server._io_wait.set()
        d_len = data.tell()

        while self.server._io_wait.is_set():
            #print 'DEBUG: SERVER LOOP'

            # ptr manipulation
            d_ptr = data.tell()
            if d_ptr > 32768:
                # Truncate
                data = BytesIO(data.read())
                d_ptr = 0
                data.seek(d_ptr)

            try:
                #print 'DEBUG: SERVER BLOCKING FOR DATA'
                pending = self.pending_data(0.8)
                if pending is None:
                    # No more data
                    self.server._io_wait.clear()
                    continue

                if not pending:
                    # nothing pending; back to io_wait
                    continue

                while self.pending_data():
                    #print 'DEBUG: SERVER BLOCKING FOR DATA....'
                    _data = self.request.recv(4096)
                    if not _data:
                        #print 'DEBUG: SERVER NO DATA (EXITING)....'
                        # Reset our sent_welcome flag
                        self.server.sent_welcome = False
                        return

                    # Buffer response
                    data.write(_data)
                    d_len = data.tell()

            except socket.error, e:
                # Socket Issue
                self.server._io_wait.clear()
                #print 'DEBUG: SOCKET ERROR (EXITING)....'
                #print 'DEBUG: ERROR %s' % str(e)
                # Reset our sent_welcome flag
                self.server.sent_welcome = False
                return


            # Seek End for size
            if d_ptr == d_len:
                continue
            data.seek(d_ptr)

            line = data.readline()
            #print 'Scanning Against: "%s"' % line

            #cur_thread = threading.current_thread()
            #response = "{}: {}".format(cur_thread.name, data)
            #self.request.sendall(response)

            # Process over-ride map
            self.server._maplock.acquire()
            override = self.server.override_map.items()
            self.server._maplock.release()

            response = None
            for k, v in override + NNTP_DEFAULT_MAP.items():
                result = k.search(line)
                if result:
                    # we matched
                    if 'response' in v:
                        response = v['response']

                    if 'stat' in v:
                        entry = str(result.group(v['stat']))
                        if not self.server.current_group:
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
                            self.server.current_group = None

                        elif entry not in self.server.group_map:
                            response = '423 No such article in this group'
                            self.server.current_group = None

                        else:
                            response = '211 %d %d %d %s' % (
                                self.server.group_map[entry][0],
                                self.server.group_map[entry][1],
                                self.server.group_map[entry][2],
                                self.server.group_map[entry][3],
                            )

                            # Set Group
                            self.server.current_group = entry

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

                        if self.server._join_group:
                            # A group join is required; perform some overhead
                            #import pdb
                            #pdb.set_trace()
                            if not self.server.current_group:
                                response = '412 No newsgroup selected'
                                break

                            elif self.server.current_group \
                                    not in self.server.fetch_map:
                                # Not found
                                response = '423 No article with that number'
                                break

                            # create a file from our fetch map
                            entry = self.server.fetch_map\
                                [self.server.current_group].get(
                                str(_result.group('id')),
                                self.server.default_fetch,
                            )

                        else:
                            try:
                                # If we are in a group, test it first
                                entry = self.server.fetch_map\
                                    [self.server.current_group].get(
                                    str(_result.group('id')),
                                    self.server.default_fetch,
                                )

                            except KeyError:
                                # Otherwise, iterate through our list and find
                                # match since there is no join_group
                                # requirement
                                found = False
                                for g in self.server.fetch_map.iterkeys():
                                    entry = self.server.fetch_map[g].get(
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
                                        entry = self.server.default_fetch

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

            try:
                self.request.sendall(response + NNTP_EOD)
            except:
                # connection lost
                #print 'DEBUG: SOCKET ERROR DURING SEND (EXITING)....'
                return
        #print 'DEBUG: handle() (EXITING)....'


def ssl_client(hostname, port, message, version=ssl.PROTOCOL_TLSv1):

    # Possible Verify Modes:
    #  - ssl.CERT_NONE
    #  - ssl.CERT_OPTIONAL
    #  - ssl.CERT_REQUIRED

    # We don't want to veryify our key since it's just a
    # localhost self signed one
    cert_reqs = ssl.CERT_NONE

    _sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    sock = ssl.wrap_socket(
        _sock,
        ca_certs="/etc/ssl/certs/ca-bundle.crt",
        cert_reqs=cert_reqs,
        ssl_version=version,
    )
    try:
        sock.connect((hostname, port))
    except ssl.SSLError, e:
        #print 'DEBUG: CLIENT DENIED SSL BY SERVER'
        #print str(e)
        sock.close()
        return

    #print repr(sock.getpeername())
    #print sock.cipher()
    #print pformat(sock.getpeercert())

    try:
        sock.sendall(message)
        print "Sent: %s" % message.strip()
        response = sock.recv(4096)
        print "Received: %s" % response.strip()
        print
    finally:
        sock.close()


def client(ip, port, message):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.connect((ip, port))

    try:
        sock.sendall(message)
        print "Sent: %s" % message.strip()
        response = sock.recv(4096)
        print "Received: %s" % response.strip()
        print
    finally:
        sock.close()

if __name__ == "__main__":
    hostname, portno = "localhost", 0

    ## SSL Checking
    nntp_server = NNTPSocketServer(
        (hostname, portno),
        NNTPBaseRequestHandler,
        secure=True,
    )
    # Get our connection stats
    ipaddr, portno = nntp_server.server_address

    # Push DUMMY NTP Server To Thread
    t = threading.Thread(
        target=nntp_server.serve_forever,
        name='NTPServer',
    )
    # Exit the server thread when the main thread terminates
    t.daemon = True
    t.start()

    ssl_client(hostname, portno, "AUTHINFO USER valid\r\n", ssl.PROTOCOL_TLSv1)
    ssl_client(hostname, portno, "AUTHINFO USER valid\r\n", ssl.PROTOCOL_SSLv3)
    nntp_server.shutdown()

    ## NON SSL
    nntp_server = NNTPSocketServer(
        (hostname, portno),
        NNTPBaseRequestHandler,
        secure=False,
    )
    # Get our connection stats
    ipaddr, portno = nntp_server.server_address

    # Push DUMMY NTP Server To Thread
    t = threading.Thread(
        target=nntp_server.serve_forever,
        name='NTPServer',
    )

    # Append file to map
    nntp_server.map('3', 'alt.bin.test', join(NNTP_TEST_VAR_PATH, '00000005.ntx'))

    # Exit the server thread when the main thread terminates
    t.daemon = True
    t.start()

    client(ipaddr, portno, "AUTHINFO USER valid\r\n")
    #nntp_server.reset()

    client(ipaddr, portno, "AUTHINFO PASS user\r\n")
    #nntp_server.reset()

    client(ipaddr, portno, "READ FILE 3\r\n")
    client(ipaddr, portno, "GROUP alt.bin.test\r\n")
    client(ipaddr, portno, "ARTICLE 3\r\n")
    client(ipaddr, portno, "Hello World 3\r\n")
    #nntp_server.reset()

    nntp_server.shutdown()
