# Tests grouping cached articles (from every group) into files and
# collections (nr update index), and saving, importing and deleting them
# (nr nzb, nr delete)
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
from datetime import datetime
from datetime import timedelta
from os.path import dirname
from os.path import join
import os
import re

import pytest
from click.testing import CliRunner
from sqlalchemy import create_engine

from newsreap.NNTPGroupDatabase import NNTPGroupDatabase
from newsreap.NNTPArchivePost import NNTPArchivePost
from newsreap.NNTPArchiver import NNTPArchiver
from newsreap.NNTPIndexer import NNTPIndexer
from newsreap.NNTPIndexer import gid_order
from newsreap.NNTPIndexer import make_gid
from newsreap.NNTPIndexer import parse_subject
from newsreap.NNTPIndexer import release_name
from newsreap.NNTPnzb import NNTPnzb
from newsreap.NNTPnzb import NZBSegmentNumber
from newsreap.objects.group.Article import Article
from newsreap.objects.index.Collection import Collection
from newsreap.objects.index.Collection import CollectionSource
from newsreap.objects.index.ObjectBase import ObjectBase as IndexBase
from newsreap.objects.index.Collection import CollectionState

from tests.FakeNNTPServer import FakeNNTPServer
from tests.SearchPlan import search_plan
from tests.conftest import config_for

GROUP = 'alt.binaries.test'
OTHER = 'alt.binaries.other'
POSTER = 'poster@example.com'
POSTED = datetime(2020, 1, 1)

# Long after everything above was posted (so it has all settled)
LATER = datetime(2021, 1, 1)

# Our test files
VAR_DIR = join(dirname(__file__), 'var')


def release(name, files=3, segments=4, skip=(), poster=POSTER, only=None,
            tag=''):
    """
    Returns the (subject, poster, message id) of every article of a made
    up release: `files` .rar files of `segments` parts each

    - skip: (file, part) pairs that are missing (never posted)
    - only: the file numbers to include (all of them if None)
    - tag: added to every Message-ID (to make a repost's ids differ)
    """
    for f in range(1, files + 1):
        if only is not None and f not in only:
            continue

        for p in range(1, segments + 1):
            if (f, p) in skip:
                continue

            yield (
                '%s [%02d/%02d] - "%s.part%02d.rar" yEnc (%d/%d)' % (
                    name, f, files, name.lower(), f, p, segments),
                poster,
                '%s%s.%d.%d@example.com' % (name.lower(), tag, f, p))


def cache(db_file, articles, start=1, posted=POSTED):
    """
    Stores articles (as `nr update search` would) and returns the next
    article number; the first is posted at `posted`, the rest a minute
    apart
    """
    db = NNTPGroupDatabase.from_file(db_file)
    session = db.session()
    for no, (subject, poster, message_id) in enumerate(articles, start):
        session.add(Article(
            message_id=message_id, article_no=no, subject=subject,
            poster=poster, size=1000 + no, lines=10,
            posted_date=posted + timedelta(minutes=no - start)))
        start_no = no + 1

    session.commit()
    db.close()
    return start_no


# Database servers the index is tested on too (besides SQLite), given as
# database URLs; ie:
#   NEWSREAP_TEST_POSTGRESQL=postgresql+psycopg2://user:pass@host/db
#   NEWSREAP_TEST_MYSQL=mysql+pymysql://user:pass@host/db
# Everything in them is removed first.
INDEX_SERVERS = [
    name for name in ('postgresql', 'mysql')
    if os.environ.get('NEWSREAP_TEST_%s' % name.upper())]


@pytest.fixture(params=['sqlite', *INDEX_SERVERS])
def index_engine(request, tmp_path):
    """
    Where our index is kept: an SQLite file, or an empty database on each
    database server given (see INDEX_SERVERS)
    """
    if request.param == 'sqlite':
        return str(tmp_path / 'cache' / 'index.db')

    url = os.environ['NEWSREAP_TEST_%s' % request.param.upper()]
    engine = create_engine(url)
    IndexBase.metadata.drop_all(engine)
    engine.dispose()
    return url


@pytest.fixture
def paths(tmp_path, index_engine):
    """
    Where our index and our test groups' articles are kept
    """
    return {
        'index': index_engine,
        GROUP: str(tmp_path / 'cache' / 'search' / 'test.db'),
        OTHER: str(tmp_path / 'cache' / 'search' / 'other.db'),
    }


def caught_up(db_file, when=LATER):
    """
    Marks a group as scanned up to `when` (as a scan that reached the
    server's newest article would)
    """
    db = NNTPGroupDatabase.from_file(db_file)
    db.caught_up('test', when)
    db.close()


def nzb_file(path, name, files, total=3, segments=2, meta=()):
    """
    Writes an NZB file of a made up release (with the Message-IDs
    release() gives) and returns its path

    - files: the file numbers it lists (out of `total`)
    - meta: the (type, value) details of its head
    """
    head = ''.join('  <meta type="%s">%s</meta>\n' % entry for entry in meta)
    body = ''.join(
        ' <file poster="%s" date="1577836800" subject="%s">\n'
        '  <groups><group>%s</group></groups>\n  <segments>\n%s'
        '  </segments>\n </file>\n' % (
            POSTER, '%s [%02d/%02d] - &quot;%s.part%02d.rar&quot; yEnc '
            '(1/%d)' % (name, f, total, name.lower(), f, segments), GROUP,
            ''.join(
                '   <segment bytes="10" number="%d">%s.%d.%d@example.com'
                '</segment>\n' % (p, name.lower(), f, p)
                for p in range(1, segments + 1)))
        for f in files)

    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">\n%s%s</nzb>\n' % (
            '<head>\n%s</head>\n' % head if head else '', body))
    return str(path)


def indexed(paths, *groups, **kwargs):
    """
    Returns an indexer that has indexed (and settled) the groups given

    - seen: when the groups were last caught up with (None to leave them
      as they are)
    """
    seen = kwargs.pop('seen', LATER)
    indexer = NNTPIndexer(paths['index'], **kwargs)
    for group in groups or (GROUP, ):
        if seen is not None:
            caught_up(paths[group], seen)
        indexer.index(group, paths[group])
    indexer.settle()
    return indexer


#
# Reading subjects
#
@pytest.mark.parametrize('subject, expected', [
    ('My.Release [01/10] - "my.release.part01.rar" yEnc (12/50)',
     ('my.release.part01.rar', 'My.Release', 12, 50, 1, 10)),
    ('"my.release.nfo" yEnc (1/1)',
     ('my.release.nfo', '', 1, 1, 0, 0)),
    ('[PRiVATE]-[WtFnZb]-[file.mkv]-[1/5] - "" yEnc 1234 (3/99)',
     ('file.mkv', '[PRiVATE]-[WtFnZb]', 3, 99, 1, 5)),
    ('Re: question (1/2)', ('', 'Re: question', 1, 2, 0, 0)),
    ('holiday photos 2', ('', 'holiday photos 2', 1, 1, 0, 0)),
    ('', ('', '', 1, 1, 0, 0)),
])
def test_subjects_are_understood(subject, expected):
    """
    The file, description and counters are read from many subject styles
    """
    info = parse_subject(subject)
    assert (info['filename'], info['desc'], info['part'], info['parts'],
            info['file_no'], info['files']) == expected


def test_segments_of_a_file_share_a_base():
    """
    Only the segment counter differs between the subjects of one file
    """
    first = parse_subject('A [1/2] - "a.part1.rar" yEnc (1/50)')['base']
    assert first == parse_subject('A [1/2] - "a.part1.rar" yEnc (7/50)')['base']
    assert first != parse_subject('A [2/2] - "a.part2.rar" yEnc (1/50)')['base']


def test_release_names():
    """
    The endings that set a release's files apart are removed
    """
    assert release_name('my.release.part01.rar') == 'my.release'
    assert release_name('my.release.vol00+01.par2') == 'my.release'
    assert release_name('my.release.r07') == 'my.release'
    assert release_name('show.s01e01.mkv') == 'show.s01e01.mkv'
    assert release_name('.nfo') == '.nfo'


#
# Indexing
#
def test_articles_become_files_and_collections(paths):
    """
    The articles of a release become its files and one collection
    """
    cache(paths[GROUP], [
        *release('Holiday', files=3, segments=4),
        *release('Other', files=1, segments=2, poster='someone@else'),
        ('Just a question', 'asker@example.com', 'q@example.com'),
    ])

    indexer = NNTPIndexer(paths['index'], chunk_size=5)
    assert indexer.index(GROUP, paths[GROUP]) is True
    assert (indexer.articles, indexer.files, indexer.collections) == \
        (15, 5, 3)
    assert str(indexer) == \
        '15 article(s) indexed; 5 new file(s) in 3 new collection(s)'
    assert indexer.settle() == 3

    holiday = indexer.find(['holiday'])[0]
    assert holiday.name == 'Holiday'
    assert (holiday.files, holiday.files_expected) == (3, 3)
    assert (holiday.segments, holiday.segments_expected) == (12, 12)
    assert holiday.size == sum(1000 + n for n in range(1, 13))
    assert holiday.state == CollectionState.Complete
    assert holiday.first_posted == POSTED
    assert holiday.last_posted == POSTED + timedelta(minutes=11)
    assert indexer.groups(holiday.id) == [GROUP]

    # Its GID comes from the first segment of its first file
    assert holiday.gid == make_gid('holiday.1.1@example.com')
    assert indexer.get(holiday.gid).id == holiday.id
    assert indexer.get(str(holiday.id)).id == holiday.id
    assert indexer.get('nope') is None

    # Nothing new; nothing done
    assert indexer.index(GROUP, paths[GROUP]) is True
    assert str(indexer) == 'nothing new to index'
    assert indexer.settle() == 0
    indexer.close()


def test_collections_span_groups(paths):
    """
    A release spread over (and crossposted to) several groups is one
    collection; each file knows which groups it was found in
    """
    cache(paths[GROUP], release('Spread', only={1, 2}))

    # File 3 was only posted to the other group; file 2 was crossposted
    cache(paths[OTHER], release('Spread', only={2, 3}))

    indexer = indexed(paths, GROUP, OTHER)
    found = indexer.find()
    assert len(found) == 1
    spread = found[0]
    assert (spread.files, spread.segments) == (3, 12)
    assert spread.complete is True
    assert indexer.groups(spread.id) == [OTHER, GROUP]

    # Listing by group finds it in either one
    assert indexer.find(groups=[GROUP])[0].id == spread.id
    assert indexer.find(groups=[OTHER])[0].id == spread.id
    assert indexer.find(groups=['alt.binaries.none']) == []
    indexer.close()


def test_collections_form_once_settled(paths):
    """
    A collection missing parts forms once its group has been seen well past
    it, and completes when the missing parts turn up later
    """
    missing = {(2, 3), (3, 1)}
    next_no = cache(paths[GROUP], release('Late', skip=missing))

    # We have only seen up to its last article; it is still forming (it is
    # years old by the clock on the wall, but we may simply not have
    # fetched the rest yet)
    indexer = indexed(paths, seen=None)
    assert indexer.find() == []
    late = indexer.find(forming=True)[0]
    assert late.state == CollectionState.Forming
    assert late.gid is None

    # An hour later is not long enough
    caught_up(paths[GROUP], POSTED + timedelta(hours=1))
    indexer.index(GROUP, paths[GROUP])
    assert indexer.settle() == 0

    # Hours later it has settled (as complete as it will get)
    caught_up(paths[GROUP], POSTED + timedelta(hours=5))
    indexer.index(GROUP, paths[GROUP])
    assert indexer.settle() == 1
    late = indexer.get(late.id)
    assert late.state == CollectionState.Settled
    assert (late.segments, late.segments_expected) == (10, 12)
    assert indexer.find(complete=False)[0].id == late.id
    assert indexer.find(complete=True) == []

    # The missing segments show up (one of them twice)
    articles = [a for a in release('Late') if a[2] in (
        'late.2.3@example.com', 'late.3.1@example.com')]
    duplicate = (articles[0][0], POSTER, 'repost@example.com')
    cache(paths[GROUP], [*articles, duplicate], start=next_no,
          posted=POSTED + timedelta(hours=6))

    indexer.index(GROUP, paths[GROUP])
    assert (indexer.articles, indexer.files, indexer.collections) == \
        (3, 0, 0)
    indexer.settle()
    late = indexer.get(late.id)
    assert (late.segments, late.segments_expected) == (12, 12)
    assert late.state == CollectionState.Complete
    indexer.close()


def test_the_least_seen_group_decides(paths):
    """
    A collection spread over groups waits for the one seen the least
    """
    cache(paths[GROUP], release('Wait', only={1}))
    cache(paths[OTHER], release('Wait', only={2}))

    # One group was scanned days later; the other only up to the post
    caught_up(paths[GROUP], POSTED + timedelta(days=2))
    indexer = indexed(paths, OTHER, seen=None)
    indexer.index(GROUP, paths[GROUP])
    indexer.settle()
    assert indexer.find(forming=True)[0].state == CollectionState.Forming

    # Once the other group is seen past it too, it settles
    caught_up(paths[OTHER], POSTED + timedelta(days=1))
    indexer.index(OTHER, paths[OTHER])
    assert indexer.settle() == 1
    assert indexer.find()[0].state == CollectionState.Settled
    indexer.close()


def test_settling_can_be_told_the_time(paths):
    """
    settle(now=...) counts every group as seen up to then, so collections
    from groups we no longer scan can settle (and they stay settled)
    """
    cache(paths[GROUP], release('Stale', skip={(1, 2)}))
    indexer = indexed(paths, seen=None)
    assert indexer.settle() == 0

    # Not long enough after its last article
    assert indexer.settle(now=POSTED + timedelta(hours=1)) == 0
    assert indexer.find() == []

    # Long enough (a time with a timezone works too)
    assert indexer.settle(now=LATER.replace(tzinfo=UTC)) == 1
    stale = indexer.find()[0]
    assert stale.state == CollectionState.Settled

    # Its group's own clock is still behind; that doesn't undo it
    indexer.settle()
    assert indexer.get(stale.id).state == CollectionState.Settled
    indexer.close()


def test_indexing_again_after_a_crash_adds_nothing(paths):
    """
    Articles indexed but not yet marked as indexed in their cache (ie: the
    program was killed in between) don't make duplicates when indexed again
    """
    cache(paths[GROUP], release('Twice', files=2, segments=3))
    indexer = indexed(paths)
    before = [(c.id, c.files, c.segments) for c in indexer.find()]
    assert before == [(1, 2, 6)]

    # As if we were stopped before the cache was told
    db = NNTPGroupDatabase.from_file(paths[GROUP])
    session = db.session()
    session.query(Article).update({Article.indexed: False})
    session.commit()
    db.close()

    indexer.index(GROUP, paths[GROUP])
    assert (indexer.files, indexer.collections) == (0, 0)
    indexer.settle()
    assert [(c.id, c.files, c.segments) for c in indexer.find()] == before
    indexer.close()


def test_reposts_are_collections_of_their_own(paths):
    """
    The same release posted again long after is a new collection
    """
    next_no = cache(paths[GROUP], release('Again', files=1, segments=2))
    cache(paths[GROUP], release('Again', files=1, segments=2, tag='.r'),
          start=next_no, posted=POSTED + timedelta(days=30))

    indexer = indexed(paths)
    found = indexer.find()
    assert len(found) == 2
    assert found[0].first_posted > found[1].first_posted
    assert found[0].gid != found[1].gid
    indexer.close()


def test_reposts_can_be_kept_together(tmp_path):
    """
    With repost_days set to 0 a repost joins the first collection
    """
    paths = {'index': str(tmp_path / 'index.db'), GROUP: str(
        tmp_path / 'test.db')}
    next_no = cache(paths[GROUP], release('Again', files=1, segments=2))
    cache(paths[GROUP], release('Again', files=1, segments=2, tag='.r'),
          start=next_no, posted=POSTED + timedelta(days=30))

    indexer = indexed(paths, repost_days=0)
    found = indexer.find()
    assert len(found) == 1
    assert found[0].segments == 2
    indexer.close()


def test_finding_collections(paths):
    """
    Collections are found by keywords (and left out with -keyword)
    """
    cache(paths[GROUP], [
        *release('Holiday.2019', files=1, segments=1),
        *release('Holiday.2020', files=1, segments=1),
        *release('Work.2020', files=1, segments=1),
    ])
    indexer = indexed(paths)

    names = lambda *k: sorted(c.name for c in indexer.find(k))  # noqa: E731
    assert names('holiday') == ['Holiday.2019', 'Holiday.2020']
    assert names('2020') == ['Holiday.2020', 'Work.2020']
    assert names('holiday', '-2019') == ['Holiday.2020']
    assert len(indexer.find(limit=1)) == 1

    # Newest first
    assert indexer.find()[0].name == 'Work.2020'
    indexer.close()


def test_searches_look_through_file_subjects(paths, tmp_path):
    """
    A keyword can be in a collection's name or in any of its file subjects
    (an obfuscated post is often only named by its NZB file)
    """
    indexer = NNTPIndexer(paths['index'])
    path = nzb_file(tmp_path / 'post.nzb', 'Xq7Kd', (1, 2), meta=(
        ('title', 'Mystery.Show'), ))
    collection_id = indexer.import_nzb(path)[0].id

    ids = lambda *k, **kw: [  # noqa: E731
        c.id for c in indexer.find(k, **kw)]
    assert ids('mystery') == [collection_id]
    assert ids('xq7kd.part02') == [collection_id]
    assert ids('mystery', 'xq7') == [collection_id]
    assert ids('mystery', '-part02') == []
    assert ids('nothing') == []

    # Merging new details renames it; the old name is no longer found
    path = nzb_file(tmp_path / 'post.nzb', 'Xq7Kd', (1, 2), meta=(
        ('title', 'Renamed.Show'), ))
    indexer.import_nzb(path, merge=True)
    assert ids('renamed') == [collection_id]
    assert ids('mystery') == []

    # Removed collections aren't found
    indexer.delete(collection_id)
    assert ids('renamed') == []
    assert ids('xq7kd') == []
    indexer.close()


def test_case_is_ignored_unless_asked(paths, tmp_path):
    """
    Searches ignore case (and accents) by default; a case sensitive search
    only finds the same case
    """
    indexer = NNTPIndexer(paths['index'])
    indexer.import_nzb(nzb_file(tmp_path / 'a.nzb', 'Cafe', (1, ), meta=(
        ('title', 'Café.Society.MiXeD'), )))

    names = lambda *k, **kw: [  # noqa: E731
        c.name for c in indexer.find(k, **kw)]
    assert names('mixed') == ['Café.Society.MiXeD']
    assert names('MIXED') == ['Café.Society.MiXeD']
    assert names('cafe.society') == ['Café.Society.MiXeD']
    assert names('mixed', case_sensitive=True) == []
    assert names('MiXeD', case_sensitive=True) == ['Café.Society.MiXeD']
    assert names('-MIXED', case_sensitive=True) == ['Café.Society.MiXeD']

    # Keywords too short for the search index still work
    assert names('xe') == ['Café.Society.MiXeD']
    assert names('Xe', case_sensitive=True) == ['Café.Society.MiXeD']
    assert names('XE', case_sensitive=True) == []

    # Quotes and wildcards are only text
    assert names('"cafe.part01') == ['Café.Society.MiXeD']
    assert names('Society"') == []
    assert names('Caf%') == []
    assert names('%z') == []
    assert names('Soc_ety') == []
    indexer.close()


def test_searches_use_the_search_index(paths, tmp_path):
    """
    Searching collections uses the database's search index (never reading
    every name and subject, which is far too slow on a big index)
    """
    indexer = NNTPIndexer(paths['index'])
    indexer.import_nzb(nzb_file(tmp_path / 'a.nzb', 'Holiday', (1, 2)))
    session = indexer.session()
    dialect = session.get_bind().dialect.name

    # The search itself (what find() narrows the collections down with)
    plan = search_plan(session, lambda: session.query(Collection.id).filter(
        Collection.id.in_(indexer._search(
            session, 0, 'subject', 'holiday'))).all())
    if dialect == 'sqlite':
        # Looked up with MATCH (M) or LIKE (L), not read row by row
        assert re.search(
            r'collection_search VIRTUAL TABLE INDEX \d+:[ML]', plan)

    elif dialect == 'postgresql':
        for index in ('collection_name_search',
                      'collection_file_subject_search',
                      'collection_file_filename_search'):
            assert index in plan

    else:
        assert 'collection_name_search' in plan
        assert 'collection_file_subject_search' in plan
        # The search runs once, not once for every collection
        assert 'DEPENDENT SUBQUERY' not in plan

    indexer.close()


def test_real_file_names_are_learned(paths):
    """
    The real file names the archive finds (ie: in a yEnc header) name the
    files of obfuscated posts, whichever of the two runs first; they can
    be searched for
    """
    def obfuscated(start):
        # Two articles of a file whose subject says nothing about it
        return [('x7f9a2b (%d/2)' % p, 'poster@example.com',
                 'part%d.%d@example.com' % (start, p)) for p in (1, 2)]

    def archived(message_id, name):
        raw = ('Subject: x\r\n\r\n=ybegin part=1 total=2 line=128 '
               'size=10 name=%s\r\ndata\r\n=yend size=5\r\n' % name)
        archiver = NNTPArchiver(paths['index'])
        session = archiver.session(create=True)
        archiver.store(session, archiver.convert(
            NNTPArchivePost(message_id, raw=raw.encode())))
        NNTPIndexer.learn_filenames(session, {message_id: name})
        session.commit()
        archiver.close()

    # Indexed first, then archived
    cache(paths[GROUP], obfuscated(1))
    indexer = indexed(paths)
    assert indexer.find(['holiday'], forming=True) == []
    indexer.close()

    archived('part1.1@example.com', 'Holiday.Video.mkv')
    indexer = NNTPIndexer(paths['index'])
    found = indexer.find(['holiday.video'], forming=True)
    assert len(found) == 1
    assert [f.filename for f in indexer._files(
        indexer.session(), found[0].id)] == ['Holiday.Video.mkv']
    indexer.close()

    # Archived first, then indexed
    archived('part2.1@example.com', 'Beach.Trip.mkv')
    cache(paths[OTHER], obfuscated(2))
    indexer = indexed(paths, OTHER)
    found = indexer.find(['beach', 'trip'], forming=True)
    assert len(found) == 1
    assert [f.filename for f in indexer._files(
        indexer.session(), found[0].id)] == ['Beach.Trip.mkv']
    indexer.close()


def test_searching_by_poster(paths):
    """
    %p looks in the posters instead (and %p- leaves them out)
    """
    cache(paths[GROUP], [
        *release('Holiday', files=1, segments=1),
        *release('Work', files=1, segments=1, poster='boss@work.com'),
    ])
    indexer = indexed(paths)

    names = lambda *k: sorted(c.name for c in indexer.find(k))  # noqa: E731
    assert names('%pboss') == ['Work']
    assert names('%p-boss') == ['Holiday']
    assert names('%pboss', 'holiday') == []
    assert names('%p+poster@') == ['Holiday']
    indexer.close()


def test_index_can_be_kept_on_a_database_server(tmp_path):
    """
    The index is an SQLite file in our cache unless index_engine names a
    database (ie: PostgreSQL or MySQL)
    """
    from newsreap.NNTPSettings import NNTPSettings

    settings = NNTPSettings()
    settings.base_dir = str(tmp_path)
    assert settings.index_database() == \
        join(str(tmp_path), 'cache', 'index.db')

    settings.nntp_database['index_engine'] = 'postgresql://u:p@host/index'
    assert settings.index_database() == 'postgresql://u:p@host/index'

    # A URL to an SQLite file works too
    path = str(tmp_path / 'elsewhere.db')
    indexer = NNTPIndexer('sqlite:///%s' % path)
    assert indexer.session() is None
    assert indexer.session(create=True) is not None
    indexer.close()
    assert NNTPIndexer('sqlite:///%s' % path).session() is not None


def test_nothing_indexed(tmp_path):
    """
    Nothing can be found, saved or removed before anything is indexed
    """
    indexer = NNTPIndexer(str(tmp_path / 'missing.db'))
    assert indexer.index(GROUP, str(tmp_path / 'never.db')) is False
    assert indexer.settle() == 0
    assert indexer.find() == []
    assert indexer.get(1) is None
    assert indexer.groups(1) == []
    assert indexer.nzb(1, str(tmp_path / 'x.nzb')) is False
    assert indexer.delete(1) is False


#
# NZB files
#
def test_collection_saved_as_nzb(paths, tmp_path):
    """
    An NZB holds every file of the collection, one article per segment and
    the groups each file was found in
    """
    next_no = cache(paths[GROUP], release('Holiday', files=2, segments=3))
    # Segment 2 of the first file was posted twice
    cache(paths[GROUP], [(
        'Holiday [01/02] - "holiday.part01.rar" yEnc (2/3)', POSTER,
        'repost@example.com')], start=next_no)

    # The second file was crossposted
    cache(paths[OTHER], release('Holiday', files=2, segments=3, only={2}))

    indexer = indexed(paths, GROUP, OTHER)
    collection = indexer.find()[0]

    path = str(tmp_path / 'holiday.nzb')
    assert indexer.nzb(collection.gid, path) is True
    assert indexer.nzb(999, path) is False
    indexer.close()

    nzb = NNTPnzb(path)
    assert nzb.is_valid() is True
    posts = list(nzb)
    # (the NZB reader names files from their subject's description)
    assert [p.filename.lower() for p in posts] == \
        ['holiday.part01.rar', 'holiday.part02.rar']

    first = [(a.no, a.id, len(next(iter(a)))) for a in posts[0]]
    assert first == [
        (1, 'holiday.1.1@example.com', 1001),
        (2, 'holiday.1.2@example.com', 1002),
        (3, 'holiday.1.3@example.com', 1003)]
    assert posts[0].groups == {GROUP}
    assert posts[1].groups == {GROUP, OTHER}
    assert posts[0].utc == POSTED
    assert nzb.meta == {'title': 'Holiday'}


def test_missing_parts_keep_their_numbers(paths, tmp_path):
    """
    A missing part leaves a gap in the NZB's segment numbers
    """
    cache(paths[GROUP], release('Gap', files=1, segments=4, skip={(1, 2)}))
    indexer = indexed(paths)

    path = str(tmp_path / 'gap.nzb')
    assert indexer.nzb(indexer.find()[0].id, path) is True
    indexer.close()

    with open(path) as f:
        content = f.read()

    for number, part in ((1, 1), (3, 3), (4, 4)):
        assert 'number="%d">gap.1.%d@example.com<' % (number, part) \
            in content
    assert 'number="2"' not in content


def test_segments_counted_by_default(paths, tmp_path):
    """
    Without asking for part numbers, segments are counted in order
    """
    source = str(tmp_path / 'source.nzb')
    cache(paths[GROUP], release('Gap', files=1, segments=3, skip={(1, 2)}))
    indexer = indexed(paths)
    indexer.nzb(indexer.find()[0].id, source)
    indexer.close()

    nzb = NNTPnzb(source)
    posts = list(nzb)
    nzb = NNTPnzb()
    for post in posts:
        nzb.add(post)

    counted = str(tmp_path / 'counted.nzb')
    assert nzb.save(counted) is True
    with open(counted) as f:
        assert 'number="2">gap.1.3@example.com<' in f.read()

    kept = str(tmp_path / 'kept.nzb')
    assert nzb.save(kept, numbering=NZBSegmentNumber.Article) is True
    with open(kept) as f:
        assert 'number="3">gap.1.3@example.com<' in f.read()

    assert nzb.save(str(tmp_path / 'bad.nzb'), numbering='nope') is False


def test_nzb_files_are_imported(paths, tmp_path):
    """
    An imported NZB file becomes a collection with the same GID it would
    have if we had scanned it, and is only added once
    """
    cache(paths[GROUP], release('Shared', files=2, segments=3,
                                skip={(2, 3)}))
    indexer = indexed(paths)
    scanned = indexer.find()[0]
    path = str(tmp_path / 'shared.nzb')
    indexer.nzb(scanned.id, path)
    indexer.close()

    # Someone else imports it into their own index
    other = NNTPIndexer(str(tmp_path / 'elsewhere.db'))
    collection, added = other.import_nzb(path)
    assert added is True
    assert collection.gid == scanned.gid
    assert collection.name == 'Shared'
    assert (collection.files, collection.segments,
            collection.segments_expected) == (2, 5, 6)
    assert collection.state == CollectionState.Settled
    assert collection.first_posted == POSTED
    assert other.groups(collection.id) == [GROUP]

    # It can be saved again just the same
    again = str(tmp_path / 'again.nzb')
    assert other.nzb(collection.gid, again) is True
    assert [len(post) for post in NNTPnzb(again)] == [3, 2]

    # Importing it twice doesn't add it twice
    collection, added = other.import_nzb(path)
    assert added is False
    assert len(other.find()) == 1

    # Something that isn't an NZB file
    junk = tmp_path / 'junk.nzb'
    junk.write_text('not xml')
    assert other.import_nzb(str(junk)) == (None, False)
    other.close()


#
# Lining up with newznab
#
@pytest.mark.parametrize('subject, expected', [
    ('My.File.rar (1/50)', 'my.file.rar'),
    ('  "A.nfo" yEnc (1/1)  ', '"a.nfo" yenc'),
    # Only the last counter is taken out (every copy of it)
    ('[1/5] Name (1/9) (2/9) (1/9)', '[1/5] name  (2/9)'),
    ('No Counter', 'no counter'),
])
def test_files_sort_like_newznab(subject, expected):
    """
    Files sort by their subject without its counter, ignoring case
    """
    assert gid_order(subject) == expected


def test_gids_match_what_newznab_reads(paths, tmp_path):
    """
    A collection's GID is the one newznab (or anyone) reads back from its
    NZB file: the first segment of the first file listed
    """
    # Byte by byte 'B' sorts before 'a', but newznab ignores case; the 'a'
    # file comes first (and its lowest part, not the first one posted)
    cache(paths[GROUP], [
        ('Files - "B.rar" yEnc (1/2)', POSTER, 'b.1@x'),
        ('Files - "B.rar" yEnc (2/2)', POSTER, 'b.2@x'),
        ('Files - "a.rar" yEnc (2/2)', POSTER, 'a.2@x'),
        ('Files - "a.rar" yEnc (1/2)', POSTER, 'a.1@x'),
    ])
    indexer = indexed(paths)
    collection = indexer.find()[0]
    assert collection.gid == make_gid('a.1@x')

    path = str(tmp_path / 'files.nzb')
    assert indexer.nzb(collection.id, path) is True
    indexer.close()

    nzb = NNTPnzb(path)
    assert nzb.gid() == collection.gid
    assert [p.subject for p in nzb][0] == 'Files - "a.rar" yEnc (1/2)'


def test_imports_keep_the_gid_newznab_gave_them(tmp_path):
    """
    An imported NZB file keeps the GID read from it, whatever order its
    files are in
    """
    indexer = NNTPIndexer(str(tmp_path / 'index.db'))

    # The sample NZB file's GID is known
    collection, added = indexer.import_nzb(
        join(VAR_DIR, 'Ubuntu-16.04.1-Server-i386.nzb'))
    assert added is True
    assert collection.gid == '8c6b3a3bc8d925cd63125f7bea31a5c9'

    # Files listed out of order; the first one listed is what counts
    path = tmp_path / 'unsorted.nzb'
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">\n'
        ' <file poster="p@x" date="1577836800" subject="Z.rar (1/1)">\n'
        '  <groups><group>%s</group></groups>\n'
        '  <segments><segment bytes="10" number="1">z.1@x</segment>'
        '</segments>\n </file>\n'
        ' <file poster="p@x" date="1577836800" subject="A.rar (1/1)">\n'
        '  <groups><group>%s</group></groups>\n'
        '  <segments><segment bytes="10" number="1">a.1@x</segment>'
        '</segments>\n </file>\n'
        '</nzb>\n' % (GROUP, GROUP))

    collection, added = indexer.import_nzb(str(path))
    assert added is True
    assert collection.gid == make_gid('z.1@x')
    indexer.close()


#
# NZB files are the real post
#
def test_nzb_files_take_over_what_we_scanned(paths, tmp_path):
    """
    An NZB file covering a scanned collection becomes it (keeping its id
    and every detail the NZB file has)
    """
    cache(paths[GROUP], release('Truth', files=2, segments=2))
    indexer = indexed(paths)
    scanned = indexer.find()[0]
    assert scanned.source == CollectionSource.Scan

    # An NZB file of the post (with details of its own)
    path = str(tmp_path / 'truth.nzb')
    indexer.nzb(scanned.id, path)
    nzb = NNTPnzb(path)
    posts = list(nzb)
    nzb = NNTPnzb()
    nzb.meta = {'title': 'The.Truth', 'password': ['secret', 'other'],
                'propername': 'The Truth'}
    for post in posts:
        nzb.add(post)
    assert nzb.save(path) is True

    collection, added = indexer.import_nzb(path)
    assert added is True
    assert collection.id == scanned.id
    assert collection.source == CollectionSource.Nzb
    assert collection.name == 'The.Truth'
    assert collection.gid == scanned.gid
    assert (collection.files, collection.segments) == (2, 4)
    assert len(indexer.find()) == 1

    # Saving it again keeps every detail
    again = str(tmp_path / 'again.nzb')
    assert indexer.nzb(collection.id, again) is True
    nzb = NNTPnzb(again)
    assert len(list(nzb)) == 2
    assert nzb.meta == {
        'title': 'The.Truth', 'password': ['secret', 'other'],
        'propername': 'The Truth'}

    # Importing it again changes nothing
    assert indexer.import_nzb(path)[1] is False
    indexer.close()


def test_scanned_leftovers_stay_scanned(paths, tmp_path):
    """
    What a scanned collection has that the NZB file doesn't list stays a
    scanned collection (with a GID of its own once formed again)
    """
    cache(paths[GROUP], release('Part', files=3, segments=2))
    indexer = indexed(paths)
    scanned = indexer.find()[0]
    scanned_id, scanned_gid = scanned.id, scanned.gid

    # An NZB file listing only the first two files
    path = tmp_path / 'part.nzb'
    files = ''.join(
        ' <file poster="%s" date="1577836800" subject="%s">\n'
        '  <groups><group>%s</group></groups>\n  <segments>\n%s'
        '  </segments>\n </file>\n' % (
            POSTER, 'Part [%02d/03] - &quot;part.part%02d.rar&quot; yEnc '
            '(1/2)' % (f, f), GROUP, ''.join(
                '   <segment bytes="10" number="%d">part.%d.%d@example.com'
                '</segment>\n' % (p, f, p) for p in (1, 2)))
        for f in (1, 2))
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">\n%s</nzb>\n'
        % files)

    collection, added = indexer.import_nzb(str(path))
    assert added is True
    assert collection.id != scanned_id
    assert collection.gid == scanned_gid
    assert (collection.files, collection.segments) == (2, 4)

    # The scanned one keeps the third file and forms again
    indexer.settle()
    leftover = indexer.get(scanned_id)
    assert (leftover.files, leftover.segments) == (1, 2)
    assert leftover.source == CollectionSource.Scan
    assert leftover.gid == make_gid('part.3.1@example.com')
    indexer.close()


def test_scanning_leaves_nzb_collections_alone(paths, tmp_path):
    """
    Articles an NZB file lists aren't put anywhere else by later scans,
    even when found again in another group
    """
    cache(paths[GROUP], release('Kept', files=1, segments=2))
    indexer = indexed(paths)
    path = str(tmp_path / 'kept.nzb')
    indexer.nzb(indexer.find()[0].id, path)
    collection, _ = indexer.import_nzb(path)

    # The same articles show up in another group
    cache(paths[OTHER], release('Kept', files=1, segments=2))
    indexer.index(OTHER, paths[OTHER])
    assert str(indexer) == \
        '2 article(s) indexed; 0 new file(s) in 0 new collection(s)'
    indexer.settle()

    found = indexer.find()
    assert [c.id for c in found] == [collection.id]
    assert indexer.groups(collection.id) == [GROUP]
    indexer.close()


#
# Deleting
#
def test_another_nzb_file_only_adds_details(paths, tmp_path):
    """
    A second NZB file with articles we have (or fewer) isn't a collection
    of its own; it only adds the details we didn't have
    """
    indexer = NNTPIndexer(paths['index'])
    first = nzb_file(tmp_path / 'first.nzb', 'Dup', (1, 2), meta=(
        ('title', 'Dup'), ('password', 'one'), ('category', 'TV')))
    collection, added = indexer.import_nzb(first)
    assert added is True
    collection_id = collection.id

    second = nzb_file(tmp_path / 'second.nzb', 'Dup', (2, ), meta=(
        ('title', 'Other'), ('password', 'two'), ('category', 'Movies'),
        ('tag', 'hd')))
    collection, added = indexer.import_nzb(second)
    assert (collection.id, added) == (collection_id, False)
    assert (indexer.details, indexer.left_out) == (2, [])
    assert collection.name == 'Dup'
    assert len(indexer.find()) == 1

    # Its GID finds our collection
    assert indexer.get(make_gid('dup.2.1@example.com')).id == collection_id

    # Every password is kept; the rest keep the first NZB file's values
    path = str(tmp_path / 'saved.nzb')
    assert indexer.nzb(collection_id, path) is True
    nzb = NNTPnzb(path)
    assert len(list(nzb)) == 2
    assert nzb.meta == {
        'title': 'Dup', 'password': ['one', 'two'], 'category': 'TV',
        'tag': 'hd'}

    # Nothing new the second time
    assert indexer.import_nzb(second)[0].id == collection_id
    assert indexer.details == 0

    # Once removed, neither NZB file brings it back
    assert indexer.delete(collection_id) is True
    assert indexer.import_nzb(second) == (None, False)
    assert indexer.import_nzb(first) == (None, False)
    indexer.close()


def test_extra_files_are_only_added_when_merging(paths, tmp_path):
    """
    Files another NZB file lists on top of ours are left out unless we
    merge (they could be fixes or bloat)
    """
    indexer = NNTPIndexer(paths['index'])
    first = nzb_file(tmp_path / 'first.nzb', 'More', (1, 2))
    collection, _ = indexer.import_nzb(first)
    collection_id, gid = collection.id, collection.gid

    # The third file was also found by scanning
    cache(paths[GROUP], release('More', files=3, segments=2, only={3}))
    indexed(paths).close()
    assert len(indexer.find()) == 2

    bigger = nzb_file(tmp_path / 'bigger.nzb', 'More', (1, 2, 3), meta=(
        ('title', 'More.Fixed'), ))
    collection, added = indexer.import_nzb(bigger)
    assert (collection.id, added) == (collection_id, False)
    assert indexer.left_out == [
        'More [03/03] - "more.part03.rar" yEnc (1/2)']
    assert collection.files == 2
    assert collection.name == 'first'

    collection, added = indexer.import_nzb(bigger, merge=True)
    assert (collection.id, added) == (collection_id, True)
    assert (indexer.merged, indexer.left_out) == (1, [])
    assert (collection.files, collection.files_expected) == (3, 3)
    assert collection.name == 'More.Fixed'
    assert collection.state == CollectionState.Complete
    assert collection.gid == gid

    # The scanned copy of the third file went with it
    assert [c.id for c in indexer.find()] == [collection_id]

    # The extra file goes last, so the GID read back stays the same
    path = str(tmp_path / 'saved.nzb')
    assert indexer.nzb(collection_id, path) is True
    nzb = NNTPnzb(path)
    assert [post.subject[:12] for post in nzb] == [
        'More [01/03]', 'More [02/03]', 'More [03/03]']
    assert nzb.gid() == gid
    indexer.close()


def test_nzb_files_sharing_only_some_articles_are_refused(paths, tmp_path):
    """
    An NZB file with only some of a collection's articles (and some of its
    own) is refused unless it is merged; only one of them can have them
    """
    indexer = NNTPIndexer(paths['index'])
    first = nzb_file(tmp_path / 'first.nzb', 'Half', (1, 2))
    collection_id = indexer.import_nzb(first)[0].id

    other = nzb_file(tmp_path / 'other.nzb', 'Half', (2, 3))
    assert indexer.import_nzb(other) == (None, False)
    assert indexer.get(collection_id).files == 2
    assert indexer.get(make_gid('half.2.1@example.com')) is None
    assert len(indexer.find()) == 1
    indexer.close()


def test_merging_replaces_what_we_had(paths, tmp_path):
    """
    A merged NZB file replaces every collection sharing its articles: one
    keeps its id, the files it doesn't list are let go of, and the details
    they had are kept unless it gives new ones
    """
    indexer = NNTPIndexer(paths['index'])
    first = nzb_file(tmp_path / 'first.nzb', 'Mix', (1, 2), total=4, meta=(
        ('title', 'Mix.Old'), ('password', 'one'), ('category', 'TV')))
    second = nzb_file(tmp_path / 'second.nzb', 'Mix', (3, 4), total=4, meta=(
        ('password', 'two'), ('tag', 'hd')))
    first_id = indexer.import_nzb(first)[0].id
    second_id = indexer.import_nzb(second)[0].id
    old_gids = [indexer.get(i).gid for i in (first_id, second_id)]

    # It lists a file of each (and one of its own)
    fixed = nzb_file(tmp_path / 'fixed.nzb', 'Mix', (2, 3, 5), total=4,
                     meta=(('title', 'Mix.Fixed'), ('category', 'Movies')))
    assert indexer.import_nzb(fixed) == (None, False)

    collection, added = indexer.import_nzb(fixed, merge=True)
    assert (collection.id, added) == (first_id, True)
    assert indexer.replaced == [second_id]
    assert (indexer.merged, indexer.dropped) == (2, 1)
    assert collection.name == 'Mix.Fixed'
    assert collection.gid == make_gid('mix.2.1@example.com')
    assert (collection.files, collection.files_expected) == (3, 3)

    # Only what it lists is left; nothing else points to the old ones
    assert [c.id for c in indexer.find(forming=True)] == [first_id]
    assert indexer.find(['part01']) == []
    assert indexer.find(['part04']) == []
    assert [c.id for c in indexer.find(['part05'])] == [first_id]
    for gid in old_gids:
        assert indexer.get(gid).id == first_id

    # Every detail they had is kept (new values replace old ones, and
    # passwords and tags are added to)
    path = str(tmp_path / 'saved.nzb')
    assert indexer.nzb(first_id, path) is True
    nzb = NNTPnzb(path)
    assert [post.subject[:11] for post in nzb] == [
        'Mix [02/04]', 'Mix [03/04]', 'Mix [05/04]']
    assert nzb.meta == {
        'title': 'Mix.Fixed', 'password': ['one', 'two'],
        'category': 'Movies', 'tag': 'hd'}

    # The articles it let go of can be found by scanning again
    cache(paths[GROUP], release('Mix', files=4, segments=2, only={1}))
    indexed(paths).close()
    leftover = [c for c in indexer.find() if c.id != first_id]
    assert [(c.source, c.files) for c in leftover] == [
        (CollectionSource.Scan, 1)]

    # Importing either old NZB file again finds the merged one
    assert indexer.import_nzb(first)[0].id == first_id
    assert indexer.import_nzb(second)[0].id == first_id
    indexer.close()


def test_deleted_collections_stay_gone(paths, tmp_path):
    """
    A deleted collection is removed and its articles (and NZB files of
    it) don't bring it back
    """
    cache(paths[GROUP], [
        *release('Keep', files=1, segments=2),
        *release('Junk', files=2, segments=2),
    ])
    indexer = indexed(paths)
    junk = indexer.find(['junk'])[0]
    path = str(tmp_path / 'junk.nzb')
    indexer.nzb(junk.id, path)

    assert indexer.delete(junk.gid) is True
    assert indexer.get(junk.id) is None
    assert [c.name for c in indexer.find()] == ['Keep']

    # It can't be deleted twice
    assert indexer.delete(junk.id) is False

    # Its articles cached again (ie: nr update search --force)
    db = NNTPGroupDatabase.from_file(paths[GROUP])
    session = db.session()
    session.query(Article).update({Article.indexed: False})
    session.commit()
    db.close()

    indexer.index(GROUP, paths[GROUP])
    indexer.settle()
    assert [c.name for c in indexer.find()] == ['Keep']

    # Its NZB file can't bring it back either
    assert indexer.import_nzb(path) == (None, False)
    assert [c.name for c in indexer.find()] == ['Keep']
    indexer.close()


#
# Old headers
#
def test_old_headers_are_purged(paths):
    """
    Indexed headers older than the retention are removed; the rest stay
    """
    cache(paths[GROUP], release('Old', files=1, segments=2))
    indexed(paths).close()
    cache(paths[GROUP], release('New', files=1, segments=2), start=10,
          posted=datetime(2020, 6, 1))

    db = NNTPGroupDatabase.from_file(paths[GROUP])
    assert db.purge(0) == 0

    # Only the indexed ones go, however old
    assert db.purge(1) == 2
    session = db.session()
    assert [a.message_id for a in session.query(Article)] == \
        ['new.1.1@example.com', 'new.1.2@example.com']
    db.close()


#
# The command line
#
def test_index_and_save_from_the_command_line(tmp_path):
    """
    nr update index, nr nzb list/save/import and nr delete work together
    """
    from newsreap.cli import cli

    group_list = tmp_path / 'group.list'
    group_list.write_text(
        '%s 0000000300 0000000001 y\n'
        '%s 0000000010 0000000001 y\n' % (GROUP, OTHER))

    with FakeNNTPServer(group_list=str(group_list)) as server:
        articles = list(release('Holiday.Photos', files=2, segments=2))
        for group, entries in (
                (GROUP, articles[:3]), (OTHER, articles[2:])):
            server.add_group(group, len(entries), 1, len(entries))
            for no, (subject, poster, message_id) in enumerate(entries, 1):
                server.add_overview(
                    group, no, subject, poster,
                    POSTED + timedelta(minutes=no), message_id, size=1000)

        config = config_for(server, tmp_path / 'config.yaml')
        runner = CliRunner()

        def nr(*args):
            return runner.invoke(cli, ['--config', config, *args], obj={})

        assert nr('db', 'init').exit_code == 0
        assert nr('update', 'groups').exit_code == 0

        # Nothing has been cached yet
        result = nr('update', 'index', GROUP)
        assert result.exit_code == 1
        assert "run 'nr update search %s' first" % GROUP in result.output

        result = nr('nzb', 'list')
        assert 'Nothing indexed yet' in result.output

        assert nr('update', 'search', GROUP).exit_code == 0
        assert nr('update', 'search', OTHER).exit_code == 0

        assert nr('alias', 'add', 'both', GROUP, OTHER).exit_code == 0
        result = nr('update', 'index', 'both')
        assert result.exit_code == 0
        assert '%s: 3 article(s) indexed; 2 new file(s) in 1 new ' \
            'collection(s)' % GROUP in result.output
        assert '1 collection(s) formed' in result.output

        result = nr('nzb', 'list', 'photos', '--group', OTHER)
        assert result.exit_code == 0
        assert '100.0%' in result.output
        assert '2/2 file(s)  Holiday.Photos' in result.output
        assert nr('nzb', 'list', '-g', 'nope').exit_code == 1

        # Searching ignores case unless asked not to
        result = nr('search', 'HOLIDAY', '-g', 'both')
        assert result.exit_code == 0
        assert 'Holiday.Photos' in result.output
        result = nr('search', '--case-sensitive', 'HOLIDAY')
        assert 'Holiday.Photos' not in result.output
        result = nr('search', '-c', 'Holiday', '--', '-Work')
        assert 'Holiday.Photos' in result.output

        # The cached headers can be searched too
        result = nr('search', '--raw', '-g', GROUP, 'holiday', 'part02')
        assert result.exit_code == 0
        assert result.output.count('holiday.photos.part02.rar') == 1
        assert nr('search', '--raw', 'holiday').exit_code == 1

        # Raw searches can match the case too, and look at posters
        raw = lambda *k: nr(  # noqa: E731
            'search', '--raw', '-g', GROUP, *k).output
        assert 'part01' not in raw('-c', 'HOLIDAY.PHOTOS')
        assert 'part01' in raw('-c', 'Holiday.Photos')
        assert 'part01' in raw('HOLIDAY.PHOTOS')
        assert 'part01' in raw('%pposter@example')
        assert 'part01' not in raw('%p-poster@example')
        assert 'part01' not in raw('%pnobody')

        # Save it into a directory of our choosing
        out = tmp_path / 'nzbs'
        out.mkdir()
        result = nr('nzb', 'save', '1', '--output', str(out))
        assert result.exit_code == 0
        nzb_file = join(str(out), 'Holiday.Photos.nzb')

        # It isn't replaced unless asked
        result = nr('nzb', 'save', '1', '--output', str(out))
        assert result.exit_code == 1
        result = nr('nzb', 'save', '1', '-o', str(out), '--force')
        assert result.exit_code == 0

        # Saving more than one needs a directory
        result = nr('nzb', 'save', '1', '2', '-o', nzb_file)
        assert result.exit_code == 1

        # An id that doesn't exist
        result = nr('nzb', 'save', '42', '-o', str(out))
        assert result.exit_code == 1

        # The NZB lists every segment, and both groups
        nzb = NNTPnzb(nzb_file)
        assert nzb.is_valid() is True
        posts = list(nzb)
        assert sum(len(post) for post in posts) == 4
        assert posts[1].groups == {GROUP, OTHER}

        # The NZB file takes over what we scanned (keeping its id)
        result = nr('nzb', 'import', nzb_file)
        assert result.exit_code == 0
        assert '[     1] Holiday.Photos (imported)' in result.output

        result = nr('nzb', 'import', nzb_file)
        assert result.exit_code == 0
        assert '(already indexed)' in result.output

        # An NZB file with an extra file only adds it when merging
        bigger = globals()['nzb_file'](
            tmp_path / 'bigger.nzb', 'Holiday.Photos', (1, 2, 3), total=2)
        result = nr('nzb', 'import', bigger)
        assert result.exit_code == 0
        assert '(already indexed)' in result.output
        result = nr('nzb', 'import', bigger, '--merge')
        assert result.exit_code == 0
        assert '[     1] Holiday.Photos (merged: 1 file(s) added, ' \
            '0 dropped)' in result.output
        assert '3/3 file(s)' in nr('nzb', 'list').output

        # Settling can be told every group was seen up to now
        assert nr('update', 'index', 'both', '--settle').exit_code == 0

        # Delete it; it doesn't come back
        result = nr('delete', '1')
        assert result.exit_code == 0
        assert 'Removed [1] Holiday.Photos' in result.output
        assert nr('delete', '1').exit_code == 1
        assert nr('update', 'index', 'both').exit_code == 0
        assert 'Holiday.Photos' not in nr('nzb', 'list').output
        assert nr('nzb', 'import', nzb_file).exit_code == 1
