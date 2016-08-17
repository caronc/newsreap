# -*- coding: utf-8 -*-
#
# A Low Level Socket Manager
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

from gevent import sleep
from gevent import socket
from gevent import ssl
from gevent import Timeout
from gevent.select import select
from gevent.select import error as SelectError

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.x509.oid import NameOID

import gevent.monkey
gevent.monkey.patch_all()

from os.path import isfile
from datetime import datetime

import errno
import re

# Logging
import logging
from newsreap.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)

# Default Binding Address
DEFAULT_BIND_ADDR = '0.0.0.0'

# Number of seconds to wait for a connection to expire/timeout
# or fail to connect before failing out right
socket.setdefaulttimeout(30.0)

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


class SocketBase(object):
    """
       Abstract class with all the functionalist needed for a socket manager.
       methods raising an exception must be implemented in derived classes.

       Arguments to initialize a SocketBase:

            host            the host to connect/listen on

            port            int (default=0)

                            - Port to bind (slave)
                            - 0 means an ephemeral port is chosen
                              (random > 32768)

            timeout         int (default=None)

                            - connection timeout

            secure          Use encryption when managing the connection
                            Expects a True or False, but you can also
                            Specify the encryption Cypher to use here
                            too.


    """
    def __init__(self, host=None, port=0, bindaddr=None, bindport=0,
                 secure=False, *args, **kwargs):

        try:
            self.port = int(port)
        except:
            self.port = 0

        self.host = host
        self.bindaddr = bindaddr
        self.bindport = bindport

        self.connected = False
        self.secure = secure

        if self.secure is None:
            # a little qwirky, but allow users to set secure to
            # None and have it treated the same way as False
            self.secure = False

        self.socket = None

        # Track the current index of the secure protocol singleton to use
        self.secure_protocol_idx = 0

        if self.secure not in (True, False):
            # If self.secure identifies an actual protocol, we want to use it
            # The below simply looks for it's existance, and if it isn't
            # present then we return None
            self.secure_protocol_idx = next((i for i, v in \
                          enumerate(SECURE_PROTOCOL_PRIORITY) \
                                             if v[0] == self.secure), None)

            if self.secure_protocol_idx is None:
                # Protocol specified was not found and/or supported; alert the
                # user with a loud bang; we're done here.
                raise AttributeError("Invalid secure protocol specified.")

        # A spot we can store our peer certificate; this is only used
        # if we're dealing with a secure connection
        self.peer_certificate = None

        # CA stands for Certificate Authority (for those reading this code)
        # This is the master list of servers that you trust for verifying
        # your certificates against.  If you're using a self-signed key
        # then this is useless to you (and you shouldn't verify)
        # You'll need these if you want to verify your host
        self._ca_certs = kwargs.get('ca_certs', "/etc/ssl/certs/ca-bundle.crt")

        # These keys are needed for hosting / listen type connections only
        self._keyfile = kwargs.get('keyfile', None)
        self._certfile = kwargs.get('certfile', None)

        # For Statistics
        self.bytes_in = 0
        self.bytes_out = 0

        # Calculated through connections
        self._local_addr = None
        self._local_port = None
        self._remote_addr = None
        self._remote_port = None

    def can_read(self, timeout=0.0):
        """
        Checks if there is data that can be read from the
        socket (if open). Returns True if there is data and
        False if not.

        It returns None if something very bad happens such as
        a dead connection (bad file descriptor), etc
        """

        # rs = Read Sockets
        # ws = Write Sockets
        # es = Error Sockets
        if self.socket is not None:
            try:
                rs, _, es = select([self.socket], [], [], timeout)
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

        # no socket or no connection
        return None

    def can_write(self, timeout=0):
        """
        Checks if there is data that can be written to the
        socket (if open). Returns True if writing is possible and
        False if not.

        It returns None if something very bad happens such as
        a dead connection (bad file descriptor), etc
        """

        # rs = Read Sockets
        # ws = Write Sockets
        # es = Error Sockets
        if self.socket is not None:
            try:
                _, ws, es = select([], [self.socket], [], timeout)
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

        # no socket or no connection
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
        self.peer_certificate = None

        # reset stats
        self.bytes_in = 0
        self.bytes_out = 0

        # reset remote connection details only
        # we keep the local ones so we can re-use them
        # if possible (especially the port)

        # Calculated through connections
        self._remote_addr = None
        self._remote_port = None

        # return any lingering data in buffer
        if data:
            return data
        return ''

    def bind(self, timeout=None, retries=3, retry_wait=10.0):
        """
          Perform socket binding if nessisary but otherwise this
          performs the connection itself
        """

        # Ensure we are not connected
        self.close()

        # Set a flag we can use
        established = False

        while not established:
            # Create a new socket
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

            # Set Reuse Address flag
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            # We don't need to bind connections that are not configured with
            # bind information.
            if not (self.bindaddr or self.bindport):
                # Store local connection details
                return True

            if not self.bindaddr:
                self.bindaddr = '0.0.0.0'
            if not self.bindport:
                self.bindport = 0

            try:
                bind_str = '%s:%s' % (self.bindaddr, self.bindport)
                self.socket.bind((self.bindaddr, self.bindport))
                logger.debug("Socket bound to %s" % bind_str)
                # Store local connection details
                return True

            except socket.error, e:
                logger.debug("Socket binding error: %s" % str(e))

                if e[0] == errno.EINTR:
                    # A Signal was caught, we aren't far enough in the
                    # listening process to continue; break so that we
                    # can handle this
                    raise SignalCaughtException('Signal received')
                # elif e[0] == errno.EADDRINUSE:

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

        # Code can't ever reach here; but to satisfy lint
        # we'll put a return statement here
        return False

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

        # Blocking until a connection
        logger.debug("Connecting to host: %s:%d" % (
            self.host,
            self.port,
        ))

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

                # Disable Blocking
                self.socket.setblocking(False)

                # Store local details of our socket
                (self._local_addr, self._local_port) = \
                        self.socket.getsockname()
                (self._remote_addr, self._remote_port) = \
                        self.socket.getpeername()

                logger.info(
                    "Connection established to %s:%d" % (
                    self._remote_addr,
                    self._remote_port,
                ))

                if self.secure is False:
                    # A non secure connection; we're done
                    break

                # Encrypt our socket (changing it into an SSLSocket Object)
                self.__encrypt_socket(timeout=timeout, server_side=False)

                # If we get here, we were successful in encrypting the
                # connection; so let's go ahead and break out of our
                # connectino loop
                break

            except ssl.SSLError, e:
                # Secure Connection Failed
                self.close()
                logger.debug(
                    "Failed to secure connection using %s / errno=%d" % (
                        SECURE_PROTOCOL_PRIORITY\
                            [self.secure_protocol_idx][1],
                        e[0],
                    ),
                )

                if self.secure is True:
                    # Fetch next (but only if nothing was explicitly
                    # specified)
                    self.__ssl_version(try_next=True)
                    continue

                # If we reach here, we had a problem with our secure connection
                # handshaking and we were explicitly told to only use 1 (one)
                # protocol.  Thus there is nothing more to retry.  So throwing
                # a SocketException() is not a good idea.  Instead, we throw
                # a SocketRetryLimit() so it can be handled differently
                # upstream
                raise SocketRetryLimit('There are no protocols left to try.')

            except socket.error, e:
                logger.debug("Socket exception received: %s" % (e));
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

    def listen(self, timeout=None, retry_wait=1.00, reuse_port=True):
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

        if reuse_port and self._local_port is not None and self.port == 0:
            # Re-use the last port we acquired that way we can close a
            # connection gracefully and not have to re-acquire a new
            # ephemeral port
            self.port = self._local_port

        # Blocking until a connection
        logger.debug("Listening for a connection at: %s:%d" % (
            self.bindaddr,
            self.port,
        ))
        if timeout:
            socket.setdefaulttimeout(timeout)
            logger.debug("Socket timeout set to :%ds" % (timeout))

        # Bind to address
        if not self.bind(timeout=timeout, retries=3):
            return False

        # Never use blocking
        self.socket.setblocking(False)

        # Listen Enabled, the 1 identifies the number of connections we
        # will accept; never handle more then 1 at a time.
        self.socket.listen(1)

        # Store local details of our socket
        (self._local_addr, self._local_port) = self.socket.getsockname()

        # Get reference time
        cur_time = datetime.now()
        while True:
            try:
                conn, (self._remote_addr, self._remote_port) = \
                        self.socket.accept()
                # If we get here, we've got a connection
                break

            except TypeError, e:
                # Timeout occurred
                pass

            except AttributeError, e:
                # Usually means someone called close() while accept() was
                # blocked. Happens when using this class with threads.
                # No problem... we'll just finish up here
                self.close()
                raise SocketException('Connection broken abruptly')

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

            # Throttle until data is available
            if self.can_read(retry_wait) is None:
                # Something very bad happened
                self.close()
                raise SocketException('Connection broken')

        # Close listening connection
        self.socket.close()

        # Swap socket with new
        self.socket = conn

        # Update our local information
        (self._local_addr, self._local_port) = self.socket.getsockname()
        (self._remote_addr, self._remote_port) = self.socket.getpeername()

        logger.info(
            "Connection established to %s:%d" % (
            self._remote_addr,
            self._remote_port,
        ))

        if self.secure is False:
            # A non secure connection; we're done

            # Toggle our connection flag
            self.connected = True

            # Return our success
            return True

        try:
            # Encrypt our socket (changing it into an SSLSocket Object)
            self.__encrypt_socket(timeout=timeout, server_side=True)

            # If we get here, we were successful in encrypting the connection;
            # so let's go ahead and break out of our connection loop

        except ssl.SSLError, e:
            # Secure Connection Failed
            logger.debug(
                "Failed to secure connection using %s / errno=%d" % (
                    SECURE_PROTOCOL_PRIORITY\
                        [self.secure_protocol_idx][1],
                    e[0],
                ),
            )
            self.close()

            if self.secure is True:
                # Fetch next (but only if nothing was explicitly
                # specified)
                self.__ssl_version(try_next=True)
                raise SocketException('Secure Connection Failed')

            # If we reach here, we had a problem with our secure connection
            # handshaking and we were explicitly told to only use 1 (one)
            # protocol.  Thus there is nothing more to retry.  So throwing
            # a SocketException() is not a good idea.  Instead, we throw
            # a SocketRetryLimit() so it can be handled differently upstream
            raise SocketRetryLimit('There are no protocols left to try.')

        except socket.error, e:
            # logger.debug("Exception received: %s " % (e));
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

        # Toggle our connection flag
        self.connected = True

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

                    # if not timeout:
                    #    raise SocketException('Connection lost')
                    break

                if timeout and bytes_read == 0:
                    # We're done
                    break

                elif bytes_read < max_bytes and not self.can_read():
                    # Process what what we have because there is nothing
                    # further to be read
                    break

                continue

            except ssl.SSLWantReadError, e:
                # Raised by SSL Socket; This is okay data was received, but not
                # all of it. Be patient and try again.
                if self.can_read(retry_wait) is None:
                    self.close()
                    raise SocketException('Connection broken')
                continue

            except ssl.SSLZeroReturnError, e:
                # Raised by SSL Socket
                self.close()
                raise SocketException('Connection broken')

            except socket.error, e:
                if e[0] == errno.EAGAIN:
                    # Timeout occurred; Sleep for a little bit
                    sleep(retry_wait)

                    # Test socket for connectivity
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

    def local_connection_info(self):
        """
        Returns a tuple of current address of 'this' server
        if listening, then it is the listing server.  If performing a
        remote connection, then it is the address that was made in
        a bind() call

        If no connection has been established, the connection returns None.
        """

        if self.socket is None:
            return None

        return (self._local_addr, self._local_port)

    def remote_connection_info(self):
        """
        Returns a tuple of current address of 'this' server
        if listening, then it is the listing server.  If performing a
        remote connection, then it is the address that was made in
        a bind() call

        If no connection has been established, the connection returns None.
        """
        if self.socket is None:
            return None

        return (self._remote_addr, self._remote_port)

    def __ssl_version(self, try_next=False):
        """
        Returns an SSL Context Object while handling the many
        supported protocols
        """

        if try_next:
            # Increment version
            self.secure_protocol_idx += 1

        while self.secure_protocol_idx < len(SECURE_PROTOCOL_PRIORITY):
            # Now return it
            return SECURE_PROTOCOL_PRIORITY[self.secure_protocol_idx][0]

        # If we reach here, we had a problem; use SocketRetryLimit() instead
        # of SocketException() since we're at the end of the line now.
        raise SocketRetryLimit('There are no protocols left to try.')

    def __encrypt_socket(self, timeout=None, retry_wait=1.00,
                         verify=True, cert_reqs=ssl.CERT_NONE,
                         server_side=False):
        """
        Wrap an existing Python socket and return an SSLSocket Object.
        this is iternally called if we're dealing with a secure
        connection.

        timeout is used to break from this function if a certain period
        elapses. Otherwise, if None is specified, we'll only break on
        completion or if an error occurs.

        This function has no return value; it either encryptes the socket
        or throws a SocketException() error.
            cert_reqs can be:
                ssl.CERT_REQUIRED:  certificate verification manditory; no
                                     certificate means to fail.
                ssl.CERT_OPTIONAL:  certificate verifcation performed but
                                     only if one was detected.
                ssl.CERT_NONE:      No certificate verification performed

            verify can kick in if ssl.CERT_NONE is specified (otherwise
            it's not nesisary).  It handles those with self-signed certificates
            by doing it's best to perform some very basic checks that can help
            abort the connection when dealing with a man-in-the-middle attack.

        """
        # Disable Blocking
        self.socket.setblocking(False)

        if self.socket is None:
            # Nothing to do if we have no socket to work with
            raise SocketException("No connection")

        # Define our default keyword arguments
        kwargs = {
            'ssl_version': self.__ssl_version(),
            'do_handshake_on_connect': False,
            'suppress_ragged_eofs': True,
        }

        kwargs['server_side'] = server_side
        if server_side:
            # We need to add a few more parameters
            kwargs['keyfile'] = self._keyfile
            kwargs['certfile'] = self._certfile

            # Verify our certificates/keys exist or abort
            # These checks are nessisary otherwise you'll get strange errors
            # like:
            # _ssl.c:341: error:140B0002:SSL \
            #       routines:SSL_CTX_use_PrivateKey_file:system lib
            #
            # The error itself will surface during the call to wrap_socket()
            # which will throw the exception ssl.SSLError
            #
            # it doesn't hurt to just check ahead of time and make the error
            # human readable
            if not isfile(self._certfile):
                raise ValueError(
                    'Could not locate Certificate: %s' % self._certfile)
            if not isfile(self._keyfile):
                raise ValueError(
                    'Could not locate Private Key: %s' % self._keyfile)

        elif self._ca_certs:
            # if we have a ca_certs reference, then let's store them now
            kwargs['ca_certs'] = self._ca_certs
            if not isfile(self._ca_certs):
                raise ValueError(
                    'Could not locate CA Certificates: %s' % \
                    self._ca_certs,
                )

            # Store our Certificate Requirements
            kwargs['cert_reqs'] = cert_reqs

        # Wrap our socket with the SSLSocket Object
        self.socket = ssl.wrap_socket(self.socket, **kwargs)

        # Get reference time
        cur_time = datetime.now()

        while True:
            # Infinit loop is nessisary for do_handshake() wrapping
            # we'll either exit this loop with a secure connection
            # or we'll time out and gracefully exit.
            try:
                # This command does a lot of the magic
                self.socket.do_handshake()

                logger.info("Secured connection using %s." % (
                    SECURE_PROTOCOL_PRIORITY\
                        [self.secure_protocol_idx][1],
                ))

                if not server_side:
                    # Store our peer certificate
                    try:
                        #self.peer_certificate = \
                        #    self.socket.getpeercert(binary_form=False)
                        # Returns None if there is no certificate for the peer
                        # on the other end; there is always a binary form, but
                        # not always the non-binary version
                        binary_cert = self.socket.getpeercert(binary_form=True)

                    except ValueError:
                        # SSL Handshaking hasn't completed yet; this is a
                        # horrible state to be in if this is the case because
                        # we're only at this part of the code because this
                        # handshaking is presumed to have already been
                        # complete.

                        # Fail at this point for the reason there is something
                        # wrong with our SSL.
                        raise SocketException('Secure handshaking failure')

                    try:
                        # load_der_x509_certificate() also throws a ValueError
                        # if it couldn't parse the certificate. So we need a
                        # separate try/except after acquiring the binary
                        # certificate
                        self.peer_certificate = x509.load_der_x509_certificate(
                            binary_cert,
                            default_backend(),
                        )
                    except ValueError:
                        # we couldn't acquire the certificate
                        self.peer_certificate = None
                        if verify:
                            raise SocketRetryLimit(
                                "Could not acquire site certificate.",
                            )
                        logger.warning("Could not acquire site certificate.")

                    if verify and cert_reqs == ssl.CERT_NONE:
                        # Our own verification process which certainly isn't
                        # bulletproof, but can help with self-signed
                        # certificates and still offer 'some' security
                        try:
                            cert_host = \
                                self.peer_certificate.subject\
                                    .get_attributes_for_oid(NameOID.COMMON_NAME)\
                                    .pop().value

                            # Perform a reverse lookup on our remote IP Address
                            (host, alias, ips) = \
                                    socket.gethostbyaddr(self._remote_addr)

                            # certificate syntax; a simple flick and we make it
                            # a regex supported expression
                            cert_host = cert_host\
                                    .replace('.', '\\.')\
                                    .replace('*\\.', '.*\\.')

                            # If we get here, we've got a hostname to work with
                            host_match_re = re.compile(cert_host, re.IGNORECASE)
                            matched_host = next((h for h in \
                                          [ host ] + alias + ips
                                          if host_match_re.match(h) \
                                                 is not None), False)

                            if not matched_host:
                                raise SocketRetryLimit(
                                    "Certificate for '%s' and does not match." % (
                                        cert_host,
                                ))

                        except socket.herror, e:
                            if e[0] == 2:
                                raise SocketRetryLimit(
                                    "Certificate for '%s' could not be resolved." % (
                                        self._remote_addr,
                                ))

                            # raise anything else
                            raise


                        # TODO: Store fingerprint (if not stored already)
                        #       If already stored, then verify that it hasn't
                        #       changed.

                        except IndexError:
                            raise SocketRetryLimit(
                                'Certificate hostname not defined!',
                            )

                # We're done
                self.connected = True
                return

            except ssl.SSLWantReadError:
                # SSL Connection will block until it is ready
                # The problem with this is can_read() and can_write()
                # all return True.  the socket needs more time
                # though to initialize so we have to call sleep here
                if self.can_read(retry_wait) is None:
                    self.close()
                    raise SocketException('Connection broken')
                continue

            except ssl.SSLWantWriteError:
                # SSL Connection will block until it is ready
                # The problem with this is can_read() and can_write()
                # all return True.  the socket needs more time
                # though to initialize so we have to call sleep here
                if self.can_write(retry_wait) is None:
                    self.close()
                    raise SocketException('Connection broken')
                continue

            if timeout:
                # Update Elapsed Time
                elapsed_time = datetime.now() - cur_time
                elapsed_time = (elapsed_time.days * 86400) \
                                 + elapsed_time.seconds \
                                 + (elapsed_time.microseconds/1e6)

                if elapsed_time > timeout:
                    # Times up
                    self.close()
                    raise SocketException(
                        'Secure Connection Timeout')

        # This code is unreachable but just to satisify standards
        # we'll put a return statement here
        return

    def __del__(self):
        """
        Handle object destruction be closing off any open connections
        """
        self.close()

    def __str__(self):
        if self.secure is False:
            return 'tcp%s:%d' % (self._remote_addr, self._remote_port)
        # else
        return 'tcps://%s:%d' % (self._remote_addr, self._remote_port)

    def __unicode__(self):
        if self.secure is False:
            return u'tcp%s:%d' % (self._remote_addr, self._remote_port)
        # else
        return u'tcps://%s:%d' % (self._remote_addr, self._remote_port)
