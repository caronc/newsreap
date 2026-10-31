# A scripted NNTP Server used to test conversations with NewsReap
#
# Copyright (C) 2026 Chris Caron <lead2gold@gmail.com>
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

"""
A small NNTP server for tests.

- Out of the box it answers like a typical Usenet provider would (login,
  compression, group listings, articles, posting).
- Tests can script any reply with on(); this makes it easy to act like the
  odd servers found in the wild (slow replies, dropped connections,
  garbage, missing terminators, etc).
- Every command it receives is recorded in `received` so a test can check
  the exact conversation that took place.

It only uses the standard socket/ssl libraries so it never relies on the
NewsReap code it is testing.
"""

import bisect
import re
import socket
import ssl
import threading
import time
import zlib
from os.path import abspath
from os.path import dirname
from os.path import join

# Where our test data lives
VAR_PATH = join(dirname(abspath(__file__)), 'var')

# Our self signed certificate (for secure connections)
CERT_FILE = join(VAR_PATH, 'ssl', 'localhost.crt')
KEY_FILE = join(VAR_PATH, 'ssl', 'localhost.key')

# The default group listing returned by LIST ACTIVE
GROUP_LIST_FILE = join(VAR_PATH, 'group.list')

EOL = b'\r\n'


def dot_stuff(body):
    """
    Prepares a multi-line body for sending: every line ends with CRLF and
    lines starting with a dot get an extra one (RFC 3977)
    """
    if isinstance(body, str):
        body = body.encode('utf-8')

    lines = body.split(b'\n')
    if lines and lines[-1] == b'':
        # The body already ended with a new line
        lines.pop()

    return b''.join(
        (b'.' + line if line.startswith(b'.') else line).rstrip(b'\r') + EOL
        for line in lines)


def dot_unstuff(body):
    """
    Undoes dot_stuff() on what a client sent us
    """
    return b''.join(
        line[1:] if line.startswith(b'..') else line
        for line in body.splitlines(keepends=True))


class Reply:
    """
    A reply for the server to send.

    - status: the status line (ie: '200 Welcome')
    - body: content for a multi-line reply; it is dot stuffed and the
      terminating '.' line is added for you
    - raw: bytes sent exactly as they are (after the status line); use this
      to send broken or unusual content
    - compress: compress the body (like servers do once
      `XFEATURE COMPRESS GZIP` is enabled)
    - chunk_size/delay: send the reply a little at a time, waiting `delay`
      seconds between each piece (like a slow or stalling server)
    - close: drop the connection once the reply is sent
    """

    def __init__(self, status=None, body=None, raw=None, compress=False,
                 chunk_size=0, delay=0.0, close=False):
        self.status = status
        self.body = body
        self.raw = raw
        self.compress = compress
        self.chunk_size = chunk_size
        self.delay = delay
        self.close = close

    def payload(self):
        """
        Returns the bytes that make up this reply
        """
        status = self.status
        if isinstance(status, str):
            status = status.encode('utf-8')

        content = b''
        if self.body is not None:
            content = dot_stuff(self.body)
            if self.compress:
                # The compressed block is followed by the terminator
                content = zlib.compress(content) + b'.' + EOL
                status += b' [COMPRESS=GZIP]'
            else:
                content += b'.' + EOL

        if self.raw is not None:
            content += self.raw if isinstance(self.raw, bytes) \
                else self.raw.encode('utf-8')

        if status is None:
            return content

        return status + EOL + content


class Session:
    """
    What the server knows about one connected client
    """
    def __init__(self, sock, address):
        self.sock = sock
        self.address = address
        self.user = None
        self.authenticated = False
        self.group = None
        self.compress = False
        self.closed = False


class FakeNNTPServer:
    """
    A scripted NNTP Server; see the module description for details
    """

    def __init__(self, secure=False, join_group=True, users=None,
                 welcome='200 newsreap test server ready (posting ok)',
                 compression=True, posting=True, group_list=GROUP_LIST_FILE):

        self.secure = secure

        # When True a GROUP must be selected before articles are fetched
        self.join_group = join_group

        # Valid logins
        self.users = {'valid': 'valid'} if users is None else users

        # The very first thing sent to a client
        self.welcome = welcome

        # Whether XFEATURE COMPRESS GZIP is supported
        self.compression = compression

        # Whether POST is accepted
        self.posting = posting

        # The content returned by LIST ACTIVE
        self.group_list = group_list

        # Every command received (in order)
        self.received = []

        # Every article posted to us
        self.posted = []

        # Our groups: name -> (count, low, high)
        self.groups = {}

        # Our articles: message id -> (groups, filepath)
        self.articles = {}

        # Overview (XOVER) entries: group -> sorted list of (number, line)
        self.overview = {}

        # Scripted rules (checked before our own replies)
        self._rules = []

        self.host = '127.0.0.1'
        self.port = None

        self._listener = None
        self._threads = []
        self._sessions = []
        self._running = threading.Event()
        self._lock = threading.Lock()

    #
    # Setting the server up
    #
    def on(self, pattern, reply, once=False):
        """
        Answer commands matching `pattern` (a regular expression) with
        `reply`, which can be:

        - a status string (ie: '480 Login first')
        - a Reply() object
        - a function taking (session, match) that returns either of the
          above (or None to let the server answer as it normally would)

        once=True removes the rule after it's been used one time.
        Rules added later are checked first.
        """
        rule = [re.compile(pattern, re.IGNORECASE), reply, once]
        with self._lock:
            self._rules.insert(0, rule)
        return self

    def add_group(self, name, count=0, low=0, high=0):
        """
        Adds a group the server knows about
        """
        self.groups[name] = (count, low, high)
        return self

    def add_article(self, message_id, filepath, groups=()):
        """
        Makes the article stored in `filepath` (headers, blank line, body)
        available by its message id within the groups specified
        """
        if isinstance(groups, str):
            groups = (groups, )

        self.articles[message_id.strip('<>')] = (tuple(groups), filepath)
        for group in groups:
            count, low, high = self.groups.get(group, (0, 0, 0))
            self.groups[group] = (count + 1, low, high + 1)
        return self

    def add_overview(self, group, number, subject, poster, date,
                     message_id, size=1000, lines=10):
        """
        Adds an overview (XOVER) entry for article `number` in `group`.

        - date is a datetime (or the text exactly as a server sends it)
        """
        if not isinstance(date, str):
            date = date.strftime('%d %b %Y %H:%M:%S +0000')

        line = '%d\t%s\t%s\t%s\t<%s>\t\t%d\t%d\tXref: fake %s:%d' % (
            number, subject, poster, date, message_id.strip('<>'), size,
            lines, group, number)

        entries = self.overview.setdefault(group, [])
        bisect.insort(entries, (number, line))

        numbers = [n for n, _ in entries]
        self.groups[group] = (len(entries), numbers[0], numbers[-1])
        return self

    def wait_for(self, command, count=1, timeout=5.0):
        """
        Waits until `command` has been received `count` times; returns True
        if it was
        """
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.received.count(command) >= count:
                return True
            time.sleep(0.01)
        return False

    def commands(self):
        """
        Returns the commands received; passwords are hidden
        """
        return [
            re.sub(r'^(AUTHINFO PASS) .*$', r'\1 ****', c, flags=re.I)
            for c in self.received]

    #
    # Running
    #
    def start(self):
        """
        Starts listening (on a random free port)
        """
        self._listener = socket.create_server((self.host, 0))
        self._listener.settimeout(0.2)
        self.port = self._listener.getsockname()[1]
        self._running.set()

        thread = threading.Thread(target=self._accept, daemon=True)
        thread.start()
        self._threads.append(thread)
        return self

    def stop(self):
        """
        Stops the server and drops any clients
        """
        self._running.clear()
        for session in list(self._sessions):
            self._close(session)

        for thread in self._threads:
            thread.join(timeout=5)

        if self._listener:
            self._listener.close()
            self._listener = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *args):
        self.stop()

    def _accept(self):
        context = None
        if self.secure:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(CERT_FILE, KEY_FILE)

        while self._running.is_set():
            try:
                sock, address = self._listener.accept()

            except (TimeoutError, OSError):
                continue

            if context:
                try:
                    sock = context.wrap_socket(sock, server_side=True)

                except (ssl.SSLError, OSError):
                    sock.close()
                    continue

            session = Session(sock, address)
            self._sessions.append(session)
            thread = threading.Thread(
                target=self._serve, args=(session, ), daemon=True)
            thread.start()
            self._threads.append(thread)

    def _serve(self, session):
        """
        Talks to one client until it leaves
        """
        sock = session.sock
        sock.settimeout(0.5)
        buffer = b''

        try:
            self._send(session, self._lookup(session, None, self.welcome))

            while self._running.is_set() and not session.closed:
                if EOL not in buffer:
                    try:
                        data = sock.recv(65536)

                    except TimeoutError:
                        continue

                    if not data:
                        break

                    buffer += data
                    continue

                line, buffer = buffer.split(EOL, 1)
                command = line.decode('latin-1')
                self.received.append(command)

                reply = self._answer(session, command)
                if reply == 'POST':
                    # Read the article that follows
                    while b'\r\n.\r\n' not in b'\r\n' + buffer:
                        try:
                            data = sock.recv(65536)

                        except TimeoutError:
                            continue

                        if not data:
                            return

                        buffer += data

                    article, buffer = (b'\r\n' + buffer).split(
                        b'\r\n.\r\n', 1)
                    self.posted.append(dot_unstuff(article[2:] + EOL))
                    reply = self._lookup(
                        session, None, '240 Article received OK')

                self._send(session, reply)

        except OSError:
            pass

        finally:
            self._close(session)

    def _close(self, session):
        if not session.closed:
            session.closed = True
            try:
                session.sock.close()

            except OSError:
                pass

    def _send(self, session, reply):
        if reply is None:
            return

        if not isinstance(reply, Reply):
            reply = Reply(reply)

        payload = reply.payload()
        size = reply.chunk_size or len(payload) or 1
        for offset in range(0, len(payload), size):
            if offset and reply.delay:
                time.sleep(reply.delay)
            session.sock.sendall(payload[offset:offset + size])

        if reply.close:
            self._close(session)

    def _lookup(self, session, match, reply):
        """
        Turns a rule's reply into something we can send
        """
        if callable(reply):
            reply = reply(session, match)
        return reply

    def _answer(self, session, command):
        """
        Works out how to reply to a command
        """
        with self._lock:
            rules = list(self._rules)

        for rule in rules:
            match = rule[0].search(command)
            if not match:
                continue

            if rule[2]:
                with self._lock:
                    if rule in self._rules:
                        self._rules.remove(rule)

            reply = self._lookup(session, match, rule[1])
            if reply is not None:
                return reply

        return self._default(session, command)

    #
    # How a typical server answers
    #
    def _default(self, session, command):
        word, _, args = command.partition(' ')
        word = word.upper()
        args = args.strip()

        if word == 'AUTHINFO':
            kind, _, value = args.partition(' ')
            if kind.upper() == 'USER':
                session.user = value
                if value in self.users:
                    return '381 PASS required'
                return '481 Authentication failed'

            if kind.upper() == 'PASS':
                if session.user in self.users \
                        and self.users[session.user] == value:
                    session.authenticated = True
                    return '281 Ok'
                return '481 Authentication failed'

            return '501 Syntax error'

        if word == 'QUIT':
            return Reply('205 Goodbye', close=True)

        if word == 'MODE':
            return '200 Reader mode, posting permitted'

        if word == 'XFEATURE':
            if self.compression and args.upper() == 'COMPRESS GZIP':
                session.compress = True
                return '290 Feature enabled'
            return '500 Unknown command'

        if self.users and not session.authenticated:
            return '480 Authentication required'

        if word == 'LIST':
            if args.upper() in ('', 'ACTIVE'):
                with open(self.group_list, 'rb') as fp:
                    return Reply(
                        '215 Newsgroups in form "group high low flags"',
                        body=fp.read(), compress=session.compress)
            return '501 Syntax error'

        if word == 'GROUP':
            if args not in self.groups:
                session.group = None
                return '411 No such newsgroup'

            session.group = args
            count, low, high = self.groups[args]
            return '211 %d %d %d %s' % (count, low, high, args)

        if word in ('ARTICLE', 'BODY', 'HEAD', 'STAT'):
            return self._article(session, word, args)

        if word in ('XOVER', 'OVER'):
            return self._xover(session, args)

        if word == 'POST':
            if not self.posting:
                return '440 Posting not permitted'
            # Tell the client to go ahead; we read the article next
            self._send(session, '340 Send article to be posted')
            return 'POST'

        return '500 What?'

    def _xover(self, session, args):
        if not session.group:
            return '412 No newsgroup selected'

        low, _, high = args.partition('-')
        try:
            low = int(low)
            high = int(high) if high else low

        except ValueError:
            return '501 Syntax error'

        entries = self.overview.get(session.group, [])
        first = bisect.bisect_left(entries, (low, ''))
        last = bisect.bisect_right(entries, (high, '\uffff'))
        return Reply(
            '224 Overview information follows',
            body=''.join(line + '\r\n' for _, line in entries[first:last]),
            compress=session.compress)

    def _article(self, session, word, args):
        if self.join_group and not session.group:
            return '412 No newsgroup selected'

        message_id = args.strip('<> ')
        entry = self.articles.get(message_id)
        if entry is None or (
                self.join_group and session.group not in entry[0]):
            return '430 No article with that message-id'

        if word == 'STAT':
            return '223 0 <%s>' % message_id

        with open(entry[1], 'rb') as fp:
            content = fp.read()

        # Split the headers from the body
        head, sep, body = content.replace(b'\r\n', b'\n').partition(b'\n\n')

        if word == 'HEAD':
            return Reply('221 0 <%s>' % message_id, body=head)

        if word == 'BODY':
            return Reply('222 0 <%s>' % message_id, body=body)

        return Reply('220 0 <%s>' % message_id, body=content)
