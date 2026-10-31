# Tracks transfer statistics (speeds, retries, articles, etc)
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
Statistics gathered while talking to an NNTP Server.

- Every action (a fetch, a post, a listing) records its own
  NNTPStatistics() object; it is found on the response (and article) the
  action returns.
- Objects can be added together (a + b, a += b, sum(...)) so the
  statistics of many actions, articles or connections can be grouped into
  one at the end.
- Only raw totals are stored (bytes, seconds, counts); averages are worked
  out when asked for.  This keeps them exact no matter how many objects
  were added together or in what order.
"""

from time import time

from .Utils import bytes_to_strsize

# The counters an NNTPStatistics() object keeps; adding two objects
# together adds each of these up.
STATISTIC_COUNTERS = (
    # Bytes received from and sent to the server
    'rx_bytes',
    'tx_bytes',

    # Seconds spent on actions (sending, waiting on and receiving
    # responses); time spent waiting to retry is not included
    'busy_time',

    # Seconds spent waiting before retrying a failed action
    'retry_wait',

    # Commands sent to the server
    'commands',

    # Connections opened (logging in included)
    'connections',

    # Articles fetched, posted, not found on the server, or that failed
    # (after all of our retries)
    'fetched',
    'posted',
    'missing',
    'failed',

    # Article headers received (ie: from XOVER when caching a group)
    'headers',

    # The number of times an action was tried again
    'retries',

    # Why actions failed (each failure is counted, retried or not):
    #  - dropped: the connection was lost
    #  - stalled: nothing was received for too long
    #  - too_slow: the transfer was far below our minimum speed
    #  - bad_replies: the server sent something we couldn't understand
    'dropped',
    'stalled',
    'too_slow',
    'bad_replies',
)


class NNTPStatistics:
    """
    Transfer statistics for one or more actions; see the module
    description for details
    """

    def __init__(self, **kwargs):
        """
        Initializes our statistics

        Any counter (ie: rx_bytes=100) can be set right away
        """
        # Prepare our counters
        for name in STATISTIC_COUNTERS:
            setattr(self, name, kwargs.get(name, 0))

        # The fastest a single action ever went (bytes per second)
        self.peak_rate = kwargs.get('peak_rate', 0.0)

        # When the earliest action started and the latest one finished
        # (in seconds since the epoch); None until something is recorded
        self.started = kwargs.get('started')
        self.finished = kwargs.get('finished')

    def reset(self):
        """
        Clears everything recorded so far
        """
        self.__init__()

    def start(self):
        """
        Marks the start of an action and returns the time it started
        """
        now = time()
        if self.started is None or now < self.started:
            # Keep the earliest start
            self.started = now

        return now

    def stop(self, started=None):
        """
        Marks the end of an action

        - started: the value start() returned; when given, the time in
          between (less any time waiting to retry) is added to our
          busy_time and the action's speed is checked against our peak
        """
        now = time()
        if self.finished is None or now > self.finished:
            # Keep the latest finish
            self.finished = now

        if started is None:
            # Only the end time was wanted
            return

        # Time spent working on the action (waiting to retry isn't work)
        busy = max(now - started - self.retry_wait, 0.0)
        self.busy_time += busy

        if busy > 0:
            # Track the fastest action we've seen
            self.peak_rate = max(
                self.peak_rate, (self.rx_bytes + self.tx_bytes) / busy)

    @property
    def total_bytes(self):
        """
        All bytes transferred (received and sent)
        """
        return self.rx_bytes + self.tx_bytes

    @property
    def elapsed(self):
        """
        Seconds from the first action starting to the last one finishing
        """
        if self.started is None or self.finished is None:
            # Nothing recorded yet
            return 0.0

        return max(self.finished - self.started, 0.0)

    @property
    def rate(self):
        """
        The overall speed (bytes per second) across everything recorded.

        When several connections work at the same time, this is the speed
        that was actually achieved.
        """
        elapsed = self.elapsed
        return self.total_bytes / elapsed if elapsed > 0 else 0.0

    @property
    def connection_rate(self):
        """
        The average speed (bytes per second) of a single connection
        """
        return self.total_bytes / self.busy_time \
            if self.busy_time > 0 else 0.0

    def __iadd__(self, other):
        """
        Adds the statistics of another object into this one
        """
        if not isinstance(other, NNTPStatistics):
            return NotImplemented

        # Counters simply add up
        for name in STATISTIC_COUNTERS:
            setattr(self, name, getattr(self, name) + getattr(other, name))

        # The fastest of either is our peak
        self.peak_rate = max(self.peak_rate, other.peak_rate)

        # We now cover from the earliest start to the latest finish
        if other.started is not None and (
                self.started is None or other.started < self.started):
            self.started = other.started

        if other.finished is not None and (
                self.finished is None or other.finished > self.finished):
            self.finished = other.finished

        return self

    def __add__(self, other):
        """
        Returns a new object holding both sets of statistics
        """
        if not isinstance(other, NNTPStatistics):
            return NotImplemented

        result = self.copy()
        result += other
        return result

    def __radd__(self, other):
        """
        Allows sum() to be used on a list of statistics
        """
        if other == 0:
            # sum() starts by adding us to 0
            return self.copy()

        return NotImplemented

    @classmethod
    def combine(cls, items):
        """
        Returns one object holding the statistics of everything in `items`

        Unlike sum(), only one new object is made along the way.
        """
        total = cls()
        for item in items:
            total += item

        return total

    def copy(self):
        """
        Returns a copy of our statistics
        """
        return NNTPStatistics(
            peak_rate=self.peak_rate,
            started=self.started,
            finished=self.finished,
            **{name: getattr(self, name) for name in STATISTIC_COUNTERS})

    def __bool__(self):
        """
        True once anything has been recorded
        """
        return self.started is not None or any(
            getattr(self, name) for name in STATISTIC_COUNTERS)

    def summary(self):
        """
        Returns a human readable summary (several lines of text)
        """
        def speed(value):
            # Show speeds the same way sizes are shown
            return '%s/s' % bytes_to_strsize(value)

        # Each entry is a (label, value) pair
        lines = [
            ('Received', bytes_to_strsize(self.rx_bytes)),
            ('Sent', bytes_to_strsize(self.tx_bytes)),
            ('Time', duration(self.elapsed)),
            ('Speed', '%s overall' % speed(self.rate)),
            ('', '%s per connection' % speed(self.connection_rate)),
            ('', '%s peak' % speed(self.peak_rate)),
        ]

        # Only mention the kinds of articles we dealt with
        articles = [
            '{:,} {}'.format(getattr(self, name), name)
            for name in ('fetched', 'posted', 'missing', 'failed')
            if getattr(self, name)]
        lines.append(('Articles', ', '.join(articles) or 'none'))

        if self.headers:
            # Only shown when headers were fetched
            lines.append(('Headers', '{:,} received'.format(self.headers)))

        lines.append(('Connections', '{:,} opened'.format(self.connections)))

        # Retries and why actions failed
        reasons = [
            '{:,} {}'.format(getattr(self, name), label)
            for name, label in (
                ('dropped', 'dropped'),
                ('stalled', 'stalled'),
                ('too_slow', 'too slow'),
                ('bad_replies', 'bad replies'))
            if getattr(self, name)]

        retries = '{:,}'.format(self.retries) if self.retries else 'none'
        if reasons:
            retries += ' (%s)' % ', '.join(reasons)
        lines.append(('Retries', retries))

        if self.retry_wait:
            # Time spent waiting to try again
            lines.append(
                ('', '%s waiting to retry' % duration(self.retry_wait)))

        return '\n'.join(
            ('%-13s %s' % (label, value)).rstrip() for label, value in lines)

    def __str__(self):
        """
        Returns our summary
        """
        return self.summary()

    def __repr__(self):
        """
        Return an unambiguous version of the object
        """
        return '<NNTPStatistics rx_bytes=%d tx_bytes=%d busy_time=%.3f ' \
            'retries=%d />' % (
                self.rx_bytes, self.tx_bytes, self.busy_time, self.retries)


def duration(seconds):
    """
    Returns seconds as easy to read text (ie: 4m 12s)
    """
    # Work in whole seconds; anything under one second is shown as is
    if seconds < 1:
        return '%.2fs' % seconds

    seconds = int(round(seconds))
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)

    if hours:
        return '%dh %dm %ds' % (hours, minutes, seconds)

    if minutes:
        return '%dm %ds' % (minutes, seconds)

    return '%ds' % seconds
