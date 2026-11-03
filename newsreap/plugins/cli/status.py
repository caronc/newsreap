# NewsReap Status CLI Plugin
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

# An overview of our setup and of everything kept so far:
#   nr status

import logging
import click
import sys

from os import listdir
from os.path import abspath
from os.path import dirname
from os.path import getsize
from os.path import isdir
from os.path import isfile
from os.path import join

try:
    from newsreap.Logging import NEWSREAP_CLI
except ImportError:
    # Path
    sys.path.insert(0, dirname(dirname(dirname(dirname(abspath(__file__))))))
    from newsreap.Logging import NEWSREAP_CLI

from sqlalchemy import distinct
from sqlalchemy import func
from sqlalchemy.exc import OperationalError

from newsreap import __title__
from newsreap import __version__
from newsreap.NNTPGroupDatabase import NNTPGroupDatabase
from newsreap.NNTPIndexDatabase import NNTPIndexDatabase
from newsreap.NNTPIndexer import SESSION_KEY
from newsreap.NNTPKeyring import NNTPKeyring
from newsreap.NNTPSettings import SQLITE_DATABASE_EXTENSION
from newsreap.Utils import bytes_to_strsize
from newsreap.objects.group.Article import Article
from newsreap.objects.index.ArchiveGroup import ArchiveGroup
from newsreap.objects.index.ArchivedPost import ArchiveKind
from newsreap.objects.index.ArchivedPost import ArchivedPost
from newsreap.objects.index.ArticleRank import ArticleRank
from newsreap.objects.index.Collection import Collection
from newsreap.objects.index.Collection import CollectionSource
from newsreap.objects.index.Collection import CollectionState
from newsreap.objects.index.CollectionFile import CollectionFile
from newsreap.objects.index.FileGroup import FileGroup
from newsreap.objects.index.NzbImport import NzbImport
from newsreap.objects.nntp.Group import Group
from newsreap.objects.nntp.GroupAlias import GroupAlias
from newsreap.objects.nntp.GroupTrack import GroupTrack

# initialize our logger
logger = logging.getLogger(NEWSREAP_CLI)

NEWSREAP_CLI_PLUGINS = 'status'

# How each line of the group table is laid out
GROUP_LINE = '  %-38s %10s %9s  %-15s %8s  %-16s  %s'


def heading(text):
    """
    Shows the title of a section
    """
    click.echo('')
    click.secho(text, bold=True)


def detail(name, value):
    """
    Shows one detail of a section
    """
    click.echo('  %-18s %s' % (name + ':', value))


def count(value):
    """
    Returns a number with its thousands separated
    """
    return '{:,}'.format(value or 0)


def when(value):
    """
    Returns when something happened (the epoch is what 'never' is kept as)
    """
    if not value or value.year < 1971:
        return 'never'
    return value.strftime('%Y-%m-%d %H:%M')


def cached_files(settings):
    """
    Returns group name -> path of every group with headers cached
    """
    cache_dir = dirname(settings.group_database('_'))
    found = {}
    if isdir(cache_dir):
        for filename in listdir(cache_dir):
            if filename.endswith(SQLITE_DATABASE_EXTENSION):
                found[filename[:-len(SQLITE_DATABASE_EXTENSION)]] = \
                    join(cache_dir, filename)
    return found


def index_session(settings):
    """
    Returns (index, session) of our index database; (None, None) if
    nothing was ever indexed
    """
    if not NNTPIndexDatabase.exists(settings.index_database()):
        return None, None

    index = NNTPIndexDatabase.from_engine(settings.index_database())
    return index, index.session()


def group_rows(settings, session, index):
    """
    Returns a dictionary for every group with headers cached (or that is
    watched): name, watched, headers, size (of its cache), collections,
    forming, archived, scanned and indexed
    """
    known = {g.name: g for g in session.query(Group)}
    cached = cached_files(settings)

    names = sorted(set(cached) | {
        name for name, g in known.items() if g.watch})

    # What is indexed (and archived) for each group
    collections = {}
    archived = {}
    if index is not None:
        for name, state, found in index.query(
                FileGroup.name, Collection.state,
                func.count(distinct(Collection.id)))\
                .join(CollectionFile,
                      CollectionFile.id == FileGroup.file_id)\
                .join(Collection,
                      Collection.id == CollectionFile.collection_id)\
                .group_by(FileGroup.name, Collection.state):
            entry = collections.setdefault(name, [0, 0])
            entry[0] += found
            if state == CollectionState.Forming:
                entry[1] += found

        archived = dict(index.query(
            ArchiveGroup.name, func.count(ArchiveGroup.message_id))
            .group_by(ArchiveGroup.name))

    # When each group was last scanned and indexed (on any server)
    tracked = {}
    for name, last_scan, last_index in session.query(
            Group.name, GroupTrack.last_scan, GroupTrack.last_index)\
            .join(GroupTrack, GroupTrack.group_id == Group.id):
        scan, indexed = tracked.get(name, (None, None))
        tracked[name] = (
            max(filter(None, (scan, last_scan)), default=None),
            max(filter(None, (indexed, last_index)), default=None))

    rows = []
    for name in names:
        headers = 0
        size = 0
        if name in cached:
            size = getsize(cached[name])
            db = NNTPGroupDatabase.from_file(cached[name])
            try:
                headers = db.session().query(
                    func.count(Article.message_id)).scalar() or 0
            finally:
                db.close()

        total, forming = collections.get(name, (0, 0))
        scan, indexed = tracked.get(name, (None, None))
        rows.append({
            'name': name, 'watched': name in known and known[name].watch,
            'headers': headers, 'size': size, 'collections': total,
            'forming': forming, 'archived': archived.get(name, 0),
            'scanned': scan, 'indexed': indexed})

    return rows


def show_groups(settings, session, index):
    """
    Shows the group table (see group_rows()); returns the rows
    """
    rows = group_rows(settings, session, index)
    if not rows:
        click.echo(
            "  Nothing fetched yet; run 'nr update <group> --newest 100000' "
            "(see 'nr group list' for the groups).")
        return rows

    click.echo(GROUP_LINE % (
        'Group (W: watched)', 'Headers', 'Cache', 'Collections', 'Archived',
        'Scanned', 'Indexed'))

    for row in rows:
        click.echo(GROUP_LINE % (
            row['name'] + (' W' if row['watched'] else ''),
            count(row['headers']),
            bytes_to_strsize(row['size']) if row['size'] else '-',
            '%d (%d forming)' % (row['collections'], row['forming'])
            if row['forming'] else '%d' % row['collections'],
            count(row['archived']), when(row['scanned']),
            when(row['indexed'])))

    return rows


def database_size(url_or_path):
    """
    Returns the size of a database: its file's (SQLite), or None for one
    kept on a database server
    """
    path = url_or_path
    if '://' in path:
        if not path.startswith('sqlite:'):
            return None
        path = path.split(':///', 1)[-1]

    return getsize(path) if isfile(path) else 0


def show_setup(ctx):
    """
    Shows where everything is, our servers and our filters
    """
    settings = ctx['NNTPSettings']
    heading('Setup')
    detail('Version', '%s v%s' % (__title__, __version__))
    detail('Configuration', settings.cfg_file or '(defaults)')
    detail('Base directory', settings.base_dir)
    detail('Work directory', settings.work_dir)

    try:
        filters = settings.filters()
        text = '%d rule(s)' % len(filters)
        if filters.problems:
            text += ', %d left out (they have problems)' % len(
                filters.problems)
        detail('Filters', '%s (%s)' % (settings.filters_file(), text))

    except ValueError as e:
        detail('Filters', 'unusable: %s' % e)

    detail('Always (nr update)', '%s (groups can say otherwise; see nr '
           'group watch)' % ', '.join(
               name if value else 'no %s' % name for name, value in (
                   ('content', settings.content()), ('nzb', settings.nzb()))))

    detail('PGP keys', 'kept from archived posts (see nr pgp)'
           if settings.pgp() else "not kept (set 'pgp: yes' under "
           "processing to keep them)")

    gpg = settings.gpg()
    if gpg is not None:
        detail('Decrypting', 'with %d secret key(s) in %s' % (
            len(set(gpg.secret_keys().values())), gpg.home)
            if gpg.available() else
            "not done (gpg isn't installed)")

    heading('Servers')
    for no, server in enumerate(settings.nntp_servers):
        secure = 'TLS, certificate checked' if server.get('secure') and \
            server.get('verify_cert', True) else 'TLS, certificate not ' \
            'checked' if server.get('secure') else 'not encrypted'
        if server.get('ca_certs'):
            secure += ' (against %s)' % server['ca_certs']

        click.echo('  %s:%s  %s, %s' % (
            server['host'], server.get('port'), secure,
            'primary' if no == 0 else 'backup'))


def show_storage(settings, cached):
    """
    Shows how big each of our databases is
    """
    heading('Storage')
    sizes = []

    engine = settings.nntp_database.get('engine')
    if isinstance(engine, str):
        size = database_size(engine)
        sizes.append(size)
        detail('Settings', '%s (%s)' % (
            bytes_to_strsize(size) if size is not None else 'on a server',
            engine))

    size = database_size(settings.index_database())
    sizes.append(size)
    detail('Index', '%s (%s)' % (
        bytes_to_strsize(size) if size is not None else 'on a server',
        settings.index_database()))

    size = sum(getsize(path) for path in cached.values())
    sizes.append(size)
    detail('Header caches', '%s (%d group(s), %s)' % (
        bytes_to_strsize(size), len(cached),
        dirname(settings.group_database('_'))))

    detail('Total', bytes_to_strsize(sum(s for s in sizes if s)))


def show_index(index, gpg=None, retries=0):
    """
    Shows what the index holds
    - gpg: the NNTPGnuPG posts are decrypted with (if any)
    - retries: how many times 'nr update' tries them again on its own
    """
    heading('Collections')
    if index is None:
        click.echo("  Nothing indexed yet.")
        return

    states = dict(index.query(Collection.state, func.count(Collection.id))
                  .group_by(Collection.state))
    detail('Total', count(sum(
        found for state, found in states.items()
        if state != CollectionState.Deleted)))
    detail('Complete', count(states.get(CollectionState.Complete)))
    detail('Settled', count(states.get(CollectionState.Settled)) +
           ' (missing parts, as complete as they will get)')
    detail('Forming', count(states.get(CollectionState.Forming)) +
           ' (articles still arriving)')
    detail('Deleted', count(states.get(CollectionState.Deleted)) +
           ' (with nr delete; they stay away)')

    detail('Obfuscated', count(
        index.query(func.count(Collection.id))
        .filter(Collection.key.like(SESSION_KEY + '\n%'))
        .filter(Collection.state != CollectionState.Deleted).scalar()) +
        ' (put together from how they were posted)')
    detail('From NZB files', count(
        index.query(func.count(Collection.id))
        .filter(Collection.source == CollectionSource.Nzb).scalar()))

    detail('Files', count(index.query(func.count(CollectionFile.id))
                          .scalar()))
    size = index.query(func.sum(Collection.size))\
        .filter(Collection.state != CollectionState.Deleted).scalar()
    detail('Size of it all', bytes_to_strsize(size or 0))

    heading('Articles')
    kinds = dict(index.query(ArchivedPost.kind, func.count(ArchivedPost.id))
                 .group_by(ArchivedPost.kind))
    detail('Kept (--content)', '%s (%s text, %s binary, %s missing, %s '
           'refused)' % (
               count(sum(kinds.values())), count(kinds.get(ArchiveKind.Text)),
               count(kinds.get(ArchiveKind.Binary)),
               count(kinds.get(ArchiveKind.Missing)),
               count(kinds.get(ArchiveKind.Refused))))
    detail('Scored or tagged', count(
        index.query(func.count(ArticleRank.message_id)).scalar()))

    keys, posts = NNTPKeyring.counts(index)
    if keys or posts:
        detail('PGP keys', '%s (%s post(s) tied to a key)' % (
            count(keys), count(posts)))

    if gpg is not None and gpg.available():
        secret = gpg.secret_keys()
        waiting = len(NNTPKeyring.locked(index, secret, final=False))
        if waiting:
            # The ones 'nr update' has run out of tries for
            done = waiting - len(NNTPKeyring.locked(
                index, secret, final=False, retries=retries))
            detail('Not decrypted yet', "%s (%s; see 'nr pgp decrypt')" % (
                count(waiting),
                '%s no longer tried by nr update' % count(done) if done
                else 'nr update tries them again'))

    read = index.query(func.count(NzbImport.message_id)).scalar() or 0
    unusable = index.query(func.count(NzbImport.message_id))\
        .filter(NzbImport.collection_id.is_(None)).scalar() or 0
    detail('NZB files read', '%s (%s unusable)' % (
        count(read), count(unusable)))


def show_aliases(session):
    """
    Shows the aliases defined (and the groups they stand for)
    """
    heading('Aliases')
    found = {}
    for alias, group in session.query(GroupAlias.name, Group.name)\
            .join(Group, Group.id == GroupAlias.group_id)\
            .order_by(GroupAlias.name, Group.name):
        found.setdefault(alias, []).append(group)

    if not found:
        click.echo("  None (see 'nr alias add').")
        return

    for alias, groups in found.items():
        click.echo('  %-18s %s' % (alias, ', '.join(groups)))


@click.command(name='status')
@click.pass_obj
def status(ctx):
    """
    Show an overview of our setup and everything kept so far.

    Where everything is (and how big it is), the servers and filters used,
    every group with something kept (how many headers are cached, the
    collections and posts found in it, when it was last fetched and
    indexed), the aliases defined, and what the index holds.

    """
    settings = ctx['NNTPSettings']
    session = settings.session()
    if not session:
        logger.error('Could not acquire a database connection.')
        exit(1)

    try:
        groups_known = session.query(func.count(Group.id)).scalar()
        watched = session.query(func.count(Group.id))\
            .filter(Group.watch.is_(True)).scalar()

    except OperationalError:
        click.echo(
            "The database isn't set up yet; run 'nr db init' first.")
        exit(1)

    show_setup(ctx)
    cached = cached_files(settings)
    show_storage(settings, cached)

    index, index_db = index_session(settings)
    try:
        heading('Groups')
        detail('Known', '%s (%s watched)%s' % (
            count(groups_known), count(watched),
            '' if groups_known else "; run 'nr update groups' for them"))
        click.echo('')
        show_groups(settings, session, index_db)

        show_aliases(session)
        show_index(index_db, settings.gpg(), settings.pgp_retries())

    finally:
        if index is not None:
            index.close()
