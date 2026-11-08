# A common NNTP Database management class for manipulating SQLAlchemy
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


# Importing these libraries forces them associate themselves
# with the ObjectBase
from .objects.nntp.Group import Group
from .objects.nntp.Group import FLAGS_LENGTH
from .objects.nntp.Group import NAME_LENGTH
from .objects.nntp.GroupAlias import GroupAlias
from .objects.nntp.GroupOption import GroupOption
from .objects.nntp.GroupTrack import GroupTrack
from .objects.nntp.Claim import Claim
from .objects.nntp.Server import Server
from .objects.nntp.ServerStats import ServerStats

# The ObjectBase which contains all of the data required to
# access our table.
from .objects.nntp.ObjectBase import ObjectBase
from .objects.nntp.Vsp import Vsp

from collections import namedtuple
from datetime import datetime
from datetime import timedelta

from sqlalchemy import case
from sqlalchemy import exists
from sqlalchemy import func
from sqlalchemy import insert
from sqlalchemy import or_
from sqlalchemy import text
from sqlalchemy import update

from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import ProgrammingError

from .Database import Database
from .NNTPStatistics import NNTPStatistics
from .NNTPStatistics import STATISTIC_COUNTERS
from .Utils import normal_server_key
from .Utils import server_key
from .Utils import utcnow
from .objects.nntp.Common import GROUP_OPTIONS
from .objects.nntp.Common import _get_groups
from .objects.nntp.Common import get_group_options
from .objects.nntp.Common import set_group_options

# Logging
import logging
from newsreap.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)

# The catch wit SQLLite when referencing paths is:
# sqlite:///relative/path/to/where we are now
# sqlite:////absolute/path/
#
# TODO: When creating an error message, it might make sense to parse
# the database path 'IF using SQLite' to make the error
# messages more feasable
# configure Session class with desired options


class UnknownGroups(LookupError):
    """
    Some of the names given match no group (or alias); nothing was changed.
    The names are in .names
    """
    def __init__(self, names):
        self.names = list(names)
        super().__init__('No group or alias is called %s.' % ', '.join(
            "'%s'" % name for name in self.names))


class UnknownServer(LookupError):
    """
    A server that isn't in the database yet ('nr db init' adds the ones
    in the configuration file); its key (see Utils.server_key()) is in
    .host
    """
    def __init__(self, host):
        self.host = host
        super().__init__(
            "The NNTP Server '%s' isn't in the database yet." % host)


class UnknownAlias(LookupError):
    """
    There is no alias by that name; it is in .alias
    """
    def __init__(self, alias):
        self.alias = alias
        super().__init__("There is no alias '%s'." % alias)


# What a removal did:
# - removed: {group name: id} of the groups taken off
# - unknown: the names given that match no group (or alias)
# - outside: the groups named that weren't part of the alias
Removal = namedtuple('Removal', ('removed', 'unknown', 'outside'))


# One server's transfers (see NNTPDatabase.transfers()); each period is an
# NNTPStatistics (its elapsed time is how long the commands ran):
# - today, yesterday, week (the last 7 days, today included), kept (every
#   day kept)
# - days: how many days are kept
# - usual: what an average day before today looks like (None until there
#   are USUAL_DAYS of them)
# - host: the server (its key; see Utils.server_key())
# - warnings: what looks wrong today next to the usual day; each is
#   (what, today's value, the usual value) with what being 'slow' (bytes
#   per second) or 'failures' (failures per command sent)
Transfers = namedtuple('Transfers', (
    'host', 'today', 'yesterday', 'week', 'kept', 'days', 'usual',
    'warnings'))

# Days needed before today is compared with the usual day
USUAL_DAYS = 3

# Today is slow when its speed is below this share of the usual speed (and
# at least SLOW_BYTES were moved, so a few small requests don't count)
SLOW_SHARE = 0.5
SLOW_BYTES = 10 * 1024 * 1024

# Today has too many failures when they are at least this many times the
# usual share, and at least FAILURE_POINTS more of every command sent
FAILURE_TIMES = 2
FAILURE_POINTS = 0.01

# What counts as a failure (each one is retried, or given up on)
FAILURES = ('dropped', 'stalled', 'too_slow', 'bad_replies')


def failure_share(stats):
    """
    Returns the failures (see FAILURES) per command sent (0.0 to 1.0)
    """
    return sum(getattr(stats, name) for name in FAILURES) / stats.commands \
        if stats.commands else 0.0


def alias_name(alias):
    """
    Returns an alias the way it is stored (lower case, no spaces around
    it); raises ValueError if nothing is left
    """
    name = str(alias or '').lower().strip()
    if not name:
        raise ValueError('An alias needs a name.')
    return name


class NNTPDatabase(Database):
    """
    A managment class to the NNTP Core Database manipulation.
    """

    def __init__(self, engine=None, reset=None):
        """
        Initialize NNTP Database Manager
        """
        super().__init__(
            base=ObjectBase,
            vsp=Vsp,
            engine=engine,
            reset=reset,
        )

    def open(self, engine=None, reset=None):
        """
        Opens the database (see Database.open()); a reset drops the claims
        table too, so it is looked for again
        """
        self._claim_table = False
        return super().open(engine=engine, reset=reset)

    def groups(self, names=None, watched=False, strict=True):
        """
        Returns {group name: id} for the groups (or aliases) named, plus
        every watched group with watched=True.

        Raises UnknownGroups (nothing is returned) if a name matches
        nothing; with strict=False those names are just left out.
        """
        names = [names] if isinstance(names, (str, int)) else list(
            names or ())

        with self.transaction() as session:
            unknown = [
                name for name in names if str(name).strip() and
                not _get_groups(session, lookup=name)]
            if unknown and strict:
                raise UnknownGroups(unknown)

            names = [name for name in names if name not in unknown]
            return _get_groups(
                session, lookup=names, watched=watched) or {}

    def group_names(self):
        """
        Returns the name of every group known (sorted)
        """
        with self.transaction() as session:
            return [name for name, in session.query(Group.name)
                    .order_by(Group.name)]

    def group_list(self, all=False):
        """
        Returns the groups that are watched or were ever fetched (every
        group the servers carry with all=True), by name, each as a dict:

        - name, id, count, flags, watch
        - content, nzb, retention: the group's own settings (None follows
          the configuration file; always None with all=True)
        """
        with self.transaction() as session:
            query = session.query(Group)
            if not all:
                # SQLAlchemy needs == here (E712 is about plain Python)
                query = query.filter(or_(
                    Group.watch == True,  # noqa: E712
                    exists().where(GroupTrack.group_id == Group.id)))

            results = query.order_by(Group.name.asc()).all()
            options = get_group_options(
                session, [r.id for r in results]) if not all else {}

        return [{
            'name': r.name,
            'id': r.id,
            'count': r.count,
            'flags': r.flags,
            'watch': bool(r.watch),
            **{name: options.get(r.id, {}).get(name)
               for name in GROUP_OPTIONS},
        } for r in results]

    def watch(self, names):
        """
        Adds the groups (or aliases) named to the watch list (their
        settings are left as they are; see configure()).

        - returns {group name: id} of the groups watched for the first time
        - raises UnknownGroups (nothing is changed) if a name matches
          nothing
        """
        with self.transaction() as session:
            return self._set_watch(session, self._find(session, names), True)

    def unwatch(self, names):
        """
        Takes the groups (or aliases) named off the watch list; a name
        that matches nothing is skipped, the rest are still removed.

        Returns a Removal: the groups that were being watched (removed)
        and the names that matched nothing (unknown).
        """
        with self.transaction() as session:
            groups, unknown = self._resolve(session, names)
            removed = self._set_watch(session, groups, False)

        return Removal(removed, unknown, [])

    @staticmethod
    def _set_watch(session, groups, watch):
        """
        Puts groups ({name: id}) on (or takes them off) the watch list;
        returns the ones that changed
        """
        changed = {}
        for name, _id in groups.items():
            if session.query(Group).filter(Group.id == _id)\
                    .filter(Group.watch == (not watch))\
                    .update({Group.watch: watch}):
                changed[name] = _id

        return changed

    def group_options(self, names):
        """
        Returns {group name: {'content': ..., 'nzb': ..., 'retention': ...}}
        for the groups (or aliases) named; None means it follows the
        configuration file.

        Raises UnknownGroups if a name matches nothing.
        """
        with self.transaction() as session:
            groups = self._find(session, names)
            found = get_group_options(session, groups.values())

        return {name: found.get(_id, dict.fromkeys(GROUP_OPTIONS))
                for name, _id in groups.items()}

    def configure(self, names, **options):
        """
        Sets the groups' (or aliases') own settings, watched or not:

        - content, nzb: True or False
        - retention: days kept when pruned (0 keeps everything)
        - None puts a setting back to what the configuration file says

        Returns group_options() for them afterwards.  Raises UnknownGroups
        (nothing is changed) if a name matches nothing, and ValueError for
        a setting that doesn't exist or a value that doesn't fit.
        """
        for name, value in options.items():
            if name not in GROUP_OPTIONS:
                raise ValueError("There is no group setting '%s'." % name)

            if value is None:
                continue

            if name == 'retention':
                if isinstance(value, bool) or not isinstance(value, int) \
                        or value < 0:
                    raise ValueError(
                        'retention is a number of days (0 or more).')

            elif not isinstance(value, bool):
                raise ValueError('%s is either True or False.' % name)

        with self.transaction() as session:
            groups = self._find(session, names)
            if options:
                set_group_options(session, groups.values(), **options)

        return self.group_options(list(groups))

    def aliases(self):
        """
        Returns {alias: [its group names]}, both sorted by name
        """
        found = {}
        with self.transaction() as session:
            for group, alias in session.query(Group.name, GroupAlias.name)\
                    .join(GroupAlias, GroupAlias.group_id == Group.id)\
                    .order_by(GroupAlias.name, Group.name):
                found.setdefault(alias, []).append(group)

        return found

    def alias_add(self, alias, names):
        """
        Makes alias stand for the groups (or aliases) named too.

        - returns {group name: id} of the groups it now stands for
        - raises UnknownGroups (nothing is added) if a name matches nothing,
          and ValueError if the alias is blank
        """
        alias = alias_name(alias)
        with self.transaction() as session:
            groups = self._find(session, names)
            for _id in groups.values():
                session.merge(GroupAlias(group_id=_id, name=alias))

        return groups

    def alias_remove(self, alias, names=None):
        """
        Takes the groups (or the groups of the aliases) named out of alias;
        without any, the alias is removed entirely.  Other aliases keep
        their groups.  Names that match nothing, or groups that aren't part
        of the alias, are skipped; the rest are still removed.

        - returns a Removal: the groups it no longer stands for (removed),
          the names that matched nothing (unknown) and the groups that
          weren't part of it (outside)
        - raises UnknownAlias if there is no such alias
        """
        alias = alias_name(alias)
        with self.transaction() as session:
            members = dict(session.query(Group.name, Group.id)
                           .join(GroupAlias, GroupAlias.group_id == Group.id)
                           .filter(GroupAlias.name == alias))
            if not members:
                raise UnknownAlias(alias)

            unknown, outside = [], []
            if names:
                groups, unknown = self._resolve(session, names)
                outside = [name for name in groups if name not in members]
                groups = {name: _id for name, _id in groups.items()
                          if name in members}

            else:
                groups = members

            if groups:
                session.query(GroupAlias)\
                    .filter(GroupAlias.name == alias)\
                    .filter(GroupAlias.group_id.in_(list(groups.values())))\
                    .delete(synchronize_session=False)

        return Removal(groups, unknown, outside)

    def server_id(self, key):
        """
        Returns the database id of a server (by its key, see
        Utils.server_key(); for a plain server that is its host); raises
        UnknownServer if it isn't there
        """
        key = normal_server_key(key)
        with self.transaction() as session:
            found = session.query(Server.id)\
                .filter(Server.key == key).first()

        if found is None:
            raise UnknownServer(key)

        return found[0]

    def save_groups(self, key, listing):
        """
        Keeps the groups a server (by its key; see server_id()) listed
        (each a dict: group, count, flags, low, high), adding the new ones
        and refreshing the rest.

        - returns how many groups were new
        - raises UnknownServer if the server isn't in the database
        """
        key = normal_server_key(key)
        with self.transaction() as session:
            found = session.query(Server.id)\
                .filter(Server.key == key).first()
            if found is None:
                raise UnknownServer(key)

            # The groups we already know about (name -> id), read once
            # rather than once per group
            known = dict(session.query(Group.name, Group.id))

            # Sort our listing into new groups and ones to update
            new_groups = []
            updates = []
            watermarks = {}
            for r in listing:
                name = r['group']
                if len(name) > NAME_LENGTH:
                    # Too long to keep (a server sending garbage); the
                    # rest of the listing is still kept
                    logger.warning(
                        "Skipped a group name longer than %d characters: "
                        "%s..." % (NAME_LENGTH, name[:NAME_LENGTH]))
                    continue

                # Convert flag list back into a string
                flags = ''.join(r['flags'])[:FLAGS_LENGTH]

                group_id = known.get(name)
                if name not in known:
                    # A group we haven't seen before
                    new_groups.append(
                        {'name': name, 'count': r['count'], 'flags': flags})

                    # Servers sometimes list a group twice; only add it once
                    known[name] = None

                elif group_id is not None:
                    # A group we know; refresh its details
                    updates.append(
                        {'id': group_id, 'count': r['count'],
                         'flags': flags})
                    watermarks[group_id] = (r['low'], r['high'])

            if new_groups:
                # Add our new groups all at once
                session.execute(insert(Group), new_groups)

            if updates:
                # Update the ones we know all at once
                session.execute(update(Group), updates)

            # Keep the server's first and last article of what we track
            for track in session.query(GroupTrack)\
                    .filter(GroupTrack.server_id == found[0]):
                if track.group_id in watermarks:
                    track.low, track.high = watermarks[track.group_id]

        return len(new_groups)

    def add_servers(self, servers):
        """
        Adds the servers that aren't in the database yet; returns the keys
        added (see Utils.server_key())

        - servers: each a server's settings (a dict with its host, port,
          username and so on), or just its host
        """
        added = []
        with self.transaction() as session:
            known = {key for key, in session.query(Server.key)}
            for server in servers:
                if isinstance(server, dict):
                    host = str(server.get('host') or '').strip().lower()
                    key = server_key(**server)

                else:
                    # A key (ie: a host, or me@host)
                    key = normal_server_key(server)
                    host = key.rpartition('@')[2]

                if key not in known:
                    # The name is only shown
                    session.add(Server(name=key, host=host, key=key))
                    known.add(key)
                    added.append(key)

        return added

    def group_counts(self):
        """
        Returns (groups known, groups watched)
        """
        with self.transaction() as session:
            return (
                session.query(func.count(Group.id)).scalar() or 0,
                session.query(func.count(Group.id))
                .filter(Group.watch.is_(True)).scalar() or 0)

    def tracked(self):
        """
        Returns {group name: (last scanned, last indexed)} for every group
        fetched (the latest of any server)
        """
        found = {}
        with self.transaction() as session:
            for name, last_scan, last_index in session.query(
                    Group.name, GroupTrack.last_scan, GroupTrack.last_index)\
                    .join(GroupTrack, GroupTrack.group_id == Group.id):
                scan, indexed = found.get(name, (None, None))
                found[name] = (
                    max(filter(None, (scan, last_scan)), default=None),
                    max(filter(None, (indexed, last_index)), default=None))

        return found

    def track(self, group_id, server_id, low, high, scanned=False):
        """
        Notes a group's first and last article on a server after a scan
        (scanned=True also notes that something was fetched just now)
        """
        with self.transaction() as session:
            entry = session.query(GroupTrack)\
                .filter(GroupTrack.group_id == group_id)\
                .filter(GroupTrack.server_id == server_id).first()

            if not entry:
                # First time we've scanned this group on this server
                entry = GroupTrack(
                    group_id=group_id,
                    server_id=server_id,
                    scan_pointer=low,
                    index_pointer=low,
                )
                session.add(entry)

            entry.low = low
            entry.high = high

            if scanned:
                # When we last fetched something
                entry.last_scan = datetime.now()

    def indexed(self, group_ids):
        """
        Notes that the groups given (by id) were just indexed
        """
        group_ids = list(group_ids)
        with self.transaction() as session:
            session.query(GroupTrack)\
                .filter(GroupTrack.group_id.in_(group_ids))\
                .update({GroupTrack.last_index: datetime.now()},
                        synchronize_session=False)

    def forget_tracking(self, group_ids):
        """
        Forgets how far the groups given (by id) were fetched and indexed
        on every server
        """
        group_ids = list(group_ids)
        with self.transaction() as session:
            session.query(GroupTrack)\
                .filter(GroupTrack.group_id.in_(group_ids))\
                .delete(synchronize_session=False)

    def record_stats(self, by_host, day=None):
        """
        Adds what a command did (NNTPStatistics by server key; see
        Utils.server_key()) to each server's row for the day (UTC today
        when not given); returns the keys recorded.

        - several workers can add to the same row at the same time
        - a server that isn't in the database (see 'nr db init') is
          skipped
        - nothing is written for a server that did nothing
        """
        from .NNTPIndexer import NNTPIndexer

        day = day or utcnow().date()
        wanted = {normal_server_key(host): stats
                  for host, stats in by_host.items()
                  if stats.commands or stats.total_bytes}
        if not wanted:
            return []

        recorded = []
        with self.transaction() as session:
            servers = dict(session.query(Server.key, Server.id)
                           .filter(Server.key.in_(list(wanted))))

            for host, stats in wanted.items():
                server_id = servers.get(host)
                if server_id is None:
                    logger.debug(
                        "No statistics kept for '%s' (it isn't in the "
                        "database)." % host)
                    continue

                # Make sure the day has a row (another worker may have made
                # it already), then add to it; adding in the database keeps
                # what other workers add at the same time
                NNTPIndexer._insert_ignore(session, ServerStats, [
                    {'server_id': server_id, 'day': day}])

                values = {
                    getattr(ServerStats, name):
                        getattr(ServerStats, name) + getattr(stats, name)
                    for name in STATISTIC_COUNTERS}
                values[ServerStats.elapsed] = \
                    ServerStats.elapsed + stats.elapsed
                values[ServerStats.peak_rate] = case(
                    (ServerStats.peak_rate < stats.peak_rate,
                     stats.peak_rate), else_=ServerStats.peak_rate)

                session.query(ServerStats)\
                    .filter(ServerStats.server_id == server_id)\
                    .filter(ServerStats.day == day)\
                    .update(values, synchronize_session=False)
                recorded.append(host)

        return recorded

    def server_stats(self, since=None):
        """
        Returns {server key: {day: NNTPStatistics}} of the days kept (from the
        day `since` on when given); its elapsed time is how long the
        commands ran that day
        """
        found = {}
        with self.transaction() as session:
            query = session.query(Server.key, ServerStats)\
                .join(ServerStats, ServerStats.server_id == Server.id)
            if since is not None:
                query = query.filter(ServerStats.day >= since)

            for host, row in query.order_by(ServerStats.day):
                found.setdefault(host, {})[row.day] = NNTPStatistics(
                    peak_rate=row.peak_rate, started=0.0,
                    finished=row.elapsed, **{
                        name: getattr(row, name)
                        for name in STATISTIC_COUNTERS})

        return found

    def transfers(self, today=None):
        """
        Returns a Transfers for every server with statistics kept, so what
        happened today can be compared with a usual day (see
        server_stats()); today is UTC today when not given
        """
        today = today or utcnow().date()
        found = []
        for host, days in self.server_stats().items():
            def period(start, end=today):
                return self._period(
                    stats for day, stats in days.items()
                    if start <= day <= end)

            before = [stats for day, stats in days.items() if day < today]
            usual = None
            warnings = []
            if len(before) >= USUAL_DAYS:
                usual = self._period(before, divide=len(before))

            now = period(today)
            if usual is not None:
                if now.total_bytes >= SLOW_BYTES and usual.rate and \
                        now.rate < usual.rate * SLOW_SHARE:
                    warnings.append(('slow', now.rate, usual.rate))

                share, normal = failure_share(now), failure_share(usual)
                if share >= normal * FAILURE_TIMES and \
                        share >= normal + FAILURE_POINTS:
                    warnings.append(('failures', share, normal))

            found.append(Transfers(
                host, now, period(today - timedelta(days=1),
                                  today - timedelta(days=1)),
                period(today - timedelta(days=6)),
                self._period(days.values()), len(days), usual, warnings))

        return found

    @staticmethod
    def _period(items, divide=1):
        """
        Returns the days given added together (divided by `divide`, for an
        average day); unlike adding NNTPStatistics, the time each day ran
        adds up too
        """
        total = NNTPStatistics()
        elapsed = 0.0
        for stats in items:
            total += stats
            elapsed += stats.elapsed

        if divide > 1:
            for name in STATISTIC_COUNTERS:
                setattr(total, name, getattr(total, name) / divide)
            elapsed /= divide

        total.started, total.finished = 0.0, elapsed
        return total

    def prune_stats(self, days, dry_run=False):
        """
        Removes the statistics of days older than `days` days (UTC); returns
        how many rows went (or would, with dry_run)
        """
        before = utcnow().date() - timedelta(days=days)
        with self.transaction() as session:
            query = session.query(ServerStats)\
                .filter(ServerStats.day < before)
            if dry_run:
                return query.count()

            return query.delete(synchronize_session=False)

    @staticmethod
    def _now(session):
        """
        Returns the time now (UTC, naive) by the database server's clock,
        so workers on machines whose clocks differ still agree on when a
        claim runs out; SQLite is on this machine, so ours is used
        """
        dialect = session.get_bind().dialect.name
        if dialect == 'postgresql':
            return session.execute(text(
                "SELECT CAST(now() AT TIME ZONE 'UTC' AS TIMESTAMP)")).scalar()

        if dialect in ('mysql', 'mariadb'):
            return session.execute(text('SELECT UTC_TIMESTAMP(6)')).scalar()

        return utcnow()

    def claim(self, names, owner, seconds, limit=None, free=()):
        """
        Claims what is named for `owner` (see Claim) for `seconds`; returns
        the names claimed, in the order given.

        - one already claimed by another owner (and not run out) is skipped
        - one the owner already has is renewed
        - limit: stop once this many are claimed (ie: connection slots)
        - free: owners known to have stopped; their claims are taken over
        - workers claiming the same name at once can't both get it
        """
        from .NNTPIndexer import NNTPIndexer

        names = list(dict.fromkeys(names))
        if not names:
            return []

        claimed = []
        with self.transaction() as session:
            now = self._now(session)
            until = now + timedelta(seconds=seconds)

            # Make sure each has a row (another worker may have made it
            # already); then take the free ones.  The update only changes a
            # row nobody else holds, so only one worker can get it.
            NNTPIndexer._insert_ignore(
                session, Claim, [{'name': name} for name in names])

            for name in names:
                if limit is not None and len(claimed) >= limit:
                    break

                taken = session.query(Claim)\
                    .filter(Claim.name == name)\
                    .filter(or_(Claim.owner.is_(None),
                                Claim.owner == owner,
                                Claim.until < now,
                                Claim.owner.in_(list(free))))\
                    .update({Claim.owner: owner, Claim.until: until},
                            synchronize_session=False)
                if taken:
                    claimed.append(name)

        return claimed

    def renew_claims(self, owner, seconds):
        """
        Gives everything `owner` has claimed another `seconds`; returns how
        many claims it has
        """
        with self.transaction() as session:
            until = self._now(session) + timedelta(seconds=seconds)
            return session.query(Claim)\
                .filter(Claim.owner == owner)\
                .update({Claim.until: until}, synchronize_session=False)

    def release_claims(self, owner, names=None):
        """
        Lets go of what `owner` claimed (only the names given, when given);
        returns how many were let go of
        """
        with self.transaction() as session:
            query = session.query(Claim).filter(Claim.owner == owner)
            if names is not None:
                query = query.filter(Claim.name.in_(list(names)))

            return query.update({Claim.owner: None, Claim.until: None},
                                synchronize_session=False)

    def claims(self, prefix=None):
        """
        Returns {name: (owner, until)} of what is claimed now (only names
        starting with `prefix` when given); claims that ran out are left
        out
        """
        with self.transaction() as session:
            query = session.query(Claim.name, Claim.owner, Claim.until)\
                .filter(Claim.owner.isnot(None))\
                .filter(Claim.until >= self._now(session))
            if prefix:
                query = query.filter(Claim.name.startswith(
                    prefix, autoescape=True))

            return {name: (owner, until) for name, owner, until in query}

    @staticmethod
    def _resolve(session, names):
        """
        Returns ({group name: id}, [names that match nothing]) for the
        groups (or aliases) named; raises ValueError if none are given
        """
        names = [name for name in (
            [names] if isinstance(names, (str, int)) else names or ())
            if str(name).strip()]
        if not names:
            raise ValueError('No groups were given.')

        found, unknown = {}, []
        for name in names:
            groups = _get_groups(session, lookup=name)
            if groups:
                found.update(groups)
            else:
                unknown.append(name)

        return found, unknown

    @classmethod
    def _find(cls, session, names):
        """
        Returns {group name: id} for the groups (or aliases) named; raises
        UnknownGroups if any of them matches nothing, and ValueError if
        none are given
        """
        found, unknown = cls._resolve(session, names)
        if unknown:
            raise UnknownGroups(unknown)

        return found
