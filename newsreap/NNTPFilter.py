# The rules of a NewsReap filters file: scoring, tagging, keeping and
# dropping articles
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
Filters keep the database trim (spam is dropped before it is ever stored)
and rank what is kept (scores and tags).  They live in a YAML file
(filters.yaml, next to config.yaml):

    filters:
      - name: recovery
        subject: { regex: '\\.par2\\b' }
        score: 10
        tag: recovery
      - name: trusted
        poster: friend@example.com
        action: keep
      - name: low-score
        score: '<-50'

Every article starts with a score of 0 and no tags.  For each article:

1. every score rule that matches adds its score and tags (in the order
   they are listed, so a later rule can look at the score so far)
2. if a keep rule matches, it is kept
3. otherwise the first drop rule that matches removes it

See NNTPFilterRule for what a rule can look at.  The rules are read once
(when NewsReap starts working) and checked in memory.
"""

import re
from collections import namedtuple
from os import chmod
from os import replace
from os.path import abspath
from os.path import expanduser
from os.path import isfile

import yaml

from .NNTPFilterBase import NNTPFilterBase
from .NNTPFilterRule import FILTER_TEXT_FIELDS
from .NNTPFilterRule import FilterAction
from .NNTPFilterRule import FilterItem
from .NNTPFilterRule import NNTPFilterRule
from .Utils import utcnow

# Logging
import logging
from newsreap.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)

# The file our rules are kept in (next to the configuration file)
FILTERS_FILE = 'filters.yaml'

# The key in the filters file our rules are listed under
FILTERS_KEY = 'filters'

# What starts a new filters file
FILTERS_HEADER = """\
# NewsReap filters (see 'nr filter --help')
#
# Every article starts with a score of 0.  Score rules (score: and/or tag:)
# all add up; then a keep rule (action: keep) protects an article, or the
# first drop rule (action: drop) to match removes it.  Anything a rule
# doesn't name isn't checked.  Case and accents don't matter unless
# case_sensitive: true is set.
#
#  - name: recovery
#    subject: { regex: '\\.par2\\b' }
#    score: 10
#    tag: recovery
#
#  - name: casino-spam
#    groups: [alt.binaries.*]
#    poster: "@spam.example"
#    after: 2026-01-01
#    action: drop
#
# score: is a number to add (10, -50), comparisons to match on ('<-50',
# '>23, <34', '>=0', '=5') or both ('>40, +5' adds 5 to anything over 40).
# Quote comparisons; YAML reads a bare > as the start of a block of text.
#
# tagged: and not_tagged: work like Apprise tags: each entry of a list is
# a choice (any will do) and the tags in one entry must all be there; a
# tag starting with ! must not be there:
#    tagged: ['video, hd', music]      # (video and hd) or music
#    tagged: 'video, !trusted'         # video, but not trusted
#
#  - name: low-score
#    score: '<-50'
"""

# What check() returns:
#  - score: the item's score
#  - tags: its tags (a frozenset)
#  - drop: the rule that removes it; None if it is kept
FilterVerdict = namedtuple('FilterVerdict', ('score', 'tags', 'drop'))


def make_trie(words):
    """
    Returns the words given as a tree of their letters ('spam' and 'span'
    share 's', 'p' and 'a'); an empty key marks where a word ends
    """
    trie = {}
    for word in words:
        node = trie
        for c in word:
            node = node.setdefault(c, {})
        node[''] = True

    return trie


def trie_pattern(trie):
    """
    Returns the regular expression (as text) that finds any word of a
    tree made by make_trie()
    """
    if '' in trie and len(trie) == 1:
        # Only the end of a word
        return ''

    branches = [re.escape(c) + trie_pattern(child)
                for c, child in sorted(trie.items()) if c]

    pattern = branches[0] if len(branches) == 1 \
        else '(?:%s)' % '|'.join(branches)

    if '' in trie:
        # A word can end here too
        pattern = '(?:%s)?' % pattern

    return pattern


def trie_regex(words):
    """
    Returns a regular expression that finds any of the words given

    The words are merged by what they start with ('spam', 'spammer' and
    'span' become 'spa(?:m(?:mer)?|n)'), so it stays quick with thousands
    of them.
    """
    return re.compile(trie_pattern(make_trie(words)))


class _Gate:
    """
    The rules (of one phase) that can only match when a word is found in
    a field.  One regular expression search finds which of the words are
    there, so only the rules needing those words are checked.
    """

    def __init__(self, field, folded):
        self.field = field
        self.folded = folded

        # word -> the (position, rule) of the rules that need it
        self.words = {}

        # Worked out by finish()
        self.trie = None
        self.regex = None

    def add(self, word, position, rule):
        """
        Puts a rule behind this gate
        """
        self.words.setdefault(word, []).append((position, rule))

    def finish(self):
        """
        Prepares the search once every rule is added
        """
        self.trie = make_trie(self.words)

        # A look ahead finds every place a word starts (even inside
        # another word)
        self.regex = re.compile('(?=%s)' % trie_pattern(self.trie))

    def rules(self, item):
        """
        Returns the (position, rule) of every rule whose word is in the
        item's field
        """
        text = item.text(self.field, folded=self.folded)

        found = []
        for match in self.regex.finditer(text):
            # Every word starting here (ie: both 'spam' and 'spammer')
            start = match.start()
            node = self.trie
            for end in range(start, len(text)):
                node = node.get(text[end])
                if node is None:
                    break

                if '' in node:
                    found.extend(self.words[text[start:end + 1]])

        return found


class _Phase:
    """
    The rules of one phase (score, keep or drop) that apply to a set of
    groups, ready to be checked quickly
    """

    def __init__(self, rules):
        # (position, rule) of every rule, in the order they are listed
        self.rules = list(enumerate(rules))

        # Rules that need a word in a field go behind a gate
        gates = {}
        self.always = []
        for position, rule in self.rules:
            key = self._gate_key(rule)
            if key is None:
                self.always.append((position, rule))
                continue

            field, folded, word = key
            gate = gates.get((field, folded))
            if gate is None:
                gate = gates[(field, folded)] = _Gate(field, folded)

            gate.add(word, position, rule)

        for gate in gates.values():
            gate.finish()

        self.gates = list(gates.values())

    @staticmethod
    def _gate_key(rule):
        """
        Returns (field, folded, word) for a rule that can only match when
        the word is in the field; None for a rule that has to be checked
        every time
        """
        if rule.match != 'all':
            # Any one of its conditions is enough
            return None

        for field in FILTER_TEXT_FIELDS:
            # Every comparison of a field has to pass, so any one needing a
            # word will do
            for match in rule.text.get(field, ()):
                word = match.literal
                if word:
                    return field, match.folded, word

        return None

    def candidates(self, item):
        """
        Returns the rules that could match an item (in their order)
        """
        found = []
        for gate in self.gates:
            found.extend(gate.rules(item))

        if not found:
            # The usual case: none of the words are there
            return self.always

        # A rule can need a word found more than once
        found = list({position: entry for position, entry in
                      ((entry[0], entry) for entry in found)}.values())
        found.extend(self.always)
        found.sort(key=lambda entry: entry[0])
        return found

    def __len__(self):
        return len(self.rules)


class _Plan:
    """
    Every rule that applies to a set of groups, split into their phases
    """

    def __init__(self, rules):
        self.score = _Phase([r for r in rules if r.score is not None or
                             r.tags])
        self.keep = _Phase([r for r in rules
                            if r.action == FilterAction.Keep])
        self.drop = _Phase([r for r in rules
                            if r.action == FilterAction.Drop])


class NNTPFilter(NNTPFilterBase):
    """
    The rules of a filters file; see the module description.

    It acts like a container of rules (by name), and can be handed to an
    NNTPConnection like any other NNTPFilterBase.
    """

    def __init__(self, path=None, rules=None, now=None):
        """
        Prepares our rules

        - path: a filters file to load (it doesn't have to exist yet)
        - rules: rules (NNTPFilterRule or their YAML form) to start with
        - now: what relative times (ie: 90d) count back from (UTC)

        Raises ValueError (saying why) if the filters file can't be read;
        a rule in it that doesn't make sense is left out (see problems).
        """
        super().__init__()

        # Our rules (by name, in the order they are listed)
        self._rules = {}

        # Why each rule of the filters file that was left out was
        self.problems = []

        # The filters file we came from (if any)
        self.path = abspath(expanduser(path)) if path else None
        self.now = now or utcnow()

        # Group(s) -> their _Plan (worked out when first needed)
        self._plans = {}

        if self.path and isfile(self.path):
            self.load(self.path)

        for rule in rules or ():
            self.add(rule)

    def load(self, path, reset=True):
        """
        Loads the rules of a filters file

        A rule that doesn't make sense is left out (saying which one, and
        why; see problems) and the rest are loaded.  Raises ValueError if
        the file itself can't be read.
        """
        try:
            with open(path, encoding='utf-8') as fp:
                content = yaml.safe_load(fp)

        except OSError as e:
            raise ValueError(
                "Can't read the filters file %s (%s)" % (path, e)) from None

        except yaml.YAMLError as e:
            raise ValueError(
                "The filters file %s isn't valid YAML: %s" % (
                    path, e)) from None

        if content is None:
            # An empty file
            entries = []

        elif isinstance(content, list):
            # Just a list of rules
            entries = content

        elif isinstance(content, dict) and set(content) <= {FILTERS_KEY}:
            entries = content.get(FILTERS_KEY) or []

        else:
            raise ValueError(
                "The filters file %s must list its rules under '%s:'" % (
                    path, FILTERS_KEY))

        if not isinstance(entries, list):
            raise ValueError(
                "The filters file %s must list its rules under '%s:'" % (
                    path, FILTERS_KEY))

        rules = {}
        problems = []
        for no, entry in enumerate(entries, start=1):
            try:
                rule = NNTPFilterRule.from_dict(entry, now=self.now)

            except (ValueError, TypeError) as e:
                name = entry.get('name') if isinstance(entry, dict) else None
                problems.append('%s: rule #%d%s: %s' % (
                    path, no, ' (%s)' % name if name else '', e))
                continue

            if rule.name in rules or (not reset and rule.name in self._rules):
                problems.append(
                    "%s: rule #%d: the name '%s' is used more than once" % (
                        path, no, rule.name))
                continue

            rules[rule.name] = rule

        for problem in problems:
            # Said loudly; the other rules are still used
            logger.error('%s (the rule was left out)' % problem)

        if reset:
            self._rules = {}
            self.problems = []

        self.problems.extend(problems)

        self._rules.update(rules)
        self._plans = {}
        logger.info('Loaded %d filter rule(s) from %s' % (len(rules), path))

    def add(self, rule):
        """
        Adds a rule (an NNTPFilterRule or its YAML form) and returns it

        Raises ValueError if it doesn't make sense or its name is taken.
        """
        if not isinstance(rule, NNTPFilterRule):
            rule = NNTPFilterRule.from_dict(rule, now=self.now)

        if rule.name in self._rules:
            raise ValueError(
                "There is already a rule named '%s'" % rule.name)

        self._rules[rule.name] = rule
        self._plans = {}
        return rule

    def remove(self, name):
        """
        Removes a rule (by name); returns it, or None if there wasn't one
        """
        rule = self._rules.pop(name, None)
        self._plans = {}
        return rule

    def unique_name(self, name):
        """
        Returns the name given, numbered (ie: name-2) if a rule has it
        """
        found = name
        no = 1
        while found in self._rules:
            no += 1
            found = '%s-%d' % (name, no)

        return found

    def uses(self, field):
        """
        Returns True if any rule looks at a field (ie: 'message_id')
        """
        return any(rule.uses(field) for rule in self._rules.values())

    @property
    def scores(self):
        """
        True if any rule scores or tags
        """
        return any(rule.score is not None or rule.tags
                   for rule in self._rules.values())

    @property
    def drops(self):
        """
        True if any rule removes articles
        """
        return any(rule.action == FilterAction.Drop
                   for rule in self._rules.values())

    def plan(self, groups):
        """
        Returns the rules that apply to an item found in these groups
        """
        if isinstance(groups, str):
            groups = (groups, )

        key = tuple(sorted(set(group.lower() for group in groups or ())))
        plan = self._plans.get(key)
        if plan is None:
            plan = self._plans[key] = _Plan([
                rule for rule in self._rules.values()
                if rule.applies_to(key)])

        return plan

    def check(self, groups, subject='', poster='', message_id='',
              posted=None, size=None, score=0, tags=(), rescore=True,
              body=None, filenames=None):
        """
        Returns what our rules make of an item (see FilterVerdict)

        - groups: the group (or groups) it was found in
        - subject / poster / message_id / posted / size: what we know
          about it (posted in UTC; None when we don't know)
        - score / tags: what it was given before
        - rescore: work its score and tags out again (from 0); otherwise
          the ones given are kept and only the keep and drop rules run
        - body / filenames: its text and the names of the files it holds,
          when known (rules on them never match otherwise)
        """
        item = FilterItem(
            subject=subject, poster=poster, message_id=message_id,
            posted=posted, size=size, body=body, filenames=filenames)

        return self.check_item(
            groups, item, score=score, tags=tags, rescore=rescore)

    def check_item(self, groups, item, score=0, tags=(), rescore=True):
        """
        Same as check(), with a FilterItem already prepared
        """
        plan = self.plan(groups)

        if rescore:
            score = 0
            tags = set()
            for _, rule in plan.score.candidates(item):
                if rule.matches(item, score, tags):
                    if rule.score is not None:
                        score += rule.score
                    tags.update(rule.tags)

        tags = frozenset(tags)

        for _, rule in plan.keep.candidates(item):
            if rule.matches(item, score, tags):
                # Protected from every drop rule
                return FilterVerdict(score, tags, None)

        for _, rule in plan.drop.candidates(item):
            if rule.matches(item, score, tags):
                return FilterVerdict(score, tags, rule)

        return FilterVerdict(score, tags, None)

    def to_yaml(self, rules=None):
        """
        Returns rules (all of ours by default) the way they are written in
        a filters file (a list of rules, ready to go under 'filters:')
        """
        rules = self._rules.values() if rules is None else rules
        return yaml.safe_dump(
            [rule.to_dict() for rule in rules], sort_keys=False,
            default_flow_style=False, allow_unicode=True, width=79)

    def save(self, path=None):
        """
        Writes every rule to the filters file (replacing what it held)

        Raises ValueError if rules were left out when it was read (they
        would be lost); they have to be fixed first.
        """
        if self.problems:
            raise ValueError(
                '%d rule(s) of %s were left out (%s); fix them first, as '
                'writing the file again would lose them' % (
                    len(self.problems), self.path, '; '.join(self.problems)))

        path = abspath(expanduser(path or self.path))
        temporary = path + '.tmp'

        with open(temporary, 'w', encoding='utf-8') as fp:
            fp.write(FILTERS_HEADER)
            fp.write('\n%s:\n' % FILTERS_KEY)
            fp.write(self._indent(self.to_yaml()))

        chmod(temporary, 0o600)
        replace(temporary, path)
        self.path = path

    def append(self, rule, path=None):
        """
        Adds a rule and writes it at the end of the filters file, leaving
        everything already in the file (comments too) as it is

        Raises ValueError if the rule doesn't make sense or its name is
        taken.
        """
        path = abspath(expanduser(path or self.path))
        rule = self.add(rule)

        if not isfile(path):
            # A new file
            self.save(path)
            return rule

        with open(path, encoding='utf-8') as fp:
            content = fp.read()

        # The rule, listed under the last key of the file (ours)
        block = self._indent(self.to_yaml([rule]))
        updated = content if content.endswith('\n') or not content \
            else content + '\n'

        try:
            found = yaml.safe_load(updated)

        except yaml.YAMLError:
            found = None

        if not found:
            # Empty (or only comments); start our list
            updated += '\n%s:\n' % FILTERS_KEY

        updated += block

        # Make sure the file still reads back with the rule in it
        try:
            check = yaml.safe_load(updated)
            entries = check.get(FILTERS_KEY) if isinstance(check, dict) \
                else check
            written = isinstance(entries, list) and any(
                isinstance(e, dict) and e.get('name') == rule.name
                for e in entries)

        except yaml.YAMLError:
            written = False

        if not written:
            # The file isn't laid out the way we expected (ie: a flow style
            # list); write it out again with every rule instead
            logger.warning(
                'Rewriting %s to add the rule (its comments are lost).' % (
                    path))
            self.save(path)
            return rule

        temporary = path + '.tmp'
        with open(temporary, 'w', encoding='utf-8') as fp:
            fp.write(updated)

        chmod(temporary, 0o600)
        replace(temporary, path)
        return rule

    @staticmethod
    def _indent(text):
        """
        Indents a YAML list so it sits under our key
        """
        return ''.join(
            '  %s' % line if line.strip() else line
            for line in text.splitlines(keepends=True))

    #
    # What an NNTPConnection (see NNTPFilterBase) asks of a filter
    #

    def score(self, group, poster, date, subject, size, lines, xgroups,
              *args, **kwargs):
        """
        Returns the score our rules give an article
        """
        return self.check(
            self._legacy_groups(group, xgroups), subject=subject, poster=poster,
            message_id=kwargs.get('id', ''), posted=date, size=size).score

    def whitelist(self, group, poster, date, subject, size, lines, xgroups,
                  *args, **kwargs):
        """
        Returns True if a keep rule protects an article
        """
        groups = self._legacy_groups(group, xgroups)
        item = FilterItem(
            subject=subject, poster=poster, message_id=kwargs.get('id', ''),
            posted=date, size=size)

        score = kwargs.get('score', 0)
        for _, rule in self.plan(groups).keep.candidates(item):
            if rule.matches(item, score):
                return True

        return False

    def blacklist(self, group, poster, date, subject, size, lines, xgroups,
                  *args, **kwargs):
        """
        Returns True if a drop rule removes an article
        """
        groups = self._legacy_groups(group, xgroups)
        item = FilterItem(
            subject=subject, poster=poster, message_id=kwargs.get('id', ''),
            posted=date, size=size)

        score = kwargs.get('score', 0)
        for _, rule in self.plan(groups).drop.candidates(item):
            if rule.matches(item, score):
                return True

        return False

    @staticmethod
    def _legacy_groups(group, xgroups):
        """
        Returns the groups an XOVER entry was found in (its own, and the
        ones it was crossposted to)
        """
        return [g for g in (group, *(xgroups or ())) if g]

    #
    # Container
    #

    def __iter__(self):
        return iter(self._rules.values())

    def __len__(self):
        return len(self._rules)

    def __contains__(self, name):
        return name in self._rules

    def __getitem__(self, name):
        return self._rules[name]

    def __bool__(self):
        return bool(self._rules)

    def keys(self):
        return self._rules.keys()

    def values(self):
        return self._rules.values()

    def items(self):
        return self._rules.items()

    def iterkeys(self):
        return iter(self._rules.keys())

    def itervalues(self):
        return iter(self._rules.values())

    def iteritems(self):
        return iter(self._rules.items())

    def __str__(self):
        return self.path or ''

    def __repr__(self):
        return '<NNTPFilter rules=%d path="%s" />' % (
            len(self._rules), self.path or '')
