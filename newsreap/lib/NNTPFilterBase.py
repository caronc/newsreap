# -*- coding: utf-8 -*-
#
# Defines a global filter list that can be applied to results
# retrieved by a group on usenet
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

"""
Filters work by catagorizing the group (by regular expression) you want
to match against before applying the filter. followed by a list of the
filter you want to apply to this match. each individual filter is defined
by another dictionary allowing filters to be defined by their fields.

Each filter entries defined fields represent 'AND' which means the more
filters you specify, the more that have to match for the filter to take
action.

If you need to do an OR style, just create more filters that handle all
possible constraints

"""

from os.path import isdir
from os.path import isfile
from os import listdir
from os.path import basename
from os.path import join
import re

# Logging
import logging
from lib.Logging import NEWSREAP_LOGGER
logger = logging.getLogger(NEWSREAP_LOGGER)

# The following processes a valid filter for processing
FILTER_LINE_RE = re.compile(
    r'^\s*([#\s]+(?P<comment>.*)#*|' +\
    r'(?P<code>[A-Z]+)(\s*(?P<op>[<>=]))?(\s*(?P<sign>[+-]))?(\s*0*(?P<score>0|[1-9][0-9]*))?' +\
    r'\s+(?P<group>[^\s]+)' +\
    r'(\s+(?P<regex>.+))?' +\
    r')\s*$',
    re.IGNORECASE,
)

# Newsreap Filter Files
FILTER_FILE_RE = re.compile(
    r'.*\.nrf$',
    re.IGNORECASE,
)


class FilterListCode(object):
    """
    Maps the filter code read from a parsed .nrf (newsreap filter file)
    """
    # Whitelist Entry
    WHITELIST = 'W'

    # Blacklist Entry
    BLACKLIST = 'B'

    # Score list Entry
    SCORELIST = 'S'

# A List of valid entries (for validation)
FILTER_LIST_CODES = (
    FilterListCode.WHITELIST,
    FilterListCode.BLACKLIST,
    FilterListCode.SCORELIST,
)


class FilterMatchCode(object):
    # Scan against the subject (Default)
    BY_SUBJECT = 'S'

    # Scan against the Poster
    BY_POSTER = 'P'


class FilterDirectives(object):
    # An optional human readable description that can get
    # prepended to logging if the matached filter is applied
    # This is useful for debugging, but otherwise not nessisary
    DESCRIPTION = 'desc'

    # Defines an integer to be used to alter the score
    # this field is only used with the Score List
    SCORE = 'score'

    # Tracks the number of filters defined; this should never
    # be pre-set unless you know what you're doing. This value
    # is automatically detected otherwise and cached
    FILTERS = 'filters'

    # Subject Filter Types
    SUBJECT_MATCHES_REGEX = '_st_regex'
    SUBJECT_STARTS_WITH = '_st_sw'
    SUBJECT_ENDS_WITH = '_st_ew'

    # Poster Filter Types
    POSTER_MATCHES_REGEX = '_po_regex'
    POSTER_STARTS_WITH = '_po_sw'
    POSTER_ENDS_WITH = '_po_ew'

    # Line Count Filter Types
    # Avoid using this if you can; there are several articles
    # in usnet which do not define this field. In which case
    # we initialize it to -1 just so it has some kind of value.
    # It is more wise to filter using the 'SIZE' directives
    LINE_COUNT_GREATER_THAN = '_lc_gt'
    LINE_COUNT_LESS_THAN = '_lc_lt'

    # Size Filter Types
    SIZE_GREATER_THAN = '_sz_gt'
    SIZE_LESS_THAN = '_sz_lt'

    # Score Filter Types
    SCORE_GREATER_THAN = '_se_gt'
    SCORE_LESS_THAN = '_se_lt'

    # Date Filter Types
    DATE_GREATER_THAN = '_de_gt'
    DATE_LESS_THAN = '_de_lt'

    # Cross-Post Filters
    XPOST_COUNT_GREATER_THAN = '_xc_gt'
    XPOST_COUNT_LESS_THAN = '_xc_lt'


class NNTPFilterBase(object):
    """
    A class that can be applied to an NNTPConnection to additionaly apply a
    pre-determined number of filters to the content as it's fetched
    """

    _scorelist = {}

    def __init__(self, paths=None):
        """
        Initialize Filter
        """
        # the regex_map contains the pre-compied lookups of all the
        # compiled regular expressions
        self._regex_map = {}

        # The hash contains all of the blacklist, whitelist and scorelist
        # precompiled; it's populated when a group is first accessed
        self._regex_hash = {}

        self._blacklist = {}
        self._whitelist = {}

        # Scoring is done before applying filters;  It's a way of marking your
        # favorite poster, keywords, and bumping up a score on them.

        # By default everything consists of a score of zero (0) unless you
        # otherwise want to adjust it. You can then later use the filters to
        # adjust the score.
        self._scorelist = {}

        # Tracks a list of loaded files
        self.loaded_files = []

        if paths:
            # Load our content if path(s) were specified
            self.load(paths)


    def load(self, paths, reset=True):
        """
        Input can be either a directory or file to scan.  If a directory is
        passed in, it is scanned for .nrf files (newsreap filter) files and
        loads their content.

        If a list is passed in, then the contents of the list are expected
        to be either directories and/or files. to which all are loaded.

        If the paths is None or contains nothing, then nothing is loaded.

        If reset is set to true, then existing content is erased in
        preparation for the new content.
        """

        if reset:
            self._regex_map = {}
            self._regex_hash = {}
            self._blacklist = {}
            self._whitelist = {}
            self._scorelist = {}
            self.loaded_files = []

        if not paths:
            # Nothing more to do
            return

        if isinstance(paths, basestring):
            # Convert to list
            paths = [paths, ]

        for path in paths:
            _paths = []
            if isdir(path):
                # Search for newsreap filters in the path specified
                for entry in listdir(path):
                    if isdir(join(path, entry)):
                        continue

                    if not FILTER_FILE_RE.match(entry):
                        continue
                    _paths.append(join(path, entry))
                # Load our content
                self.load(paths=_paths, reset=False)

            elif isfile(path):
                # Parse the file
                lineno = 0
                logger.info('Loading filters from %s' % (path))
                with open(path) as f:
                    for line in f:
                        # Increment line no
                        lineno += 1

                        if not len(line.rstrip()):
                            # Blank line; ignore these
                            continue

                        # Parseable?
                        result = FILTER_LINE_RE.match(line)
                        if not result:
                            logger.warning(
                                "%s[:%d] not parseable." % (
                                    basename(path), lineno,
                            ))
                            continue

                        if result.group('comment') is not None:
                            # Ignore commented lines
                            continue

                        try:
                            code = result.group('code').upper()

                        except AttributeError:
                            # None Type; Assign Default
                            code = '%s%s' % (
                                FilterListCode.SCORELIST,
                                FilterMatchCode.BY_SUBJECT,
                            )

                        if code[0] not in FILTER_LIST_CODES:
                            logger.warning(
                                "%s[:%d] not has an invalid code (%s)." % (
                                    basename(path), lineno, str(code),
                            ))
                            continue

                        # Store List Code
                        l_code = code[0]

                        op = result.group('op')
                        if op is None or op == '=':
                            op = FilterDirectives.SCORE

                        elif op == '>':
                            op = FilterDirectives.SCORE_GREATER_THAN

                        elif op == '<':
                            op = FilterDirectives.SCORE_LESS_THAN

                        else:
                            logger.warning(
                                "%s[:%d] contains an invalid operation code (%s) (expecting < > or =)." % (
                                    basename(path), lineno, str(op),
                            ))
                            continue
                        if l_code == FilterListCode.SCORELIST and \
                           op != FilterDirectives.SCORE:
                            logger.warning(
                                "%s[:%d] contains an invalid operation code (%s) (not expecting one)." % (
                                    basename(path), lineno, str(op),
                            ))
                            continue

                        score = result.group('score')
                        if score is not None:
                            sign = result.group('sign')
                            if not sign:
                                sign = '+'

                            elif sign not in ('-', '+'):
                                logger.warning(
                                    "%s[:%d] not has an invalid sign (%s) (expecting +/-)." % (
                                        basename(path), lineno, str(sign),
                                ))
                                continue

                            # Apply sign and convert score to integer
                            if sign == '-':
                                score = -int(score)
                            else:
                                score = int(score)

                        elif l_code == FilterListCode.SCORELIST:
                            # A score is manditory
                            logger.warning(
                                "%s[:%d] (scorelist) not has no score associated with it." % (
                                    basename(path), lineno,
                            ))
                            continue

                        try:
                            group = result.group('group').lower()

                        except AttributeError:
                            # None Type; Assign Default
                            group = '.*'

                        _filter = {
                            FilterDirectives.DESCRIPTION: \
                                'file="%s", line="%d"' % (path, lineno),
                        }

                        if score is not None:
                            _filter[op] = score

                        regex = result.group('regex')
                        if regex is not None:
                            if code[1] == 'S':
                                _filter[FilterDirectives.SUBJECT_MATCHES_REGEX] = regex

                            elif code[1] == 'P':
                                _filter[FilterDirectives.POSTER_MATCHES_REGEX] = regex

                        try:
                            if l_code == FilterListCode.BLACKLIST:
                                self.blacklist_append(group, _filter)

                            elif l_code == FilterListCode.WHITELIST:
                                self.whitelist_append(group, _filter)

                            elif l_code == FilterListCode.SCORELIST:
                                self.scorelist_append(group, _filter)

                        except ValueError, e:
                            # Internal Parse Error Occured
                            logger.warning(
                                "%s[:%d] %s." % (
                                    basename(path), lineno, str(e),
                            ))
                            continue

            else:
                logger.warning(
                    'Unsupported type <%s> passed as filter' % type(path),
                )



    def scorelist_append(self, group_regex, entry):
        """
        Add a scoring entry to list
        """
        if not isinstance(group_regex, basestring):
            raise ValueError(
                "Invalid group regex specified; (expecting a string)",
            )

        # Prepare filter count
        entry[FilterDirectives.FILTERS] = \
            len([l for l in entry if l[0] == '_'])

        if entry[FilterDirectives.FILTERS] <= 0:
            raise ValueError(
                "There was no filters specified.",
            )

        # Compile Subject and/or Poster if necessary
        if FilterDirectives.SUBJECT_MATCHES_REGEX in entry:
            if isinstance(entry[FilterDirectives.SUBJECT_MATCHES_REGEX],
                    basestring):
                try:
                    entry[FilterDirectives.SUBJECT_MATCHES_REGEX] = \
                        re.compile(
                            entry[FilterDirectives.SUBJECT_MATCHES_REGEX],
                            re.IGNORECASE,
                        )
                except:
                    raise ValueError(
                        "Invalid subject regex '%s'" % \
                        entry[FilterDirectives.SUBJECT_MATCHES_REGEX],
                    )

        if FilterDirectives.POSTER_MATCHES_REGEX in entry:
            if isinstance(entry[FilterDirectives.POSTER_MATCHES_REGEX],
                    basestring):
                try:
                    entry[FilterDirectives.POSTER_MATCHES_REGEX] = \
                        re.compile(
                            entry[FilterDirectives.POSTER_MATCHES_REGEX],
                            re.IGNORECASE,
                        )
                except:
                    raise ValueError(
                        "Invalid poster regex '%s'" % \
                        entry[FilterDirectives.POSTER_MATCHES_REGEX],
                    )

        if FilterDirectives.SCORE not in entry:
            raise ValueError(
                "There was no score specified.",
            )

        if group_regex not in self._regex_map:
            try:
                # pre-compile regular expression for both hashing
                # and a way of preforming some early validation
                self._regex_map[group_regex] = re.compile(group_regex)

            except:
                raise ValueError(
                    "Invalid group regex '%s'" % group_regex,
                )

        if group_regex not in self._scorelist:
            self._scorelist[group_regex] = list()
        self._scorelist[group_regex].append(entry)

        if len(self._regex_hash):
            # Force a rehash next time we apply our list
            self._regex_hash = {}


    def blacklist_append(self, group_regex, entry):
        """
        Add a blacklist entry to list
        """
        if not isinstance(group_regex, basestring):
            raise ValueError(
                "Invalid group regex specified; (expecting a string)",
            )

        # Prepare filter count
        entry[FilterDirectives.FILTERS] = \
            len([l for l in entry if l[0] == '_'])

        if entry[FilterDirectives.FILTERS] <= 0:
            raise ValueError(
                "There was no filters specified.",
            )

        # Compile Subject and/or Poster if necessary
        if FilterDirectives.SUBJECT_MATCHES_REGEX in entry:
            if isinstance(entry[FilterDirectives.SUBJECT_MATCHES_REGEX],
                    basestring):
                try:
                    entry[FilterDirectives.SUBJECT_MATCHES_REGEX] = \
                        re.compile(
                            entry[FilterDirectives.SUBJECT_MATCHES_REGEX],
                            re.IGNORECASE,
                        )
                except:
                    raise ValueError(
                        "Invalid subject regex '%s'" % \
                        entry[FilterDirectives.SUBJECT_MATCHES_REGEX],
                    )

        if FilterDirectives.POSTER_MATCHES_REGEX in entry:
            if isinstance(entry[FilterDirectives.POSTER_MATCHES_REGEX],
                    basestring):
                try:
                    entry[FilterDirectives.POSTER_MATCHES_REGEX] = \
                        re.compile(
                            entry[FilterDirectives.POSTER_MATCHES_REGEX],
                            re.IGNORECASE,
                        )
                except:
                    raise ValueError(
                        "Invalid poster regex '%s'" % \
                        entry[FilterDirectives.POSTER_MATCHES_REGEX],
                    )

        if group_regex not in self._regex_map:
            try:
                # pre-compile regular expression for both hashing
                # and a way of preforming some early validation
                self._regex_map[group_regex] = re.compile(group_regex)

            except:
                raise ValueError(
                    "Invalid group regex '%s'" % group_regex,
                )

        if group_regex not in self._blacklist:
            self._blacklist[group_regex] = list()
        self._blacklist[group_regex].append(entry)

        if len(self._regex_hash):
            # Force a rehash next time we apply our list
            self._regex_hash = {}


    def whitelist_append(self, group_regex, entry):
        """
        Add a whitelist entry to list
        """
        if not isinstance(group_regex, basestring):
            raise ValueError(
                "Invalid group regex specified; (expecting a string)",
            )

        # Prepare filter count
        entry[FilterDirectives.FILTERS] = \
            len([l for l in entry if l[0] == '_'])

        if entry[FilterDirectives.FILTERS] <= 0:
            raise ValueError(
                "There was no filters specified.",
            )

        # Compile Subject and/or Poster if necessary
        if FilterDirectives.SUBJECT_MATCHES_REGEX in entry:
            if isinstance(entry[FilterDirectives.SUBJECT_MATCHES_REGEX],
                    basestring):
                try:
                    entry[FilterDirectives.SUBJECT_MATCHES_REGEX] = \
                        re.compile(
                            entry[FilterDirectives.SUBJECT_MATCHES_REGEX],
                            re.IGNORECASE,
                        )
                except:
                    raise ValueError(
                        "Invalid subject regex '%s'" % \
                        entry[FilterDirectives.SUBJECT_MATCHES_REGEX],
                    )

        if FilterDirectives.POSTER_MATCHES_REGEX in entry:
            if isinstance(entry[FilterDirectives.POSTER_MATCHES_REGEX],
                    basestring):
                try:
                    entry[FilterDirectives.POSTER_MATCHES_REGEX] = \
                        re.compile(
                            entry[FilterDirectives.POSTER_MATCHES_REGEX],
                            re.IGNORECASE,
                        )
                except:
                    raise ValueError(
                        "Invalid poster regex '%s'" % \
                        entry[FilterDirectives.POSTER_MATCHES_REGEX],
                    )

        if group_regex not in self._regex_map:
            try:
                # pre-compile regular expression for both hashing
                # and a way of preforming some early validation
                self._regex_map[group_regex] = re.compile(group_regex)

            except:
                raise ValueError(
                    "Invalid group regex '%s'" % group_regex,
                )

        if group_regex not in self._whitelist:
            self._whitelist[group_regex] = list()
        self._whitelist[group_regex].append(entry)

        if len(self._regex_hash):
            # Force a rehash next time we apply our list
            self._regex_hash = {}


    def lazy_re_fetch(self, group):
        """
        This function takes a group and builds it's matching
        hash entries based on filters, whitelist, etc
        """
        if group not in self._regex_hash:
            # Store the group in a hash
            self._regex_hash[group] = {
                FilterListCode.BLACKLIST: [],
                FilterListCode.WHITELIST: [],
                FilterListCode.SCORELIST: [],
            }

            # Build Groups Black List
            for _k, _v in self._blacklist.iteritems():
                k = self._regex_map.get(_k, re.compile(_k))
                if k.match(group):
                    # Keys are prefixed with (underscore); a count makes it
                    # easier later to apply multiple filters and not trying
                    # to pre-determine ahead of time if they've all matched
                    for v in _v:
                        if FilterDirectives.FILTERS not in v:
                            v[FilterDirectives.FILTERS] = \
                                    len([l for l in v if l[0] == '_'])

                        self._regex_hash[group][FilterListCode.BLACKLIST].append(v)

            # Build Groups White List
            for _k, _v in self._whitelist.iteritems():
                k = self._regex_map.get(_k, re.compile(_k))
                if k.match(group):
                    # Keys are prefixed with (underscore); a count makes it
                    # easier later to apply multiple filters and not trying
                    # to pre-determine ahead of time if they've all matched
                    for v in _v:
                        if FilterDirectives.FILTERS not in v:
                            v[FilterDirectives.FILTERS] = \
                                    len([l for l in v if l[0] == '_'])

                        self._regex_hash[group][FilterListCode.WHITELIST].append(v)

            # Build Groups Score List
            for _k, _v in self._scorelist.iteritems():
                k = self._regex_map.get(_k, re.compile(_k))
                if k.match(group):
                    # Keys are prefixed with (underscore); a count makes it
                    # easier later to apply multiple filters and not trying
                    # to pre-determine ahead of time if they've all matched
                    for v in _v:
                        if FilterDirectives.FILTERS not in v:
                            v[FilterDirectives.FILTERS] = \
                                    len([l for l in v if l[0] == '_'])

                        self._regex_hash[group][FilterListCode.SCORELIST].append(v)

        return self._regex_hash[group]


    def _match(self, entry, poster, date, subject, size, lines, xgroups,
               *args, **kwargs):
        """
        The generic function called to scan whitelists, blacklists,
        and scorelists which is defined by 'typelist',
        """

        # Acquire a match count
        matches = entry.get(FilterDirectives.FILTERS, 0)
        if not matches:
            # Probably spit a warning out here (TODO)
            return False

        # Acquire Score
        score = kwargs.get(FilterDirectives.SCORE, 0)
        if FilterDirectives.SCORE_GREATER_THAN in entry:
            if score > entry[FilterDirectives.SCORE_GREATER_THAN]:
                matches -= 1
                if matches <= 0:
                    return True
            else:
                # No match
                return False

        if FilterDirectives.SCORE_LESS_THAN in entry:
            if score < entry[FilterDirectives.SCORE_LESS_THAN]:
                matches -= 1
                if matches <= 0:
                    return True
            else:
                # No match
                return False

        if FilterDirectives.SUBJECT_MATCHES_REGEX in entry:
            if entry[FilterDirectives.SUBJECT_MATCHES_REGEX].match(subject):
                matches -= 1
                if matches <= 0:
                    return True
            else:
                # No match
                return False

        if FilterDirectives.SUBJECT_STARTS_WITH in entry:
            if entry[FilterDirectives.SUBJECT_STARTS_WITH] == \
               subject[0:len(entry[FilterDirectives.SUBJECT_STARTS_WITH])]:
                matches -= 1
                if matches <= 0:
                    return True
            else:
                # No match
                return False

        if FilterDirectives.SUBJECT_ENDS_WITH in entry:
            if entry[FilterDirectives.SUBJECT_ENDS_WITH] == \
               subject[-len(entry[FilterDirectives.SUBJECT_ENDS_WITH]):]:
                matches -= 1
                if matches <= 0:
                    return True
            else:
                # No match
                return False

        if FilterDirectives.POSTER_MATCHES_REGEX in entry:
            if entry[FilterDirectives.POSTER_MATCHES_REGEX].match(poster):
                matches -= 1
                if matches <= 0:
                    return True
            else:
                # No match
                return False

        if FilterDirectives.POSTER_STARTS_WITH in entry:
            if entry[FilterDirectives.POSTER_STARTS_WITH] == \
               poster[0:len(entry[FilterDirectives.POSTER_STARTS_WITH])]:
                matches -= 1
                if matches <= 0:
                    return True
            else:
                # No match
                return False

        if FilterDirectives.POSTER_ENDS_WITH in entry:
            if entry[FilterDirectives.POSTER_ENDS_WITH] == \
               poster[-len(entry[FilterDirectives.POSTER_ENDS_WITH]):]:
                matches -= 1
                if matches <= 0:
                    return True
            else:
                # No match
                return False

        if FilterDirectives.LINE_COUNT_GREATER_THAN in entry:
            if lines > entry[FilterDirectives.LINE_COUNT_GREATER_THAN]:
                matches -= 1
                if matches <= 0:
                    return True
            else:
                # No match
                return False

        if FilterDirectives.LINE_COUNT_LESS_THAN in entry:
            if lines < entry[FilterDirectives.LINE_COUNT_LESS_THAN]:
                matches -= 1
                if matches <= 0:
                    return True
            else:
                # No match
                return False

        if FilterDirectives.SIZE_GREATER_THAN in entry:
            if size > entry[FilterDirectives.SIZE_GREATER_THAN]:
                matches -= 1
                if matches <= 0:
                    return True
            else:
                # No match
                return False

        if FilterDirectives.SIZE_LESS_THAN in entry:
            if size < entry[FilterDirectives.SIZE_LESS_THAN]:
                matches -= 1
                if matches <= 0:
                    return True
            else:
                # No match
                return False

        if FilterDirectives.DATE_GREATER_THAN in entry:
            if date > entry[FilterDirectives.DATE_GREATER_THAN]:
                matches -= 1
                if matches <= 0:
                    return True
            else:
                # No match
                return False

        if FilterDirectives.DATE_LESS_THAN in entry:
            if date < entry[FilterDirectives.DATE_LESS_THAN]:
                matches -= 1
                if matches <= 0:
                    return True
            else:
                # No match
                return False

        if FilterDirectives.XPOST_COUNT_GREATER_THAN in entry:
            if len(xgroups) > entry[FilterDirectives.XPOST_COUNT_GREATER_THAN]:
                matches -= 1
                if matches <= 0:
                    return True
            else:
                # No match
                return False

        if FilterDirectives.XPOST_COUNT_LESS_THAN in entry:
            if len(xgroups) < entry[FilterDirectives.XPOST_COUNT_LESS_THAN]:
                matches -= 1
                if matches <= 0:
                    return True
            else:
                # No match
                return False

        return matches <= 0


    def whitelist(self, group, poster, date, subject, size, lines, xgroups,
                  *args, **kwargs):
        """
        Returns true if entry matches a filter identifed on the whitelist
        """

        whitelist = self.lazy_re_fetch(group)[FilterListCode.WHITELIST]
        if not whitelist:
            # No whitelist entries to apply
            return False

        for entry in iter(whitelist):
            if self._match(entry, poster, date, subject, size, lines, xgroups,
                           *args, **kwargs):
                # We can stop on our first match
                return True

        # no match
        return False


    def blacklist(self, group, poster, date, subject, size, lines, xgroups,
                  *args, **kwargs):
        """
        Returns true if entry matches a filter identifed on the blacklist
        """

        blacklist = self.lazy_re_fetch(group)[FilterListCode.BLACKLIST]
        if not blacklist:
            # No blacklist entries to apply
            return False

        for entry in iter(blacklist):
            if self._match(entry, poster, date, subject, size, lines, xgroups,
                           *args, **kwargs):
                # We can stop on our first match
                return True

        # no match
        return False


    def score(self, group, poster, date, subject, size, lines, xgroups,
              *args, **kwargs):
        """
        Scores content based on what is passed in; zero (0) is always returned
        since that is the default score to assign to anything if nothing
        was matched otherwise the calculated score is returned.
        """

        scorelist = self.lazy_re_fetch(group)[FilterListCode.SCORELIST]
        if not scorelist:
            # No scores applied
            return 0

        # Our score counter
        score = 0

        for entry in iter(scorelist):

            new_score = entry.get(FilterDirectives.SCORE)
            if not new_score:
                # No score defined
                continue

            if self._match(entry, poster, date, subject, size, lines, xgroups,
                           *args, **kwargs):

                # (no stopping); apend score and keep going
                score += new_score

        # not all matches
        return score
