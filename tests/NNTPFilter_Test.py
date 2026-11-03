# Test the filter rules (scoring, tagging, keeping and dropping articles)
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

import gevent.monkey
gevent.monkey.patch_all()

from datetime import UTC
from datetime import date
from datetime import datetime

import pytest
import yaml

from newsreap.NNTPFilter import NNTPFilter
from newsreap.NNTPFilter import trie_regex
from newsreap.NNTPFilterRule import NNTPFilterRule
from newsreap.NNTPFilterRule import fold
from newsreap.NNTPFilterRule import tags_from_text
from newsreap.NNTPFilterRule import tags_to_text

GROUP = 'alt.binaries.test'
NOW = datetime(2026, 6, 1)


def filters(*rules):
    """
    Returns an NNTPFilter holding the rules given (in their YAML form)
    """
    return NNTPFilter(rules=rules, now=NOW)


def dropped(rules, group=GROUP, **item):
    """
    Returns True if the rules drop the item described (as a purge does,
    with the rules on scores and tags too)
    """
    item.setdefault('by_score', True)
    return rules.check(group, **item).drop is not None


def test_plain_text_ignores_case_and_accents():
    """
    Plain text is found anywhere in the field, whatever its case or accents
    """
    rules = filters({'subject': 'CAFÉ'})
    assert dropped(rules, subject='Best cafe in town')
    assert dropped(rules, subject='best Café in town')
    assert not dropped(rules, subject='best coffee in town')

    assert fold('Ça Café') == 'ca cafe'


def test_case_sensitive_rules_compare_exactly():
    """
    case_sensitive: true makes case (and accents) matter
    """
    rules = filters({'subject': 'Café', 'case_sensitive': True})
    assert dropped(rules, subject='the Café')
    assert not dropped(rules, subject='the café')
    assert not dropped(rules, subject='the Cafe')


def test_regular_expressions_match_anywhere():
    """
    A regular expression is searched for anywhere in the field and ignores
    case unless the rule is case sensitive
    """
    rules = filters({'subject': {'regex': r'\.PAR2\b'}})
    assert dropped(rules, subject='"my.vol01+02.par2" yEnc (1/1)')
    assert not dropped(rules, subject='"my.par2x" yEnc (1/1)')

    rules = filters({'subject': {'regex': 'Spam'}, 'case_sensitive': True})
    assert dropped(rules, subject='more Spam')
    assert not dropped(rules, subject='more spam')


@pytest.mark.parametrize('match, subject, expected', [
    ({'is': 'hello world'}, 'Hello World', True),
    ({'is': 'hello'}, 'hello world', False),
    ({'starts': 'hello'}, 'Hello world', True),
    ({'starts': 'world'}, 'hello world', False),
    ({'ends': 'world'}, 'hello WORLD', True),
    ({'ends': 'hello'}, 'hello world', False),
    ({'contains': 'lo wo'}, 'hello world', True),
    ({'contains': 'lo wo', 'not': True}, 'hello world', False),
    ({'contains': 'xyz', 'not': True}, 'hello world', True),
])
def test_text_can_be_compared_in_several_ways(match, subject, expected):
    """
    Text can be compared as is, by how it starts or ends, by what it
    contains, and any of them can be turned around with not
    """
    assert dropped(filters({'subject': match}), subject=subject) is expected


def test_a_list_of_values_must_all_match():
    """
    A field given several comparisons must pass every one of them (as
    search conditions do); match: any makes one enough
    """
    rules = filters({'poster': ['spam', {'regex': r'^bot\d+@'}]})
    assert dropped(rules, poster='bot42@spam.example')
    assert not dropped(rules, poster='x@spam.example')
    assert not dropped(rules, poster='bot42@example.com')

    rules = filters({'match': 'any',
                     'poster': ['spam.example', {'regex': r'^bot\d+@'}]})
    assert dropped(rules, poster='x@spam.example')
    assert dropped(rules, poster='bot42@example.com')
    assert not dropped(rules, poster='friend@example.com')


def test_wildcards_match_the_whole_value_like_searches():
    """
    glob compares the whole value with * and ? wildcards, ignoring case
    and accents unless the comparison (or the rule) says case matters
    """
    rules = filters({'subject': {'glob': 'CAFÉ*'}})
    assert dropped(rules, subject='cafe photos')
    assert not dropped(rules, subject='the cafe')

    rules = filters({'subject': {'glob': 'Café*', 'case_sensitive': True}})
    assert dropped(rules, subject='Café photos')
    assert not dropped(rules, subject='cafe photos')

    rules = filters({'case_sensitive': True,
                     'subject': [{'glob': '*Foo*'},
                                 {'glob': '*bar*', 'case_sensitive': False}]})
    assert dropped(rules, subject='a Foo and a BAR')
    assert not dropped(rules, subject='a foo and a BAR')

    # Written back the way it was given
    assert rules.to_yaml().count('case_sensitive') == 2

    with pytest.raises(ValueError, match='true or false'):
        filters({'subject': {'glob': 'x', 'case_sensitive': 'yes'}})


def test_unknown_bodies_and_file_names_never_match():
    """
    A rule on the body or file names only matches where they are known
    (ie: archived posts), even when it is turned around with not
    """
    rules = filters({'name': 'quiet', 'body': {'glob': '*good*',
                                               'not': True}})
    assert not dropped(rules, subject='x')
    assert dropped(rules, subject='x', body='nothing here')
    assert not dropped(rules, subject='x', body='all good')

    rules = filters({'filename': {'glob': '*.exe'}})
    assert not dropped(rules, subject='x')
    assert not dropped(rules, subject='x', filenames=[])
    assert dropped(rules, subject='x', filenames=['a.nfo', 'setup.EXE'])

    rules = filters({'filename': {'glob': '*.exe', 'not': True}})
    assert dropped(rules, subject='x', filenames=[])
    assert not dropped(rules, subject='x')


def test_conditions_must_all_match_unless_any_is_asked_for():
    """
    Every condition must match by default; with match: any, one is enough
    """
    rules = filters({'subject': 'free', 'poster': 'spam'})
    assert dropped(rules, subject='free stuff', poster='spam@x')
    assert not dropped(rules, subject='free stuff', poster='friend@x')

    rules = filters({'subject': 'free', 'poster': 'spam', 'match': 'any'})
    assert dropped(rules, subject='free stuff', poster='friend@x')
    assert dropped(rules, subject='photos', poster='spam@x')
    assert not dropped(rules, subject='photos', poster='friend@x')


def test_time_ranges_only_cover_what_was_posted_inside_them():
    """
    after and before limit a rule to what was posted between them (dates,
    or how long ago); an unknown posting time never matches
    """
    rules = filters({'subject': 'x', 'after': date(2026, 1, 1),
                     'before': '2026-02-01'})
    assert dropped(rules, subject='x', posted=datetime(2026, 1, 1))
    assert dropped(rules, subject='x', posted=datetime(2026, 1, 31, 23))
    assert not dropped(rules, subject='x', posted=datetime(2026, 2, 1))
    assert not dropped(rules, subject='x', posted=datetime(2025, 12, 31))
    assert not dropped(rules, subject='x', posted=None)

    # How long ago (counted back from now)
    rules = filters({'before': '30d'})
    assert dropped(rules, posted=datetime(2026, 4, 1))
    assert not dropped(rules, posted=datetime(2026, 5, 15))

    rules = filters({'after': '12h'})
    assert dropped(rules, posted=datetime(2026, 5, 31, 13))
    assert not dropped(rules, posted=datetime(2026, 5, 31, 11))

    # A time with a timezone is moved to UTC
    rule = NNTPFilterRule(
        after=datetime(2026, 1, 1, 5, tzinfo=UTC), action='drop')
    assert rule.after == datetime(2026, 1, 1, 5)


def test_sizes_can_be_limited():
    """
    size over/under compare an article's size; an unknown size never
    matches
    """
    rules = filters({'size': {'over': '1KB', 'under': '1MB'}})
    assert dropped(rules, size=5000)
    assert not dropped(rules, size=1024)
    assert not dropped(rules, size=2 * 1024 * 1024)
    assert not dropped(rules, size=None)


def test_rules_can_be_limited_to_groups():
    """
    A rule only applies to the groups it lists (wildcards work, case
    doesn't matter); an item found in several groups needs only one
    """
    rules = filters({'poster': 'spam', 'groups': ['alt.binaries.*']})
    assert dropped(rules, 'ALT.binaries.test', poster='spam@x')
    assert not dropped(rules, 'alt.test', poster='spam@x')
    assert dropped(rules, ['alt.test', 'alt.binaries.x'], poster='spam@x')


def test_scores_add_up_and_tags_are_combined():
    """
    Every score rule that matches adds its score and tags; every item
    starts at 0 with no tags
    """
    rules = filters(
        {'name': 'par2', 'subject': {'regex': r'\.par2\b'}, 'score': 10,
         'tag': 'recovery'},
        {'name': 'video', 'subject': '.mkv', 'score': 25,
         'tag': ['video', 'HD']},
        {'name': 'shouty', 'subject': {'regex': '!!!'}, 'score': -30})

    verdict = rules.check(GROUP, subject='a.mkv b.par2 !!!')
    assert verdict.score == 10 + 25 - 30
    assert verdict.tags == {'recovery', 'video', 'hd'}
    assert verdict.drop is None

    verdict = rules.check(GROUP, subject='nothing')
    assert (verdict.score, verdict.tags) == (0, frozenset())


def test_score_rules_see_the_score_so_far():
    """
    Score rules run in the order they are listed; a later one can look at
    the score and tags the earlier ones gave
    """
    rules = filters(
        {'subject': 'good', 'score': 50, 'tag': 'good'},
        {'score': '>40, +5', 'tag': 'great'},
        {'tagged': 'good', 'score': 1})

    verdict = rules.check(GROUP, subject='good stuff')
    assert verdict.score == 56
    assert verdict.tags == {'good', 'great'}


def test_purges_can_drop_by_score_and_tags():
    """
    Purges run the drop rules on scores and tags after scoring, so they
    can remove what scores too low or carries a tag; scanning never does
    """
    rules = filters(
        {'subject': 'casino', 'score': -100, 'tag': 'spam'},
        {'name': 'low', 'score': '<-50'},
        {'name': 'tagged', 'tagged': ['nsfw', 'junk']},
        {'subject': 'junk', 'score': 0, 'tag': 'junk'})

    def purged(subject):
        return rules.check(GROUP, subject=subject, by_score=True).drop

    assert purged('casino').name == 'low'
    assert purged('junk mail').name == 'tagged'
    assert purged('photos') is None

    # Scanned, they are only scored
    verdict = rules.check(GROUP, subject='casino')
    assert (verdict.score, verdict.drop) == (-100, None)


@pytest.mark.parametrize('score, results', [
    ('<34, >23', {20: False, 24: True, 33: True, 34: False}),
    ('<=0', {-1: True, 0: True, 1: False}),
    ('>=10', {9: False, 10: True}),
    ('=5', {4: False, 5: True, 6: False}),
    ('< -50', {-51: True, -50: False}),
])
def test_scores_can_be_compared(score, results):
    """
    A score given as comparisons is a condition (every comparison must
    pass)
    """
    rules = filters({'score': score})
    for value, expected in results.items():
        assert dropped(rules, score=value, rescore=False) is expected


def test_score_rules_can_compare_and_add():
    """
    A score with a comparison and an amount adds the amount to what passes
    the comparison
    """
    rule = NNTPFilterRule(score='>40, +5', tag='great')
    assert (rule.score, rule.score_tests) == (5, (('>', 40), ))
    assert rule.action == 'score'

    rule = NNTPFilterRule(subject='x', score='-5')
    assert (rule.score, rule.score_tests) == (-5, ())

    with pytest.raises(ValueError, match='nothing to match on'):
        NNTPFilterRule(score=5)


def test_tags_combine_like_apprise_tags():
    """
    Each tag choice given will do; the tags within one choice must all be
    there; case doesn't matter
    """
    from newsreap.NNTPFilterRule import tag_logic
    from newsreap.NNTPFilterRule import tags_match

    none = frozenset()
    logic = tag_logic(['Video, HD', 'music'])
    assert logic == ((frozenset({'video', 'hd'}), none),
                     (frozenset({'music'}), none))
    assert tags_match(logic, {'video', 'hd', 'x'})
    assert tags_match(logic, {'music'})
    assert not tags_match(logic, {'video'})
    assert not tags_match(logic, set())

    assert tag_logic('a b,c') == ((frozenset({'a', 'b', 'c'}), none), )
    assert tag_logic(['a', 'A']) == ((frozenset({'a'}), none), )
    for empty in ('', '!', '  ', ', !'):
        with pytest.raises(ValueError, match='has no tag'):
            tag_logic(['a', empty])
    assert tag_logic(None) == ()

    rules = filters(
        {'name': 'both', 'tagged': 'video, hd'},
        {'name': 'either', 'tagged': ['music', 'books']})
    assert rules.check(GROUP, tags={'video'}, rescore=False,
                       by_score=True).drop is None
    assert rules.check(GROUP, tags={'video', 'hd'}, rescore=False,
                       by_score=True).drop.name == 'both'
    assert rules.check(GROUP, tags={'books'}, rescore=False,
                       by_score=True).drop.name == 'either'


def test_tags_starting_with_an_exclamation_mark_must_not_be_there():
    """
    !tag asks for what doesn't have the tag, on its own or along with
    tags that must be there
    """
    from newsreap.NNTPFilterRule import tag_logic
    from newsreap.NNTPFilterRule import tag_logic_text
    from newsreap.NNTPFilterRule import tags_match

    logic = tag_logic(['video, !Sample', '!junk'])
    assert logic == ((frozenset({'video'}), frozenset({'sample'})),
                     (frozenset(), frozenset({'junk'})))
    assert tag_logic_text(logic) == ['video, !sample', '!junk']

    assert tags_match(logic, {'video'})
    assert tags_match(logic, set())
    assert tags_match(logic, {'video', 'junk'})
    assert not tags_match(logic, {'junk'})
    assert not tags_match(logic, {'video', 'sample', 'junk'})

    with pytest.raises(ValueError, match='and its opposite'):
        tag_logic('a, !a')

    with pytest.raises(ValueError, match="can't be a tag"):
        tag_logic('!bad?')

    # Rules read them the same way
    rules = filters({'name': 'unsafe', 'subject': 'x',
                     'tagged': 'video, !trusted'})
    assert dropped(rules, subject='x', tags={'video'}, rescore=False)
    assert not dropped(
        rules, subject='x', tags={'video', 'trusted'}, rescore=False)
    assert rules['unsafe'].to_dict()['tagged'] == 'video, !trusted'

    rule = NNTPFilterRule(tagged='!trusted')
    assert rule.name == 'drop-tagged-not-trusted'


def test_score_searches_take_comparisons_only():
    """
    A score search takes comparisons (no amount to add)
    """
    from newsreap.NNTPFilterRule import score_tests

    assert score_tests('<34, >23') == (('<', 34), ('>', 23))
    assert score_tests(None) == ()
    assert score_tests('') == ()

    for value in ('10', '>5, +1', 'lots'):
        with pytest.raises(ValueError):
            score_tests(value, '--score')


def test_times_can_be_dates_or_how_long_ago():
    """
    A time is a date, a date and time, or how long ago
    """
    from newsreap.NNTPFilterRule import parse_time

    assert parse_time('2026-01-31') == datetime(2026, 1, 31)
    assert parse_time('2026-01-31 10:30') == datetime(2026, 1, 31, 10, 30)
    assert parse_time('2d', now=NOW) == datetime(2026, 5, 30)
    assert parse_time('2019') == datetime(2019, 1, 1)
    assert parse_time('2019-06') == datetime(2019, 6, 1)
    assert parse_time(2019) == datetime(2019, 1, 1)
    assert parse_time(None) is None

    # How long ago needs its unit (a number alone is a year)
    with pytest.raises(ValueError, match='needs a unit'):
        parse_time('90')

    with pytest.raises(ValueError, match='must be a date'):
        parse_time('2019-13')
    assert parse_time(' ') is None

    with pytest.raises(ValueError, match='--after must be a date'):
        parse_time('whenever', '--after')

    # Too far back to work out
    for value in ('99999999d', '9999999999y'):
        with pytest.raises(ValueError, match='too far back'):
            parse_time(value, '--after')


def test_rules_can_leave_out_tags():
    """
    not_tagged matches what doesn't have the tags (the same choices as
    tagged)
    """
    rules = filters(
        {'name': 'keep-nothing-trusted', 'subject': 'x',
         'not_tagged': ['trusted', 'mine, safe']})

    assert dropped(rules, subject='x', rescore=False)
    assert not dropped(rules, subject='x', tags={'trusted'}, rescore=False)
    assert dropped(rules, subject='x', tags={'mine'}, rescore=False)
    assert not dropped(
        rules, subject='x', tags={'mine', 'safe'}, rescore=False)


def test_crossposts_can_be_counted():
    """
    crossposts compares how many groups an article was posted to; a plain
    number is an exact count, and an unknown count never matches
    """
    rules = filters({'crossposts': '>3'})
    assert dropped(rules, crossposts=5)
    assert not dropped(rules, crossposts=3)
    assert not dropped(rules, crossposts=None)

    rules = filters({'crossposts': 2, 'action': 'drop'})
    assert dropped(rules, crossposts=2)
    assert not dropped(rules, crossposts=1)

    # The group it was found in counts once, whatever its case
    assert NNTPFilterRule.count_groups(
        [GROUP, GROUP.upper(), 'alt.binaries.other']) == 2
    assert NNTPFilterRule.count_groups([]) is None

    rule = NNTPFilterRule(crossposts='>=2, <10')
    assert rule.to_dict()['crossposts'] == '>=2, <10'
    assert NNTPFilterRule.from_dict(rule.to_dict()).crossposts == \
        rule.crossposts

    with pytest.raises(ValueError, match='crossposts takes a number'):
        NNTPFilterRule(crossposts='many')


def test_header_drops_happen_before_scoring():
    """
    A drop rule removes an article before any score rule runs; drop rules
    on the score are left to purges
    """
    checked = []

    class Watched(NNTPFilterRule):
        def matches(self, item, score=0, tags=frozenset()):
            checked.append(self.name)
            return super().matches(item, score, tags)

    rules = NNTPFilter(now=NOW)
    for entry in (
            {'name': 'popular', 'subject': 'photos', 'score': 10},
            {'name': 'spam', 'poster': '@spam.example'},
            {'name': 'unwanted', 'score': '<5'}):
        rules.add(Watched.from_dict(entry, now=NOW))

    verdict = rules.check(GROUP, subject='photos', poster='bot@spam.example')
    assert verdict.drop.name == 'spam'
    assert 'popular' not in checked
    assert verdict.score == 0

    # Only scored (the spam rule is passed over without being checked; its
    # words aren't there)
    checked.clear()
    verdict = rules.check(GROUP, subject='other', poster='me@example.com')
    assert (verdict.drop, verdict.score) == (None, 0)
    assert checked == []

    # A purge judges it on its score too
    assert rules.check(
        GROUP, subject='other', by_score=True).drop.name == 'unwanted'
    assert rules.check(
        GROUP, subject='photos', by_score=True).drop is None

    # Asked only for its score, nothing is dropped
    verdict = rules.check(
        GROUP, subject='photos', poster='bot@spam.example', drop=False)
    assert (verdict.score, verdict.drop) == (10, None)


def test_keep_rules_on_the_score_only_protect_in_purges():
    """
    A keep rule on the score can't stop a drop rule on the header (that
    one runs first); in a purge it protects from drop rules on the score
    """
    rules = filters(
        {'name': 'popular', 'subject': 'photos', 'score': 10},
        {'name': 'spam', 'poster': '@spam.example'},
        {'name': 'loved', 'score': '>5', 'action': 'keep'},
        {'name': 'quiet', 'score': '<20'})

    assert dropped(rules, subject='photos', poster='bot@spam.example')
    assert not dropped(rules, subject='photos', poster='me@example.com')
    assert dropped(rules, subject='other', poster='me@example.com')


def test_keep_rules_protect_from_drop_rules():
    """
    A keep rule wins over every drop rule
    """
    rules = filters(
        {'name': 'spam', 'subject': 'free'},
        {'name': 'friend', 'poster': 'friend@example.com',
         'action': 'keep'})

    assert dropped(rules, subject='free photos', poster='x@y')
    assert not dropped(
        rules, subject='free photos', poster='Friend@Example.com')


def test_stored_scores_are_used_unless_rescoring():
    """
    Without rescoring, the score and tags given are what the rules see
    (and score rules don't run)
    """
    rules = filters(
        {'subject': 'x', 'score': 10},
        {'name': 'low', 'score': '<0'})

    verdict = rules.check(GROUP, subject='x', score=-5, tags={'old'},
                          rescore=False, by_score=True)
    assert verdict.score == -5
    assert verdict.tags == {'old'}
    assert verdict.drop.name == 'low'

    verdict = rules.check(GROUP, subject='x', score=-5, tags={'old'})
    assert (verdict.score, verdict.tags, verdict.drop) == (
        10, frozenset(), None)


def test_rules_for_a_whole_group_can_score():
    """
    A score rule with only groups scores everything in them; a drop rule
    must say what it drops
    """
    rules = filters({'groups': [GROUP], 'score': 3, 'match': 'any'})
    assert rules.check(GROUP, subject='anything').score == 3
    assert rules.check('alt.other', subject='anything').score == 0

    with pytest.raises(ValueError, match='nothing to match on'):
        filters({'groups': [GROUP], 'action': 'drop'})


@pytest.mark.parametrize('rule, problem', [
    ({'subject': 'x', 'colour': 'red'}, 'unknown setting'),
    ({'subject': {'regex': '(unclosed'}}, 'regular expression'),
    ({'subject': {'regex': 'a', 'is': 'b'}}, 'takes one of'),
    ({'subject': {'matches': 'a'}}, 'takes one of'),
    ({'body': {'glob': ''}}, 'needs some text'),
    ({'subject': ''}, 'needs some text'),
    ({'subject': 5}, 'must be text'),
    ({'subject': 'x', 'action': 'drop', 'score': 5}, "can't score"),
    ({'subject': 'x', 'action': 'score'}, 'needs a score'),
    ({'subject': 'x', 'action': 'hide'}, "isn't an action"),
    ({'subject': 'x', 'match': 'some'}, "match must be"),
    ({'subject': 'x', 'score': 'lots'}, 'whole number'),
    ({'subject': 'x', 'score': True}, 'whole number'),
    ({'subject': 'x', 'score': '5, 6'}, 'one amount'),
    ({'subject': 'x', 'score': '<<5'}, 'comparisons'),
    ({'subject': 'x', 'score': '<5, +1', 'action': 'drop'}, "can't score"),
    ({'subject': 'x', 'tagged': 'a, b!'}, "can't be a tag"),
    ({'subject': 'x', 'not_tagged': 5}, 'tags must be'),
    ({'subject': 'x', 'tag': 'two words'}, "can't be a tag"),
    ({'size': {'over': 'big'}}, 'must be a size'),
    ({'size': 10}, "takes 'over'"),
    ({'after': 'whenever'}, 'must be a date'),
    ({'groups': 5, 'subject': 'x'}, 'groups must be'),
    ({}, 'nothing to match on'),
    ('subject: x', 'set of settings'),
])
def test_rules_that_make_no_sense_are_refused(rule, problem):
    """
    A rule that doesn't make sense is refused with a reason
    """
    with pytest.raises(ValueError, match=problem):
        filters(rule)


def test_rule_names_are_made_up_and_kept_unique():
    """
    A rule without a name gets one from what it matches; names can't be
    used twice
    """
    rule = NNTPFilterRule(poster='@Spam.Example', action='drop')
    assert rule.name == 'drop-poster-spam-example'

    rules = filters({'name': 'one', 'subject': 'x'})
    assert rules.unique_name('one') == 'one-2'
    assert rules.unique_name('two') == 'two'

    with pytest.raises(ValueError, match='already a rule'):
        rules.add({'name': 'one', 'subject': 'y'})


def test_rules_survive_being_written_and_read_back(tmp_path):
    """
    Rules written to a filters file read back the same
    """
    rules = filters(
        {'name': 'a', 'groups': ['alt.binaries.*'], 'match': 'any',
         'case_sensitive': True, 'subject': [{'regex': r'\d+'}, 'x'],
         'poster': {'ends': '@x', 'not': True}, 'after': date(2026, 1, 1),
         'before': '30d', 'size': {'over': '1MB'}, 'score': '>1, <50',
         'tagged': ['b', 'a, c'], 'not_tagged': 'd', 'action': 'drop'},
        {'name': 'c', 'subject': 'x', 'score': '>=3, +2'},
        {'name': 'b', 'message_id': 'bulk', 'score': -5, 'tag': 'bulk'})

    path = tmp_path / 'filters.yaml'
    rules.save(str(path))
    assert oct(path.stat().st_mode & 0o777) == '0o600'

    loaded = NNTPFilter(str(path), now=NOW)
    assert [r.to_dict() for r in loaded] == [r.to_dict() for r in rules]
    assert list(loaded.keys()) == ['a', 'c', 'b']
    assert loaded['a'].to_dict()['score'] == '>1, <50'
    assert loaded['a'].to_dict()['tagged'] == ['b', 'a, c']
    assert loaded['c'].to_dict()['score'] == '>=3, +2'
    assert 'a' in loaded and loaded['b'].score == -5


def test_saving_one_rule_keeps_the_rest_of_the_file(tmp_path):
    """
    A rule added to an existing filters file is written at its end;
    everything already there (comments too) stays as it was
    """
    path = tmp_path / 'filters.yaml'
    path.write_text(
        '# my own notes\nfilters:\n  # the old spammer\n'
        '  - name: old\n    poster: old@spam\n')

    rules = NNTPFilter(str(path), now=NOW)
    rules.append({'name': 'new', 'subject': 'casino'})

    content = path.read_text()
    assert content.startswith(
        '# my own notes\nfilters:\n  # the old spammer\n')
    assert [r.name for r in NNTPFilter(str(path))] == ['old', 'new']

    # A file that only holds comments starts the list
    path.write_text('# nothing yet\n')
    NNTPFilter(str(path)).append({'name': 'first', 'subject': 'x'})
    assert path.read_text().startswith('# nothing yet\n')
    assert [r.name for r in NNTPFilter(str(path))] == ['first']

    # A file we can't add to that way is written out again
    path.write_text('filters: [{name: flow, subject: x}]\n')
    NNTPFilter(str(path)).append({'name': 'next', 'subject': 'y'})
    assert [r.name for r in NNTPFilter(str(path))] == ['flow', 'next']

    # A new file is created
    path.unlink()
    NNTPFilter(str(path)).append({'name': 'only', 'subject': 'z'})
    assert [r.name for r in NNTPFilter(str(path))] == ['only']


@pytest.mark.parametrize('content, problem', [
    ('filters:\n  - name: x\n    subject: [unclosed\n', "isn't valid YAML"),
    ('rules:\n  - subject: x\n', "under 'filters:'"),
    ('filters: just text\n', "under 'filters:'"),
])
def test_broken_filters_files_are_refused(tmp_path, content, problem):
    """
    A filters file that can't be read says where the problem is, and
    nothing in it is used
    """
    path = tmp_path / 'filters.yaml'
    path.write_text(content)
    with pytest.raises(ValueError, match=problem):
        NNTPFilter(str(path))


@pytest.mark.parametrize('content, problem', [
    ('filters:\n  - name: ok\n    subject: x\n  - name: bad\n'
     '    subject: {regex: "("}\n', 'rule #2 (bad)'),
    ('filters:\n  - name: ok\n    subject: x\n  - name: ok\n'
     '    subject: y\n', 'more than once'),
])
def test_broken_rules_are_left_out(tmp_path, caplog, content, problem):
    """
    A rule that doesn't make sense is left out (saying which, and why); the
    others are still used, and the file isn't written over (it would lose
    the rule)
    """
    path = tmp_path / 'filters.yaml'
    path.write_text(content)
    filters = NNTPFilter(str(path))
    assert [r.name for r in filters] == ['ok']
    assert len(filters.problems) == 1 and problem in filters.problems[0]
    assert problem in caplog.text and 'left out' in caplog.text

    with pytest.raises(ValueError, match='fix them first'):
        filters.save()
    assert path.read_text() == content


def test_filters_files_can_be_empty_or_a_plain_list(tmp_path):
    """
    An empty filters file holds no rules; the rules can also be listed
    without the filters: key
    """
    path = tmp_path / 'filters.yaml'
    path.write_text('')
    assert len(NNTPFilter(str(path))) == 0

    path.write_text('- name: a\n  subject: x\n')
    assert list(NNTPFilter(str(path)).keys()) == ['a']

    # A file that doesn't exist yet is fine too
    assert len(NNTPFilter(str(tmp_path / 'none.yaml'))) == 0


def test_word_search_finds_every_word():
    """
    The regular expression used to rule out many rules at once finds each
    of its words, including ones that start the same way
    """
    words = ['spam', 'spammer', 'span', 'x.y', 'a+b', 'é']
    regex = trie_regex(words)
    for word in words:
        assert regex.search('-- %s --' % word)

    assert not regex.search('spa x-y ab')


def test_many_rules_find_the_right_one():
    """
    With thousands of rules, every one of them still matches what it
    should (including words that contain each other)
    """
    rules = filters(
        *({'name': 'p%d' % no, 'poster': 'spammer%d@' % no}
          for no in range(3000)),
        {'name': 'short', 'poster': 'spammer1'},
        {'name': 'tagger', 'subject': 'Holiday', 'score': 1, 'tag': 'trip'})

    assert rules.check(GROUP, poster='SPAMMER2999@x').drop.name == 'p2999'
    assert rules.check(GROUP, poster='spammer17@x').drop.name == 'p17'
    assert rules.check(GROUP, poster='spammer1x').drop.name == 'short'
    assert rules.check(GROUP, poster='friend@x').drop is None
    assert rules.check(GROUP, subject='holiday').tags == {'trip'}


def test_only_rules_whose_words_are_found_are_checked():
    """
    Of many rules looking for words in the same field, only the ones whose
    word is there are checked, even when words overlap or repeat
    """
    from newsreap.NNTPFilterRule import FilterItem

    rules = filters(
        *({'name': 'p%d' % no, 'poster': 'other%d@bad' % no}
          for no in range(1000)),
        {'name': 'spam', 'poster': 'spam'},
        {'name': 'spammer', 'poster': 'spammer'},
        {'name': 'ammer', 'poster': 'ammer'},
        {'name': 'regex', 'poster': {'regex': 'x'}})

    drop = rules.plan(GROUP).drop
    found = drop.candidates(FilterItem(poster='spammer@bad spam'))
    assert [rule.name for _, rule in found] == [
        'spam', 'spammer', 'ammer', 'regex']

    found = drop.candidates(FilterItem(poster='friend@example.com'))
    assert [rule.name for _, rule in found] == ['regex']


def test_filters_work_where_older_filters_did():
    """
    The rules answer the questions an NNTPConnection asks of a filter
    (score, whitelist, blacklist) for an XOVER entry
    """
    rules = filters(
        {'subject': '.mkv', 'score': 25},
        {'name': 'spam', 'poster': 'strange@email.com'},
        {'name': 'mine', 'subject': 'Keep.Me', 'action': 'keep'})

    entry = {
        'id': 'testuniqueid',
        'article_no': 1,
        'poster': 'Unknown Poster <strange@email.com>',
        'date': datetime(2000, 1, 1),
        'subject': 'What.A.Great.Show (1/1) "what.a.great.show.mkv" (1/1)',
        'size': 25000,
        'lines': 3000,
        'group': GROUP,
        'score': 0,
        'xgroups': {'alt.binaries.ohwell': 2},
    }

    assert rules.score(**entry) == 25
    assert rules.blacklist(**entry) is True
    assert rules.whitelist(**entry) is False

    entry['subject'] = 'Keep.Me'
    assert rules.whitelist(**entry) is True


def test_tags_are_stored_sorted_and_space_separated():
    """
    Tags are kept as sorted, space separated text
    """
    assert tags_to_text({'video', 'hd', 'video'}) == 'hd video'
    assert tags_to_text(['Video', 'VIDEO', ' hd ', '']) == 'hd video'
    assert tags_from_text('hd video') == {'hd', 'video'}
    assert tags_from_text('HD hd') == {'hd'}

    # Tags that don't fit are left out whole (never cut short)
    assert tags_to_text(['aaaa', 'bbbb', 'cccc'], limit=10) == 'aaaa bbbb'
    assert len(tags_to_text('t%03d' % no for no in range(100))) <= 256
    assert tags_from_text('') == frozenset()
    assert tags_from_text(None) == frozenset()


def test_rules_read_their_settings_from_yaml():
    """
    A rule written in YAML (as a person would type it) reads as expected
    """
    entry = yaml.safe_load(
        "name: casino\n"
        "groups: [alt.binaries.*]\n"
        "poster: '@spam.example'\n"
        "subject: { regex: '\\bcasino\\b' }\n"
        "after: 2026-01-01\n"
        "size: { over: 10MB }\n")

    rule = NNTPFilterRule.from_dict(entry, now=NOW)
    assert rule.action == 'drop'
    assert rule.after == datetime(2026, 1, 1)
    assert rule.size_over == 10 * 1024 * 1024
    assert rule.groups == ('alt.binaries.*', )
