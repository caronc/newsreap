# NewsReap filters: drop what you don't want, score and tag the rest
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

# The filter commands keep the database trim and ranked:
#   nr filter purge --iposter '*@spam.example' -n   # see what would go
#   nr filter purge --iposter '*@spam.example' --save
#                                    # remove it, and keep dropping it later
#   nr filter purge --saved          # apply every saved rule again
#   nr filter score --isubject '*.par2*' --score 10 --tag recovery --save
#   nr filter rescore                # work scores and tags out again
#   nr filter ls
#   nr filter rm <name>
#
# Rules live in the filters directory (next to config.yaml): every .yaml
# (or .yml) file in it is read, in the order of their names (00-spam.yaml
# before 50-scores.yml).  Rules saved with --save go into its saved.yaml.
# They are applied to everything 'nr update' stores from then on.

import logging
import click
import sys

from fnmatch import fnmatchcase
from functools import update_wrapper
from os.path import abspath
from os.path import dirname
from os.path import isfile

try:
    from newsreap.Logging import NEWSREAP_CLI
except ImportError:
    # Path
    sys.path.insert(0, dirname(dirname(dirname(dirname(abspath(__file__))))))
    from newsreap.Logging import NEWSREAP_CLI

from newsreap.CliOptions import confirmed
from newsreap.CliOptions import matching
from newsreap.NNTPFilter import FILTERS_KEY
from newsreap.NNTPFilter import NNTPFilter
from newsreap.NNTPFilterRule import FilterAction
from newsreap.NNTPFilterRule import GROUP_WILDCARDS
from newsreap.NNTPFilterRule import NNTPFilterRule
from newsreap.NNTPPurger import NNTPPurger
from newsreap.Database import DatabaseUnavailable

# initialize our logger
logger = logging.getLogger(NEWSREAP_CLI)

NEWSREAP_CLI_PLUGINS = {
    # format:
    # cli short hand group: function prefix
    'filter': {
        'prefix': 'filter',
        'desc': 'Drop, score and tag articles (the filters directory)',
    },
}


# The options conditions() adds (besides the find style ones)
CONDITION_OPTIONS = ('after', 'before', 'larger', 'smaller', 'crossposts',
                     'tagged', 'match_any', 'groups', 'name')


def conditions(command):
    """
    Adds the options that describe what a rule matches to a command: the
    same find style options 'nr search' takes (--subject, --isubject,
    --regex-subject, --iregex-subject, and the same for --poster, --body,
    --filename and --message-id; ! in front leaves out), plus posting
    time, size, how many groups it was posted to, tags, groups and the
    rule's name.  The command is given
    them as `conditions` (a dict).
    """
    def wrapper(*args, terms, **kwargs):
        found = {name: kwargs.pop(name) for name in CONDITION_OPTIONS}
        found['terms'] = terms
        return command(*args, conditions=found, **kwargs)

    update_wrapper(wrapper, command)

    options = (
        click.option('--after', default=None, metavar='WHEN',
                     help='Only what was posted at or after this: a date '
                          '(ie: 2026-01-31), a year, or how long ago (ie: '
                          '90d, 12h, 2w).'),
        click.option('--before', default=None, metavar='WHEN',
                     help='Only what was posted before this (the same as '
                          '--after).'),
        click.option('--larger', default=None,
                     help='Only articles bigger than this (ie: 10MB).'),
        click.option('--smaller', default=None,
                     help='Only articles smaller than this (ie: 1KB).'),
        click.option('--crossposts', default=None, metavar='COUNT',
                     help="Only articles posted to this many groups: a "
                          "number, or comparisons (ie: '>5', '>=2, <10').  "
                          "Known from the cached headers (not for posts "
                          "kept or collections)."),
        click.option('--tagged', multiple=True,
                     help="Only what has this tag; !tag for what doesn't.  "
                          "Give it more than once for any of them, or as "
                          "'a, b' (or 'a, !b') for all of them."),
        click.option('--any', 'match_any', is_flag=True,
                     help='Match when any condition does (instead of '
                          'all of them).'),
        click.option('--group', '-g', 'groups', multiple=True,
                     help='Only in this group or alias; wildcards work '
                          '(ie: alt.binaries.*).  Can be given more than '
                          'once.'),
        click.option('--name', default=None,
                     help='What to call the rule (one is made up '
                          'otherwise).'),
    )

    for option in reversed(options):
        wrapper = option(wrapper)

    return matching(wrapper)


def make_rule(ctx, action, conditions, score=None, tags=()):
    """
    Returns the rule the command line describes; None if it describes no
    conditions at all (exits if it doesn't make sense)
    """
    entry = {}
    for term in conditions['terms']:
        # Written the way filters.yaml takes it (a whole match, as the
        # search options are)
        if term.kind == 'regex':
            found = {'regex': '^(?:%s)$' % term.original}

        else:
            found = {'glob': term.original}

        if term.negate:
            found['not'] = True

        if term.case_sensitive:
            found['case_sensitive'] = True

        entry.setdefault(term.field, []).append(found)

    entry = {field: found[0] if len(found) == 1 else found
             for field, found in entry.items()}

    entry.update({
        'after': conditions['after'],
        'before': conditions['before'],
        'tagged': list(conditions['tagged']) or None,
        'crossposts': conditions['crossposts'],
    })

    larger, smaller = conditions['larger'], conditions['smaller']
    if larger is not None or smaller is not None:
        entry['size'] = {
            k: v for k, v in (('over', larger), ('under', smaller))
            if v is not None}

    entry = {k: v for k, v in entry.items() if v is not None}
    groups = scope(ctx, conditions['groups'])

    # On a drop rule any score given is something to compare with; on a
    # score rule only one holding a comparison is
    compares = score is not None and (
        action == FilterAction.Drop or any(c in str(score) for c in '<>='))
    if not entry and not compares and not (
            groups and action == FilterAction.Score):
        # Nothing to match on
        return None

    if score is not None:
        entry['score'] = score

    entry.update({
        'action': action,
        'groups': groups or None,
        'match': 'any' if conditions['match_any'] else 'all',
        'name': conditions['name'],
        'tag': list(tags) or None,
    })

    try:
        return NNTPFilterRule.from_dict(
            {k: v for k, v in entry.items() if v is not None})

    except ValueError as e:
        logger.error('The filter does not make sense: %s' % e)
        exit(1)


def load_filters(ctx):
    """
    Returns our saved filters (exits if the filters file has a problem)
    """
    try:
        return ctx['NNTPSettings'].filters()

    except ValueError as e:
        logger.error(str(e))
        exit(1)


def cached_groups(ctx, groups):
    """
    Returns the names of the groups (aliases and wildcards allowed) whose
    headers are cached; every one of them when none are given
    """
    settings = ctx['NNTPSettings']
    try:
        known = settings.group_names()

    except DatabaseUnavailable:
        logger.error("The database is not correctly configured.")
        exit(1)

    names = set()
    if not groups:
        # Every group we know of
        names.update(known)

    for group in groups:
        if any(c in group for c in GROUP_WILDCARDS):
            # Every group it matches
            pattern = group.lower()
            names.update(
                name for name in known
                if fnmatchcase(name.lower(), pattern))
            continue

        found = settings.groups(group, strict=False)
        if found:
            names.update(found)

        else:
            # Not one we know of; it may still have been cached (or be in
            # the index)
            names.add(group)

    return sorted(name for name in names
                  if isfile(settings.group_database(name)))


def scope(ctx, groups):
    """
    Returns the group names (aliases worked out) a rule given these
    groups applies to, keeping wildcards as they are
    """
    found = []
    for group in groups:
        names = None
        if not any(c in group for c in GROUP_WILDCARDS):
            try:
                names = ctx['NNTPSettings'].groups(group, strict=False)

            except DatabaseUnavailable:
                # Nothing to work out the aliases from
                pass

        found.extend(sorted(names) if names else [group])

    return found


def run(ctx, purger, groups, rule=None):
    """
    Runs a purge over every place articles are kept and shows what it
    found
    """
    settings = ctx['NNTPSettings']
    names = cached_groups(ctx, groups)

    # The header caches, one group at a time
    for name in names:
        before = purger.headers.checked, purger.headers.dropped, \
            purger.headers.rescored
        purger.purge_headers(name, settings.group_database(name))
        checked = purger.headers.checked - before[0]
        dropped = purger.headers.dropped - before[1]
        rescored = purger.headers.rescored - before[2]
        if dropped or rescored:
            click.echo('%s: %d of %d header(s) dropped, %d rescored' % (
                name, dropped, checked, rescored))

    # The archive, then the index (shared by every group); the index goes
    # last so its collections take on the scores just worked out
    try:
        if purger.purge_archive():
            count = purger.archive
            click.echo('archive: %d of %d post(s) dropped, %d rescored' % (
                count.dropped, count.checked, count.rescored))

        if purger.purge_index():
            count = purger.index
            click.echo(
                'index: %d file(s) dropped, %d collection(s) removed, %d '
                'rescored' % (count.files, count.collections, count.rescored))

    finally:
        purger.close()

    samples = [*purger.headers.samples, *purger.index.samples,
               *purger.archive.samples]
    if samples:
        click.echo('Some of what %s dropped:' % (
            'would be' if purger.dry_run else 'was'))
        for found, subject, poster, name in samples:
            click.echo('  [%s] %s  (%s; %s)' % (name, subject, poster, found))

    if purger.dry_run:
        click.echo('Dry run: nothing was changed.')


def save_rule(ctx, filters, rule, save, dry_run):
    """
    Saves a rule in the filters file (with --save), or shows how to
    """
    path = ctx['NNTPSettings'].filters_file()
    if save and not dry_run:
        try:
            filters.append(rule, path)

        except (ValueError, OSError) as e:
            logger.error('Could not save the rule: %s' % e)
            exit(1)

        click.echo("Saved the rule '%s' in %s" % (
            rule.name, filters.source(rule.name)))
        return

    click.echo(
        "To keep applying this rule, run again with --save, or add it to "
        "%s under '%s:':" % (path, FILTERS_KEY))
    click.echo(filters._indent(filters.to_yaml([rule])).rstrip())


@click.command(name='purge')
@conditions
@click.option('--score', default=None,
              help="Only what scores like this, ie: '<-50' or '>23, <34' "
                   "(< <= > >= =).")
@click.option('--dry-run', '-n', is_flag=True,
              help='Only show what would be removed.')
@click.option('--save', is_flag=True,
              help='Keep the rule (in saved.yaml of the filters '
                   'directory) so later scans drop the same things.')
@click.option('--saved', is_flag=True,
              help='Apply every saved rule again (instead of conditions).')
@click.option('--nzb', is_flag=True,
              help='Include collections imported from NZB files.')
@click.option('--show', type=click.IntRange(min=0), default=10,
              help='How many examples of what is removed to show.')
@click.pass_obj
def filter_purge(ctx, conditions, score, dry_run, save, saved, nzb, show):
    """
    Remove articles everywhere they are kept.

    The conditions are the same as 'nr search' takes (see its help): the
    whole subject, poster, body, file name or Message-ID must match the
    pattern given (* and ? are wildcards, the i versions ignore case and
    accents, the regex versions take a regular expression, and ! in front
    leaves out what matches); every one given narrows it down further:

    \b
        nr filter purge --iposter '*@spam.example' --dry-run
        nr filter purge --isubject '*casino*' --isubject '!*review*'
        nr filter purge --ibody '*buy now*' --tagged '!trusted' --save
        nr filter purge --score '<-50' --dry-run
        nr filter purge --saved --dry-run

    What matches is removed from the header caches, the index and the
    archive; try it with --dry-run first, then add --save to keep dropping
    the same things when new articles arrive.  A saved rule on the score or
    tags (--score, --tagged) is only used by purges, since new articles are
    scored after the header rules have had their say; run this again to
    clear out what scores low.  Bodies are only known for
    archived posts (and file names for indexed and archived ones), so
    those conditions only reach them.  With --saved instead of conditions,
    every saved rule (every file of the filters directory) is applied
    again, and scores and tags are worked out again (from 0) too.

    """
    groups, name = conditions['groups'], conditions['name']
    filters = load_filters(ctx)
    rule = make_rule(ctx, FilterAction.Drop, conditions, score=score)

    if rule is not None and saved:
        logger.error('Give conditions or --saved, not both.')
        exit(1)

    if rule is None:
        if save or name:
            logger.error('--save and --name need a condition to save.')
            exit(1)

        if not saved:
            # Nothing is removed without saying what
            click.echo(click.get_current_context().get_help())
            exit(1)

        if not filters.drops and not filters.scores:
            click.echo(
                'There are no saved rules in %s.' % (
                    ctx['NNTPSettings'].filters_file()))
            return

        if not dry_run and not confirmed(
                ctx, 'Remove everything your saved rules drop, everywhere it '
                     'is kept (--dry-run shows what first)?'):
            click.echo('Nothing was removed.')
            return

        # Every saved rule; scores and tags are worked out again too
        purger = NNTPPurger(
            filters, index_db=ctx['NNTPSettings'].index_database(),
            rescore=True, dry_run=dry_run, nzb=nzb,
            groups=scope(ctx, groups) if groups else None,
            samples=show)
        run(ctx, purger, groups)
        return

    if rule.name in filters and (save or name):
        logger.error("There is already a rule named '%s'." % rule.name)
        exit(1)

    rule.name = filters.unique_name(rule.name)

    if not dry_run and not confirmed(
            ctx, 'Remove what matches, everywhere it is kept (--dry-run '
                 'shows what first)?'):
        click.echo('Nothing was removed.')
        return

    # Only this rule; the scores and tags stored are what it sees
    purger = NNTPPurger(
        NNTPFilter(rules=[rule]),
        index_db=ctx['NNTPSettings'].index_database(), rescore=False,
        dry_run=dry_run, nzb=nzb,
        groups=scope(ctx, groups) if groups else None,
        samples=show)
    run(ctx, purger, groups, rule)
    save_rule(ctx, filters, rule, save, dry_run)


@click.command(name='score')
@conditions
@click.option('--score', 'value', default=None,
              help="What to add to the score of what matches (ie: 10 or "
                   "-50); comparisons can come first, ie: '>40, +5' adds 5 "
                   "to what already scores over 40.")
@click.option('--tag', '-t', 'tags', multiple=True,
              help='Tag what matches (can be given more than once).')
@click.option('--dry-run', '-n', is_flag=True,
              help='Only show what would change.')
@click.option('--save', is_flag=True,
              help='Keep the rule (in saved.yaml of the filters '
                   'directory) so later scans score the same way.')
@click.option('--nzb', is_flag=True,
              help='Include collections imported from NZB files.')
@click.pass_obj
def filter_score(ctx, conditions, value, tags, dry_run, save, nzb):
    """
    Score and/or tag articles everywhere they are kept.

    The conditions are the same as 'nr filter purge' takes.  Every article
    starts with a score of 0 and no tags; every score rule that matches
    adds to them.  Scores and tags are worked out again (from 0) from every
    saved rule plus this one, so running it twice doesn't add twice.

    \b
        nr filter score --isubject '*.par2*' --score 10 --tag recovery

    """
    groups, name = conditions['groups'], conditions['name']
    if value is None and not tags:
        logger.error('Give a --score and/or a --tag.')
        exit(1)

    filters = load_filters(ctx)
    rule = make_rule(
        ctx, FilterAction.Score, conditions, score=value, tags=tags)

    if rule is None:
        logger.error('Give at least one condition (or a --group).')
        exit(1)

    if rule.name in filters and (save or name):
        logger.error("There is already a rule named '%s'." % rule.name)
        exit(1)

    rule.name = filters.unique_name(rule.name)

    # Every saved rule, plus this one
    combined = NNTPFilter(rules=[*filters, rule])
    purger = NNTPPurger(
        combined, index_db=ctx['NNTPSettings'].index_database(),
        rescore=True, drop=False, dry_run=dry_run, nzb=nzb,
        groups=scope(ctx, groups) if groups else None)
    run(ctx, purger, groups, rule)
    save_rule(ctx, filters, rule, save, dry_run)


@click.command(name='rescore')
@click.option('--group', '-g', 'groups', multiple=True,
              help='Only in this group or alias (wildcards work).')
@click.option('--dry-run', '-n', is_flag=True,
              help='Only show what would change.')
@click.option('--nzb', is_flag=True,
              help='Include collections imported from NZB files.')
@click.pass_obj
def filter_rescore(ctx, groups, dry_run, nzb):
    """
    Work every score and tag out again from the saved rules.

    Run it after changing the score rules of the filters directory.  Nothing is
    removed (see 'nr filter purge' for that).

    """
    filters = load_filters(ctx)
    purger = NNTPPurger(
        filters, index_db=ctx['NNTPSettings'].index_database(),
        rescore=True, drop=False, dry_run=dry_run, nzb=nzb,
        groups=scope(ctx, groups) if groups else None)
    run(ctx, purger, groups)


@click.command(name='ls')
@click.pass_obj
def filter_list(ctx):
    """
    Show the saved rules.
    """
    filters = load_filters(ctx)
    path = ctx['NNTPSettings'].filters_file()
    if not filters:
        click.echo('There are no saved rules in %s.' % path)
        return

    # The rules of each file, in the order they are used
    first = True
    for source in dict.fromkeys(
            filters.source(rule.name) for rule in filters.values()):
        if not first:
            click.echo('')
        first = False
        click.echo('# %s' % source)
        click.echo(filters.to_yaml(filters.rules_of(source)).rstrip())


@click.command(name='rm')
@click.argument('names', nargs=-1, required=True)
@click.pass_obj
def filter_remove(ctx, names):
    """
    Remove saved rules (by name).

    What they removed before doesn't come back; scores and tags they gave
    change once 'nr filter rescore' is run.  The filters file each one is
    in is written out again (comments in it are lost); the other files are
    left alone.

    """
    filters = load_filters(ctx)
    missing = [name for name in names if name not in filters]
    for name in missing:
        logger.error("There is no rule named '%s'." % name)

    if missing:
        exit(1)

    for name in names:
        filters.remove(name)

    try:
        filters.save(ctx['NNTPSettings'].filters_file())

    except ValueError as e:
        # Rules were left out when it was read; they would be lost
        logger.error(str(e))
        exit(1)

    except OSError as e:
        logger.error('Could not save the filters file: %s' % e)
        exit(1)

    for name in names:
        click.echo("Removed the rule '%s'" % name)
