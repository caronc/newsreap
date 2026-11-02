# One rule of a NewsReap filters file (see NNTPFilter)
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
A rule looks at an article's subject, poster, Message-ID, posting time,
size, score and tags (only the ones it names; anything left out isn't
checked) and, when they match, does one of three things:

- score: adds to the article's score and/or tags it (every score rule
  that matches adds up)
- keep: protects the article from every drop rule
- drop: removes the article (the first drop rule to match wins)

A rule written in YAML looks like this:

    - name: casino-spam
      groups: [alt.binaries.*]      # left out = every group
      match: all                    # all (default) or any
      poster: "@spam.example"       # plain text = contains
      subject: { regex: '\\bcasino\\b' }
      after: 2026-01-01             # only articles posted in this range
      before: 90d                   # (a date, or how long ago)
      size: { over: 10MB }
      score: '<10'                  # (comparisons; quote them)
      not_tagged: trusted           # (tags work like Apprise's)
      action: drop

A score is either how much a score rule adds (score: 10), comparisons a
rule needs ('<34, >23'; < <= > >= =), or both ('>40, +5').  Tags given to
tagged: or not_tagged: are choices (any of them will do); the tags in one
choice ('video, hd') must all be there.

Text is compared without caring about case or accents unless
`case_sensitive: true` is set (regular expressions ignore case, but not
accents).
"""

import re
import unicodedata
from datetime import UTC
from datetime import date
from datetime import datetime
from datetime import timedelta
from fnmatch import fnmatchcase

from dateutil.parser import parse as parse_date

from .Utils import strsize_to_bytes
from .Utils import utcnow


class FilterAction:
    """
    What a rule does to the articles it matches
    """
    # Adds to their score and/or tags them
    Score = 'score'

    # Protects them from every drop rule
    Keep = 'keep'

    # Removes them
    Drop = 'drop'


# A list of the actions defined above
FILTER_ACTIONS = (
    FilterAction.Score,
    FilterAction.Keep,
    FilterAction.Drop,
)

# The text fields a rule can look at
FILTER_TEXT_FIELDS = ('subject', 'poster', 'message_id', 'body',
                      'filename')

# How a text field can be compared; plain text means 'contains'
FILTER_TEXT_OPS = ('contains', 'is', 'starts', 'ends', 'glob', 'regex')

# Every setting a rule may have (anything else is a mistake)
FILTER_RULE_KEYS = (
    'name', 'action', 'groups', 'match', 'case_sensitive',
    *FILTER_TEXT_FIELDS, 'after', 'before', 'size', 'tagged', 'not_tagged',
    'score', 'tag',
)

# What a tag may look like: letters, numbers and a few symbols (tags are
# kept in lower case, so case never matters)
# No spaces or commas; tags are stored space separated.
TAG_RE = re.compile(r'^[a-z0-9][a-z0-9_.+-]{0,63}$', re.ASCII)

# How long ago, ie: 90d (days), 12h (hours), 2w (weeks), 30m (minutes),
# 1y (years); the unit is needed, so a year alone (2019) is a date
RELATIVE_RE = re.compile(
    r'^\s*(?P<value>\d+(\.\d+)?)\s*(?P<unit>[mhdwy])\s*$',
    re.IGNORECASE | re.ASCII)

# A number on its own (not a year)
NUMBER_RE = re.compile(r'^\s*\d+(\.\d+)?\s*$', re.ASCII)

# A year (2019) or a year and month (2019-06) on its own: its first day
PARTIAL_DATE_RE = re.compile(
    r'^\s*(?P<year>\d{4})([-/.](?P<month>\d{1,2}))?\s*$', re.ASCII)

# What each unit above is worth
RELATIVE_UNITS = {
    'm': timedelta(minutes=1),
    'h': timedelta(hours=1),
    'd': timedelta(days=1),
    'w': timedelta(weeks=1),
    'y': timedelta(days=365),
}

# The characters that make a group name a wildcard pattern
GROUP_WILDCARDS = '*?['


def unaccent(text):
    """
    Returns text without its accents: 'Café' becomes 'Cafe'
    """
    if text.isascii():
        # Nothing to do (the common case)
        return text

    # Split accented letters into the letter plus its accent, then drop
    # the accents
    return ''.join(
        c for c in unicodedata.normalize('NFKD', text)
        if not unicodedata.combining(c))


def fold(text):
    """
    Returns text the way it is compared when case (and accents) don't
    matter: 'Café' and 'CAFE' both become 'cafe'
    """
    return unaccent(text).casefold()


# How long the stored tags can be (see tags_to_text())
TAGS_MAX_LENGTH = 256

# One part of a score setting: a comparison (ie: <34, >=10, =0) or, with no
# comparison, how much to add (ie: 10, +5, -50)
SCORE_TERM_RE = re.compile(
    r'^\s*(?P<op><=|>=|<|>|=)?\s*(?P<value>[+-]?\d+)\s*$', re.ASCII)

# How each comparison is checked
SCORE_TESTS = {
    '<': lambda score, value: score < value,
    '<=': lambda score, value: score <= value,
    '>': lambda score, value: score > value,
    '>=': lambda score, value: score >= value,
    '=': lambda score, value: score == value,
}

# What separates the tags that must all be there in a tag search (ie:
# 'video, hd' or 'video hd')
TAG_SPLIT_RE = re.compile(r'[\s,]+')


def tags_to_text(tags, limit=TAGS_MAX_LENGTH):
    """
    Returns tags the way they are stored: lower case, each one once,
    sorted and space separated; tags that don't fit in `limit` characters
    are left out whole (never cut short)
    """
    text = ''
    for tag in sorted({str(tag).strip().lower() for tag in tags} - {''}):
        joined = '%s %s' % (text, tag) if text else tag
        if len(joined) > limit:
            break
        text = joined

    return text


def tags_from_text(text):
    """
    Returns the tags stored as text (see tags_to_text())
    """
    return frozenset((text or '').lower().split())


def tag_logic(values):
    """
    Returns tags to look for, the way Apprise reads them: each value given
    is one choice (any of them will do), and the tags within one value
    (separated by commas or spaces) must all be there.  A tag starting
    with ! must not be there.

        'video'                   video
        ['video', 'music']        video or music
        ['video, hd']             video and hd
        ['video, hd', 'music']    (video and hd) or music
        '!junk'                   anything without junk
        'video, !sample'          video, but not sample

    Returns a tuple of (wanted, unwanted) frozensets, one per choice (empty
    when no tags were given); raises ValueError for something that can't
    be a tag.  Case doesn't matter.
    """
    if values is None:
        return ()

    if isinstance(values, str):
        values = [values]

    if not isinstance(values, (list, tuple, set, frozenset)):
        raise ValueError('tags must be a tag or a list of tags')

    logic = []
    for value in values:
        if isinstance(value, (list, tuple, set, frozenset)):
            # Already split up (every one of them is needed)
            tags = [str(v) for v in value]

        else:
            tags = TAG_SPLIT_RE.split(str(value))

        wanted = set()
        unwanted = set()
        for tag in tags:
            tag = tag.strip().lower()
            negate = tag.startswith('!')
            tag = tag.lstrip('!').strip()
            if not tag:
                continue

            if not TAG_RE.match(tag):
                raise ValueError(
                    "'%s' can't be a tag (use letters, numbers and _ . + -, "
                    "up to 64 of them; ! in front leaves it out)" % tag)

            (unwanted if negate else wanted).add(tag)

        if wanted & unwanted:
            raise ValueError(
                "'%s' asks for a tag and its opposite (%s)" % (
                    value, ', '.join(sorted(wanted & unwanted))))

        choice = (frozenset(wanted), frozenset(unwanted))
        if (wanted or unwanted) and choice not in logic:
            logic.append(choice)

    return tuple(logic)


def tags_match(logic, tags):
    """
    Returns True if tags (a set) satisfy the logic tag_logic() returned
    """
    return any(wanted <= tags and unwanted.isdisjoint(tags)
               for wanted, unwanted in logic)


def tag_logic_text(logic):
    """
    Returns tag logic (see tag_logic()) the way it is written: a list of
    choices, the tags of each one comma separated (! for the unwanted)
    """
    return [', '.join([*sorted(wanted), *('!%s' % t for t in sorted(
        unwanted))]) for wanted, unwanted in logic]


def score_tests(value, name='score'):
    """
    Returns the comparisons a score search was given (ie: '<34, >23'; see
    NNTPFilterRule), as (op, number) pairs; raises ValueError for anything
    else
    """
    if value is None or not str(value).strip():
        return ()

    try:
        tests, amount = NNTPFilterRule._score(str(value))

    except ValueError as e:
        raise ValueError(str(e).replace('score', name, 1)) from None

    if amount is not None or not tests:
        raise ValueError(
            "%s takes comparisons (ie: '<34, >23', '>=10'), not '%s'" % (
                name, value))

    return tests


def parse_time(value, name='time', now=None):
    """
    Returns the (UTC) time given: a date, a date and time, or how long ago
    (ie: 90d, 12h, 2w); None if none was given; raises ValueError for
    something else
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return None

    return NNTPFilterRule._time(value, name, now or utcnow())


class FilterItem:
    """
    What a rule looks at: one article (or one file of a collection, or one
    archived post)
    """

    __slots__ = ('subject', 'poster', 'message_id', 'posted', 'size',
                 'body', 'filename', '_folded')

    def __init__(self, subject='', poster='', message_id='', posted=None,
                 size=None, body=None, filenames=None):
        """
        Prepares an item

        - posted: when it was posted (UTC); None if we don't know
        - size: its size in bytes; None if we don't know
        - body: its text; None if we don't know it (ie: only its header
          was fetched)
        - filenames: the names of the files it holds; None if we don't
          know
        """
        self.subject = subject or ''
        self.poster = poster or ''
        self.message_id = (message_id or '').strip().strip('<>')
        self.posted = posted
        self.size = size
        self.body = body
        self.filename = None if filenames is None \
            else [name for name in filenames if name]

        # The folded (see fold()) text fields, worked out when first needed
        self._folded = {}

    def values(self, field, folded=False):
        """
        Returns the values of a text field (a list; file names can be
        many), folded (see fold()) if asked; None if we don't know them
        """
        value = getattr(self, field)
        if value is None:
            return None

        values = value if isinstance(value, list) else [value]
        if not folded:
            return values

        try:
            return self._folded[field]

        except KeyError:
            # Not needed until now
            found = self._folded[field] = [fold(v) for v in values]
            return found

    def text(self, field, folded=False):
        """
        Returns a text field as one piece of text (file names one per
        line); empty if we don't know it
        """
        values = self.values(field, folded=folded)
        return '\n'.join(values) if values else ''


class TextMatch:
    """
    One way of comparing a text field (ie: the subject contains 'casino')
    """

    def __init__(self, op, value, negate=False, case_sensitive=False):
        """
        Prepares a comparison

        - op: one of FILTER_TEXT_OPS ('glob' is a whole match with * and ?
          wildcards, as 'nr search --subject' does)
        - value: the text (or regular expression) to compare with
        - negate: match when the comparison fails instead
        - case_sensitive: case (and accents) matter
        """
        if op not in FILTER_TEXT_OPS:
            raise ValueError(
                "'%s' isn't a way to compare text (use one of: %s)" % (
                    op, ', '.join(FILTER_TEXT_OPS)))

        if not isinstance(value, str) or not value:
            raise ValueError("'%s' needs some text to compare with" % op)

        self.op = op
        self.value = value
        self.negate = bool(negate)
        self.case_sensitive = bool(case_sensitive)

        # Accents never matter when case doesn't (the text is folded)
        pattern = value if self.case_sensitive else unaccent(value)

        # What the text is compared with
        self._regex = None
        self._value = None
        if op == 'regex':
            try:
                self._regex = re.compile(
                    pattern, 0 if self.case_sensitive else re.IGNORECASE)

            except re.error as e:
                raise ValueError(
                    "'%s' isn't a valid regular expression (%s)" % (
                        value, e)) from None

        elif op == 'glob':
            # The same wildcards as searching (see NNTPSearchTerm)
            from .NNTPSearchTerm import compiled
            self._regex = compiled('glob', pattern, self.case_sensitive)

        else:
            self._value = value if self.case_sensitive else fold(value)

    @property
    def folded(self):
        """
        True if this compares the folded text (see fold())
        """
        return not self.case_sensitive

    @property
    def literal(self):
        """
        Text that must be found in the field for this to match; None if
        there isn't any (a regular expression, or a negated comparison)
        """
        if self.negate or self.op == 'regex':
            return None

        if self.op == 'glob':
            # The longest piece of text between its wildcards
            from .NNTPSearchTerm import glob_parts
            pieces = [v for p, v in glob_parts(self.value) if p == 'text']
            if not pieces:
                return None

            piece = max(pieces, key=len)
            return piece if self.case_sensitive else fold(piece)

        return self._value

    def test(self, text):
        """
        Returns True if one value matches (not turned around by negate)
        """
        if self.op == 'glob':
            return self._regex.fullmatch(text) is not None

        if self._regex is not None:
            return self._regex.search(text) is not None

        if self.op == 'contains':
            return self._value in text

        if self.op == 'is':
            return self._value == text

        if self.op == 'starts':
            return text.startswith(self._value)

        return text.endswith(self._value)

    def __call__(self, item, field):
        """
        Returns True if the item's field matches; a field we don't know
        (ie: the body of an article only its header was fetched of) never
        matches, even turned around
        """
        values = item.values(field, folded=self.folded)
        if values is None:
            return False

        found = any(self.test(value) for value in values)
        return found != self.negate

    def to_yaml(self, case_sensitive=False):
        """
        Returns this comparison the way it is written in a filters file
        (case_sensitive: the rule's own, so it is only written when this
        one differs)
        """
        if self.op == 'contains' and not self.negate and \
                self.case_sensitive == case_sensitive:
            return self.value

        entry = {self.op: self.value}
        if self.negate:
            entry['not'] = True

        if self.case_sensitive != case_sensitive:
            entry['case_sensitive'] = self.case_sensitive

        return entry

    def __repr__(self):
        return '<TextMatch %s%s %r />' % (
            'not ' if self.negate else '', self.op, self.value)


class NNTPFilterRule:
    """
    One rule; see the module description
    """

    def __init__(self, name=None, action=None, groups=None, match='all',
                 case_sensitive=False, subject=None, poster=None,
                 message_id=None, body=None, filename=None, after=None,
                 before=None, size=None,
                 tagged=None, not_tagged=None, score=None,
                 tag=None, now=None):
        """
        Prepares a rule; the arguments are the settings of a rule in a
        filters file (see the module description)

        - now: what relative times (ie: 90d) are counted back from (UTC);
          defaults to now

        Raises ValueError (saying what is wrong) if the rule doesn't make
        sense.
        """
        self.name = str(name).strip() if name is not None else ''
        self.case_sensitive = bool(case_sensitive)

        # How the conditions combine
        if match not in ('all', 'any'):
            raise ValueError("match must be 'all' or 'any'")
        self.match = match

        # What it does
        # The score: comparisons it must pass, and how much it adds
        self.score_tests, self.score = self._score(score)
        self.tags = self._tags(tag, 'tag')

        if action is None:
            # A rule that scores or tags; otherwise it removes
            action = FilterAction.Score \
                if self.score is not None or self.tags else FilterAction.Drop

        if action not in FILTER_ACTIONS:
            raise ValueError(
                "'%s' isn't an action (use one of: %s)" % (
                    action, ', '.join(FILTER_ACTIONS)))
        self.action = action

        if action == FilterAction.Score and \
                self.score is None and not self.tags:
            raise ValueError('a score rule needs a score and/or a tag')

        if action == FilterAction.Drop and (
                self.score is not None or self.tags):
            raise ValueError(
                "a drop rule can't score or tag (what it matches is "
                "removed)")

        # The groups it applies to (wildcards allowed); empty for all
        self.groups = self._groups(groups)

        # The text fields it looks at: field -> comparisons (any of them)
        self.text = {}
        for field, value in zip(
                FILTER_TEXT_FIELDS, (subject, poster, message_id, body,
                                     filename)):
            if value is not None:
                self.text[field] = self._text(field, value)

        # When it was posted (as written, and worked out)
        now = now or utcnow()
        self._after = after
        self._before = before
        self.after = self._time(after, 'after', now)
        self.before = self._time(before, 'before', now)

        # How big it is
        self._size = size
        self.size_over = self.size_under = None
        if size is not None:
            if not isinstance(size, dict) or not size or \
                    set(size) - {'over', 'under'}:
                raise ValueError(
                    "size takes 'over' and/or 'under' (ie: size: "
                    "{ over: 10MB })")

            self.size_over = self._bytes(size.get('over'), 'size over')
            self.size_under = self._bytes(size.get('under'), 'size under')

        # Its score and tags (as worked out by the score rules)
        # The tags it must (or mustn't) have; see tag_logic()
        self.tagged = self._logic(tagged, 'tagged')
        self.not_tagged = self._logic(not_tagged, 'not_tagged')

        if not self.conditions and (
                action == FilterAction.Drop or not self.groups):
            # It would match everything
            raise ValueError(
                'the rule has nothing to match on (give it a subject, '
                'poster, message_id, after, before, size, a score to '
                "compare with (ie: score: '<-50'), tagged or not_tagged)")

        if not self.name:
            self.name = self.suggest_name()

        # A score or keep rule for whole groups matches everything in them
        self._everything = not self.conditions

    @classmethod
    def from_dict(cls, entry, now=None):
        """
        Returns a rule from its YAML form (see the module description)

        Raises ValueError (saying what is wrong) if it doesn't make sense.
        """
        if not isinstance(entry, dict):
            raise ValueError('a rule must be a set of settings (name: ...)')

        unknown = sorted(str(key) for key in entry
                         if key not in FILTER_RULE_KEYS)
        if unknown:
            raise ValueError(
                'unknown setting(s): %s' % ', '.join(unknown))

        return cls(now=now, **entry)

    def to_dict(self):
        """
        Returns this rule the way it is written in a filters file
        """
        entry = {'name': self.name}
        if self.groups:
            entry['groups'] = list(self.groups)

        if self.match != 'all':
            entry['match'] = self.match

        if self.case_sensitive:
            entry['case_sensitive'] = True

        for field, matches in self.text.items():
            values = [m.to_yaml(self.case_sensitive) for m in matches]
            entry[field] = values[0] if len(values) == 1 else values

        for key, value in (('after', self._after),
                           ('before', self._before),
                           ('size', self._size)):
            if value is not None:
                entry[key] = value

        for key, logic in (('tagged', self.tagged),
                           ('not_tagged', self.not_tagged)):
            if logic:
                values = tag_logic_text(logic)
                entry[key] = values[0] if len(values) == 1 else values

        if self.score_tests:
            # Comparisons (and what it adds, if anything)
            entry['score'] = ', '.join(
                ['%s%d' % test for test in self.score_tests] +
                (['%+d' % self.score] if self.score is not None else []))

        elif self.score is not None:
            entry['score'] = self.score

        if self.tags:
            entry['tag'] = sorted(self.tags) \
                if len(self.tags) > 1 else next(iter(self.tags))

        entry['action'] = self.action
        return entry

    @property
    def conditions(self):
        """
        How many conditions (besides the groups) the rule has
        """
        return len(self.text) + sum(
            value is not None for value in (
                self.after, self.before, self.size_over, self.size_under,
                self.score_tests or None)) + \
            bool(self.tagged) + bool(self.not_tagged)

    def uses(self, field):
        """
        Returns True if the rule looks at a field (ie: 'message_id' or
        'size')
        """
        if field in FILTER_TEXT_FIELDS:
            return field in self.text

        if field == 'size':
            return self.size_over is not None or self.size_under is not None

        if field == 'posted':
            return self.after is not None or self.before is not None

        if field == 'score':
            return bool(self.score_tests)

        if field == 'tags':
            return bool(self.tagged or self.not_tagged)

        return False

    def applies_to(self, groups):
        """
        Returns True if the rule applies to an item found in these groups
        (one of them is enough)
        """
        if not self.groups:
            # Every group
            return True

        for group in groups:
            group = group.lower()
            for pattern in self.groups:
                if fnmatchcase(group, pattern):
                    return True

        return False

    def matches(self, item, score=0, tags=frozenset()):
        """
        Returns True if the item (see FilterItem) matches the rule

        - score / tags: what the score rules have given it so far
        """
        if self._everything:
            # Only its groups decide
            return True

        # Every condition, or just one of them
        wanted = self.match == 'all'

        for field, matches in self.text.items():
            # Each comparison counts on its own (a field given several must
            # pass all of them, as search conditions do)
            for match in matches:
                found = match(item, field)
                if found != wanted:
                    # All: one failed; any: one matched
                    return found

        if self.after is not None or self.before is not None:
            # A time we don't know never matches
            found = item.posted is not None and \
                (self.after is None or item.posted >= self.after) and \
                (self.before is None or item.posted < self.before)
            if found != wanted:
                return found

        if self.size_over is not None or self.size_under is not None:
            # A size we don't know never matches
            found = item.size is not None and \
                (self.size_over is None or item.size > self.size_over) and \
                (self.size_under is None or item.size < self.size_under)
            if found != wanted:
                return found

        if self.score_tests:
            # Every comparison (ie: >23 and <34)
            found = all(SCORE_TESTS[op](score, value)
                        for op, value in self.score_tests)
            if found != wanted:
                return found

        if self.tagged:
            found = tags_match(self.tagged, tags)
            if found != wanted:
                return found

        if self.not_tagged:
            found = not tags_match(self.not_tagged, tags)
            if found != wanted:
                return found

        # All: nothing failed; any: nothing matched
        return wanted

    def suggest_name(self):
        """
        Returns a name made from what the rule matches (ie:
        'poster-spam-example')
        """
        parts = [self.action]
        for field, matches in self.text.items():
            parts.extend((field.replace('_', '-'), matches[0].value))

        for label, logic in (('tagged', self.tagged),
                             ('not-tagged', self.not_tagged)):
            if logic:
                wanted, unwanted = logic[0]
                parts.extend((label, (sorted(wanted) or ['not'])[0]))
                if not wanted:
                    parts.append(sorted(unwanted)[0])

        if self.tags:
            parts.extend(('tag', sorted(self.tags)[0]))

        if len(parts) == 1 and self.groups:
            parts.append(self.groups[0])

        name = re.sub(r'[^a-z0-9]+', '-', fold(' '.join(parts)))
        return name.strip('-')[:48].strip('-') or self.action

    def _text(self, field, value):
        """
        Returns the comparisons a text field was given
        """
        values = value if isinstance(value, (list, tuple)) else [value]
        if not values:
            raise ValueError('%s needs some text to compare with' % field)

        matches = []
        for value in values:
            if isinstance(value, str):
                # Plain text
                matches.append(TextMatch(
                    'contains', value, case_sensitive=self.case_sensitive))
                continue

            if not isinstance(value, dict):
                raise ValueError(
                    '%s must be text, or a comparison (ie: { regex: ... })'
                    % field)

            options = {'not', 'case_sensitive'}
            ops = [key for key in value if key not in options]
            unknown = set(value) - set(FILTER_TEXT_OPS) - options
            if len(ops) != 1 or unknown:
                raise ValueError(
                    '%s takes one of: %s (and optionally not: true and '
                    'case_sensitive: true/false)' % (
                        field, ', '.join(FILTER_TEXT_OPS)))

            case_sensitive = value.get('case_sensitive', self.case_sensitive)
            if not isinstance(case_sensitive, bool):
                raise ValueError(
                    '%s: case_sensitive must be true or false' % field)

            try:
                matches.append(TextMatch(
                    ops[0], value[ops[0]], negate=value.get('not', False),
                    case_sensitive=case_sensitive))

            except ValueError as e:
                raise ValueError('%s: %s' % (field, e)) from None

        return matches

    @staticmethod
    def _groups(groups):
        """
        Returns the group patterns a rule was given (lower case)
        """
        if groups is None:
            return ()

        if isinstance(groups, str):
            groups = [groups]

        if not isinstance(groups, (list, tuple)) or not all(
                isinstance(group, str) and group.strip()
                for group in groups):
            raise ValueError('groups must be a list of group names')

        return tuple(group.strip().lower() for group in groups)

    @staticmethod
    def _tags(tags, name):
        """
        Returns the tags given (as a set); raises ValueError for one that
        can't be a tag
        """
        if tags is None:
            return frozenset()

        if isinstance(tags, str):
            tags = [tags]

        if not isinstance(tags, (list, tuple, set, frozenset)):
            raise ValueError('%s must be a tag or a list of tags' % name)

        found = set()
        for tag in tags:
            tag = str(tag).strip().lower()
            if not TAG_RE.match(tag):
                raise ValueError(
                    "'%s' can't be a tag (use letters, numbers "
                    "and _ . + -, up to 64 of them)" % tag)
            found.add(tag)

        return frozenset(found)

    @staticmethod
    def _logic(value, name):
        """
        Returns the tag logic (see tag_logic()) a rule was given
        """
        try:
            return tag_logic(value)

        except ValueError as e:
            raise ValueError('%s: %s' % (name, e)) from None

    @staticmethod
    def _score(value):
        """
        Returns (comparisons, amount) for a score setting:
        - a number (ie: 10, -50) is how much a match adds
        - comparisons (ie: '<34, >23', '>=10', '=0') must all pass for the
          rule to match
        - both together (ie: '>40, +5') add 5 to what scores over 40
        """
        if value is None:
            return (), None

        if isinstance(value, bool) or not isinstance(value, (int, str)):
            # YAML reads yes/no as True/False; neither is a score
            raise ValueError(
                "score must be a whole number (ie: 10) or comparisons (ie: "
                "'<34, >23')")

        tests = []
        amount = None
        for term in str(value).split(','):
            found = SCORE_TERM_RE.match(term)
            if not found:
                raise ValueError(
                    "score must be a whole number (ie: 10) or comparisons "
                    "(ie: '<34, >23'), not '%s'" % str(value).strip())

            number = int(found.group('value'))
            if found.group('op'):
                tests.append((found.group('op'), number))

            elif amount is not None:
                raise ValueError('score can only add one amount')

            else:
                amount = number

        return tuple(tests), amount

    @staticmethod
    def _bytes(value, name):
        """
        Returns a size in bytes (ie: 10MB); None if none was given
        """
        if value is None:
            return None

        try:
            size = strsize_to_bytes(value) if not isinstance(value, bool) \
                else None

        except (TypeError, ValueError):
            # Not something we can read as a size
            size = None

        if size is None or size < 0:
            raise ValueError(
                "%s must be a size (ie: 500KB, 10MB)" % name)

        return size

    @staticmethod
    def _time(value, name, now):
        """
        Returns the (UTC) time a rule was given: a date, a date and time,
        or how long ago (ie: 90d); None if none was given
        """
        if value is None:
            return None

        if isinstance(value, datetime):
            # Times are kept in UTC without a timezone attached
            if value.tzinfo is not None:
                value = value.astimezone(UTC).replace(tzinfo=None)
            return value

        if isinstance(value, date):
            # Midnight of that day
            return datetime(value.year, value.month, value.day)

        found = RELATIVE_RE.match(str(value))
        if found:
            # How long ago
            unit = RELATIVE_UNITS[found.group('unit').lower()]
            return now - unit * float(found.group('value'))

        found = PARTIAL_DATE_RE.match(str(value))
        if found:
            # The first day of the year (or month); otherwise the rest
            # would be filled in from today
            try:
                return datetime(int(found.group('year')),
                                int(found.group('month') or 1), 1)

            except ValueError:
                raise ValueError(
                    "%s must be a date (ie: 2026-01-31) or how long ago (ie: "
                    "90d, 12h, 2w)" % name) from None

        if NUMBER_RE.match(str(value)):
            # A number without a unit; days, hours, ... ?
            raise ValueError(
                "%s needs a unit for how long ago (ie: %sd for days, %sh for "
                "hours)" % (name, str(value).strip(), str(value).strip()))

        try:
            return NNTPFilterRule._time(
                parse_date(str(value)), name, now)

        except (ValueError, OverflowError):
            raise ValueError(
                "%s must be a date (ie: 2026-01-31) or how long ago (ie: "
                "90d, 12h, 2w)" % name) from None

    def __str__(self):
        return self.name

    def __repr__(self):
        return '<NNTPFilterRule name="%s" action="%s" />' % (
            self.name, self.action)
