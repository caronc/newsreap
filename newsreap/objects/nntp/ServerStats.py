# What we sent to and received from each server, one row per day
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

from sqlalchemy import BigInteger
from sqlalchemy import Column
from sqlalchemy import Date
from sqlalchemy import Float
from sqlalchemy import ForeignKey
from sqlalchemy import Integer

from .ObjectBase import ObjectBase


class ServerStats(ObjectBase):
    """
    The transfer statistics (see NNTPStatistics) of one server for one day
    (UTC).  Every command adds what it did to the day's row, so several
    workers running at once share it.  Only totals are kept; averages are
    worked out when shown.
    """
    __tablename__ = 'server_stats'

    # The server
    server_id = Column(Integer, ForeignKey('server.id'), primary_key=True)

    # The day (UTC)
    day = Column(Date, primary_key=True)

    # Bytes received and sent
    rx_bytes = Column(BigInteger, nullable=False, default=0)
    tx_bytes = Column(BigInteger, nullable=False, default=0)

    # Seconds the commands ran for, spent on actions, and spent waiting to
    # retry
    elapsed = Column(Float, nullable=False, default=0.0)
    busy_time = Column(Float, nullable=False, default=0.0)
    retry_wait = Column(Float, nullable=False, default=0.0)

    # The fastest a single action went (bytes per second)
    peak_rate = Column(Float, nullable=False, default=0.0)

    # Counts (see NNTPStatistics for what each one is)
    commands = Column(BigInteger, nullable=False, default=0)
    connections = Column(BigInteger, nullable=False, default=0)
    fetched = Column(BigInteger, nullable=False, default=0)
    posted = Column(BigInteger, nullable=False, default=0)
    missing = Column(BigInteger, nullable=False, default=0)
    failed = Column(BigInteger, nullable=False, default=0)
    headers = Column(BigInteger, nullable=False, default=0)
    retries = Column(BigInteger, nullable=False, default=0)
    dropped = Column(BigInteger, nullable=False, default=0)
    stalled = Column(BigInteger, nullable=False, default=0)
    too_slow = Column(BigInteger, nullable=False, default=0)
    bad_replies = Column(BigInteger, nullable=False, default=0)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def __repr__(self):
        return "<ServerStats(server_id=%s, day=%s)>" % (
            self.server_id, self.day)
