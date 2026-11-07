# NewsReap NNTP Database Caching Functions
#
# Copyright (C) 2015-2026 Chris Caron <lead2gold@gmail.com>
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

# The update commands keep NewsReap's database current:
#   - update groups: caches the groups every server carries
#   - update search: caches the article headers of groups so they can be
#                    searched offline (see NNTPGroupScanner)
#   - update index:  groups the cached articles into files and collections
#                    (see NNTPGroupIndexer)

import logging
import click
import sys

from os import access
from os import W_OK
from os.path import isdir
from os.path import dirname
from os.path import abspath
from datetime import timedelta

try:
    from newsreap.Logging import NEWSREAP_CLI
except ImportError:
    # Path
    sys.path.insert(0, dirname(dirname(dirname(dirname(abspath(__file__))))))
    from newsreap.Logging import NEWSREAP_CLI

from newsreap.CliOptions import confirmed
from newsreap.Database import DatabaseUnavailable
from newsreap.NNTPDatabase import UnknownGroups
from newsreap.NNTPDatabase import UnknownServer

from newsreap.NNTPConnection import NNTPConnection
from newsreap.NNTPGroupScanner import NNTPGroupScanner
from newsreap.NNTPGroupScanner import DEFAULT_BATCH_SIZE
from newsreap.NNTPGroupDatabase import NNTPGroupDatabase
from newsreap.NNTPArchiver import DEFAULT_ARCHIVE_MAX_SIZE
from newsreap.NNTPArchiver import NNTPArchiver
from newsreap.NNTPPruner import NNTPPruner
from newsreap.NNTPPurger import NNTPPurger
from newsreap.NNTPNzbReader import NNTPNzbReader
from newsreap.NNTPFilterRule import parse_time
from newsreap.NNTPIndexer import NNTPIndexer
from newsreap.Utils import utcnow

# initialize our logger
logger = logging.getLogger(NEWSREAP_CLI)

# What is said when the database can't be used
NO_DATABASE = "The database isn't set up yet; run 'nr db init' first."

NEWSREAP_CLI_PLUGINS = {
    # format:
    # cli short hand group: function prefix
    'update': {
        'prefix': 'update',
        'desc': 'Fetch what is new in groups, ready to search',
        # 'nr update a.b.test' is 'nr update run a.b.test'
        'default': 'run',
    },
}


def load_filters(settings):
    """
    Returns the filters to apply (None if there are no rules); exits if
    the filters file has a problem, before anything is fetched
    """
    try:
        filters = settings.filters()

    except ValueError as e:
        logger.error(str(e))
        exit(1)

    return filters if len(filters) else None


def server_id(settings, host):
    """
    Returns the database id of a server; exits (saying why) if the
    database or the server isn't set up yet
    """
    try:
        return settings.server_id(host)

    except DatabaseUnavailable:
        logger.error(NO_DATABASE)
        exit(1)

    except UnknownServer as e:
        logger.error("%s Run 'nr db init' to add it." % e)
        exit(1)


def parse_date(value, name):
    """
    Turns a date the user gave us into a (UTC) datetime, the way every
    other command reads them (exits, saying why, if it can't)

    - value: the text given (ie: 2024, 2024.02, 2024-02-01 10:00, or how
      long ago: 90d)
    - name: the option it came from (for the error message)
    """
    try:
        found = parse_time(value, '--date-%s' % name)

    except ValueError as e:
        logger.error(str(e))
        exit(1)

    if found is None:
        logger.error('--date-%s needs a date.' % name)
        exit(1)

    return found


@click.command(name='groups')
@click.pass_obj
def update_groups(ctx):
    """
    Cache all Usenet groups.

    The following function queries all servers defined and caches their
    statistics to the database for caching (speed).  This function can be
    called as often as you wish to re-caches the data in the database.

    """
    settings = ctx['NNTPSettings']

    # Track whether every server answered
    failed = False

    # Get Group Listings for all of our servers
    for s in settings.nntp_servers:
        try:
            settings.server_id(s['host'])

        except DatabaseUnavailable:
            logger.error(NO_DATABASE)
            exit(1)

        except UnknownServer as e:
            logger.warning("%s Run 'nr db init' to add it." % e)
            failed = True
            continue

        # Fetch the server's group listing
        con = NNTPConnection(**s)
        try:
            results = con.groups()

        finally:
            # Our connection's statistics are part of the total
            con.close()
            ctx['NNTPManager'].add_stats(con.stats, host=s['host'])

        if not results:
            logger.warning(
                "Could not fetch the group listing from '%s'." % s['host'])
            failed = True
            continue

        try:
            new = settings.save_groups(s['host'], results)

        except UnknownServer as e:
            logger.error("%s Run 'nr db init' to add it." % e)
            exit(1)

        click.echo('%s: %d group(s) listed, %d new.' % (
            s['host'], len(results), new))

    if failed:
        exit(1)


# Options shared by the commands that fetch headers
HEADER_OPTIONS = (
    click.option('--date-from', '-f',
                 help='Only articles posted on or after this date (ie: '
                      '2024, 2024-02-01, or how long ago: 90d).'),
    click.option('--date-to', '-t',
                 help='Only articles posted on or before this date (the '
                      'same as --date-from).'),
    click.option('--days', '-d', type=click.IntRange(min=1),
                 help='Only articles posted in the last X days.'),
    click.option('--newest', '-n', type=click.IntRange(min=1),
                 help='Only the newest X articles.'),
    click.option('--batch-size', '-b', type=click.IntRange(min=1),
                 help='Article headers fetched per request (default: '
                      'header_batch_size from the configuration).'),
    click.option('--batches', '-c', type=click.IntRange(min=1),
                 help='Only process this many batches (per group).'),
)

# Options shared by the commands that keep the content of articles
CONTENT_OPTIONS = (
    click.option('--max-size', '-m', default=None, type=str,
                 help='The biggest article (or NZB file) fetched (ie: '
                      '64KB); defaults to the archive_max_size setting.'),
    click.option('--hooks', '-k', default=None, type=str,
                 help='More archive hooks to use (see '
                      'newsreap/hooks/archive/).'),
    click.option('--no-default-hooks', is_flag=True,
                 help="Don't use the built in archive hooks."),
)


def options(*decorators):
    """
    Applies a list of click options to a command
    """
    def wrapper(command):
        for decorator in reversed(decorators):
            command = decorator(command)
        return command
    return wrapper


def prepare(ctx, groups, watched):
    """
    Returns the groups asked for (name -> id); exits if there are none
    """
    try:
        found = ctx['NNTPSettings'].groups(groups, watched=watched)

    except DatabaseUnavailable:
        logger.error(NO_DATABASE)
        exit(1)

    except UnknownGroups as e:
        # Every one given has to be a group (or alias) we know of
        for name in e.names:
            logger.error(
                "There is no group (or alias) named '%s'; see 'nr group "
                "ls --all' (or fetch the list with 'nr update groups')." %
                name)
        exit(1)

    if not found:
        logger.error(
            "No groups were given; name them (see 'nr group ls'), or use "
            "--watched for the watched ones.")
        exit(1)

    return found


def header_dates(date_from, date_to, days):
    """
    Returns the (from, to) posting dates headers are fetched for; exits if
    they don't make sense
    """
    if date_from is not None:
        date_from = parse_date(date_from, 'from')

    if days:
        # The last X days
        # (cached post dates are kept in UTC)
        since = utcnow() - timedelta(days=days)
        date_from = max(date_from, since) if date_from else since

    if date_to is not None:
        date_to = parse_date(date_to, 'to')

    if date_to and date_from and date_from > date_to:
        logger.error(
            "The from date can not be larger then the to date.",
        )
        exit(1)

    return date_from, date_to


def fetch_headers(ctx, groups, date_from=None, date_to=None,
                  newest=None, batch_size=None, batches=None, force=False):
    """
    Fetches (and caches) the headers of the groups given (name -> id);
    filters drop, score and tag them as they arrive.  Returns False if not
    everything could be fetched
    """
    if not ctx['NNTPSettings'].nntp_servers:
        logger.error("There are no servers defined.")
        exit(1)

    # What we drop (and how we score and tag the rest)
    filters = load_filters(ctx['NNTPSettings'])

    # Articles are always fetched from our primary server
    s = ctx['NNTPSettings'].nntp_servers[0]
    server = server_id(ctx['NNTPSettings'], s['host'])

    # Get our RamDisk if we got one
    ramdisk = ctx['NNTPSettings'].nntp_processing.get('ramdisk')
    if ramdisk:
        if not (isdir(ramdisk) and access(ramdisk, W_OK)):
            logger.warning('Ramdisk "%s" is not accessible.' % (ramdisk))
            # Turn it off so we don't reference it
            ramdisk = None
        else:
            logger.info('Using ramdisk: %s' % (ramdisk))

    if not batch_size:
        # Use our configuration
        batch_size = ctx['NNTPSettings'].nntp_processing.get(
            'header_batch_size', DEFAULT_BATCH_SIZE)

    # Track whether every group was fully fetched, and the ones too big to
    # fetch whole the first time
    complete = True
    too_big = []

    for name, group_id in groups.items():
        scanner = NNTPGroupScanner(
            ctx['NNTPManager'], name,
            db_file=ctx['NNTPSettings'].group_database(name),
            server=s['host'],
            batch_size=batch_size,
            ramdisk=ramdisk,
            filters=filters,
            index_db=ctx['NNTPSettings'].index_database(),
        )

        try:
            if not scanner.scan(
                    date_from=date_from, date_to=date_to, newest=newest,
                    force=force, limit=batches) and not scanner.too_big:
                complete = False

            if scanner.too_big:
                # It says why; running it again won't help
                too_big.append(name)

        finally:
            # Even when stopped early, track what we know about the group
            if scanner.low is not None:
                ctx['NNTPSettings'].track(
                    group_id, server, scanner.low, scanner.high,
                    scanned=bool(scanner.batches))

            click.echo(str(scanner))

    if too_big:
        exit(1)

    return complete


def reset_groups(ctx, groups):
    """
    Removes everything kept of the groups given (name -> id): their cached
    headers, how far they were fetched, and what was indexed and kept from
    them (see NNTPIndexer.forget_group())
    """
    pruner = NNTPPruner(ctx['NNTPSettings'])
    for name in groups:
        found = pruner.forget_group(name)
        click.echo(
            '%s: reset (%d file(s), %d collection(s) and %d post(s) '
            'removed)' % (name, found.files, found.collections, found.posts))


def prune_groups(ctx, groups):
    """
    Removes what is older than each group's retention (see 'nr group set
    --retention' and the retention setting), and the server statistics
    older than the statistics setting; groups that keep everything are left
    alone
    """
    pruner = NNTPPruner(ctx['NNTPSettings'])
    if pruner.statistics:
        removed = ctx['NNTPSettings'].prune_stats(pruner.statistics)
        if removed:
            click.echo('%d day(s) of server statistics removed (older than '
                       '%d day(s)).' % (removed, pruner.statistics))

    for name in groups:
        found = pruner.prune_group(name)
        if found.days:
            click.echo(
                '%s: pruned to %d day(s) (%d header(s), %d file(s), %d '
                'collection(s) and %d post(s) removed)' % (
                    name, found.days, found.headers, found.files,
                    found.collections, found.posts))


@click.command(name='run', hidden=True)
@click.argument('groups', nargs=-1)
@click.option('--watched', '-w', is_flag=True, help='All watched groups.')
@options(*HEADER_OPTIONS)
@click.option('--content/--no-content', default=None,
              help='Also keep what the small articles say (see --max-size) '
                   "so 'nr search --posts' can search their text.  Without "
                   "either, each group does as 'nr group set' says (or as "
                   "the content setting says).")
@click.option('--nzb/--no-nzb', default=None,
              help='Also read the NZB files posted (no bigger than '
                   '--max-size) into the index.  Without either, each group '
                   "does as 'nr group set' says (or as the nzb setting "
                   "says).")
@click.option('--rescore', is_flag=True,
              help="Work every score and tag out again from the saved rules "
                   "(ie: after changing the filters files); no new headers are "
                   "fetched.")
@click.option('--cached', is_flag=True,
              help='Fetch no new headers; work with the ones already '
                   'cached (ie: --nzb or --content on what you have).')
@click.option('--reset', is_flag=True,
              help='Remove everything kept of the groups first (their '
                   'headers, and what was indexed and kept from them), '
                   'then fetch them as if for the first time.')
@options(*CONTENT_OPTIONS)
@click.option('--force', is_flag=True,
              help='Do it again even if it was done before (fetch the '
                   'headers asked for again, ask again for articles no '
                   'server had, read NZB files again).')
@click.option('--no-index', is_flag=True,
              help="Only cache the headers; group them into collections "
                   "later with 'nr update index'.")
@click.option('--settle', is_flag=True,
              help='Count every group as seen up to now, so collections '
                   'from groups you no longer scan can settle too.')
@click.option('--prune', is_flag=True,
              help="Afterwards, remove what is older than each group's "
                   "retention, and old server statistics (as 'nr prune' "
                   "does, without asking).")
@click.pass_obj
def update_run(ctx, groups, watched, date_from, date_to, days, newest,
               batch_size, batches, content, nzb, rescore, cached, reset,
               max_size, hooks, no_default_hooks, force, no_index, settle,
               prune):
    """
    Fetch what is new in groups, ready to search.

    Each step only does what wasn't done before, using what is already
    kept and fetching only what is missing:

    \b
      1. the article headers are fetched (and cached, one database per
         group); filters drop, score and tag them as they arrive
      2. they are grouped into files and collections (unless --no-index)
      3. --content: the small articles are fetched and what they say is
         kept, so their text can be searched (nr search --posts)
      4. --nzb: the NZB files posted are read into the index
      5. --rescore: every score and tag is worked out again
      6. --prune: what is older than each group's retention is removed
         (and server statistics older than the statistics setting)

    A group can be told to always do steps 3 and 4 ('nr group set
    --content yes --nzb yes'), or every group can (the content and nzb
    settings under processing: in the configuration file).  --no-content
    and --no-nzb leave them out for one run.

    Without dates or counts, a group fetched before carries on from where
    it left off (anything missed, and everything new).  The first time, a
    big group needs to be told how far back to go (--newest, --days or
    --date-from).  A range overlapping what was fetched only fetches the
    rest, unless --force is given; --reset starts the group over.  With
    --cached or --rescore no new headers are fetched.

    The switches can be combined:

    \b
        nr update a.b.test --newest 200000
        nr update a.b.test
        nr update a.b.test --cached --nzb --content
        nr update --watched --rescore
        nr update a.b.test --reset --days 7

    """
    groups = prepare(ctx, groups, watched)

    # No new headers are fetched
    offline = rescore or cached
    if offline and (date_from or date_to or days or newest or batches or
                    reset):
        logger.error(
            '--%s fetches no new headers; leave out the date and count '
            'options (and --reset).' % ('rescore' if rescore else 'cached'))
        exit(1)

    if reset:
        if not confirmed(
                ctx, 'Remove everything kept of %s (headers, collections and '
                     'posts) and start over?' % ', '.join(sorted(groups))):
            click.echo('Nothing was reset.')
            exit(1)
        reset_groups(ctx, groups)

    # Track whether every step could be done
    complete = True

    if not offline:
        date_from, date_to = header_dates(date_from, date_to, days)
        complete = fetch_headers(
            ctx, groups, date_from=date_from, date_to=date_to,
            newest=newest, batch_size=batch_size, batches=batches,
            force=force)

    if not no_index or settle:
        # Ready for 'nr search' (whatever was fetched, even if not all of
        # it)
        complete = index_groups(
            ctx['NNTPSettings'], groups, settle=settle) and complete

    # The groups whose content is kept and whose NZB files are read
    settings = ctx['NNTPSettings']
    options = settings.group_options(groups)
    with_content = chosen(
        groups, options, 'content', content,
        None if rescore else settings.content())
    with_nzb = chosen(
        groups, options, 'nzb', nzb, None if rescore else settings.nzb())

    if with_content:
        complete = fetch_content(
            ctx, with_content, max_size=max_size, force=force, hooks=hooks,
            default_hooks=not no_default_hooks) and complete

    if with_nzb:
        complete = read_nzbs(
            ctx, with_nzb, max_size=max_size, force=force) and complete

    if rescore:
        rescore_groups(ctx, groups)

    if prune:
        prune_groups(ctx, groups)

    if not complete:
        logger.warning(
            'Not everything was done; run the same command again to do the '
            'rest.')
        exit(1)


def chosen(groups, options, name, given, default):
    """
    Returns the groups (name -> id) a step is done for:
    - given: what the command line said (True or False); None if nothing
    - options: what each group says (see NNTPDatabase.group_options())
    - default: what the configuration file says; None does the step only
      when asked (ie: --rescore runs)
    """
    if given is not None:
        return groups if given else {}

    if default is None:
        return {}

    found = {}
    for group, group_id in groups.items():
        value = options.get(group, {}).get(name)
        if value if value is not None else default:
            found[group] = group_id

    return found


@click.command(name='search')
@click.argument('groups', nargs=-1)
@options(*HEADER_OPTIONS)
@click.option('--force', is_flag=True,
              help='Fetch articles again even if they were fetched before.')
@click.option('--watched', '-w', is_flag=True, help='All watched groups.')
@click.option('--no-index', is_flag=True,
              help="Only cache the headers; group them into collections "
                   "later with 'nr update index'.")
@click.pass_obj
def update_search(ctx, groups, date_from, date_to, days, newest, batch_size,
                  batches, force, watched, no_index):
    """
    Fetch the headers of groups (the same as 'nr update').

    The headers are cached (one database per group, as there are so many)
    and then grouped into files and collections (as 'nr update index'
    does), ready for 'nr search'.

    Only what hasn't been fetched before is fetched, so running this again
    picks up new articles (and anything a previous run missed or didn't get
    to).  Narrow down what is fetched with the date and count options.

    """
    groups = prepare(ctx, groups, watched)
    date_from, date_to = header_dates(date_from, date_to, days)

    complete = fetch_headers(
        ctx, groups, date_from=date_from, date_to=date_to,
        newest=newest, batch_size=batch_size, batches=batches, force=force)

    if not no_index:
        # Ready for 'nr search' (whatever was fetched, even if not all of it)
        index_groups(ctx['NNTPSettings'], groups)

    if not complete:
        logger.warning(
            'Not everything was fetched; run the same command again to '
            'fetch the rest.')
        exit(1)


@click.command(name='index')
@click.argument('groups', nargs=-1)
@click.option('--watched', '-w', is_flag=True, help='All watched groups.')
@click.option('--settle', is_flag=True,
              help='Count every group as seen up to now, so collections '
                   'from groups you no longer scan can settle too.')
@click.pass_obj
def update_index(ctx, groups, watched, settle):
    """
    Group cached articles into files and collections.

    Works through the articles 'nr update search' cached and works out
    which file (and which collection of files) each one belongs to.  A
    collection can span many groups.  Only articles not indexed before are
    looked at.  'nr update search' already does this (unless --no-index
    is given).  Use 'nr nzb ls' to see the results and 'nr nzb save' to
    turn any of them into an NZB file.

    """
    groups = prepare(ctx, groups, watched)
    if not index_groups(ctx['NNTPSettings'], groups, settle=settle):
        exit(1)


def index_groups(settings, groups, settle=False):
    """
    Indexes the articles cached for the groups given (name -> group id);
    returns False if one of them had nothing cached

    - settle: count every group as seen up to now (see --settle)
    """
    # How we treat collections (see the processing section of the
    # configuration file)
    processing = settings.nntp_processing
    indexer = NNTPIndexer(
        settings.index_database(),
        settle_time=processing['collection_settle_time'],
        repost_days=processing['repost_days'],
        filters=load_filters(settings))

    # Track whether every group could be indexed
    complete = True

    try:
        for name, group_id in groups.items():
            db_file = settings.group_database(name)
            if not indexer.index(name, db_file):
                click.echo(
                    "%s: nothing cached yet; run 'nr update %s' first." % (
                        name, name))
                complete = False
                continue

            click.echo('%s: %s' % (name, indexer))

            if processing['raw_retention']:
                # Usenet is big; let go of the old headers we've indexed
                cache = NNTPGroupDatabase.from_file(db_file)
                try:
                    removed = cache.purge(processing['raw_retention'])

                finally:
                    cache.close()

                if removed:
                    click.echo('%s: %d old header(s) removed' % (
                        name, removed))

            # Track when the group was last indexed
            settings.indexed([group_id])

        # Work out which collections have formed (across every group)
        if indexer.settle(now=utcnow() if settle else None):
            click.echo('%d collection(s) formed' % indexer.formed)

    finally:
        indexer.close()

    return complete


@click.command(name='archive')
@click.argument('groups', nargs=-1)
@click.option('--watched', '-w', is_flag=True, help='All watched groups.')
@options(*CONTENT_OPTIONS)
@click.option('--force', is_flag=True,
              help='Ask again for the articles no server had before.')
@click.pass_obj
def update_archive(ctx, groups, watched, max_size, hooks, no_default_hooks,
                   force):
    """
    Keep what the small articles of groups say (as 'nr update --content').

    Fetches the articles 'nr update' cached the headers of that are no
    bigger than --max-size, and keeps what they say (decoded, so it can be
    searched with 'nr search --posts').  Files they hold (yEnc, uuencode,
    attachments) are only noted, never kept.  Articles kept before aren't
    fetched again.

    """
    groups = prepare(ctx, groups, watched)
    if not fetch_content(
            ctx, groups, max_size=max_size, force=force, hooks=hooks,
            default_hooks=not no_default_hooks):
        exit(1)


def max_article_size(settings, max_size):
    """
    Returns the biggest article fetched for its content (as given, or the
    archive_max_size setting)
    """
    return max_size or settings.nntp_processing.get('archive_max_size') \
        or DEFAULT_ARCHIVE_MAX_SIZE


def fetch_content(ctx, groups, max_size=None, force=False, hooks=None,
                  default_hooks=True):
    """
    Keeps what the small articles of the groups given say; returns False
    if a group had nothing cached
    """
    settings = ctx['NNTPSettings']
    try:
        archiver = NNTPArchiver(
            settings.index_database(), hooks=hooks,
            default_hooks=default_hooks,
            max_size=max_article_size(settings, max_size),
            filters=load_filters(settings), pgp=settings.pgp(),
            gpg=settings.gpg(), retries=settings.pgp_retries())

    except ValueError as e:
        logger.error(str(e))
        exit(1)

    # Track whether every group could be archived
    complete = True

    try:
        for name in groups:
            if not archiver.archive(
                    name, settings.group_database(name),
                    ctx['NNTPManager'], work_dir=settings.work_dir,
                    force=force):
                click.echo(
                    "%s: nothing cached yet; run 'nr update %s' first." % (
                        name, name))
                complete = False
                continue

            click.echo('%s: %s' % (name, archiver))

    finally:
        archiver.close()

    return complete


def read_nzbs(ctx, groups, max_size=None, force=False):
    """
    Reads the NZB files posted to the groups given into the index; returns
    False if one of them has nothing indexed yet
    """
    settings = ctx['NNTPSettings']
    indexer = NNTPIndexer(settings.index_database())
    try:
        reader = NNTPNzbReader(
            indexer, max_size=max_article_size(settings, max_size))

    except ValueError as e:
        indexer.close()
        logger.error(str(e))
        exit(1)

    # Track whether every group could be read
    complete = True

    try:
        for name in groups:
            if not reader.read(
                    name, ctx['NNTPManager'], settings.work_dir,
                    force=force):
                click.echo(
                    "%s: nothing indexed yet; run 'nr update %s' first." % (
                        name, name))
                complete = False
                continue

            click.echo('%s: %s' % (name, reader))
            if reader.failed:
                complete = False

    finally:
        indexer.close()

    return complete


@click.command(name='nzb')
@click.argument('groups', nargs=-1)
@click.option('--watched', '-w', is_flag=True, help='All watched groups.')
@click.option('--max-size', '-m', default=None, type=str,
              help='The biggest NZB file fetched (ie: 64KB); defaults to '
                   'the archive_max_size setting.')
@click.option('--force', is_flag=True,
              help='Read them again even if they were read before.')
@click.pass_obj
def update_nzb(ctx, groups, watched, max_size, force):
    """
    Read the NZB files posted to groups (as 'nr update --nzb').

    The NZB files the index found (no bigger than --max-size) are fetched
    and what they describe is added to the index (as 'nr nzb import'
    does).  Each one is only read once.

    """
    groups = prepare(ctx, groups, watched)
    if not read_nzbs(ctx, groups, max_size=max_size, force=force):
        exit(1)


def rescore_groups(ctx, groups):
    """
    Works every score and tag of the groups given out again from the saved
    rules (nothing is removed)
    """
    from newsreap.plugins.cli.filter import load_filters as saved_rules
    from newsreap.plugins.cli.filter import run

    # With no rules saved, every score and tag goes back to nothing
    purger = NNTPPurger(
        saved_rules(ctx),
        index_db=ctx['NNTPSettings'].index_database(),
        rescore=True, drop=False, groups=sorted(groups))
    run(ctx, purger, sorted(groups))
