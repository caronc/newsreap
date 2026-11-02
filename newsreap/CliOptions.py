# Command line options shared by more than one nr command
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
Options used by more than one command line plugin (plugins are loaded from
their own files, so they can't import each other)
"""

import click
from functools import update_wrapper

from .NNTPFilterRule import parse_time
from .NNTPFilterRule import score_tests
from .NNTPFilterRule import tag_logic
from .NNTPSearchTerm import NNTPSearchTerm
from .NNTPSearchTerm import SEARCH_OPTIONS

# Logging
import logging
from .Logging import NEWSREAP_CLI
logger = logging.getLogger(NEWSREAP_CLI)


# The options ranked() adds (their names as the commands' arguments)
RANK_OPTIONS = ('tags', 'without_tags', 'min_score', 'max_score', 'scores',
                'after', 'before')


def ranked(command):
    """
    Adds the options that narrow results down by tag, score (see 'nr
    filter') and posting time to a command; the command is given them as
    `rank` (a dict ready for NNTPIndexer.find() and NNTPArchiver.find()),
    and exits (saying why) if one can't be used
    """
    def wrapper(*args, **kwargs):
        rank = {name: kwargs.pop(name) for name in RANK_OPTIONS}
        check_rank(rank)
        return command(*args, rank=rank, **kwargs)

    update_wrapper(wrapper, command)

    options = (
        click.option('--tag', '-t', 'tags', multiple=True,
                     help="Only what has this tag; !tag for what doesn't.  "
                          "Give it more than once for any of them, or as "
                          "'a, b' (or 'a, !b') for all of them."),
        click.option('--exclude-tag', '-T', 'without_tags', multiple=True,
                     help="Leave out what has this tag (the same as --tag "
                          "'!tag')."),
        click.option('--score', 'scores', multiple=True, metavar='TESTS',
                     help="Only what scores like this, ie: '>=10' or "
                          "'<34, >23' (< <= > >= =).  Every one given must "
                          "pass."),
        click.option('--minscore', '-A', 'min_score', default=None,
                     type=int, help='Leave out what scores lower than this.'),
        click.option('--maxscore', '-B', 'max_score', default=None,
                     type=int, help='Leave out what scores higher than this.'),
        click.option('--after', default=None, metavar='WHEN',
                     help='Only what was posted at or after this: a date '
                          '(ie: 2026-01-31) or how long ago (ie: 90d, 12h, '
                          '2w).'),
        click.option('--before', default=None, metavar='WHEN',
                     help='Only what was posted before this (the same as '
                          '--after).'),
    )

    for option in reversed(options):
        wrapper = option(wrapper)

    return wrapper


def check_rank(rank):
    """
    Exits (saying why) if any of the tags, score comparisons or times given
    can't be used
    """
    for name in ('after', 'before'):
        value = rank.get(name)
        if value is not None and not str(value).strip():
            logger.error('--%s needs a date (or how long ago, ie: 90d).' % (
                name))
            exit(1)

    try:
        tag_logic(rank.get('tags'))
        tag_logic(rank.get('without_tags'))
        for value in rank.get('scores') or ():
            score_tests(value, '--score')

        after = parse_time(rank.get('after'), '--after')
        before = parse_time(rank.get('before'), '--before')

    except ValueError as e:
        logger.error(str(e))
        exit(1)

    if after is not None and before is not None and after >= before:
        logger.error('--after has to be earlier than --before.')
        exit(1)


# What each searchable field is (for the help of its options)
MATCHING_FIELDS = {
    'subject': 'subject',
    'body': 'body (the text of an archived post)',
    'poster': 'poster',
    'filename': 'file name',
    'message_id': 'Message-ID (without its < >)',
}


def matching(command):
    """
    Adds find(1) style search options to a command (--subject, --isubject,
    --regex-subject, --iregex-subject and the same for --body, --poster,
    --filename and --message-id); the command is given them as `terms` (a list of
    NNTPSearchTerm), and exits (saying why) if one can't be used
    """
    def wrapper(*args, **kwargs):
        options = {name: kwargs.pop(name) for name in SEARCH_OPTIONS}
        try:
            terms = NNTPSearchTerm.from_options(**options)

        except ValueError as e:
            logger.error(str(e))
            exit(1)

        return command(*args, terms=terms, **kwargs)

    update_wrapper(wrapper, command)

    for name, (field, kind, case_sensitive) in reversed(
            SEARCH_OPTIONS.items()):
        what = MATCHING_FIELDS[field]
        if kind == 'regex':
            text = 'The whole %s matches this regular expression' % what
        else:
            text = 'The whole %s matches this (* and ? are wildcards)' % what

        text += '; case matters' if case_sensitive \
            else "; case and accents don't matter"
        text += '.  Start it with ! to leave out what matches.'

        wrapper = click.option(
            '--%s' % name.replace('_', '-'), name, multiple=True,
            metavar='PATTERN', help=text)(wrapper)

    return wrapper


class DefaultGroup(click.Group):
    """
    A group of commands that runs one of them (the default) when what
    follows isn't the name of another; ie: 'nr update a.b.test' runs
    'nr update run a.b.test' while 'nr update groups' still works

    Its help is the default command's, followed by the other commands.
    """

    def __init__(self, *args, default=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.default = default

    def parse_args(self, ctx, args):
        if args and self.default in self.commands and \
                args[0] not in self.commands and \
                args[0] not in ctx.help_option_names:
            args = [self.default, *args]

        return super().parse_args(ctx, args)

    def format_help(self, ctx, formatter):
        command = self.commands.get(self.default)
        if command is None:
            return super().format_help(ctx, formatter)

        # Shown as if it were the group itself
        sub = click.Context(
            command, info_name=ctx.info_name, parent=ctx.parent)
        command.format_help(sub, formatter)
        self.format_commands(ctx, formatter)
