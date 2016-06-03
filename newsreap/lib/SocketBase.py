# -*- coding: utf-8 -*-
#
# A Low Level Socket Manager
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

from gevent import sleep
from gevent import socket
from gevent import Timeout
from gevent.select import select
from gevent.select import error as SelectError
import gevent.monkey
gevent.monkey.patch_all()

import errno
import ssl
from datetime import datetime

# Logging
import logging
from lib.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)

# Default Binding Address
DEFAULT_BIND_ADDR = '0.0.0.0'

# Number of seconds to wait for a connection to expire/timeout
# or fail to connect before failing out right
socket.setdefaulttimeout(30.0)

class ConnectionType:
    # Establish a remote connection
    CONNECT = 'connect'
    # Establish a secure remote connection
    SECURE_CONNECT = 'ssl_connect'
    # Listen for a connection
    LISTEN = 'listen'

    # TODO: create an exlusive TLS connect

try:
    SECURE_PROTOCOL_PRIORITY = (
        # The first element is the default priority
        # the remainders are tried next
         (ssl.PROTOCOL_TLSv1_2, u'TLS v1.2'),
         (ssl.PROTOCOL_TLSv1_1, u'TLS v1.1'),
         (ssl.PROTOCOL_TLSv1, u'TLS v1.0'),

        # The following are not 100% secure but are sometimes
        # the only option
         (ssl.PROTOCOL_SSLv23, u'SSL v2.0/3.0'),
    )

except AttributeError:
    # Python v2.7.8 or less
    SECURE_PROTOCOL_PRIORITY = (
        # The first element is the default priority
        # the remainders are tried next
         (ssl.PROTOCOL_TLSv1, u'TLS v1.0'),

        # The following are not 100% secure but are sometimes
        # the only option
         (ssl.PROTOCOL_SSLv23, u'SSL v2.0/3.0'),
    )


class SignalCaughtException(Exception):
    """ Generic Signal Caught Exception;
        This is issued if the user interrupts the action
        such as pressing Ctrl-C or sending a SIGTERM, etc
    """
    pass


class SocketException(Exception):
    """generic socket manager exception class"""
    pass


class SocketRetryLimit(Exception):
    """generic socket manager exception for retry limits reached class"""
    pass

try:
    from OpenSSL import SSL
    WantReadError = SSL.WantReadError
    SSLSocketError = SSL.SysCallError
    ZeroReturnError = SSL.ZeroReturnError

except ImportError:
    # Support systems without SSL Support
    class DummySSLException(Exception):
        pass

    class WantReadError(Exception):
        def __init__(self, value):
            self.param = value

    class ZeroReturnError(Exception):
        def __init__(self, value):
            self.param = value

    class SSLSocketError(Exception):
        def __init__(self, value):
            self.param = value

        def __str__(self):
            return repr(self.param)

    class SSL(object):
        Error = DummySSLException


class SocketBase(object):
    """
       Abstract class with all the functionalist needed for a socket manager.
       methods raising an exception must be implemented in derived classes.

       Arguments to initialize a SocketBase:

            mode            Define Connection Type

            port            int (default=9999)

                            - Port to bind (slave)

            timeout         int (default=None)

                            - connection timeout

    """
    def __init__(self, host=None, port=0, bindaddr=None, bindport=0,
                 mode=ConnectionType.CONNECT, *args, **kwargs):

        try:
            self.port = int(port)
        except:
            self.port = 0

        self.host = host
        self.bindaddr = bindaddr
        self.bindport = bindport

        self.connected = False
        self.mode = mode

        self.socket = None

        # Track the current index of the secure protocol singleton to use
        self.secure_protocol_idx = 0

        # A spot we can store our peer certificate; this is only used
        # if we're dealing with a secure connection
        self.peer_certificate = {}

        # For Statistics
        self.bytes_in = 0
        self.bytes_out = 0

    def __del__(self):
        """
        Handle object destruction be closing off any open connections
        """
        self.close()

    def can_read(self, timeout=0.0):
        """
        Checks if there is data that can be read from the
        socket (if open). Returns True if there is data and
        False if not.
        """

        # rs = Read Sockets
        # ws = Write Sockets
        # es = Error Sockets
        if self.connected and self.socket:
            try:
                rs, _, es = select([self.socket] , [], [], timeout)
            except (SelectError, socket.error), e:
                if e[0] == errno.EBADF:
                    # Bad File Descriptor... hmm
                    self.close()
                    return None

            if len(es) > 0:
                # Bad File Descriptor
                self.close()
                return None

            return len(rs) > 0

        # Really Bad; no socket
        return None


    def can_write(self, timeout=0):
        """
        Checks if there is data that can be written to the
        socket (if open). Returns True if writing is possible and
        False if not.
        """

        # rs = Read Sockets
        # ws = Write Sockets
        # es = Error Sockets
        if self.connected and self.socket:
            try:
                _, ws, es = select([], [self.socket] , [], timeout)
            except (SelectError, socket.error), e:
                if e[0] == errno.EBADF:
                    # Bad File Descriptor... hmm
                    self.close()
                    return None

            if len(es) > 0:
                # Bad File Descriptor
                self.close()
                return None

            return len(ws) > 0

        # Really Bad; no socket
        return None


    def close(self):
        """ Socket Wrapper for people using this class as if it were just
            a python socket class
            """
        data = ''

        # Close socket and exit gracefully as retry count was met
        if self.socket:
            # Copy rest of input buffer
            try:
                data = self.socket.recv()
                if data is None:
                    data = ''

            except Exception:
                pass

            try:
                self.socket.shutdown()
            except:
                # Close connection
                try:
                    self.socket.close()
                except:
                    pass

            # Remove Socket Reference
            self.socket = None

        # update connection flag
        self.connected = False

        # reset our peer certificate
        self.peer_certificate = {}

        # reset stats
        self.bytes_in = 0
        self.bytes_out = 0

        # return any lingering data in buffer
        if data:
            return data
        return ''


    def bind(self, timeout=None, retries=3, retry_wait=10.0):
        """
          Perform socket binding if nessisary
        """

        # Ensure we are not connected
        self.close()

        # Set a flag we can use
        established = False

        while not established:
            if self.mode == ConnectionType.CONNECT:
                # Create a new socket
                self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

            elif self.mode == ConnectionType.SECURE_CONNECT:
                # Create & Secure a new socket
                self.socket = SSL.Connection(
                    SSL.Context(
                        SECURE_PROTOCOL_PRIORITY[self.secure_protocol_idx][0],
                    ),
                    socket.socket(
                        socket.AF_INET,
                        socket.SOCK_STREAM,
                    ),
                )

            # Set Reuse Address flag
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            # We don't need to bind connections that are not configured with
            # bind information.
            if not (self.bindaddr or self.bindport):
                return True

            bindaddr = self.bindaddr
            bindport = self.bindport
            if not self.bindaddr:
                bindaddr = '0.0.0.0'
            if not self.bindport:
                bindport = 0

            try:
                bind_str = '%s:%s' % (bindaddr, bindport)
                self.socket.bind((bindaddr, self.bindport))
                logger.debug("Socket bound to %s" % bind_str)
                return True

            except socket.error, e:
                logger.debug("Socket binding error: %s" % str(e))

                if e[0] == errno.EINTR:
                    # A Signal was caught, we aren't far enough in the
                    # listening process to continue; break so that we
                    # can handle this
                    raise SignalCaughtException('Signal received')
                #elif e[0] == errno.EADDRINUSE:

            # Close socket
            self.close()

            # Update Counter
            if retries is not None:
                retries = retries - 1

                if retries <= 0:
                    # Fatal situation
                    logger.error("Failed to bind to %s", bind_str)
                    raise SocketException(
                        "Failed to bind to %s", bind_str,
                    )
            else:
                # No retries, then just return
                return False

            # no need to thrash
            sleep(retry_wait)


    def connect(self, timeout=None, retry_wait=1.00):
        """
           input Parameters:
           -timeout     How long connect() should block for before giving up
                        and moving on to one of the retires

           -retry_wait  How many seconds to wait before trying again on a
                        timeout (only applicable if retries is specified)

           output Parameters:
           -True if connection established, False otherwise

           Description:
           establish connection according to object attributes.
           return established connection as self.socket.

        """

        connection_str = '%s:%s' % (self.host, self.port)
        # Blocking until a connection
        logger.debug("Connecting to host: %s" % connection_str)

        if timeout:
            socket.setdefaulttimeout(timeout)
            logger.debug("Socket timeout set to :%ds" % (timeout))

        # Get reference time
        cur_time = datetime.now()

        while True:
            if not self.bind(timeout=timeout, retries=3):
                return False

            # Keep alive flag
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

            try:
                # Simple protocol that returns if good, otherwise
                # it throws an exception,  So the next line is always
                # to break out of the loop (had we not already thrown
                # an exception
                self.socket.connect((
                    socket.gethostbyname(self.host),
                    int(self.port),
                ))

                logger.info("Connection established to %s", connection_str)

                if self.mode == ConnectionType.SECURE_CONNECT:
                    while True:
                        try:
                            self.socket.do_handshake()
                            logger.info("Secured connection using %s." % (
                                SECURE_PROTOCOL_PRIORITY\
                                    [self.secure_protocol_idx][1],
                            ))

                            # Store our certificate
                            self.peer_certificate = self.socket.get_peer_certificate()
                            break

                        except WantReadError, e:
                            self.can_read()

                # We're Done
                break

            except SSLSocketError, e:
                # Secure Connection Failed
                logger.error(
                    "Failed to secure connection using %s / errno=%d" % (
                        SECURE_PROTOCOL_PRIORITY\
                            [self.secure_protocol_idx][1],
                        e[0],
                    ),
                )
                self.close()
                raise SocketException('Secure Connection Failed')

            except socket.error, e:
                #logger.debug("Exception received: %s " % (e));
                if e[0] == errno.EINTR:
                    # Ensure socket is closed
                    self.close()
                    # A Signal was caught,
                    # return so we can handle this
                    raise

            except:
                # Close socket
                self.close()
                # Raise issue
                raise

            # Close socket
            self.close()

            if timeout:
                # Compare reference time with time now and sleep for the
                # difference defined by timeout
                delta_time = datetime.now() - cur_time
                delta_time = (delta_time.days * 86400) + delta_time.seconds \
                             + (delta_time.microseconds/1e6)
                if delta_time >= timeout:
                    # Connection timeout elapsed
                    self.close()
                    raise SocketException('Connection timeout')

            # Throttle retry
            sleep(retry_wait)

        self.connected = True
        return True


    def listen(self, timeout=None, retry_wait=1.00):
        """
           input Parameters:
           -timeout     How long accept() should block for before giving up and
                        moving on to one of the retires

           -retry_wait  How many seconds to wait before trying again on a
                        timeout (only applicable if retries is specified)

           output Parameters:
           -True if connection established, False otherwise

           Description:
           establish connection according to object attributes.
           return established connection as self.socket.

        """

        # Blocking until a connection
        connection_str = '%s:%s' % (self.bindaddr, self.port)
        logger.debug("Listening for a connection at: %s" % connection_str)
        if timeout:
            socket.setdefaulttimeout(timeout)
            logger.debug("Socket timeout set to :%ds" % (timeout))

        # Bind to address
        if not self.bind(timeout=timeout, retries=3):
            return False

        if timeout is None:
            self.socket.setblocking(True)
        else:
            self.socket.setblocking(False)

        # Listen Enabled, the 1 identifies the number of connections we
        # will accept; never handle more then 1 at a time.
        self.socket.listen(1)

        # Get reference time
        cur_time = datetime.now()
        while True:
            try:
                conn, self.host = self.socket.accept()
                break
            except TypeError, e:
                # Timeout occurred
                pass
            except socket.error, e:
                if e[0] == errno.EAGAIN:
                    # Timeout occurred
                    pass

                elif e[0] == errno.EINTR:
                    # A Signal was caught,
                    # return so we can handle this
                    raise
                else:
                    # Raise all other exceptions
                    raise
            except:
                # Close socket
                self.close()
                # Raise issue
                raise

            if timeout:
                # Compare reference time with time now and sleep for the
                # difference defined by timeout
                delta_time = datetime.now() - cur_time
                delta_time = (delta_time.days * 86400) + delta_time.seconds \
                             + (delta_time.microseconds/1e6)
                if delta_time >= timeout:
                    # Connection timeout elapsed
                    self.close()
                    raise SocketException('Connection timeout')

            # Throttle retry
            sleep(retry_wait)

        # Close listening connection
        self.close()

        # Swap socket with new
        self.socket = conn

        # Set Blocking on our new socket
        self.socket.setblocking(True)

        # Toggle our connection flag
        self.connected = True

        logger.info("Connection established to %s", connection_str)
        return True


    def read(self, max_bytes=32768, timeout=None, retry_wait=0.25):
        """read()

           max_bytes:  Identify how many bytes to read from TCP stream

           timeout: If specified, then we will read until the amount of
                    bytes are read, or until the timeout (which ever comes
                    first)

           raise an exception if connection lost.
        """
        total_data = []

        # Get reference time
        cur_time = datetime.now()
        # track bytes read
        bytes_read = 0
        # Current elapsed time
        elapsed_time = 0.0

        if not self.connected:
            # No connection
            return ''

        # Make sure we're not blocking
        self.socket.setblocking(False)

        while self.connected and bytes_read < max_bytes:
            # put in a while-loop since this can block for some time
            # if there is no data to read, SIGHUP can cause it to bump
            # as well.

            # Update Elapsed Time
            elapsed_time = datetime.now() - cur_time
            elapsed_time = (elapsed_time.days * 86400) \
                             + elapsed_time.seconds \
                             + (elapsed_time.microseconds/1e6)

            if timeout and elapsed_time > timeout:
                # Time is up; return what we have
                break

            if timeout and not self.can_read(timeout-elapsed_time):
                # Times up
                break

            try:
                # Fetch data
                data = self.socket.recv(max_bytes-bytes_read)

                if data:
                    # Store data
                    bytes_read += len(data)
                    total_data.append(data)

                    # Statistical Purposes
                    self.bytes_in += bytes_read

                # If we reach here, then we aren't using timeouts and the
                # socket returned nothing...
                if not data:
                    # We lost the connection
                    data = self.close()
                    if data:
                        # Store data
                        bytes_read += len(data)
                        self.bytes_in += len(data)
                        total_data.append(data)

                    if not timeout:
                        raise SocketException('Connection lost')

                if timeout and bytes_read == 0:
                    # We're done
                    break

                elif bytes_read < max_bytes and not self.can_read():
                    # Process what what we have because there is nothing
                    # further to be read
                    break

                continue

            except ZeroReturnError, e:
                # Raised by SSL Socket
                self.close()
                raise SocketException('Connection broken')

            except WantReadError, e:
                # SSL Connection will block until it is ready
                # The problem with this is can_read() and can_write()
                # all return True.  the socket needs more time
                # though to initialize so we have to call sleep here
                sleep(retry_wait)
                continue

            except socket.error, e:
                if e[0] == errno.EAGAIN:
                    # Timeout occurred; Sleep for a little bit
                    sleep(retry_wait)
                    if self.can_read(retry_wait) is None:
                        self.close()
                        raise SocketException('Connection broken')
                    continue

                elif e[0] == errno.EINTR:
                    # A Signal was caught return early
                    raise SignalCaughtException('Signal received')

                # A signal such as EPIPE was received to get here.
                # we can assume the connection is dead
                data = self.close()
                if data:
                    # Store data
                    bytes_read += len(data)
                    self.bytes_in += len(data)
                    total_data.append(data)

                if not timeout:
                    raise SocketException('Connection broken')

        # Return Buffer
        return ''.join(total_data)


    def send(self, data, max_bytes=None, retry_wait=0.25):
        """ Socket Wrapper for people using this class as if it were just
            a python socket class; always ignore EINTR flags to finish
            sending the data
        """
        tot_bytes = 0
        if not max_bytes:
            max_bytes = len(data)

        while self.connected and (max_bytes - tot_bytes) > 0:

            # This timer is used to determine how long we should
            # wait before assuming we can't send content and that we
            # have connection problems.

            stale_timeout = max(((max_bytes-tot_bytes)/10800.0), 15.0)

            if not self.can_write(stale_timeout):
                # can't write down pipe; something has gone wrong
                self.close()
                raise SocketException('Connection write wait timeout')

            # bump 10 seconds onto our timeout
            stale_timeout += 10

            # Initialize our stale timeout timer
            stale_timer = Timeout(stale_timeout)

            try:
                # Start stale_timer
                stale_timer.start()

                # Send data
                bytes_sent = self.socket.send(
                    data[tot_bytes:tot_bytes+max_bytes],
                )
                stale_timer.cancel()

                if not bytes_sent:
                    self.close()
                    raise SocketException('Connection lost')

                # Handle content received
                tot_bytes += bytes_sent

                # Statistical Purposes
                self.bytes_out += bytes_sent

            except Timeout:
                # Timeout occurred; Sleep for a little bit
                sleep(retry_wait)
                if self.can_write() is None:
                    self.close()
                    raise SocketException('Connection broken due to timeout')

            except socket.error, e:
                if e[0] == errno.EAGAIN:
                    # Timeout occurred; Sleep for a little bit
                    sleep(retry_wait)
                    if self.can_write() is None:
                        self.close()
                        raise SocketException('Connection broken')

                elif e[0] == errno.EINTR:
                    # A Signal was caught, resend this data before
                    # raising the signal higher.  signals can wait when
                    # there is data flowing
                    raise SignalCaughtException('Signal received')
                else:
                    # errno.EPIPE (Broken Pipe) usually at this point
                    self.close()
                    raise SocketException('Connection lost')
            except Exception, e:
                self.close()
                raise
            finally:
                # always stop the stale_timer
                stale_timer.cancel()

        return tot_bytes


    def __str__(self):
        if self.mode == ConnectionType.SECURE_CONNECT:
            return 'tcps://%s:%d' % (self.host, self.port)
        # else
        return 'tcp%s:%d' % (self.host, self.port)


    def __unicode__(self):
        if self.mode == ConnectionType.SECURE_CONNECT:
            return u'tcps://%s:%d' % (self.host, self.port)
        # else
        return u'tcp%s:%d' % (self.host, self.port)
