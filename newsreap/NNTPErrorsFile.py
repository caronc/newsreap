# The .ERRORS file beside a file that couldn't all be put together
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
The .ERRORS file beside a file that couldn't all be put together (as Pan
does).  It is YAML, so it can be read by you and by NewsReap:

- problems: what is wrong with the file, a line each
- parts: the articles no server had (or only had damaged), with when they
  were asked for and a hash of each server asked (see NNTPRetry), so a
  later run only asks for them again when that can help

It is only ever written once a download has tried everything it can, and
only beside a file that isn't whole: a file without one is whole.  One
that can't be read back (changed by hand, cut short, too big) counts as
the file being damaged, so the file is downloaded again.
"""

from datetime import UTC
from datetime import datetime
from os import fsencode
from os import remove
from os import replace
from os import stat
from os.path import basename

import yaml

from .Utils import utcnow

import logging
from newsreap.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)

# What is added to a file's name for the file saying what is wrong with it
ERRORS_EXTENSION = '.ERRORS'

# What a file of ours says it is (a later one we don't know can't be used)
ERRORS_VERSION = 1

# The biggest one read back (bytes); a bigger one isn't one of ours
MAX_ERRORS_SIZE = 64 * 1048576

# How an article that couldn't be had went (see parts)
PART_MISSING = 'missing'
PART_DAMAGED = 'damaged'
PART_NOT_A_FILE = 'not-a-file'
PART_STATES = (PART_MISSING, PART_DAMAGED, PART_NOT_A_FILE)

# How long a line is written before it is wrapped (each problem stays on a
# line of its own, so it can be searched for)
MAX_LINE = 65536

# The fastest YAML reader there is
Loader = getattr(yaml, 'CSafeLoader', yaml.SafeLoader)


class Dumper(getattr(yaml, 'CSafeDumper', yaml.SafeDumper)):
    """
    Writes YAML as we like to read it: text with an apostrophe in it is put
    in double quotes (in single quotes, the apostrophe would be doubled)
    """


Dumper.add_representer(str, lambda dumper, value: dumper.represent_scalar(
    'tag:yaml.org,2002:str', value, style='"' if "'" in value else None))


def as_text(value):
    """
    Returns text as it can be written: the bytes of a name that isn't UTF-8
    (ie: Latin-1, as old posts often name their files) are shown as \ufffd
    """
    return fsencode(value).decode('utf-8', 'replace')


def as_utc(value):
    """
    Returns a date as naive UTC (as we write them); one written with a
    timezone (ie: by hand) is moved to UTC
    """
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


class NNTPErrorsFile:
    """
    The .ERRORS file of a file; see the module description
    """

    def __init__(self, path):
        """
        Prepares the .ERRORS file of a file (nothing is read yet; see
        load())

        - path: the file it is about (it needn't be there)
        """
        self.path = path
        self.target = path + ERRORS_EXTENSION

        # What is wrong with it (a line each)
        self.problems = []

        # The articles that couldn't be had ({message_id: {'part', 'state',
        # 'tried_at', 'tried_by', 'posted'}}); see PART_STATES
        self.parts = {}

        # There is one (see load()), and it couldn't be read back
        self.found = False
        self.corrupt = False

    def load(self):
        """
        Reads it back; returns ourselves.  When there is none, found is
        False; when it can't be used, corrupt is True (and nothing is
        known of its parts).
        """
        self.problems, self.parts = [], {}
        self.found = self.corrupt = False
        try:
            if stat(self.target).st_size > MAX_ERRORS_SIZE:
                raise ValueError('it is too big to be one of ours')

            with open(self.target, encoding='utf-8') as fp:
                data = yaml.load(fp, Loader=Loader)

        except FileNotFoundError:
            return self

        except (OSError, ValueError, RecursionError, yaml.YAMLError) as e:
            self.found = self.corrupt = True
            logger.warning(
                '%s could not be read (%s); its file is downloaded again.'
                % (self.target, e))
            return self

        self.found = True
        try:
            self._read(data)

        except (TypeError, ValueError) as e:
            self.problems, self.parts = [], {}
            self.corrupt = True
            logger.warning(
                '%s could not be read (%s); its file is downloaded again.'
                % (self.target, e))

        return self

    def _read(self, data):
        """
        Takes what a .ERRORS file holds; raises ValueError (or TypeError)
        when it isn't what we write
        """
        if not isinstance(data, dict):
            raise ValueError("it isn't a list of settings")

        if data.get('version') != ERRORS_VERSION:
            raise ValueError('it is not a version we know')

        if data.get('file') != as_text(basename(self.path)):
            raise ValueError("it is about another file (%s)" % data.get(
                'file'))

        problems = data.get('problems') or []
        if not isinstance(problems, list) or not all(
                isinstance(line, str) for line in problems):
            raise ValueError('its problems are not lines of text')

        parts = data.get('parts') or {}
        if not isinstance(parts, dict):
            raise ValueError('its parts are not listed by Message-ID')

        found = {}
        for message_id, entry in parts.items():
            if not isinstance(message_id, str) or not isinstance(entry, dict):
                raise ValueError('a part is not listed by its Message-ID')

            tried_by = entry.get('tried_by')
            if entry.get('state') not in PART_STATES or not isinstance(
                    entry.get('tried_at'), datetime) or not isinstance(
                    tried_by, list) or not all(
                    isinstance(h, str) for h in tried_by) or not isinstance(
                    entry.get('part', 0), int) or not isinstance(
                    entry.get('posted'), (datetime, type(None))):
                raise ValueError('part <%s> is not as we wrote it' % (
                    message_id))

            found[message_id] = {
                'part': entry.get('part', 0), 'state': entry['state'],
                'tried_at': as_utc(entry['tried_at']), 'tried_by': tried_by,
                'posted': as_utc(entry.get('posted'))}

        self.problems, self.parts = problems, found

    def save(self, problems=None, parts=None, heading=None):
        """
        Writes it (under a temporary name, renamed once it is whole);
        returns True if it could be.  Not being able to is only warned
        about.

        - problems / parts: see the module description (the ones we hold
          when not given)
        - heading: the first line of the note on top (what went wrong)
        """
        if problems is not None:
            self.problems = list(problems)
        if parts is not None:
            self.parts = dict(parts)

        name = as_text(basename(self.path))
        note = (
            '%s (%s UTC).\n'
            'What is missing is left as zeros, and what is damaged is used '
            'as it came.\nRunning the same download again asks for it '
            'again while the post is new\n(a server can take a while to '
            'receive it), or once you add a server.  This\nfile goes once '
            'it is all there.  If it is changed so it can no longer be\n'
            'read, the file is downloaded again.' % (
                as_text(heading or '%s could not all be put together' % name),
                utcnow().strftime('%Y-%m-%d %H:%M')))

        text = ''.join('# %s\n' % line if line else '#\n'
                       for line in note.splitlines()) + '\n' + yaml.dump({
                           'version': ERRORS_VERSION, 'file': name,
                           'problems': [
                               as_text(line) for line in self.problems],
                           'parts': {
                               as_text(message_id): {
                                   key: entry[key] for key in (
                                       'part', 'state', 'tried_at',
                                       'tried_by', 'posted')
                                   if entry.get(key) is not None}
                               for message_id, entry in self.parts.items()},
                       }, Dumper=Dumper, sort_keys=False, allow_unicode=True,
                           default_flow_style=False, width=MAX_LINE)
        try:
            with open(self.target + '.tmp', 'w', encoding='utf-8') as fp:
                fp.write(text)
            replace(self.target + '.tmp', self.target)

        except OSError as e:
            logger.warning('Could not write %s (%s).' % (self.target, e))
            self.discard(self.target + '.tmp')
            return False

        self.found, self.corrupt = True, False
        return True

    def remove(self):
        """
        Removes it (once its file is whole); returns True if it is gone.
        Not being able to is warned about, and means the file is downloaded
        again next time.
        """
        gone = self.discard(self.target)
        if gone:
            self.found = self.corrupt = False
        return gone

    @staticmethod
    def discard(path):
        """
        Removes a file if it is there; returns True if it is gone (not
        being able to is warned about)
        """
        try:
            remove(path)

        except FileNotFoundError:
            pass

        except OSError as e:
            logger.warning('Could not remove %s (%s).' % (path, e))
            return False

        return True

    def __len__(self):
        """
        How many of its articles couldn't be had
        """
        return len(self.parts)

    def __iter__(self):
        """
        The Message-IDs of its articles that couldn't be had
        """
        return iter(self.parts)

    def __contains__(self, message_id):
        return message_id in self.parts

    def keys(self):
        return self.parts.keys()

    def values(self):
        return self.parts.values()

    def items(self):
        return self.parts.items()

    def iterkeys(self):
        return iter(self.parts.keys())

    def itervalues(self):
        return iter(self.parts.values())

    def iteritems(self):
        return iter(self.parts.items())

    def __str__(self):
        return self.target

    def __repr__(self):
        return '<NNTPErrorsFile path="%s" parts=%d />' % (
            self.target, len(self.parts))
