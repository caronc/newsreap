# Test find(1) style search conditions
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


import pytest

from newsreap.NNTPSearchTerm import NNTPSearchTerm
from newsreap.NNTPSearchTerm import SEARCH_OPTIONS
from newsreap.NNTPSearchTerm import SearchKind
from newsreap.NNTPSearchTerm import sql_match


def term(pattern, **kwargs):
    """
    Returns a subject condition
    """
    return NNTPSearchTerm('subject', pattern, **kwargs)


@pytest.mark.parametrize('pattern, text, expected', [
    ('Holiday', 'Holiday', True),
    ('Holiday', 'My Holiday', False),
    ('*Holiday*', 'My Holiday Photos', True),
    ('Holiday*', 'Holiday Photos', True),
    ('Holiday*', 'My Holiday', False),
    ('*.rar', 'pics.part01.rar', True),
    ('*.rar', 'pics.part01.rar.par2', False),
    ('Hol?day', 'Holiday', True),
    ('Hol?day', 'Holday', False),
    ('Hol?day', 'Holiiday', False),
    ('*', '', True),
    ('a*b*c', 'a-x-b-y-c', True),
    (r'50\%*', '50% off', True),
    (r'what\?', 'what?', True),
    (r'what\?', 'whats', False),
    (r'\*star\*', '*star*', True),
    ('[x]', '[x]', True),
    ('*holiday*', 'My Holiday', False),
])
def test_wildcards_match_the_whole_text_like_find(pattern, text, expected):
    """
    * matches anything and ? one character; the whole text must match
    (case matters); a backslash keeps a wildcard as it is
    """
    assert term(pattern).matches(text) is expected


def test_the_i_versions_ignore_case_and_accents():
    """
    Case and accents don't matter for the case insensitive versions
    """
    found = term('*CAFÉ*', case_sensitive=False)
    assert found.matches('best cafe in town')
    assert found.matches('Best Café')
    assert not found.matches('coffee')

    assert not term('*CAFÉ*').matches('best cafe')
    assert term('*Café*').matches('Best Café')


def test_regular_expressions_match_the_whole_text():
    """
    A regular expression must match all of the text (as find -regex does)
    """
    found = term(r'.*\(1/\d+\).*', kind=SearchKind.Regex)
    assert found.matches('My Post (1/20) yEnc')
    assert not found.matches('My Post (a/20)')

    assert not term('holiday', kind=SearchKind.Regex).matches('a holiday')
    assert term('re: .*', kind=SearchKind.Regex,
                case_sensitive=False).matches('RE: Coffee')
    assert not term('re: .*', kind=SearchKind.Regex).matches('RE: Coffee')


def test_a_leading_exclamation_mark_leaves_out_what_matches():
    """
    A pattern starting with ! matches what doesn't match the rest; \\!
    looks for a real !
    """
    found = term('!*sample*')
    assert found.negate
    assert found.matches('the real thing')
    assert not found.matches('a sample')

    found = term(r'\!wow*')
    assert not found.negate
    assert found.matches('!wow that')


@pytest.mark.parametrize('pattern, kind, problem', [
    ('', SearchKind.Glob, 'needs something'),
    ('!', SearchKind.Glob, 'needs something'),
    ('(', SearchKind.Regex, 'valid regular expression'),
])
def test_unusable_patterns_are_refused(pattern, kind, problem):
    """
    Empty patterns and broken regular expressions are refused
    """
    with pytest.raises(ValueError, match=problem):
        term(pattern, kind=kind)


def test_conditions_are_read_from_their_options():
    """
    Each option says what is searched, how, and whether case matters
    """
    assert sorted(SEARCH_OPTIONS) == sorted(
        '%s%s%s' % (i, r, f)
        for f in ('subject', 'body', 'poster', 'filename', 'message_id')
        for i in ('', 'i') for r in ('', 'regex_'))

    terms = NNTPSearchTerm.from_options(
        subject=['a*'], isubject=['b', 'c'], regex_body=['.*x'],
        iregex_poster=['y'], ifilename=['*.nfo'], filename=(),
        imessage_id=['*@spam'])

    assert [str(t) for t in terms] == [
        '--subject a*', '--isubject b', '--isubject c', '--regex-body .*x',
        '--iregex-poster y', '--ifilename *.nfo', '--imessage-id *@spam']
    assert [(t.field, t.kind, t.case_sensitive) for t in terms][3:] == [
        ('body', 'regex', True), ('poster', 'regex', False),
        ('filename', 'glob', False), ('message_id', 'glob', False)]

    with pytest.raises(ValueError, match="isn't a search option"):
        NNTPSearchTerm.from_options(colour=['x'])

    with pytest.raises(ValueError, match="can't be searched"):
        NNTPSearchTerm('colour', 'x')


def test_patterns_give_what_the_indexes_can_look_for():
    """
    The text a wildcard pattern needs (long enough to look up) and the
    words sure to be in what matches are worked out for the search indexes
    """
    assert term('*big holiday*').pieces() == ['big holiday']
    assert term('*ab*holiday?pics*').pieces() == ['holiday', 'pics']
    assert term('!*holiday*').pieces() == []
    assert term('.*', kind=SearchKind.Regex).pieces() == []

    # The first word after a wildcard may be the end of a longer one
    assert term('*liday photos*').words() == ['photos']
    assert term('holiday*').words() == ['holiday']
    assert term('*a.par2').words() == ['par2']
    assert term('*x*').words() == []


def test_patterns_become_like_patterns():
    """
    Wildcards become LIKE's, and LIKE's own wildcards are kept as text
    """
    assert term('*50%_off?').like() == '%50/%/_off_'
    assert term('a/b*').like() == 'a//b%'


def test_nothing_matches_a_missing_value():
    """
    A value the database doesn't have matches nothing
    """
    assert sql_match('glob', '*', 1, None) == 0
    assert sql_match('regex', '(', 1, 'x') == 0
