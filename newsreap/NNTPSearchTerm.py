# One find(1) style search condition (ie: --isubject '*holiday*')
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
Search conditions work like the tests of the find command:

    --subject 'Holiday*'        the whole subject matches (case matters)
    --isubject '*holiday*'      the same, ignoring case (and accents)
    --regex-subject '.*\\(1/\\d+\\).*'
                                the whole subject matches the regular
                                expression (case matters)
    --iregex-subject ...        the same, ignoring case (and accents)

The same goes for --body, --poster, --filename (a file's name, as its
yEnc or MIME header gives it) and --message-id.  Subject, From (the poster),
Message-ID and the body are the article's own (NNTP) parts.  In a
pattern, * matches anything (even nothing) and ? matches one character;
put a backslash in front of either to look for it as it is.  A pattern
starting with ! matches what doesn't match the rest (\\! for a real !).

Every condition given has to match, so each one narrows the results down
further.
"""

import re
from functools import lru_cache

from sqlalchemy import and_
from sqlalchemy import func
from sqlalchemy import literal

from .NNTPFilterRule import fold
from .NNTPFilterRule import unaccent


class SearchKind:
    """
    How a pattern is read
    """
    # * and ? wildcards (as find -name)
    Glob = 'glob'

    # A regular expression (as find -regex)
    Regex = 'regex'


# What can be searched (not every search has all of them); 'any' is what a
# plain keyword looks at (subjects, text and file names)
SEARCH_FIELDS = ('subject', 'body', 'poster', 'filename', 'message_id')
SEARCH_ANY = 'any'

# Every search option: name -> (field, kind, case sensitive); ie: subject,
# isubject, regex_subject and iregex_subject
SEARCH_OPTIONS = {
    '%s%s%s' % (prefix, regex, field): (
        field, SearchKind.Regex if regex else SearchKind.Glob, not prefix)
    for field in SEARCH_FIELDS
    for prefix in ('', 'i')
    for regex in ('', 'regex_')
}

# The shortest piece of a pattern worth narrowing a search down with
SEARCH_PIECE_LENGTH = 3

# A word (as the word searches see them: letters and numbers)
WORD_RE = re.compile(r'[^\W_]+')

# A wildcard (or an escaped character) in a pattern
GLOB_TOKEN_RE = re.compile(r'\\(.)|([*?])', re.DOTALL)


def glob_parts(pattern):
    """
    Splits a wildcard pattern into its parts: ('text', 'abc'), ('*', '')
    or ('?', '')
    """
    parts = []
    position = 0
    text = ''
    for found in GLOB_TOKEN_RE.finditer(pattern):
        text += pattern[position:found.start()]
        position = found.end()

        if found.group(1) is not None:
            # An escaped character is just text
            text += found.group(1)
            continue

        if text:
            parts.append(('text', text))
            text = ''
        parts.append((found.group(2), ''))

    text += pattern[position:]
    if text:
        parts.append(('text', text))

    return parts


@lru_cache(maxsize=256)
def compiled(kind, pattern, case_sensitive):
    """
    Returns the Python regular expression a pattern is checked with (it
    must match the whole text); kept for reuse
    """
    if kind == SearchKind.Glob:
        regex = ''.join(
            re.escape(value) if part == 'text'
            else '.*' if part == '*' else '.'
            for part, value in glob_parts(pattern))

    else:
        regex = pattern

    return re.compile(
        regex, re.DOTALL | (0 if case_sensitive else re.IGNORECASE))


def sql_match(kind, pattern, case_sensitive, text):
    """
    Returns 1 if text matches a pattern (0 if not); SQLite calls this as
    newsreap_match() (see Database.sqlite_functions())
    """
    if text is None:
        return 0

    try:
        regex = compiled(kind, pattern, bool(case_sensitive))

    except re.error:
        # A bad expression matches nothing (it was checked before use)
        return 0

    if not case_sensitive:
        # Case and accents don't matter
        text = fold(text)

    return 1 if regex.fullmatch(text) else 0


class NNTPSearchTerm:
    """
    One search condition; see the module description
    """

    def __init__(self, field, pattern, kind=SearchKind.Glob,
                 case_sensitive=True):
        """
        Prepares a condition

        - field: what is searched (one of SEARCH_FIELDS)
        - pattern: what it must match (a leading ! turns it around)
        - kind: SearchKind.Glob or SearchKind.Regex
        - case_sensitive: case (and accents) matter

        Raises ValueError (saying why) for something that can't be used.
        """
        if field not in SEARCH_FIELDS and field != SEARCH_ANY:
            raise ValueError("'%s' can't be searched" % field)

        if kind not in (SearchKind.Glob, SearchKind.Regex):
            raise ValueError("'%s' isn't a kind of pattern" % kind)

        self.field = field
        self.kind = kind
        self.case_sensitive = bool(case_sensitive)

        # A leading ! leaves out what matches
        self.negate = pattern.startswith('!')
        if self.negate:
            pattern = pattern[1:]

        elif pattern.startswith('\\!'):
            pattern = pattern[1:]

        if not pattern:
            raise ValueError('--%s needs something to look for' % self.option)

        # As given, and as compared (accents never matter when case
        # doesn't; case is ignored by the expression itself)
        self.original = pattern
        self.pattern = pattern if self.case_sensitive else unaccent(pattern)

        try:
            compiled(kind, pattern, self.case_sensitive)

        except re.error as e:
            raise ValueError("--%s '%s' isn't a valid regular expression "
                             '(%s)' % (self.option, pattern, e)) from None

    @property
    def option(self):
        """
        The command line option this condition is given with
        """
        return '%s%s%s' % (
            '' if self.case_sensitive else 'i',
            'regex-' if self.kind == SearchKind.Regex else '',
            self.field.replace('_', '-'))

    def matches(self, text):
        """
        Returns True if the text matches (a ! pattern is turned around)
        """
        found = bool(sql_match(
            self.kind, self.pattern, self.case_sensitive, text))
        return found != self.negate

    def pieces(self):
        """
        Returns the text that has to be found in anything that matches
        (the parts of a wildcard pattern long enough to search the indexes
        with); none for a regular expression or a ! pattern
        """
        if self.negate or self.kind != SearchKind.Glob:
            return []

        return [value for part, value in glob_parts(self.pattern)
                if part == 'text' and len(value.strip()) >=
                SEARCH_PIECE_LENGTH]

    def exact(self):
        """
        Returns the text the value must be exactly (a pattern without
        wildcards, where case matters); None otherwise
        """
        if self.negate or self.kind != SearchKind.Glob or \
                not self.case_sensitive:
            return None

        parts = glob_parts(self.pattern)
        if len(parts) == 1 and parts[0][0] == 'text':
            return parts[0][1]

        return None

    def words(self):
        """
        Returns the words that have to be found in anything that matches,
        for searching by words (ie: the archive): only ones that are sure
        to start a word, the last one possibly only the start of one
        """
        if self.negate or self.kind != SearchKind.Glob:
            return []

        found = []
        parts = glob_parts(self.pattern)
        for no, (part, value) in enumerate(parts):
            if part != 'text':
                continue

            words = WORD_RE.findall(value)
            if words and no > 0 and WORD_RE.match(value):
                # Right after a wildcard; it could be the end of a word
                words = words[1:]

            if words:
                # The last one may only be the start of a word (right before
                # a wildcard); word searches treat their last word that way
                found.append(' '.join(words))

        return found

    def like(self):
        """
        Returns the pattern as an SQL LIKE pattern (escaped with '/')
        """
        return ''.join(
            value.replace('/', '//').replace('%', '/%').replace('_', '/_')
            if part == 'text' else '%' if part == '*' else '_'
            for part, value in glob_parts(self.pattern))

    def condition(self, dialect, column):
        """
        Returns the SQL condition for `column` matching (ignoring a !;
        the caller leaves out what matches then)
        """
        if dialect == 'postgresql':
            # Found with the (accent ignoring) search index first
            searched = func.newsreap_unaccent(column)
            if self.kind == SearchKind.Glob:
                found = searched.ilike(
                    func.newsreap_unaccent(self.like()), escape='/')
                exact = column.like(self.like(), escape='/')

            else:
                regex = '^(?:%s)$' % self.original
                found = searched.op('~*')(func.newsreap_unaccent(regex))
                exact = column.op('~')(regex)

            return and_(found, exact) if self.case_sensitive else found

        if dialect == 'mysql':
            if self.kind == SearchKind.Glob:
                # The usual (_ci) collations ignore case and accents
                found = column.like(self.like(), escape='/')
                return and_(found, column.collate('utf8mb4_bin').like(
                    self.like(), escape='/')) if self.case_sensitive \
                    else found

            # Its expressions can't ignore accents; the pattern is used as
            # it was given
            return func.regexp_like(
                column, '^(?:%s)$' % self.original,
                'c' if self.case_sensitive else 'i') == 1

        # SQLite (and anything else): checked by Python
        return func.newsreap_match(
            self.kind, self.pattern, literal(int(self.case_sensitive)),
            column) == 1

    @classmethod
    def from_options(cls, **options):
        """
        Returns the conditions given as command line options: the keys are
        the option names (ie: isubject, regex_body), the values a list of the
        patterns given

        Raises ValueError (saying why) for one that can't be used.
        """
        terms = []
        for name, patterns in options.items():
            if not patterns:
                continue

            try:
                field, kind, case_sensitive = SEARCH_OPTIONS[
                    name.replace('-', '_')]

            except KeyError:
                raise ValueError(
                    "'%s' isn't a search option" % name) from None

            for pattern in patterns:
                terms.append(cls(field, pattern, kind=kind,
                                 case_sensitive=case_sensitive))

        return terms

    def __str__(self):
        return '--%s %s%s' % (
            self.option, '!' if self.negate else '', self.pattern)

    def __repr__(self):
        return '<NNTPSearchTerm %s />' % str(self)
