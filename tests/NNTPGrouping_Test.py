# Test putting obfuscated posts together from how they were posted
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


import random
import string
from datetime import timedelta

import pytest

from newsreap.NNTPIndexer import NNTPIndexer
from newsreap.NNTPIndexer import is_obfuscated
from newsreap.NNTPIndexer import looks_random
from newsreap.NNTPIndexer import message_id_shape
from newsreap.NNTPIndexer import parse_subject
from newsreap.NNTPIndexer import subject_template
from newsreap.NNTPIndexer import xref_key
from newsreap.objects.index.Collection import Collection
from newsreap.objects.index.Collection import CollectionState

from tests.NNTPIndexer_Test import GROUP
from tests.NNTPIndexer_Test import LATER
from tests.NNTPIndexer_Test import POSTED
from tests.NNTPIndexer_Test import cache
from tests.NNTPIndexer_Test import caught_up
from tests.NNTPIndexer_Test import index_engine  # noqa: F401
from tests.NNTPIndexer_Test import indexed
from tests.NNTPIndexer_Test import paths  # noqa: F401
from tests.NNTPIndexer_Test import release

# Made up names are the same every run
RANDOM = random.Random(42)


def made_up(length=32, letters=string.ascii_letters + string.digits):
    """
    Returns a made up name (as obfuscated posts use)
    """
    return ''.join(RANDOM.choice(letters) for _ in range(length))


def obfuscated(count, poster, domain='ngPost', counter=None):
    """
    Returns (subject, poster, Message-ID) of the articles of an obfuscated
    post: a made up subject (and Message-ID) for every article

    - poster: who posted it (None for a made up one every article)
    - counter: (name, segments) to give every file's segments a counter
      instead (ie: 'kJh7Gf6d5S4a3 (1/50)')
    """
    for no in range(count):
        if counter:
            name, segments = counter
            subject = '%s (%d/%d)' % (
                name[no // segments], no % segments + 1, segments)
        else:
            subject = made_up()

        yield (subject, poster or '%s@%s.com' % (
            made_up(13), domain), '%s@%s' % (
            made_up(32, '0123456789abcdef'), domain))


def collections(indexer):
    """
    Returns (name, segments, files) of every collection (forming or not)
    """
    session = indexer.session()
    return sorted(
        (c.name, c.segments, c.files) for c in session.query(Collection))


@pytest.mark.parametrize('word, expected', [
    ('IqQSNkadldiFeElFXOXWqREaNCrRNDhH', True),
    ('xPKM8Eru8n2NmSbaQZyp7k', True),
    ('d6ba0d96aeef4fbe9eee6e16c74731f4', True),
    ('HolidayPhotosTrip', False),
    ('TheBigBangTheory', False),
    ('Documentaries2019', False),
    ('UPPERCASEWORDHERE', False),
    ('20231105123456', False),
    ('ngPost', False),
])
def test_made_up_words_are_recognised(word, expected):
    """
    Long words mixing digits in, or switching case all the time, are made
    up; words joined together, a year or shouting aren't
    """
    assert looks_random(word) is expected


@pytest.mark.parametrize('subject, expected', [
    ('IqQSNkadldiFeElFXOXWqREaNCrRNDhH', True),
    ('kJh7Gf6d5S4a3 (1/1463)', True),
    ('', True),
    ('Re: Coffee', False),
    ('holiday photos', False),
    ('Documentaries', False),
    ('[23/63] "x.part23.rar" (1/2)', False),
    ('xPKM8Eru8n2NmSbaQZyp7k [01/20] - "a.rar" yEnc (1/9)', False),
])
def test_subjects_that_say_nothing_are_obfuscated(subject, expected):
    """
    A subject without a file name or a file counter, that is one made up
    word (or nothing), can't tell which post it is part of
    """
    assert is_obfuscated(parse_subject(subject)) is expected


def test_every_random_subject_is_obfuscated():
    """
    However the made up name was made, it is recognised
    """
    for letters in (string.ascii_letters + string.digits,
                    string.ascii_letters, '0123456789abcdef'):
        for _ in range(2000):
            assert is_obfuscated(parse_subject(made_up(20, letters)))


def test_what_posting_tools_leave_behind():
    """
    Subjects, Message-IDs and Xrefs are reduced to what the articles of one
    post share
    """
    assert subject_template(made_up()) == 'x'
    assert subject_template('%s (3/50)' % made_up(14)) == 'x(#/#)'
    assert subject_template('My Post [01/20] - "a.rar" (3/50)') == \
        'My Post (#/#) - "*" (#/#)'

    assert message_id_shape('<d6ba0d96aeef4fbe9eee6e16c74731f4@ngPost>') == \
        'x@ngpost'
    assert message_id_shape('part1of20.123456@news.example.com') == \
        message_id_shape('part7of20.654321@news.example.com')

    assert xref_key('a.b.two a.b.one') == xref_key('a.b.one a.b.two')
    assert xref_key('a.b.one') != xref_key('a.b.two')
    assert xref_key('') == ''


def test_obfuscated_posts_are_put_together_by_poster(paths):
    """
    The articles of an obfuscated post are one collection (one for each
    poster), not a collection each; real posts are left as they are
    """
    articles = [*obfuscated(30, 'one@ngPost.com'),
                *obfuscated(20, 'two@ngPost.com'),
                *release('Holiday', files=2, segments=2)]
    RANDOM.shuffle(articles)
    cache(paths[GROUP], articles)

    indexer = indexed(paths)
    assert collections(indexer) == [
        ('Holiday', 4, 2),
        ('Obfuscated post by one@ngPost.com', 30, 1),
        ('Obfuscated post by two@ngPost.com', 20, 1)]

    # How much of one there is isn't known: it settles, never completes,
    # and shows that
    post = indexer.find(keywords=['one@ngPost'])[0]
    assert post.state == CollectionState.Settled
    assert '      ? ' in NNTPIndexer.summary(post)
    indexer.close()


def test_a_counter_still_keeps_the_files_apart(paths):
    """
    Made up names with a segment counter: one collection, a file for each
    name
    """
    names = [made_up(14) for _ in range(3)]
    cache(paths[GROUP], obfuscated(
        12, 'one@ngPost.com', counter=(names, 4)))

    indexer = indexed(paths)
    assert collections(indexer) == [
        ('Obfuscated post by one@ngPost.com', 12, 3)]
    post = indexer.find()[0]
    assert (post.segments, post.segments_expected) == (12, 12)
    assert post.state == CollectionState.Complete
    indexer.close()


def test_made_up_posters_are_put_together_by_where_they_are_from(paths):
    """
    A made up poster for every article: they are put together by the
    domain they all give
    """
    cache(paths[GROUP], [*obfuscated(40, None),
                         *obfuscated(25, None, domain='other')])

    indexer = indexed(paths)
    assert collections(indexer) == [
        ('Obfuscated post (by *@ngpost.com)', 40, 1),
        ('Obfuscated post (by *@other.com)', 25, 1)]
    indexer.close()


def test_a_pause_in_posting_starts_another_post(paths):
    """
    The same poster posting again after a pause is another post; going on
    across index runs is not
    """
    next_no = cache(paths[GROUP], obfuscated(10, 'one@ngPost.com'))
    caught_up(paths[GROUP], POSTED + timedelta(minutes=10))
    indexer = NNTPIndexer(paths['index'], session_minutes=30)
    indexer.index(GROUP, paths[GROUP])

    # Picked up where it left off (posted a minute apart)
    next_no = cache(paths[GROUP], obfuscated(5, 'one@ngPost.com'),
                    start=next_no, posted=POSTED + timedelta(minutes=10))
    indexer.index(GROUP, paths[GROUP])
    assert collections(indexer) == [
        ('Obfuscated post by one@ngPost.com', 15, 1)]

    # Hours later
    cache(paths[GROUP], obfuscated(5, 'one@ngPost.com'), start=next_no,
          posted=POSTED + timedelta(hours=3))
    caught_up(paths[GROUP], LATER)
    indexer.index(GROUP, paths[GROUP])
    indexer.settle()
    assert collections(indexer) == [
        ('Obfuscated post by one@ngPost.com', 5, 1),
        ('Obfuscated post by one@ngPost.com', 15, 1)]
    indexer.close()


def test_the_files_of_an_obfuscated_post_are_counted_by_size(paths):
    """
    Every article of a file is the same size but its last one, which is
    shorter: the short ones say how many files an obfuscated post has
    (shown as an estimate)
    """
    from newsreap.NNTPGroupDatabase import NNTPGroupDatabase
    from newsreap.objects.group.Article import Article

    cache(paths[GROUP], obfuscated(30, 'one@ngPost.com'))

    # Three files of ten articles (posted mixed together, as ngPost does)
    db = NNTPGroupDatabase.from_file(paths[GROUP])
    session = db.session()
    for no, entry in enumerate(session.query(Article)
                               .order_by(Article.article_no)):
        entry.size = 640000 if no in (4, 17, 25) else 740000
    session.commit()
    db.close()

    indexer = indexed(paths)
    post = indexer.find()[0]
    assert post.files_expected == 3
    assert '     ~3 file(s)  Obfuscated post by one@ngPost.com' in \
        NNTPIndexer.summary(post)
    indexer.close()
