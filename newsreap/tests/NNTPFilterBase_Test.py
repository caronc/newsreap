# -*- encoding: utf-8 -*-
#
# A base testing class/library to test the Filters
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

import sys
if 'threading' in sys.modules:
    #  gevent patching since pytests import
    #  the sys library before we do.
    del sys.modules['threading']

import gevent.monkey
gevent.monkey.patch_all()

import pytz
import re
from os.path import dirname
from os.path import join
from os.path import abspath
from copy import deepcopy as copy
from datetime import datetime
from datetime import timedelta

try:
    from tests.TestBase import TestBase

except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

from lib.Utils import strsize_to_bytes
from lib.NNTPFilterBase import NNTPFilterBase
from lib.NNTPFilterBase import FilterDirectives


class NNTPFilterBase_Test(TestBase):

    def setUp(self):
        """
        Grab a few more things from the config
        """
        super(NNTPFilterBase_Test, self).setUp()

        # Create a template entry we can clone from to make
        # thing easier to manipulate in each individual test
        self.template_entry = {
            'id': 'testuniqueid',
            'article_no': 1,
            'poster': 'Unknown Poster <strange@email.com>',
            'date': datetime(2000, 1, 1, 0, 0, 0, tzinfo=pytz.UTC),
            'subject': 'What.A.Great.Show (1/1) "what.a.great.show.mkv" Yenc (1/1)',
            'size': strsize_to_bytes('25M'),
            'lines': 3000,
            'group': 'alt.binaries.test',
            'xgroups': { 'alt.binaries.ohwell': 2, 'alt.binaries.ohwell2': 3, },
        }

    def test_scorelist_prep_errors(self):
        """
        Test error handling when appending to a scorelist
        """
        fb = NNTPFilterBase()
        # We're starting with an empty list
        assert len(fb._scorelist) == 0

        # Determine that it was not successfully added
        assert len(fb._scorelist) == 0

        # Handling Invalid Group
        try:
            fb.scorelist_append(None, {
                FilterDirectives.DESCRIPTION: "Bad Entry (no group)",
                FilterDirectives.SCORE: -50,
                FilterDirectives.SUBJECT_STARTS_WITH: "Not how it's done",
            })
            # We flat out fail if we make it here
            assert False

        except ValueError:
            # Correctly Denied Filter
            assert True

        # Determine that it was not successfully added
        assert len(fb._scorelist) == 0

        # Handling Invalid Regular Expression
        try:
            fb.scorelist_append('alt.((((.*', {
                FilterDirectives.DESCRIPTION: "Bad Entry (bad regex)",
                FilterDirectives.SCORE: -50,
                FilterDirectives.SUBJECT_STARTS_WITH: "Not how it's done",
            })
            # We flat out fail if we make it here
            assert False

        except ValueError:
            # Correctly Denied Filter
            assert True

        # Determine that it was not successfully added
        assert len(fb._scorelist) == 0

        # Handling Missing Score
        try:
            fb.scorelist_append('ignore.whatever', {
                FilterDirectives.DESCRIPTION: "Bad Entry (no score)",
                FilterDirectives.SUBJECT_STARTS_WITH: "Not how it's done",
            })
            # We flat out fail if we make it here
            assert False

        except ValueError:
            # Correctly Denied Filter
            assert True

        # Determine that it was not successfully added
        assert len(fb._scorelist) == 0

        # Handling Missing Filter(s)
        try:
            fb.scorelist_append('ignore.whatever', {
                FilterDirectives.DESCRIPTION: "Bad Entry (no filters)",
                FilterDirectives.SCORE: -50,
            })
            # We flat out fail if we make it here
            assert False

        except ValueError:
            # Correctly Denied Filter
            assert True

        # Determine that it was not successfully added
        assert len(fb._scorelist) == 0

        # Handling Invalid Regular Expression for Subject
        try:
            fb.scorelist_append('ignore.whatever', {
                FilterDirectives.DESCRIPTION: "Bad Entry (invalid re)",
                FilterDirectives.SCORE: -50,
                FilterDirectives.SUBJECT_MATCHES_REGEX: '*.))))',
            })
            # We flat out fail if we make it here
            assert False

        except ValueError:
            # Correctly Denied Filter
            assert True

        # Determine that it was not successfully added
        assert len(fb._scorelist) == 0

        # Handling Invalid Regular Expression for Poster
        try:
            fb.scorelist_append('ignore.whatever', {
                FilterDirectives.DESCRIPTION: "Bad Entry (invalid re)",
                FilterDirectives.SCORE: -50,
                FilterDirectives.POSTER_MATCHES_REGEX: '*.))))',
            })
            # We flat out fail if we make it here
            assert False

        except ValueError:
            # Correctly Denied Filter
            assert True

        # Determine that it was not successfully added
        assert len(fb._scorelist) == 0


    def test_score_subject(self):
        """
        Score Testing for Subject
        """
        entry = copy(self.template_entry)

        fb = NNTPFilterBase()
        # List initializes to being empty
        assert len(fb._scorelist) == 0

        fb.scorelist_append('alt.regex.*', {
            FilterDirectives.DESCRIPTION: "Test Regex (string)",
            FilterDirectives.SCORE: 10,
            # This will automatically get compiled
            FilterDirectives.SUBJECT_MATCHES_REGEX: '.*\.mkv.*',
        })

        fb.scorelist_append('alt.regex.*', {
            FilterDirectives.DESCRIPTION: "Test Regex (compiled)",
            FilterDirectives.SCORE: 20,
            # Here we provide it already compiled
            FilterDirectives.SUBJECT_MATCHES_REGEX:
                re.compile('.*\.(avi|mkv).*'),
        })

        fb.scorelist_append('alt.startswith.*', {
            FilterDirectives.DESCRIPTION: "Test StartsWith",
            FilterDirectives.SCORE: 50,
            FilterDirectives.SUBJECT_STARTS_WITH: 'What.A.Great.Show',
        })

        fb.scorelist_append('alt.endswith.*', {
            FilterDirectives.DESCRIPTION: "Test EndsWith",
            FilterDirectives.SCORE: -50,
            FilterDirectives.SUBJECT_ENDS_WITH: '(1/1)',
        })

        # hash table always starts empty and is populated on demand
        assert len(fb._regex_hash) == 0
        assert fb.score(**entry) == 0

        # check our hash table
        assert len(fb._regex_hash) == 1

        entry['group'] = 'alt.regex.group'

        assert fb.score(**entry) == 30

        # our group is differnet so we have a new hash entry
        assert len(fb._regex_hash) == 2

        # Entry By Name
        entry['group'] = 'alt.startswith.group'
        assert fb.score(**entry) == 50

        # we should have 3 hashes now since we've queried 3 groups
        assert len(fb._regex_hash) == 3

        # Appending a new entry resets the hash
        fb.scorelist_append('alt.startswith.*', {
            FilterDirectives.DESCRIPTION: "Test StartsWith",
            FilterDirectives.SCORE: -10,
            FilterDirectives.SUBJECT_STARTS_WITH: 'What.A',
        })
        assert len(fb._regex_hash) == 0

        # Test Scoring; we match on 2 entries we've added now
        assert fb.score(**entry) == 40

        # we'll have hashed the new table
        assert len(fb._regex_hash) == 1

        entry['group'] = 'alt.endswith.something'
        assert fb.score(**entry) == -50


    def test_score_poster(self):
        """
        Score Testing for Poster
        """
        entry = copy(self.template_entry)

        fb = NNTPFilterBase()
        # List initializes to being empty
        assert len(fb._scorelist) == 0

        fb.scorelist_append('alt.regex.*', {
            FilterDirectives.DESCRIPTION: "Test Regex (string)",
            FilterDirectives.SCORE: 15,
            FilterDirectives.POSTER_MATCHES_REGEX: '.*inkle Beans.*',
        })

        fb.scorelist_append('alt.regex.*', {
            FilterDirectives.DESCRIPTION: "Test Regex (compiled)",
            FilterDirectives.SCORE: 35,
            FilterDirectives.POSTER_MATCHES_REGEX:
            re.compile('.*binkle@example.com.*'),
        })

        fb.scorelist_append('alt.startswith.*', {
            FilterDirectives.DESCRIPTION: "Test StartsWith",
            FilterDirectives.SCORE: -100,
            FilterDirectives.POSTER_STARTS_WITH: 'Unknown Poster',
        })

        fb.scorelist_append('alt.endswith.*', {
            FilterDirectives.DESCRIPTION: "Test EndsWith",
            FilterDirectives.SCORE: 150,
            FilterDirectives.POSTER_ENDS_WITH: 'email.com>',
        })

        # hash table always starts empty and is populated on demand
        assert len(fb._regex_hash) == 0
        assert fb.score(**entry) == 0

        # check our hash table
        assert len(fb._regex_hash) == 1

        # skew the entry a bit so we can test other matches
        entry['poster'] = 'Binkle Beans <binkle@example.com>'
        entry['group'] = 'alt.regex.group'

        # We now match both of our scoring rules
        assert fb.score(**entry) == 50

        # our group is differnet so we have a new hash entry
        assert len(fb._regex_hash) == 2

        # switch over to test startswith
        entry['group'] = 'alt.endswith.group'
        entry['poster'] = 'Unknown Poster <strange@email.com>'

        # We'll match our entry
        assert fb.score(**entry) == 150

        # we should have 3 hashes now since we've queried 3 groups
        assert len(fb._regex_hash) == 3

        entry['group'] = 'alt.startswith.group'
        # test startswith
        assert fb.score(**entry) == -100

        # we should have 4 hashes now since we've queried 4 groups
        assert len(fb._regex_hash) == 4

        # Appending a new entry resets the hash
        fb.scorelist_append('alt.startswith.*', {
            FilterDirectives.DESCRIPTION: "Test StartsWith",
            FilterDirectives.SCORE: -10,
            FilterDirectives.SUBJECT_STARTS_WITH: 'What.A',
        })
        assert len(fb._regex_hash) == 0

        # Test Scoring; we match on 2 entries we've added now
        assert fb.score(**entry) == -110

        # we'll have hashed the new table
        assert len(fb._regex_hash) == 1


    def test_score_line_count(self):
        """
        Score Testing for Line Count
        """
        entry = copy(self.template_entry)

        fb = NNTPFilterBase()
        # List initializes to being empty
        assert len(fb._scorelist) == 0

        fb.scorelist_append('alt.greater.than.*', {
            FilterDirectives.DESCRIPTION: "Test Greater Than",
            FilterDirectives.SCORE: 50,
            FilterDirectives.LINE_COUNT_GREATER_THAN:
                self.template_entry['lines']
        })

        fb.scorelist_append('alt.less.than.*', {
            FilterDirectives.DESCRIPTION: "Test Less Than",
            FilterDirectives.SCORE: -50,
            FilterDirectives.LINE_COUNT_LESS_THAN:
                self.template_entry['lines']
        })

        # We aren't in the right group so we shouldn't match
        assert fb.score(**entry) == 0

        # check our hash table
        assert len(fb._regex_hash) == 1

        entry['group'] = 'alt.greater.than.'

        # We will still fail even now because we are neither greater
        # or less; we're the same
        assert fb.score(**entry) == 0

        # our hash will change to 2
        assert len(fb._regex_hash) == 2

        entry['group'] = 'alt.less.than.'

        # The same situation applies to the less than group too
        assert fb.score(**entry) == 0

        # our hash will change to 3
        assert len(fb._regex_hash) == 3

        # If we subtract 1 to our base
        entry['lines'] = self.template_entry['lines'] - 1

        # Now we should match our less than case
        assert fb.score(**entry) == -50

        entry['group'] = 'alt.greater.than.'

        # however we are still not greater than
        assert fb.score(**entry) == 0

        # If we add 1 to our base
        entry['lines'] = self.template_entry['lines'] + 1

        # Now we should match
        assert fb.score(**entry) == 50

        entry['group'] = 'alt.less.than.'

        # But not the other way
        assert fb.score(**entry) == 0

        # our group never changed; so we should have no hash update
        assert len(fb._regex_hash) == 3


    def test_score_size(self):
        """
        Score Testing for Size
        """
        entry = copy(self.template_entry)

        fb = NNTPFilterBase()
        # List initializes to being empty
        assert len(fb._scorelist) == 0

        fb.scorelist_append('alt.greater.than.*', {
            FilterDirectives.DESCRIPTION: "Test Greater Than",
            FilterDirectives.SCORE: 50,
            FilterDirectives.SIZE_GREATER_THAN:
                self.template_entry['size']
        })

        fb.scorelist_append('alt.less.than.*', {
            FilterDirectives.DESCRIPTION: "Test Less Than",
            FilterDirectives.SCORE: -50,
            FilterDirectives.SIZE_LESS_THAN:
                self.template_entry['size']
        })

        # We aren't in the right group so we shouldn't match
        assert fb.score(**entry) == 0

        # check our hash table
        assert len(fb._regex_hash) == 1

        entry['group'] = 'alt.greater.than.'

        # We will still fail even now because we are neither greater
        # or less; we're the same
        assert fb.score(**entry) == 0

        # our hash will change to 2
        assert len(fb._regex_hash) == 2

        entry['group'] = 'alt.less.than.'

        # The same situation applies to the less than group too
        assert fb.score(**entry) == 0

        # our hash will change to 3
        assert len(fb._regex_hash) == 3

        # If we subtract 1 to our base
        entry['size'] = self.template_entry['size'] - 1

        # Now we should match our less than case
        assert fb.score(**entry) == -50

        entry['group'] = 'alt.greater.than.'

        # however we are still not greater than
        assert fb.score(**entry) == 0

        # If we add 1 to our base
        entry['size'] = self.template_entry['size'] + 1

        # Now we should match
        assert fb.score(**entry) == 50

        entry['group'] = 'alt.less.than.'

        # But not the other way
        assert fb.score(**entry) == 0

        # our group never changed; so we should have no hash update
        assert len(fb._regex_hash) == 3


    def test_score_date(self):
        """
        Score Testing for Date
        """
        entry = copy(self.template_entry)

        fb = NNTPFilterBase()
        # List initializes to being empty
        assert len(fb._scorelist) == 0

        fb.scorelist_append('alt.greater.than.*', {
            FilterDirectives.DESCRIPTION: "Test Greater Than",
            FilterDirectives.SCORE: 50,
            FilterDirectives.DATE_GREATER_THAN:
                self.template_entry['date']
        })

        fb.scorelist_append('alt.less.than.*', {
            FilterDirectives.DESCRIPTION: "Test Less Than",
            FilterDirectives.SCORE: -50,
            FilterDirectives.DATE_LESS_THAN:
                self.template_entry['date']
        })

        # We aren't in the right group so we shouldn't match
        assert fb.score(**entry) == 0

        # check our hash table
        assert len(fb._regex_hash) == 1

        entry['group'] = 'alt.greater.than.'

        # We will still fail even now because we are neither greater
        # or less; we're the same
        assert fb.score(**entry) == 0

        # our hash will change to 2
        assert len(fb._regex_hash) == 2

        entry['group'] = 'alt.less.than.'

        # The same situation applies to the less than group too
        assert fb.score(**entry) == 0

        # our hash will change to 3
        assert len(fb._regex_hash) == 3

        # If we subtract 1 to our base
        entry['date'] = self.template_entry['date'] - timedelta(seconds=1)

        # Now we should match our less than case
        assert fb.score(**entry) == -50

        entry['group'] = 'alt.greater.than.'

        # however we are still not greater than
        assert fb.score(**entry) == 0

        # If we add 1 to our base
        entry['date'] = self.template_entry['date'] + timedelta(seconds=1)

        # Now we should match
        assert fb.score(**entry) == 50

        entry['group'] = 'alt.less.than.'

        # But not the other way
        assert fb.score(**entry) == 0

        # our group never changed; so we should have no hash update
        assert len(fb._regex_hash) == 3


    def test_score_xgroups(self):
        """
        Score Testing for Cross Posts Count
        """
        entry = copy(self.template_entry)

        fb = NNTPFilterBase()
        # List initializes to being empty
        assert len(fb._scorelist) == 0

        fb.scorelist_append('alt.greater.than.*', {
            FilterDirectives.DESCRIPTION: "Test Greater Than",
            FilterDirectives.SCORE: 50,
            FilterDirectives.XPOST_COUNT_GREATER_THAN:
                len(self.template_entry['xgroups']),
        })

        fb.scorelist_append('alt.less.than.*', {
            FilterDirectives.DESCRIPTION: "Test Less Than",
            FilterDirectives.SCORE: -50,
            FilterDirectives.XPOST_COUNT_LESS_THAN:
                len(self.template_entry['xgroups']),
        })

        # We aren't in the right group so we shouldn't match
        assert fb.score(**entry) == 0

        # check our hash table
        assert len(fb._regex_hash) == 1

        entry['group'] = 'alt.greater.than.'

        # We will still fail even now because we are neither greater
        # or less; we're the same
        assert fb.score(**entry) == 0

        # our hash will change to 2
        assert len(fb._regex_hash) == 2

        entry['group'] = 'alt.less.than.'

        # The same situation applies to the less than group too
        assert fb.score(**entry) == 0

        # our hash will change to 3
        assert len(fb._regex_hash) == 3

        # If we subtract 1 to our base
        entry['xgroups'].pop(entry['xgroups'].keys()[0])

        # Now we should match our less than case
        assert fb.score(**entry) == -50

        entry['group'] = 'alt.greater.than.'

        # however we are still not greater than
        assert fb.score(**entry) == 0

        # If we add 1 to our base
        entry['xgroups'] = dict(
            self.template_entry['xgroups'].items() + \
            [('%s.another' % self.template_entry['xgroups'].keys()[0], 4),],
        )

        # Now we should match
        assert fb.score(**entry) == 50

        entry['group'] = 'alt.less.than.'

        # But not the other way
        assert fb.score(**entry) == 0

        # our group never changed; so we should have no hash update
        assert len(fb._regex_hash) == 3


    def test_blacklist_prep_errors(self):
        """
        Test error handling when appending to a blacklist
        """

        fb = NNTPFilterBase()
        # We're starting with an empty list
        assert len(fb._blacklist) == 0

        # Handling Invalid Regular Expression
        try:
            fb.blacklist_append('alt.((((.*', {
                FilterDirectives.DESCRIPTION: "Bad Entry (bad regex)",
                FilterDirectives.SUBJECT_STARTS_WITH: "Not how it's done",
            })
            # We flat out fail if we make it here
            assert False

        except ValueError:
            # Correctly Denied Filter
            assert True

        # Determine that it was not successfully added
        assert len(fb._blacklist) == 0

        # Handling Missing Filter(s)
        try:
            fb.blacklist_append('ignore.whatever', {
                FilterDirectives.DESCRIPTION: "Bad Entry (no filters)",
            })
            # We flat out fail if we make it here
            assert False

        except ValueError:
            # Correctly Denied Filter
            assert True

        # Determine that it was not successfully added
        assert len(fb._blacklist) == 0


    def test_blacklist_subject(self):
        """
        Blacklist Testing for Subject
        """
        entry = copy(self.template_entry)

        fb = NNTPFilterBase()
        # List initializes to being empty
        assert len(fb._blacklist) == 0

        fb.blacklist_append('alt.regex.*', {
            FilterDirectives.DESCRIPTION: "Test Regex (string)",
            # This will automatically get compiled (case-insensitive)
            FilterDirectives.SUBJECT_MATCHES_REGEX: '.*\.mkv.*',
        })

        fb.blacklist_append('alt.case.sensitive.*', {
            FilterDirectives.DESCRIPTION: "Test Regex (compiled)",
            # Here we provide it already compiled
            FilterDirectives.SUBJECT_MATCHES_REGEX:
                # compiled without case sensitive switch
                # This plays an important note
                re.compile('.*\.(avi|MkV).*'),
        })

        fb.blacklist_append('alt.startswith.*', {
            FilterDirectives.DESCRIPTION: "Test StartsWith",
            FilterDirectives.SUBJECT_STARTS_WITH: 'What.A.Great.Show',
        })

        fb.blacklist_append('alt.endswith.*', {
            FilterDirectives.DESCRIPTION: "Test EndsWith",
            FilterDirectives.SUBJECT_ENDS_WITH: '(1/1)',
        })

        # hash table always starts empty and is populated on demand
        assert len(fb._regex_hash) == 0
        assert fb.blacklist(**entry) == False

        # check our hash table
        assert len(fb._regex_hash) == 1

        entry['group'] = 'alt.regex.group'

        assert fb.blacklist(**entry) == True

        # our group is differnet so we have a new hash entry
        assert len(fb._regex_hash) == 2

        entry['subject'] = re.sub('.mkv', '.mKv',
                                  self.template_entry['subject'])

        # We're case insensitive; so this will still match
        assert fb.blacklist(**entry) == True

        # Same group; so hash shouldn't change
        assert len(fb._regex_hash) == 2

        # Entry By Name
        entry['group'] = 'alt.startswith.group'
        assert fb.blacklist(**entry) == True

        # we should have 3 hashes now since we've queried 3 groups
        assert len(fb._regex_hash) == 3

        # Appending a new entry resets the hash
        fb.blacklist_append('alt.startswith.*', {
            FilterDirectives.DESCRIPTION: "Test StartsWith",
            FilterDirectives.SUBJECT_STARTS_WITH: 'What.A',
        })
        assert len(fb._regex_hash) == 0

        entry['group'] = 'alt.endswith.something'
        assert fb.blacklist(**entry) == True

        # we'll have hashed the new table
        assert len(fb._regex_hash) == 1

        entry['group'] = 'alt.case.sensitive.regex.group'
        # This fails because we don't know the mKv extension
        assert fb.blacklist(**entry) == False

        # our hash should increment
        assert len(fb._regex_hash) == 2

        entry['subject'] = re.sub('.mkv', '.MkV',
                                  self.template_entry['subject'])

        # We're case insensitive; so this will match now
        assert fb.blacklist(**entry) == True

        # So will this
        entry['subject'] = re.sub('.mkv', '.avi',
                                  self.template_entry['subject'])
        assert fb.blacklist(**entry) == True

        # our hash should have never changed
        assert len(fb._regex_hash) == 2


    def test_blacklist_poster(self):
        """
        Blacklist Testing for Poster
        """
        entry = copy(self.template_entry)

        fb = NNTPFilterBase()
        # List initializes to being empty
        assert len(fb._blacklist) == 0

        fb.blacklist_append('alt.regex.*', {
            FilterDirectives.DESCRIPTION: "Test Regex (string)",
            # This will automatically get compiled (case-insensitive)
            FilterDirectives.POSTER_MATCHES_REGEX: '.*StrAngE@email.com.*',
        })

        fb.blacklist_append('alt.case.sensitive.*', {
            FilterDirectives.DESCRIPTION: "Test Regex (compiled)",
            # Here we provide it already compiled
            FilterDirectives.POSTER_MATCHES_REGEX:
                # compiled without case sensitive switch
                # This plays an important note
                re.compile('.*(UnKnown|pOsteR).*'),
        })

        fb.blacklist_append('alt.startswith.*', {
            FilterDirectives.DESCRIPTION: "Test StartsWith",
            FilterDirectives.POSTER_STARTS_WITH: 'Unknown ',
        })

        fb.blacklist_append('alt.endswith.*', {
            FilterDirectives.DESCRIPTION: "Test EndsWith",
            FilterDirectives.POSTER_ENDS_WITH: '.com>',
        })

        # hash table always starts empty and is populated on demand
        assert len(fb._regex_hash) == 0
        assert fb.blacklist(**entry) == False

        # check our hash table
        assert len(fb._regex_hash) == 1

        entry['group'] = 'alt.regex.group'

        assert fb.blacklist(**entry) == True

        # our group is differnet so we have a new hash entry
        assert len(fb._regex_hash) == 2

        entry['poster'] = re.sub('Unknown', 'UnKnoWN',
                                 self.template_entry['poster'])

        # We're case insensitive; so this will still match
        assert fb.blacklist(**entry) == True

        # Same group; so hash shouldn't change
        assert len(fb._regex_hash) == 2

        # Entry By Name
        entry['group'] = 'alt.startswith.group'
        assert fb.blacklist(**entry) == False

        # Now put the name back to test it
        entry['poster'] = self.template_entry['poster']
        assert fb.blacklist(**entry) == True

        # we should have 3 hashes now since we've queried 3 groups
        assert len(fb._regex_hash) == 3

        # Appending a new entry resets the hash
        fb.blacklist_append('alt.startswith.*', {
            FilterDirectives.DESCRIPTION: "Test StartsWith",
            FilterDirectives.POSTER_STARTS_WITH: 'UnKnoWN',
        })
        entry['poster'] = self.template_entry['poster']

        assert len(fb._regex_hash) == 0

        assert fb.blacklist(**entry) == True
        entry['poster'] = re.sub('Unknown', 'UnKnoWN',
                                 self.template_entry['poster'])

        # we'll have hashed the new table
        assert len(fb._regex_hash) == 1

        entry['group'] = 'alt.endswith.something'
        assert fb.blacklist(**entry) == True

        # we'll have hashed the new table
        assert len(fb._regex_hash) == 2

        entry['group'] = 'alt.case.sensitive.regex.group'
        entry['poster'] = self.template_entry['poster']

        # This fails because we don't know the 'Unknown' keyword
        assert fb.blacklist(**entry) == False

        # our hash should increment
        assert len(fb._regex_hash) == 3

        # Adjust it...
        entry['poster'] = re.sub('Unknown', 'UnKnown',
                                 self.template_entry['poster'])

        # We're case insensitive; so this will match now
        assert fb.blacklist(**entry) == True

        # This will work too
        entry['poster'] = re.sub('Poster', 'pOsteR',
                                 self.template_entry['poster'])
        assert fb.blacklist(**entry) == True

        # our hash should have never changed
        assert len(fb._regex_hash) == 3


    def test_blacklist_line_count(self):
        """
        Blacklist Testing for Line Count
        """
        entry = copy(self.template_entry)

        fb = NNTPFilterBase()
        # List initializes to being empty
        assert len(fb._blacklist) == 0

        fb.blacklist_append('alt.greater.than.*', {
            FilterDirectives.DESCRIPTION: "Test Greater Than",
            FilterDirectives.LINE_COUNT_GREATER_THAN:
                self.template_entry['lines']
        })

        fb.blacklist_append('alt.less.than.*', {
            FilterDirectives.DESCRIPTION: "Test Less Than",
            FilterDirectives.LINE_COUNT_LESS_THAN:
                self.template_entry['lines']
        })

        # We aren't in the right group so we shouldn't match
        assert fb.blacklist(**entry) == False

        # check our hash table
        assert len(fb._regex_hash) == 1

        entry['group'] = 'alt.greater.than.'

        # We will still fail even now because we are neither greater
        # or less; we're the same
        assert fb.blacklist(**entry) == False

        # our hash will change to 2
        assert len(fb._regex_hash) == 2

        entry['group'] = 'alt.less.than.'

        # The same situation applies to the less than group too
        assert fb.blacklist(**entry) == False

        # our hash will change to 3
        assert len(fb._regex_hash) == 3

        # If we subtract 1 to our base
        entry['lines'] = self.template_entry['lines'] - 1

        # Now we should match our less than case
        assert fb.blacklist(**entry) == True

        entry['group'] = 'alt.greater.than.'

        # however we are still not greater than
        assert fb.blacklist(**entry) == False

        # If we add 1 to our base
        entry['lines'] = self.template_entry['lines'] + 1

        # Now we should match
        assert fb.blacklist(**entry) == True

        entry['group'] = 'alt.less.than.'

        # But not the other way
        assert fb.blacklist(**entry) == False

        # our group never changed; so we should have no hash update
        assert len(fb._regex_hash) == 3


    def test_blacklist_size(self):
        """
        Blacklist Testing for Size
        """
        entry = copy(self.template_entry)

        fb = NNTPFilterBase()
        # List initializes to being empty
        assert len(fb._blacklist) == False

        fb.blacklist_append('alt.greater.than.*', {
            FilterDirectives.DESCRIPTION: "Test Greater Than",
            FilterDirectives.SIZE_GREATER_THAN:
                self.template_entry['size']
        })

        fb.blacklist_append('alt.less.than.*', {
            FilterDirectives.DESCRIPTION: "Test Less Than",
            FilterDirectives.SIZE_LESS_THAN:
                self.template_entry['size']
        })

        # We aren't in the right group so we shouldn't match
        assert fb.blacklist(**entry) == False

        # check our hash table
        assert len(fb._regex_hash) == 1

        entry['group'] = 'alt.greater.than.'

        # We will still fail even now because we are neither greater
        # or less; we're the same
        assert fb.blacklist(**entry) == False

        # our hash will change to 2
        assert len(fb._regex_hash) == 2

        entry['group'] = 'alt.less.than.'

        # The same situation applies to the less than group too
        assert fb.blacklist(**entry) == False

        # our hash will change to 3
        assert len(fb._regex_hash) == 3

        # If we subtract 1 to our base
        entry['size'] = self.template_entry['size'] - 1

        # Now we should match our less than case
        assert fb.blacklist(**entry) == True

        entry['group'] = 'alt.greater.than.'

        # however we are still not greater than
        assert fb.blacklist(**entry) == False

        # If we add 1 to our base
        entry['size'] = self.template_entry['size'] + 1

        # Now we should match
        assert fb.blacklist(**entry) == True

        entry['group'] = 'alt.less.than.'

        # But not the other way
        assert fb.blacklist(**entry) == False

        # our group never changed; so we should have no hash update
        assert len(fb._regex_hash) == 3


    def test_blacklist_date(self):
        """
        Blacklist Testing for Date
        """
        entry = copy(self.template_entry)

        fb = NNTPFilterBase()
        # List initializes to being empty
        assert len(fb._blacklist) == False

        fb.blacklist_append('alt.greater.than.*', {
            FilterDirectives.DESCRIPTION: "Test Greater Than",
            FilterDirectives.DATE_GREATER_THAN:
                self.template_entry['date']
        })

        fb.blacklist_append('alt.less.than.*', {
            FilterDirectives.DESCRIPTION: "Test Less Than",
            FilterDirectives.DATE_LESS_THAN:
                self.template_entry['date']
        })

        # We aren't in the right group so we shouldn't match
        assert fb.blacklist(**entry) == False

        # check our hash table
        assert len(fb._regex_hash) == 1

        entry['group'] = 'alt.greater.than.'

        # We will still fail even now because we are neither greater
        # or less; we're the same
        assert fb.blacklist(**entry) == False

        # our hash will change to 2
        assert len(fb._regex_hash) == 2

        entry['group'] = 'alt.less.than.'

        # The same situation applies to the less than group too
        assert fb.blacklist(**entry) == False

        # our hash will change to 3
        assert len(fb._regex_hash) == 3

        # If we subtract 1 to our base
        entry['date'] = self.template_entry['date'] - timedelta(seconds=1)

        # Now we should match our less than case
        assert fb.blacklist(**entry) == True

        entry['group'] = 'alt.greater.than.'

        # however we are still not greater than
        assert fb.blacklist(**entry) == False

        # If we add 1 to our base
        entry['date'] = self.template_entry['date'] + timedelta(seconds=1)

        # Now we should match
        assert fb.blacklist(**entry) == True

        entry['group'] = 'alt.less.than.'

        # But not the other way
        assert fb.blacklist(**entry) == False

        # our group never changed; so we should have no hash update
        assert len(fb._regex_hash) == 3


    def test_blacklist_xgroups(self):
        """
        Blacklist Testing for Cross Posts Count
        """
        entry = copy(self.template_entry)

        fb = NNTPFilterBase()
        # List initializes to being empty
        assert len(fb._blacklist) == False

        fb.blacklist_append('alt.greater.than.*', {
            FilterDirectives.DESCRIPTION: "Test Greater Than",
            FilterDirectives.XPOST_COUNT_GREATER_THAN:
                len(self.template_entry['xgroups']),
        })

        fb.blacklist_append('alt.less.than.*', {
            FilterDirectives.DESCRIPTION: "Test Less Than",
            FilterDirectives.XPOST_COUNT_LESS_THAN:
                len(self.template_entry['xgroups']),
        })

        # We aren't in the right group so we shouldn't match
        assert fb.blacklist(**entry) == False

        # check our hash table
        assert len(fb._regex_hash) == 1

        entry['group'] = 'alt.greater.than.'

        # We will still fail even now because we are neither greater
        # or less; we're the same
        assert fb.blacklist(**entry) == False

        # our hash will change to 2
        assert len(fb._regex_hash) == 2

        entry['group'] = 'alt.less.than.'

        # The same situation applies to the less than group too
        assert fb.blacklist(**entry) == False

        # our hash will change to 3
        assert len(fb._regex_hash) == 3

        # If we subtract 1 to our base
        entry['xgroups'].pop(entry['xgroups'].keys()[0])

        # Now we should match our less than case
        assert fb.blacklist(**entry) == True

        entry['group'] = 'alt.greater.than.'

        # however we are still not greater than
        assert fb.blacklist(**entry) == False

        # If we add 1 to our base
        entry['xgroups'] = dict(
            self.template_entry['xgroups'].items() + \
            [('%s.another' % self.template_entry['xgroups'].keys()[0], 4),],
        )

        # Now we should match
        assert fb.blacklist(**entry) == True

        entry['group'] = 'alt.less.than.'

        # But not the other way
        assert fb.blacklist(**entry) == False

        # our group never changed; so we should have no hash update
        assert len(fb._regex_hash) == 3


    def test_blacklist_score(self):
        """
        Blacklist Testing for Scoring
        """
        entry = copy(self.template_entry)

        fb = NNTPFilterBase()
        # List initializes to being empty
        assert len(fb._blacklist) == False

        fb.blacklist_append('alt.greater.than.*', {
            FilterDirectives.DESCRIPTION: "Test Greater Than",
            FilterDirectives.SCORE_GREATER_THAN: 50,
        })

        fb.blacklist_append('alt.less.than.*', {
            FilterDirectives.DESCRIPTION: "Test Less Than",
            FilterDirectives.SCORE_LESS_THAN: 50,
        })

        # We aren't in the right group so we shouldn't match
        assert fb.blacklist(**entry) == False

        # check our hash table
        assert len(fb._regex_hash) == 1

        entry['group'] = 'alt.greater.than.'

        assert fb.blacklist(**entry) == False

        # our hash will change to 2
        assert len(fb._regex_hash) == 2

        entry['group'] = 'alt.less.than.'

        # By default everything is scored at 0; so this will be good
        assert fb.blacklist(**entry) == True

        # our hash will change to 3
        assert len(fb._regex_hash) == 3

        # Now we'll add a score which will increase our score value
        # to something above 50 (what we're checking)
        fb.scorelist_append('alt.greater.than.*', {
            FilterDirectives.DESCRIPTION: "Test Regex (string)",
            FilterDirectives.SCORE: 51,
            # This will automatically get compiled
            FilterDirectives.SUBJECT_MATCHES_REGEX: '.*\.mkv.*',
        })

        # Now we'll add a score which will increase our score value
        # to something above 50 (what we're checking)
        fb.scorelist_append('alt.less.than.*', {
            FilterDirectives.DESCRIPTION: "Test Regex (string)",
            FilterDirectives.SCORE: 51,
            # This will automatically get compiled
            FilterDirectives.SUBJECT_MATCHES_REGEX: '.*\.mkv.*',
        })


        # our hash will drop to 0
        assert len(fb._regex_hash) == 0

        # But now we meet our score
        entry['group'] = 'alt.greater.than.'
        entry['score'] = fb.score(**entry)
        assert entry['score'] == 51
        assert fb.blacklist(**entry) == True

        # 1 for the hash table after all that
        assert len(fb._regex_hash) == 1

        # Now we should match our less than case
        assert fb.blacklist(**entry) == True

        entry['group'] = 'alt.less.than.'

        # however we are still not greater than
        entry['score'] = 49
        assert fb.blacklist(**entry) == True

        entry['group'] = 'alt.less.than.'
        entry['score'] = fb.score(**entry)
        assert entry['score'] == 51
        assert fb.blacklist(**entry) == False

        # our group never changed; so we should have no hash update
        assert len(fb._regex_hash) == 2


    def test_whitelist_prep_errors(self):
        """
        Test error handling when appending to a whitelist
        """

        fb = NNTPFilterBase()
        # We're starting with an empty list
        assert len(fb._whitelist) == 0

        # Handling Invalid Regular Expression
        try:
            fb.whitelist_append('alt.((((.*', {
                FilterDirectives.DESCRIPTION: "Bad Entry (bad regex)",
                FilterDirectives.SUBJECT_STARTS_WITH: "Not how it's done",
            })
            # We flat out fail if we make it here
            assert False

        except ValueError:
            # Correctly Denied Filter
            assert True

        # Determine that it was not successfully added
        assert len(fb._whitelist) == 0

        # Handling Missing Filter(s)
        try:
            fb.whitelist_append('ignore.whatever', {
                FilterDirectives.DESCRIPTION: "Bad Entry (no filters)",
            })
            # We flat out fail if we make it here
            assert False

        except ValueError:
            # Correctly Denied Filter
            assert True

        # Determine that it was not successfully added
        assert len(fb._whitelist) == 0


    def test_whitelist_subject(self):
        """
        Whitelist Testing for Subject
        """
        entry = copy(self.template_entry)

        fb = NNTPFilterBase()
        # List initializes to being empty
        assert len(fb._whitelist) == 0

        fb.whitelist_append('alt.regex.*', {
            FilterDirectives.DESCRIPTION: "Test Regex (string)",
            # This will automatically get compiled (case-insensitive)
            FilterDirectives.SUBJECT_MATCHES_REGEX: '.*\.mkv.*',
        })

        fb.whitelist_append('alt.case.sensitive.*', {
            FilterDirectives.DESCRIPTION: "Test Regex (compiled)",
            # Here we provide it already compiled
            FilterDirectives.SUBJECT_MATCHES_REGEX:
                # compiled without case sensitive switch
                # This plays an important note
                re.compile('.*\.(avi|MkV).*'),
        })

        fb.whitelist_append('alt.startswith.*', {
            FilterDirectives.DESCRIPTION: "Test StartsWith",
            FilterDirectives.SUBJECT_STARTS_WITH: 'What.A.Great.Show',
        })

        fb.whitelist_append('alt.endswith.*', {
            FilterDirectives.DESCRIPTION: "Test EndsWith",
            FilterDirectives.SUBJECT_ENDS_WITH: '(1/1)',
        })

        # hash table always starts empty and is populated on demand
        assert len(fb._regex_hash) == 0
        assert fb.whitelist(**entry) == False

        # check our hash table
        assert len(fb._regex_hash) == 1

        entry['group'] = 'alt.regex.group'

        assert fb.whitelist(**entry) == True

        # our group is differnet so we have a new hash entry
        assert len(fb._regex_hash) == 2

        entry['subject'] = re.sub('.mkv', '.mKv',
                                  self.template_entry['subject'])

        # We're case insensitive; so this will still match
        assert fb.whitelist(**entry) == True

        # Same group; so hash shouldn't change
        assert len(fb._regex_hash) == 2

        # Entry By Name
        entry['group'] = 'alt.startswith.group'
        assert fb.whitelist(**entry) == True

        # we should have 3 hashes now since we've queried 3 groups
        assert len(fb._regex_hash) == 3

        # Appending a new entry resets the hash
        fb.whitelist_append('alt.startswith.*', {
            FilterDirectives.DESCRIPTION: "Test StartsWith",
            FilterDirectives.SUBJECT_STARTS_WITH: 'What.A',
        })
        assert len(fb._regex_hash) == 0

        entry['group'] = 'alt.endswith.something'
        assert fb.whitelist(**entry) == True

        # we'll have hashed the new table
        assert len(fb._regex_hash) == 1

        entry['group'] = 'alt.case.sensitive.regex.group'
        # This fails because we don't know the mKv extension
        assert fb.whitelist(**entry) == False

        # our hash should increment
        assert len(fb._regex_hash) == 2

        entry['subject'] = re.sub('.mkv', '.MkV',
                                  self.template_entry['subject'])

        # We're case insensitive; so this will match now
        assert fb.whitelist(**entry) == True

        # So will this
        entry['subject'] = re.sub('.mkv', '.avi',
                                  self.template_entry['subject'])
        assert fb.whitelist(**entry) == True

        # our hash should have never changed
        assert len(fb._regex_hash) == 2


    def test_whitelist_poster(self):
        """
        Whitelist Testing for Poster
        """
        entry = copy(self.template_entry)

        fb = NNTPFilterBase()
        # List initializes to being empty
        assert len(fb._whitelist) == 0

        fb.whitelist_append('alt.regex.*', {
            FilterDirectives.DESCRIPTION: "Test Regex (string)",
            # This will automatically get compiled (case-insensitive)
            FilterDirectives.POSTER_MATCHES_REGEX: '.*StrAngE@email.com.*',
        })

        fb.whitelist_append('alt.case.sensitive.*', {
            FilterDirectives.DESCRIPTION: "Test Regex (compiled)",
            # Here we provide it already compiled
            FilterDirectives.POSTER_MATCHES_REGEX:
                # compiled without case sensitive switch
                # This plays an important note
                re.compile('.*(UnKnown|pOsteR).*'),
        })

        fb.whitelist_append('alt.startswith.*', {
            FilterDirectives.DESCRIPTION: "Test StartsWith",
            FilterDirectives.POSTER_STARTS_WITH: 'Unknown ',
        })

        fb.whitelist_append('alt.endswith.*', {
            FilterDirectives.DESCRIPTION: "Test EndsWith",
            FilterDirectives.POSTER_ENDS_WITH: '.com>',
        })

        # hash table always starts empty and is populated on demand
        assert len(fb._regex_hash) == 0
        assert fb.whitelist(**entry) == False

        # check our hash table
        assert len(fb._regex_hash) == 1

        entry['group'] = 'alt.regex.group'

        assert fb.whitelist(**entry) == True

        # our group is differnet so we have a new hash entry
        assert len(fb._regex_hash) == 2

        entry['poster'] = re.sub('Unknown', 'UnKnoWN',
                                 self.template_entry['poster'])

        # We're case insensitive; so this will still match
        assert fb.whitelist(**entry) == True

        # Same group; so hash shouldn't change
        assert len(fb._regex_hash) == 2

        # Entry By Name
        entry['group'] = 'alt.startswith.group'
        assert fb.whitelist(**entry) == False

        # Now put the name back to test it
        entry['poster'] = self.template_entry['poster']
        assert fb.whitelist(**entry) == True

        # we should have 3 hashes now since we've queried 3 groups
        assert len(fb._regex_hash) == 3

        # Appending a new entry resets the hash
        fb.whitelist_append('alt.startswith.*', {
            FilterDirectives.DESCRIPTION: "Test StartsWith",
            FilterDirectives.POSTER_STARTS_WITH: 'UnKnoWN',
        })
        entry['poster'] = self.template_entry['poster']

        assert len(fb._regex_hash) == 0

        assert fb.whitelist(**entry) == True
        entry['poster'] = re.sub('Unknown', 'UnKnoWN',
                                 self.template_entry['poster'])

        # we'll have hashed the new table
        assert len(fb._regex_hash) == 1

        entry['group'] = 'alt.endswith.something'
        assert fb.whitelist(**entry) == True

        # we'll have hashed the new table
        assert len(fb._regex_hash) == 2

        entry['group'] = 'alt.case.sensitive.regex.group'
        entry['poster'] = self.template_entry['poster']

        # This fails because we don't know the 'Unknown' keyword
        assert fb.whitelist(**entry) == False

        # our hash should increment
        assert len(fb._regex_hash) == 3

        # Adjust it...
        entry['poster'] = re.sub('Unknown', 'UnKnown',
                                 self.template_entry['poster'])

        # We're case insensitive; so this will match now
        assert fb.whitelist(**entry) == True

        # This will work too
        entry['poster'] = re.sub('Poster', 'pOsteR',
                                 self.template_entry['poster'])
        assert fb.whitelist(**entry) == True

        # our hash should have never changed
        assert len(fb._regex_hash) == 3


    def test_whitelist_line_count(self):
        """
        Whitelist Testing for Line Count
        """
        entry = copy(self.template_entry)

        fb = NNTPFilterBase()
        # List initializes to being empty
        assert len(fb._whitelist) == 0

        fb.whitelist_append('alt.greater.than.*', {
            FilterDirectives.DESCRIPTION: "Test Greater Than",
            FilterDirectives.LINE_COUNT_GREATER_THAN:
                self.template_entry['lines']
        })

        fb.whitelist_append('alt.less.than.*', {
            FilterDirectives.DESCRIPTION: "Test Less Than",
            FilterDirectives.LINE_COUNT_LESS_THAN:
                self.template_entry['lines']
        })

        # We aren't in the right group so we shouldn't match
        assert fb.whitelist(**entry) == False

        # check our hash table
        assert len(fb._regex_hash) == 1

        entry['group'] = 'alt.greater.than.'

        # We will still fail even now because we are neither greater
        # or less; we're the same
        assert fb.whitelist(**entry) == False

        # our hash will change to 2
        assert len(fb._regex_hash) == 2

        entry['group'] = 'alt.less.than.'

        # The same situation applies to the less than group too
        assert fb.whitelist(**entry) == False

        # our hash will change to 3
        assert len(fb._regex_hash) == 3

        # If we subtract 1 to our base
        entry['lines'] = self.template_entry['lines'] - 1

        # Now we should match our less than case
        assert fb.whitelist(**entry) == True

        entry['group'] = 'alt.greater.than.'

        # however we are still not greater than
        assert fb.whitelist(**entry) == False

        # If we add 1 to our base
        entry['lines'] = self.template_entry['lines'] + 1

        # Now we should match
        assert fb.whitelist(**entry) == True

        entry['group'] = 'alt.less.than.'

        # But not the other way
        assert fb.whitelist(**entry) == False

        # our group never changed; so we should have no hash update
        assert len(fb._regex_hash) == 3


    def test_whitelist_size(self):
        """
        Whitelist Testing for Size
        """
        entry = copy(self.template_entry)

        fb = NNTPFilterBase()
        # List initializes to being empty
        assert len(fb._whitelist) == False

        fb.whitelist_append('alt.greater.than.*', {
            FilterDirectives.DESCRIPTION: "Test Greater Than",
            FilterDirectives.SIZE_GREATER_THAN:
                self.template_entry['size']
        })

        fb.whitelist_append('alt.less.than.*', {
            FilterDirectives.DESCRIPTION: "Test Less Than",
            FilterDirectives.SIZE_LESS_THAN:
                self.template_entry['size']
        })

        # We aren't in the right group so we shouldn't match
        assert fb.whitelist(**entry) == False

        # check our hash table
        assert len(fb._regex_hash) == 1

        entry['group'] = 'alt.greater.than.'

        # We will still fail even now because we are neither greater
        # or less; we're the same
        assert fb.whitelist(**entry) == False

        # our hash will change to 2
        assert len(fb._regex_hash) == 2

        entry['group'] = 'alt.less.than.'

        # The same situation applies to the less than group too
        assert fb.whitelist(**entry) == False

        # our hash will change to 3
        assert len(fb._regex_hash) == 3

        # If we subtract 1 to our base
        entry['size'] = self.template_entry['size'] - 1

        # Now we should match our less than case
        assert fb.whitelist(**entry) == True

        entry['group'] = 'alt.greater.than.'

        # however we are still not greater than
        assert fb.whitelist(**entry) == False

        # If we add 1 to our base
        entry['size'] = self.template_entry['size'] + 1

        # Now we should match
        assert fb.whitelist(**entry) == True

        entry['group'] = 'alt.less.than.'

        # But not the other way
        assert fb.whitelist(**entry) == False

        # our group never changed; so we should have no hash update
        assert len(fb._regex_hash) == 3


    def test_whitelist_date(self):
        """
        Whitelist Testing for Date
        """
        entry = copy(self.template_entry)

        fb = NNTPFilterBase()
        # List initializes to being empty
        assert len(fb._whitelist) == False

        fb.whitelist_append('alt.greater.than.*', {
            FilterDirectives.DESCRIPTION: "Test Greater Than",
            FilterDirectives.DATE_GREATER_THAN:
                self.template_entry['date']
        })

        fb.whitelist_append('alt.less.than.*', {
            FilterDirectives.DESCRIPTION: "Test Less Than",
            FilterDirectives.DATE_LESS_THAN:
                self.template_entry['date']
        })

        # We aren't in the right group so we shouldn't match
        assert fb.whitelist(**entry) == False

        # check our hash table
        assert len(fb._regex_hash) == 1

        entry['group'] = 'alt.greater.than.'

        # We will still fail even now because we are neither greater
        # or less; we're the same
        assert fb.whitelist(**entry) == False

        # our hash will change to 2
        assert len(fb._regex_hash) == 2

        entry['group'] = 'alt.less.than.'

        # The same situation applies to the less than group too
        assert fb.whitelist(**entry) == False

        # our hash will change to 3
        assert len(fb._regex_hash) == 3

        # If we subtract 1 to our base
        entry['date'] = self.template_entry['date'] - timedelta(seconds=1)

        # Now we should match our less than case
        assert fb.whitelist(**entry) == True

        entry['group'] = 'alt.greater.than.'

        # however we are still not greater than
        assert fb.whitelist(**entry) == False

        # If we add 1 to our base
        entry['date'] = self.template_entry['date'] + timedelta(seconds=1)

        # Now we should match
        assert fb.whitelist(**entry) == True

        entry['group'] = 'alt.less.than.'

        # But not the other way
        assert fb.whitelist(**entry) == False

        # our group never changed; so we should have no hash update
        assert len(fb._regex_hash) == 3


    def test_whitelist_xgroups(self):
        """
        Whitelist Testing for Cross Posts Count
        """
        entry = copy(self.template_entry)

        fb = NNTPFilterBase()
        # List initializes to being empty
        assert len(fb._whitelist) == False

        fb.whitelist_append('alt.greater.than.*', {
            FilterDirectives.DESCRIPTION: "Test Greater Than",
            FilterDirectives.XPOST_COUNT_GREATER_THAN:
                len(self.template_entry['xgroups']),
        })

        fb.whitelist_append('alt.less.than.*', {
            FilterDirectives.DESCRIPTION: "Test Less Than",
            FilterDirectives.XPOST_COUNT_LESS_THAN:
                len(self.template_entry['xgroups']),
        })

        # We aren't in the right group so we shouldn't match
        assert fb.whitelist(**entry) == False

        # check our hash table
        assert len(fb._regex_hash) == 1

        entry['group'] = 'alt.greater.than.'

        # We will still fail even now because we are neither greater
        # or less; we're the same
        assert fb.whitelist(**entry) == False

        # our hash will change to 2
        assert len(fb._regex_hash) == 2

        entry['group'] = 'alt.less.than.'

        # The same situation applies to the less than group too
        assert fb.whitelist(**entry) == False

        # our hash will change to 3
        assert len(fb._regex_hash) == 3

        # If we subtract 1 to our base
        entry['xgroups'].pop(entry['xgroups'].keys()[0])

        # Now we should match our less than case
        assert fb.whitelist(**entry) == True

        entry['group'] = 'alt.greater.than.'

        # however we are still not greater than
        assert fb.whitelist(**entry) == False

        # If we add 1 to our base
        entry['xgroups'] = dict(
            self.template_entry['xgroups'].items() + \
            [('%s.another' % self.template_entry['xgroups'].keys()[0], 4),],
        )

        # Now we should match
        assert fb.whitelist(**entry) == True

        entry['group'] = 'alt.less.than.'

        # But not the other way
        assert fb.whitelist(**entry) == False

        # our group never changed; so we should have no hash update
        assert len(fb._regex_hash) == 3


    def test_whitelist_score(self):
        """
        Whitelist Testing for Scoring
        """
        entry = copy(self.template_entry)

        fb = NNTPFilterBase()
        # List initializes to being empty
        assert len(fb._whitelist) == False

        fb.whitelist_append('alt.greater.than.*', {
            FilterDirectives.DESCRIPTION: "Test Greater Than",
            FilterDirectives.SCORE_GREATER_THAN: 50,
        })

        fb.whitelist_append('alt.less.than.*', {
            FilterDirectives.DESCRIPTION: "Test Less Than",
            FilterDirectives.SCORE_LESS_THAN: 50,
        })

        # We aren't in the right group so we shouldn't match
        assert fb.whitelist(**entry) == False

        # check our hash table
        assert len(fb._regex_hash) == 1

        entry['group'] = 'alt.greater.than.'

        assert fb.whitelist(**entry) == False

        # our hash will change to 2
        assert len(fb._regex_hash) == 2

        entry['group'] = 'alt.less.than.'

        # By default everything is scored at 0; so this will be good
        assert fb.whitelist(**entry) == True

        # our hash will change to 3
        assert len(fb._regex_hash) == 3

        # Now we'll add a score which will increase our score value
        # to something above 50 (what we're checking)
        fb.scorelist_append('alt.greater.than.*', {
            FilterDirectives.DESCRIPTION: "Test Regex (string)",
            FilterDirectives.SCORE: 51,
            # This will automatically get compiled
            FilterDirectives.SUBJECT_MATCHES_REGEX: '.*\.mkv.*',
        })

        # Now we'll add a score which will increase our score value
        # to something above 50 (what we're checking)
        fb.scorelist_append('alt.less.than.*', {
            FilterDirectives.DESCRIPTION: "Test Regex (string)",
            FilterDirectives.SCORE: 51,
            # This will automatically get compiled
            FilterDirectives.SUBJECT_MATCHES_REGEX: '.*\.mkv.*',
        })


        # our hash will drop to 0
        assert len(fb._regex_hash) == 0

        # But now we meet our score
        entry['group'] = 'alt.greater.than.'
        entry['score'] = fb.score(**entry)
        assert entry['score'] == 51
        assert fb.whitelist(**entry) == True

        # 1 for the hash table after all that
        assert len(fb._regex_hash) == 1

        # Now we should match our less than case
        assert fb.whitelist(**entry) == True

        entry['group'] = 'alt.less.than.'

        # however we are still not greater than
        entry['score'] = 49
        assert fb.whitelist(**entry) == True

        entry['group'] = 'alt.less.than.'
        entry['score'] = fb.score(**entry)
        assert entry['score'] == 51
        assert fb.whitelist(**entry) == False

        # our group never changed; so we should have no hash update
        assert len(fb._regex_hash) == 2


    # Test the loading of the same content by file
    def test_file_load(self):
        """
        Load the simple filters by file
        """
        entry = copy(self.template_entry)
        fb = NNTPFilterBase(paths=join(self.var_dir, 'simple.nrf'))

        # Our hash will start at 0 (Zero)
        assert len(fb._regex_hash) == 0

        # But now we meet our score
        entry['subject'] = 'A great video called "blah.avi"'
        assert fb.blacklist(**entry) == False

        entry['subject'] = 'A malicious file because it is "blah.avi.exe"'
        assert fb.blacklist(**entry) == True

        # Now load the directory; it should just find the same nrf file.
        fbd = NNTPFilterBase(paths=self.var_dir)
