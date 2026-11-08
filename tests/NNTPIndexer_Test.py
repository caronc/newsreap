# Tests grouping cached articles (from every group) into files and
# collections (nr update), and saving, importing and deleting them
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
from newsreap.NNTPIndexer import sorted_in_runs
from newsreap.NNTPnzb import NNTPnzb
from newsreap.NNTPnzb import NZBSegmentNumber
from newsreap.objects.group.Article import Article
from newsreap.objects.index.CollectionFile import CollectionFile
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
    Stores articles (as `nr update` would) and returns the next
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
#   NEWSREAP_TEST_POSTGRESQL=postgresql+psycopg://user:pass@host/db
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
        return str(tmp_path / 'index.db')

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


def open_in(folder):
    """
    How many files this process has open in a folder (or below it)
    """
    found = 0
    for fd in os.listdir('/proc/self/fd'):
        try:
            found += os.readlink('/proc/self/fd/%s' % fd).startswith(
                folder + os.sep)

        except OSError:
            # (it was closed while we looked)
            pass

    return found


def sort_runs(monkeypatch):
    """
    Notes how many runs each sort done in runs (see sorted_in_runs())
    left behind just before its folder was removed
    """
    from newsreap import NNTPIndexer as module

    found = []
    remove = module.rmtree

    def noted(folder, *args, **kwargs):
        found.append(len(os.listdir(folder)))
        return remove(folder, *args, **kwargs)

    monkeypatch.setattr(module, 'rmtree', noted)
    return found


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

    # 20 minutes later is not long enough
    caught_up(paths[GROUP], POSTED + timedelta(minutes=20))
    indexer.index(GROUP, paths[GROUP])
    assert indexer.settle() == 0

    # An hour later it has settled (as complete as it will get)
    caught_up(paths[GROUP], POSTED + timedelta(hours=1))
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
    assert indexer.settle(now=POSTED + timedelta(minutes=20)) == 0
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


def test_a_big_nzb_file_is_imported_a_chunk_at_a_time(paths, tmp_path):
    """
    The files of an NZB file are looked up a chunk at a time (not with a
    query each), and every one is kept in the order listed
    """
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    path = nzb_file(tmp_path / 'big.nzb', 'Big', range(1, 1201),
                    total=1200, segments=1)
    lookups = []

    def counted(conn, cursor, statement, *args):
        # The key may be quoted ("key" or `key`)
        if statement.lstrip().upper().startswith('SELECT') and \
                re.search(r'collection_file\.\W?key\W? (IN|=)', statement):
            lookups.append(statement)

    event.listen(Engine, 'before_cursor_execute', counted)
    indexer = NNTPIndexer(paths['index'])
    try:
        collection, added = indexer.import_nzb(path)

    finally:
        event.remove(Engine, 'before_cursor_execute', counted)

    session = indexer.session()
    files = session.query(CollectionFile)\
        .filter(CollectionFile.collection_id == collection.id)\
        .order_by(CollectionFile.position).all()
    assert len(files) == 1200
    assert files[0].filename == 'big.part01.rar'
    assert files[-1].filename == 'big.part1200.rar'
    assert len(lookups) == 3
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
    The index is an SQLite file in our data directory unless index_engine
    names a database (ie: PostgreSQL or MySQL)
    """
    from newsreap.NNTPSettings import NNTPSettings

    settings = NNTPSettings()
    settings.base_dir = str(tmp_path)
    assert settings.index_database() == \
        join(str(tmp_path), 'index.db')

    settings.nntp_database['index_engine'] = \
        'postgresql+psycopg://u:p@host/index'
    assert settings.index_database() == 'postgresql+psycopg://u:p@host/index'

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

    # Its articles cached again (ie: nr update --force)
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
def test_index_and_save_from_the_command_line(tmp_path, caplog,
                                              monkeypatch):
    """
    nr update, nr nzb list/save/import and nr delete work together
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

        def nr(*args, **kwargs):
            return runner.invoke(cli, ['--config', config, *args], obj={},
                                 **kwargs)

        assert nr('db', 'init').exit_code == 0
        assert nr('group', 'refresh').exit_code == 0

        # Nothing has been cached yet
        result = nr('update', '--cached', GROUP)
        assert result.exit_code == 1
        assert "run 'nr update %s' first" % GROUP in result.output

        result = nr('nzb', 'list')
        assert 'Nothing indexed yet' in result.output

        # Only cached (indexed below)
        assert nr('update', '--no-index', GROUP).exit_code == 0
        assert nr('update', '--no-index', OTHER).exit_code == 0
        assert 'Nothing indexed yet' in nr('search').output

        assert nr('alias', 'add', 'both', GROUP, OTHER).exit_code == 0
        result = nr('update', '--cached', 'both')
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

        # Files it has on top of ours are counted, and the first few named
        from newsreap import NNTPIndexer as module
        monkeypatch.setattr(module, 'LEFT_OUT_SAMPLES', 1)
        extra = globals()['nzb_file'](
            tmp_path / 'extra.nzb', 'Holiday.Photos', (1, 2, 3, 4, 5),
            total=2)
        caplog.clear()
        result = nr('nzb', 'import', extra)
        assert result.exit_code == 0
        assert '(already indexed)' in result.output
        assert "lists 3 file(s) collection #1 doesn't have" in caplog.text
        assert 'part03.rar' in caplog.text
        assert 'part04.rar' not in caplog.text
        assert '... and 2 more' in caplog.text

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
        assert nr('update', '--cached', 'both', '--settle').exit_code == 0

        # Delete it (you are asked first); it doesn't come back
        result = nr('delete', '1', input='n\n')
        assert 'Nothing was removed.' in result.output
        assert '3/3 file(s)' in nr('nzb', 'list').output
        result = nr('-y', 'delete', '1')
        assert result.exit_code == 0
        assert 'Removed [1] Holiday.Photos' in result.output
        assert nr('delete', '1').exit_code == 1
        assert nr('update', '--cached', 'both').exit_code == 0
        assert 'Holiday.Photos' not in nr('nzb', 'list').output
        assert nr('nzb', 'import', nzb_file).exit_code == 1


def test_fetched_headers_can_be_searched_right_away(tmp_path):
    """
    nr update indexes what it fetched; nr db status shows what is
    where; a search that finds nothing says why
    """
    from newsreap.cli import cli
    from newsreap.Utils import utcnow

    group_list = tmp_path / 'group.list'
    group_list.write_text('%s 0000000007 0000000001 y\n' % GROUP)

    with FakeNNTPServer(group_list=str(group_list)) as server:
        # One posted long ago, one still arriving (a part is missing)
        for no, ((subject, poster, message_id), posted) in enumerate([
                *((a, POSTED) for a in release(
                    'Holiday.Photos', files=2, segments=2)),
                *((a, utcnow()) for a in release(
                    'Fresh.Upload', files=2, segments=2, skip={(2, 2)}))],
                1):
            server.add_overview(
                GROUP, no, subject, poster, posted + timedelta(minutes=no),
                message_id, size=1000)

        config = config_for(server, tmp_path / 'config.yaml')
        runner = CliRunner()

        def nr(*args, **kwargs):
            return runner.invoke(cli, ['--config', config, *args], obj={},
                                 **kwargs)

        assert nr('db', 'init').exit_code == 0
        assert nr('group', 'refresh').exit_code == 0

        # Nothing fetched yet
        assert "run 'nr update <group> --newest 100000' first" in \
            nr('search').output
        assert 'Nothing fetched yet' in nr('db', 'status').output

        result = nr('update', GROUP)
        assert result.exit_code == 0, result.output
        assert '%s: 7 article(s) indexed; 4 new file(s) in 2 new ' \
            'collection(s)' % GROUP in result.output
        assert 'Holiday.Photos' in nr('search', 'holiday').output

        # What was fetched and indexed, by group
        result = nr('db', 'status')
        assert result.exit_code == 0
        line = next(row for row in result.output.splitlines()
                    if row.strip().startswith(GROUP)).split()
        assert line[:5] == [GROUP, '7', line[2], '2', '(1']
        assert line[5:7] == ['forming)', '0']

        # A group given as a keyword
        result = nr('search', GROUP)
        assert result.output == (
            "Nothing found.\n'%s' is a group; to look in it: nr search "
            "-g %s\n" % (GROUP, GROUP))

        # Collections still forming are left out (unless asked for)
        assert nr('search', 'fresh').output == (
            'Nothing found.\n1 collection(s) still forming match; add '
            '--forming to see them.\n')
        assert 'Fresh.Upload' in nr('search', '--forming', 'fresh').output


def test_articles_not_done_yet_are_read_in_order_without_sorting(paths):
    """
    Indexing (and archiving) read the cached articles not done yet in
    article order straight from an index; sorting every article left on
    each chunk slows big groups down more and more
    """
    import sqlite3
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    cache(paths[GROUP], release('Ordered', files=2, segments=3))
    caught_up(paths[GROUP])

    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if 'FROM article' in statement and \
                statement.lstrip().upper().startswith('SELECT'):
            statements.append((statement, parameters))

    event.listen(Engine, 'before_cursor_execute', capture)
    try:
        NNTPIndexer(paths['index']).index(GROUP, paths[GROUP])

    finally:
        event.remove(Engine, 'before_cursor_execute', capture)

    # What archiving asks (see NNTPArchiver.archive())
    statements.append((
        'SELECT article_no FROM article WHERE size <= ? AND archived = 0 '
        'AND article_no > ? ORDER BY article_no LIMIT 10', (65536, 0)))

    db = sqlite3.connect(paths[GROUP])
    try:
        plans = [
            ' '.join(str(row[-1]) for row in db.execute(
                'EXPLAIN QUERY PLAN ' + statement, parameters or ()))
            for statement, parameters in statements
            if 'ORDER BY' in statement]

    finally:
        db.close()

    assert len(plans) >= 2
    for plan in plans:
        assert 'TEMP B-TREE' not in plan, plan


def test_old_posts_kept_from_a_group_can_be_let_go(index_engine):
    """
    forget_group(before=...) only lets go of the posts older than the date,
    and keeps one another group has too
    """
    from newsreap.objects.index.ArchiveGroup import ArchiveGroup
    from newsreap.objects.index.ArchivedPost import ArchivedPost

    indexer = NNTPIndexer(index_engine)
    session = indexer.session(create=True)
    for message_id, days, groups in (('old@x', 60, (GROUP, )),
                                     ('new@x', 2, (GROUP, )),
                                     ('shared@x', 60, (GROUP, OTHER))):
        session.add(ArchivedPost(
            message_id=message_id, posted=LATER - timedelta(days=days)))
        session.flush()
        session.add_all(ArchiveGroup(message_id=message_id, name=name)
                        for name in groups)
    session.commit()

    before = LATER - timedelta(days=30)
    try:
        assert indexer.count_group(GROUP, before=before) == (0, 2)
        assert indexer.forget_group(GROUP, before=before) == (0, 0, 1)
        assert {p.message_id for p in session.query(ArchivedPost)} == {
            'new@x', 'shared@x'}
        assert {(g.message_id, g.name) for g in session.query(
            ArchiveGroup)} == {('new@x', GROUP), ('shared@x', OTHER)}

    finally:
        indexer.close()


def index_worker(index, group, path, start):
    """
    Indexes (and settles) one group in a process of its own, once `start`
    is set (see test_workers_indexing_at_once_share_their_collections)
    """
    start.wait()
    indexer = NNTPIndexer(index, chunk_size=40)
    try:
        indexer.index(group, path)
        indexer.settle()

    finally:
        indexer.close()


def open_worker(index, start):
    """
    Opens (and closes) the index a few times in a process of its own, once
    `start` is set (see test_workers_can_open_the_index_at_once)
    """
    from newsreap.NNTPIndexDatabase import NNTPIndexDatabase

    start.wait()
    for _ in range(5):
        NNTPIndexDatabase.from_engine(index).close()


def test_workers_can_open_the_index_at_once(paths):
    """
    Several workers opening the index at the same moment don't trip over
    each other setting it up (its tables and search)
    """
    import multiprocessing

    context = multiprocessing.get_context('spawn')
    start = context.Event()
    workers = [context.Process(target=open_worker, args=(
        paths['index'], start)) for _ in range(6)]
    for worker in workers:
        worker.start()
    start.set()
    for worker in workers:
        worker.join(120)
        assert worker.exitcode == 0


def test_workers_indexing_at_once_share_their_collections(paths):
    """
    Two workers indexing groups that share their posts (crossposted) into
    one index at the same time make each collection once, with every
    file and article in it
    """
    import multiprocessing

    names = ['Shared%02d' % no for no in range(30)]
    for group in (GROUP, OTHER):
        cache(paths[group], [
            article for name in names for article in release(name)])
        caught_up(paths[group])

    # Something made the index first (as 'nr db init' does)
    NNTPIndexer(paths['index']).session(create=True)

    context = multiprocessing.get_context('spawn')
    start = context.Event()
    workers = [context.Process(target=index_worker, args=(
        paths['index'], group, paths[group], start)) for group in (
            GROUP, OTHER)]
    for worker in workers:
        worker.start()
    start.set()
    for worker in workers:
        worker.join(120)
        assert worker.exitcode == 0

    indexer = NNTPIndexer(paths['index'])
    found = indexer.find()
    assert sorted(entry.name for entry in found) == names
    assert {(entry.files, entry.segments) for entry in found} == {(3, 12)}
    assert all(indexer.groups(entry.id) == [OTHER, GROUP] for entry in found)
    indexer.close()


def test_a_chunk_is_tried_again_when_another_worker_is_in_the_way(
        paths, monkeypatch):
    """
    - a chunk that met a lock is undone and tried again (a few times at
      most), and nothing is lost or counted twice
    - any other error isn't tried again
    """
    import sqlite3
    from sqlalchemy.exc import OperationalError
    import newsreap.NNTPIndexer as module

    cache(paths[GROUP], release('Retried'))
    monkeypatch.setattr(module, 'sleep', lambda seconds: None)

    original = NNTPIndexer._archived_filenames
    failures = []

    def in_the_way(self, session, message_ids):
        if failures:
            raise OperationalError(
                'UPDATE', {}, failures.pop(0))
        return original(self, session, message_ids)

    monkeypatch.setattr(NNTPIndexer, '_archived_filenames', in_the_way)

    failures[:] = [sqlite3.OperationalError('database is locked')] * 2
    indexer = indexed(paths)
    assert (indexer.articles, indexer.files, indexer.collections) == (
        12, 3, 1)
    found, = indexer.find()
    assert (found.files, found.segments) == (3, 12)
    indexer.close()

    cache(paths[OTHER], release('Broken'))
    for error, tries in (
            (sqlite3.OperationalError('database is locked'),
             module.INDEX_TRIES),
            (sqlite3.OperationalError('no such table: segment'), 1)):
        failures[:] = [error] * (module.INDEX_TRIES + 1)
        indexer = NNTPIndexer(paths['index'])
        with pytest.raises(OperationalError):
            indexer.index(OTHER, paths[OTHER])
        indexer.close()
        assert len(failures) == module.INDEX_TRIES + 1 - tries


@pytest.mark.parametrize('orig, expected', [
    (type('E', (Exception, ), {'sqlstate': '40P01'})(), True),
    (type('E', (Exception, ), {'pgcode': '40001'})(), True),
    (type('E', (Exception, ), {'sqlstate': '23505'})(), False),
    (Exception(1213, 'Deadlock found'), True),
    (Exception(1205, 'Lock wait timeout exceeded'), True),
    (Exception(1062, 'Duplicate entry'), False),
    (Exception('database is locked'), True),
    (Exception('no such table'), False),
    (None, False),
])
def test_contended_errors_are_told_apart(orig, expected):
    """
    Only a database saying another worker was in the way counts
    """
    from newsreap.Database import contended

    error = Exception()
    error.orig = orig
    assert contended(error) is expected


def test_the_index_is_let_go_of_after_each_step(paths, tmp_path):
    """
    Indexing, settling and importing an NZB file leave nothing open, so
    other workers (and the archive) can change the index right away
    """
    cache(paths[GROUP], release('Holiday'))
    indexer = indexed(paths)
    assert not indexer.session().in_transaction()

    assert indexer.settle() == 0
    assert not indexer.session().in_transaction()

    # One that is refused, and one that is added
    bad = tmp_path / 'bad.nzb'
    bad.write_text('nothing')
    assert indexer.import_nzb(str(bad)) == (None, False)
    collection, _ = indexer.import_nzb(
        nzb_file(tmp_path / 'a.nzb', 'Cafe', (1, )))
    assert not indexer.session().in_transaction()

    # Refused once it has looked (it was removed)
    indexer.delete(collection.id)
    assert indexer.import_nzb(
        nzb_file(tmp_path / 'a.nzb', 'Cafe', (1, ))) == (None, False)
    assert not indexer.session().in_transaction()
    indexer.close()


def test_chunks_are_made_without_reading_everything_first():
    """
    chunked() only reads one chunk ahead, so little of a big iterator is
    held at once
    """
    from newsreap.NNTPIndexer import chunked

    read = []

    def items():
        for no in range(1000):
            read.append(no)
            yield no

    chunks = chunked(items(), 3)
    assert next(chunks) == [0, 1, 2]
    assert len(read) == 3
    assert next(chunks) == [3, 4, 5]
    assert len(read) == 6
    assert list(chunked([], 3)) == []
    assert list(chunked(range(7), 3)) == [[0, 1, 2], [3, 4, 5], [6]]


def test_removing_articles_cascades_to_their_files_and_collection(paths):
    """
    Removing articles keeps what is left of their collection (counted
    again, no longer complete); a file left with no articles goes, and so
    does a collection left with no files
    """
    from newsreap.objects.index.CollectionFile import CollectionFile

    articles = list(release('Holiday', files=2, segments=2))
    cache(paths[GROUP], articles)
    indexer = indexed(paths)
    collection = indexer.find()[0]
    assert (collection.files, collection.segments) == (2, 4)
    assert collection.state == CollectionState.Complete
    ids = [message_id for _, _, message_id in articles]

    # One part of the first file: the file and collection stay
    assert indexer.forget_articles(['<%s>' % ids[0]]) == (1, 0, 0)
    collection = indexer.get(collection.id)
    assert (collection.files, collection.segments) == (2, 3)
    assert collection.state == CollectionState.Settled

    # The rest of the first file: the file goes
    assert indexer.forget_articles([ids[1], 'unknown@x']) == (1, 1, 0)
    assert indexer.get(collection.id).files == 1

    # Everything left: the collection goes too
    assert indexer.forget_articles(ids[2:]) == (2, 1, 1)
    assert indexer.get(collection.id) is None
    assert indexer.session().query(CollectionFile).count() == 0
    indexer.close()


def test_articles_are_removed_as_their_message_ids_are_read(paths):
    """
    Removing articles reads the Message-IDs given a chunk at a time and
    removes each chunk before reading on, so any number can be given
    """
    from newsreap.NNTPIndexer import IN_LIST_SIZE

    articles = list(release('Holiday', files=1, segments=3))
    cache(paths[GROUP], articles)
    indexer = indexed(paths)
    collection = indexer.find()[0]
    ids = [message_id for _, _, message_id in articles]

    def given(first, later, seen):
        # The first chunk, then what is left of the collection once it
        # has been read past
        yield first
        for no in range(IN_LIST_SIZE - 1):
            yield 'filler%d@x' % no
        found = indexer.get(collection.id)
        seen.append(found.segments if found else None)
        yield later

    try:
        seen = []
        assert indexer.forget_articles(given(ids[0], ids[1], seen))[0] == 2
        assert seen == [2]

        seen = []
        assert indexer.purge(given(ids[2], 'other@x', seen)).collections \
            == 1
        assert seen == [None]

    finally:
        indexer.close()


def cached_ids(db_file):
    """
    Returns the Message-IDs a group's header cache holds
    """
    db = NNTPGroupDatabase.from_file(db_file)
    try:
        return {m for m, in db.session().query(Article.message_id)}

    finally:
        db.close()


def kept_post(session, message_id, group):
    """
    Adds a post to the archive (as 'nr update --content' keeps it)
    """
    from newsreap.objects.index.ArchiveGroup import ArchiveGroup
    from newsreap.objects.index.ArchivedPost import ArchivedPost

    session.add(ArchivedPost(message_id=message_id, subject='kept'))
    session.flush()
    session.add(ArchiveGroup(message_id=message_id, name=group))
    session.commit()


def test_deleting_a_collection_takes_everything_kept_of_it(paths):
    """
    delete() removes what is kept of the collection's articles too: their
    headers in every group they were cached in, the posts the archive kept,
    and their scores and tags; the collection stays, marked as deleted
    """
    from newsreap.NNTPArticleRanks import NNTPArticleRanks
    from newsreap.NNTPIndexer import Purged
    from newsreap.objects.index.ArchivedPost import ArchivedPost

    articles = list(release('Holiday', files=1, segments=2))
    ids = [message_id for _, _, message_id in articles]
    cache(paths[GROUP], articles)
    cache(paths[OTHER], articles)
    cache(paths[GROUP], [('unrelated', POSTER, 'unrelated@x')], start=10)
    indexer = indexed(paths, GROUP, OTHER)
    try:
        collection = next(c for c in indexer.find() if c.name == 'Holiday')
        session = indexer.session()
        NNTPArticleRanks.save(session, {ids[0]: (5, frozenset(['seen']))})
        session.commit()
        kept_post(session, ids[1], GROUP)

        assert indexer.delete(collection.id, group_database=paths.get)
        assert indexer.purged == Purged(
            headers=4, posts=1, articles=2, files=1, collections=1)

        assert cached_ids(paths[GROUP]) == {'unrelated@x'}
        assert cached_ids(paths[OTHER]) == set()
        assert NNTPArticleRanks.load(session, ids) == {}
        assert session.query(ArchivedPost).count() == 0
        assert session.get(Collection, collection.id).state == \
            CollectionState.Deleted

    finally:
        indexer.close()


def test_deleting_a_collection_reads_its_articles_a_page_at_a_time(paths):
    """
    delete() reads a collection's articles a page of files at a time
    rather than with one query for each file
    """
    from sqlalchemy import event
    from newsreap.NNTPIndexer import Purged

    articles = list(release('Holiday', files=40, segments=2))
    cache(paths[GROUP], articles)
    indexer = indexed(paths, GROUP)
    try:
        collection = next(c for c in indexer.find() if c.name == 'Holiday')
        session = indexer.session()

        # No page holds more articles than asked for (2 files of 2)
        pages = list(indexer._file_pages(session, collection.id, size=5))
        assert max(len(page) for page in pages) == 2
        assert len({i for page in pages for i in page}) == 40

        reads = []

        def note(conn, cursor, statement, *args):
            if statement.lstrip().upper().startswith('SELECT') and \
                    'FROM segment' in statement:
                reads.append(statement)

        engine = session.get_bind()
        event.listen(engine, 'before_cursor_execute', note)
        try:
            assert indexer.delete(collection.id, group_database=paths.get)

        finally:
            event.remove(engine, 'before_cursor_execute', note)

        assert indexer.purged == Purged(
            headers=80, posts=0, articles=80, files=40, collections=1)
        assert len(reads) < 10
        assert cached_ids(paths[GROUP]) == set()

    finally:
        indexer.close()


def test_purging_articles_takes_everything_kept_of_them(paths):
    """
    purge() removes articles everywhere: from the header cache of each
    group the index or the archive knows they were in (and the groups
    given), the archive, their scores and tags, and the index (a file and
    collection left with nothing go)
    """
    from newsreap.NNTPArticleRanks import NNTPArticleRanks
    from newsreap.NNTPIndexer import Purged

    articles = list(release('Holiday', files=1, segments=1))
    indexed_id = articles[0][2]
    cache(paths[GROUP], articles)
    cache(paths[OTHER], articles)
    indexer = indexed(paths, GROUP, OTHER)
    try:
        session = indexer.session()
        NNTPArticleRanks.save(session, {indexed_id: (5, frozenset())})
        session.commit()

        # Cached after indexing: one only the archive knows the group of,
        # and one only the search that found it does
        cache(paths[OTHER], [('kept', POSTER, 'kept@x')], start=10)
        kept_post(session, 'kept@x', OTHER)
        cache(paths[GROUP], [('found', POSTER, 'found@x')], start=20)

        assert indexer.purge(['<%s>' % indexed_id],
                             group_database=paths.get) == Purged(
            headers=2, posts=0, articles=1, files=1, collections=1)
        assert cached_ids(paths[GROUP]) == {'found@x'}
        assert cached_ids(paths[OTHER]) == {'kept@x'}

        assert indexer.purge(['kept@x'], group_database=paths.get) == \
            Purged(headers=1, posts=1, articles=0, files=0, collections=0)
        assert cached_ids(paths[OTHER]) == set()
        assert NNTPArticleRanks.load(session, [indexed_id]) == {}
        assert indexer.find() == []

        assert indexer.purge(['found@x'], group_database=paths.get,
                             groups={GROUP}).headers == 1
        assert cached_ids(paths[GROUP]) == set()

        # Without where the caches are, they are left alone
        cache(paths[GROUP], [('again', POSTER, 'again@x')], start=30)
        assert indexer.purge(['again@x'], groups={GROUP}).headers == 0
        assert cached_ids(paths[GROUP]) == {'again@x'}

    finally:
        indexer.close()


def test_saving_a_collection_reads_its_files_a_page_at_a_time(
        paths, tmp_path, monkeypatch):
    """
    nzb() reads a page of files (their groups and articles) at a time and
    writes each file as it goes, so a collection of any size is never all
    held; the files keep their order, and one with no articles left is
    left out
    """
    import weakref
    from sqlalchemy import event
    from newsreap import NNTPIndexer as module
    from newsreap.objects.index.CollectionFile import CollectionFile
    from newsreap.objects.index.Segment import Segment

    # Pages of 3 files (2 articles each); what they are sorted by is
    # sorted 7 files at a time
    monkeypatch.setattr(module, 'ARTICLE_PAGE_SIZE', 6)
    monkeypatch.setattr(module, 'SORT_RUN_SIZE', 7)
    runs = sort_runs(monkeypatch)

    live = weakref.WeakSet()
    most = [0]

    class Tracked(module.NNTPArticle):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            live.add(self)
            most[0] = max(most[0], len(live))

    monkeypatch.setattr(module, 'NNTPArticle', Tracked)

    cache(paths[GROUP], list(release('Holiday', files=40, segments=2)))
    indexer = indexed(paths, GROUP)
    try:
        collection = next(c for c in indexer.find() if c.name == 'Holiday')
        session = indexer.session()

        # The order newznab gives them
        subjects = [subject for _, _, subject in sorted(
            (gid_order(subject), file_id, subject)
            for file_id, subject in session.query(
                CollectionFile.id, CollectionFile.subject)
            .filter(CollectionFile.collection_id == collection.id))]
        assert [entry.subject for entry in indexer._files(
            session, collection.id)] == subjects

        # One file loses its articles
        emptied = next(iter(indexer._files(session, collection.id)))
        session.query(Segment).filter(Segment.file_id == emptied.id)\
            .delete(synchronize_session=False)
        session.commit()

        reads = []

        def note(conn, cursor, statement, *args):
            if statement.lstrip().upper().startswith('SELECT') and (
                    'FROM segment' in statement
                    or 'FROM file_group' in statement):
                reads.append(statement)

        engine = session.get_bind()
        event.listen(engine, 'before_cursor_execute', note)
        try:
            path = str(tmp_path / 'holiday.nzb')
            runs.clear()
            assert indexer.nzb(collection.id, path, work_dir=str(
                tmp_path)) is True

        finally:
            event.remove(engine, 'before_cursor_execute', note)

    finally:
        indexer.close()

    # 14 pages, a query each for their groups and articles
    assert len(reads) <= 28
    assert most[0] <= 6

    # 40 files were sorted in 6 runs, which are gone again
    assert runs == [6]
    assert open_in(str(tmp_path)) == 0
    assert not [name for name in os.listdir(tmp_path)
                if name.startswith('newsreap-sort-')]

    posts = list(NNTPnzb(path))
    assert [post.subject for post in posts] == subjects[1:]
    assert all(len(post) == 2 for post in posts)


def test_importing_an_nzb_file_holds_a_few_of_its_articles_at_once(
        paths, tmp_path, monkeypatch):
    """
    import_nzb() reads the NZB file a file at a time and saves its
    articles a chunk at a time, so an NZB file of any size is never all
    held; everything it lists is still imported
    """
    import gc
    import weakref
    from sqlalchemy import event
    import sys
    from newsreap import NNTPIndexer as module
    from newsreap.objects.index.CollectionFile import CollectionFile

    # (the package's NNTPnzb is the class; its module is wanted here)
    nzb_module = sys.modules['newsreap.NNTPnzb']

    # Chunks of 3 files (4 articles each)
    monkeypatch.setattr(module, 'ARTICLE_PAGE_SIZE', 10)

    live = weakref.WeakSet()
    most = [0]

    class Tracked(nzb_module.NNTPArticle):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            live.add(self)
            most[0] = max(most[0], len(live))

    monkeypatch.setattr(nzb_module, 'NNTPArticle', Tracked)

    path = nzb_file(tmp_path / 'a.nzb', 'Holiday', range(1, 31), total=30,
                    segments=4)

    indexer = NNTPIndexer(paths['index'])
    try:
        writes = []
        files = [0]

        def note(conn, cursor, statement, *args):
            if statement.lstrip().upper().startswith('INSERT') and \
                    'segment' in statement.split('(')[0]:
                writes.append(statement)

                # The files held while a chunk's articles are saved
                gc.collect()
                files[0] = max(files[0], sum(
                    isinstance(item, CollectionFile)
                    for item in gc.get_objects()))

        engine = indexer.session(create=True).get_bind()
        event.listen(engine, 'before_cursor_execute', note)
        try:
            collection, added = indexer.import_nzb(path)

        finally:
            event.remove(engine, 'before_cursor_execute', note)

        assert added is True
        assert (collection.files_expected, collection.files,
                collection.segments) == (30, 30, 120)
        assert collection.poster == POSTER

        # Its articles were saved a chunk at a time
        assert len(writes) >= 10

        # A chunk (3 files) and the file read next, out of 120 articles
        assert most[0] <= 16

        # Only the files of a chunk, out of 30
        assert 1 <= files[0] <= 3

        saved = str(tmp_path / 'saved.nzb')
        assert indexer.nzb(collection.id, saved) is True
        assert sorted(NNTPnzb(saved).message_ids()) == sorted(
            NNTPnzb(path).message_ids())

    finally:
        indexer.close()


def test_an_import_looks_its_articles_up_by_message_id(paths, tmp_path):
    """
    Finding which collections have an NZB file's articles starts from the
    articles themselves; starting from the collections would read every
    article they have, for each chunk of Message-IDs
    """
    from sqlalchemy import event

    # A scanned collection; the NZB files take its articles
    cache(paths[GROUP], release('Claimed', files=2, segments=2))
    indexed(paths).close()

    indexer = NNTPIndexer(paths['index'])
    try:
        found = []

        def note(conn, cursor, statement, parameters, *args):
            if 'JOIN collection ' in statement and \
                    'segment.message_id IN' in statement:
                found.append((statement, parameters))

        engine = indexer.session(create=True).get_bind()
        event.listen(engine, 'before_cursor_execute', note)
        try:
            # (whole chunks of Message-IDs; SQLite plans a short list
            # differently)
            first = nzb_file(tmp_path / 'first.nzb', 'Claimed',
                             range(1, 300), total=300)
            assert indexer.import_nzb(first)[1] is True

            more = nzb_file(tmp_path / 'more.nzb', 'Claimed',
                            range(1, 301), total=300)
            assert indexer.import_nzb(more)[1] is False
            assert len(indexer.left_out) == 1

        finally:
            event.remove(engine, 'before_cursor_execute', note)

        # (claimed, then its owners twice and what the second one shares)
        assert len(found) >= 4
        if engine.dialect.name != 'sqlite':
            # (only SQLite's plans are read here)
            return

        with engine.connect() as connection:
            cursor = connection.connection.cursor()
            for statement, parameters in found:
                plan = cursor.execute(
                    'EXPLAIN QUERY PLAN ' + statement, parameters).fetchall()
                assert 'SEARCH segment' in plan[0][-1], plan

    finally:
        indexer.close()


def test_large_sorts_are_done_in_runs_kept_on_disk(tmp_path, monkeypatch):
    """
    sorted_in_runs() sorts what fits in memory there; more is sorted in
    runs kept in temporary files, giving the same order:
    - only a few of those files are open at once, however many runs there
      are
    - they are closed and removed once done, or stopped part way
    """
    from newsreap import NNTPIndexer as module

    work_dir = str(tmp_path)
    runs = sort_runs(monkeypatch)
    most = [0]

    def note():
        most[0] = max(most[0], open_in(work_dir))

    def fed(items):
        for item in items:
            note()
            yield item

    def sort(items, size):
        most[0] = 0
        found = []
        for item in sorted_in_runs(fed(items), size=size, work_dir=work_dir):
            note()
            found.append(item)

        assert open_in(work_dir) == 0
        assert os.listdir(work_dir) == []
        return found

    items = [(i % 7 == 0, i * 37 % 11, 'subject %d' % (i * 13 % 17), i,
              None) for i in range(50)]

    assert sort(items, 100) == sorted(items)
    assert (runs, most[0]) == ([], 0)

    # 7 runs, read together
    assert sort(items, 8) == sorted(items)
    assert runs == [7]
    assert 1 <= most[0] <= 7

    # 25 runs, read 3 at a time: they are merged into bigger runs first
    runs.clear()
    monkeypatch.setattr(module, 'SORT_MERGE_SIZE', 3)
    assert sort(items, 2) == sorted(items)
    assert runs == [3]
    assert 1 <= most[0] <= 4

    # Stopped part way, its files are still closed and removed
    found = sorted_in_runs(iter(items), size=2, work_dir=work_dir)
    assert next(found) == min(items)
    assert open_in(work_dir) == 3
    found.close()
    assert open_in(work_dir) == 0
    assert os.listdir(work_dir) == []

    assert list(sorted_in_runs(iter(()), size=8)) == []

    # Exactly one run's worth
    assert list(sorted_in_runs(iter(items[:8]), size=8)) == sorted(items[:8])


def test_an_article_listed_twice_is_counted_once(paths, tmp_path,
                                                 monkeypatch):
    """
    An NZB file listing an article twice (far apart) shares no more with
    a collection than it really does
    """
    from newsreap import NNTPIndexer as module

    # Two articles are looked up at a time (so the two copies are looked
    # up separately)
    chunked = module.chunked
    monkeypatch.setattr(module, 'chunked', lambda items, size=2: chunked(
        items, min(size, 2)))

    indexer = NNTPIndexer(paths['index'])
    try:
        first = nzb_file(tmp_path / 'first.nzb', 'Twice', (1, 2, 3))
        assert indexer.import_nzb(first)[1] is True

        # It shares two files of three, and lists the first one twice
        other = nzb_file(tmp_path / 'other.nzb', 'Twice', (1, 2, 4, 1))
        assert indexer.import_nzb(other) == (None, False)
        assert indexer.left_out == []

    finally:
        indexer.close()


def test_an_nzb_file_changed_while_it_is_imported_is_read_as_it_was(
        paths, tmp_path, monkeypatch):
    """
    import_nzb() reads its own copy of the NZB file, so replacing the file
    part way through changes nothing; the copy is removed afterwards
    """
    import sys
    nzb_module = sys.modules['newsreap.NNTPnzb']

    path = nzb_file(tmp_path / 'a.nzb', 'Holiday', (1, 2, 3))
    other = nzb_file(tmp_path / 'b.nzb', 'Other', (1, 2), total=2)

    # The file is replaced once it was found to be valid
    is_valid = nzb_module.NNTPnzb.is_valid

    def replaced(self):
        found = is_valid(self)
        if os.path.exists(other):
            os.replace(other, path)
        return found

    monkeypatch.setattr(nzb_module.NNTPnzb, 'is_valid', replaced)

    work = tmp_path / 'work'
    work.mkdir()

    indexer = NNTPIndexer(paths['index'])
    try:
        collection, added = indexer.import_nzb(path, work_dir=str(work))
        assert added is True
        assert collection.gid == make_gid('holiday.1.1@example.com')
        assert (collection.files, collection.segments) == (3, 6)
        assert collection.name == 'a'
        assert list(work.iterdir()) == []

        # A missing file can't be read
        assert indexer.import_nzb(str(tmp_path / 'missing.nzb'),
                                  work_dir=str(work)) == (None, False)
        assert list(work.iterdir()) == []

    finally:
        indexer.close()


def test_many_details_are_checked_for_repeats_quickly(paths, tmp_path):
    """
    Looking for a detail we have already doesn't compare it with every
    detail kept so far (an NZB file can list a great many)
    """
    import time

    class Nzb:
        meta = {'title': 'Many', 'tag': [
            'tag %d' % i for i in range(20000)] + ['tag 1']}

    started = time.monotonic()
    meta = NNTPIndexer._meta(Nzb())
    assert time.monotonic() - started < 2
    assert len(meta) == 20001
    assert meta[:3] == [('title', 'Many'), ('tag', 'tag 0'), ('tag', 'tag 1')]

    class Counted(str):
        compared = 0

        def __eq__(self, other):
            Counted.compared += 1
            return str.__eq__(self, other)

        __hash__ = str.__hash__

    indexer = NNTPIndexer(paths['index'])
    try:
        path = nzb_file(tmp_path / 'many.nzb', 'Many', (1, ))
        collection, _ = indexer.import_nzb(path)
        session = indexer.session()

        indexer.details = 0
        indexer._merge_meta(session, collection.id, [
            ('tag', Counted('tag %d' % (i % 2000))) for i in range(4000)])
        assert indexer.details == 2000
        assert Counted.compared < 4000

    finally:
        indexer.close()


def test_an_nzb_file_is_read_once_for_its_articles(paths, tmp_path,
                                                   monkeypatch):
    """
    The articles an NZB file lists are read and sorted once, however many
    times an import needs them; what is kept of them is gone afterwards
    """
    from newsreap import NNTPIndexer as module

    # Kept on disk from the start
    monkeypatch.setattr(module, 'SPOOL_SIZE', 10)

    reads = []
    read = NNTPnzb.message_ids

    def counted(self, *args, **kwargs):
        reads.append(self)
        return read(self, *args, **kwargs)

    monkeypatch.setattr(NNTPnzb, 'message_ids', counted)

    work = tmp_path / 'work'
    work.mkdir()

    # Some of it was scanned
    cache(paths[GROUP], release('Once', files=4, segments=2, only={1}))
    indexer = indexed(paths)
    try:
        # Taking over what was scanned
        first = nzb_file(tmp_path / 'first.nzb', 'Once', (1, 2), total=4)
        reads.clear()
        assert indexer.import_nzb(first, work_dir=str(work))[1] is True
        assert len(reads) == 1

        # Sharing articles with what we have
        more = nzb_file(tmp_path / 'more.nzb', 'Once', (1, 2, 3), total=4)
        reads.clear()
        assert indexer.import_nzb(more, work_dir=str(work))[1] is False
        assert len(reads) == 1
        assert indexer.left_out_count == 1

        # Replacing what we have
        reads.clear()
        assert indexer.import_nzb(
            more, merge=True, work_dir=str(work))[1] is True
        assert len(reads) == 1
        assert (indexer.merged, indexer.dropped) == (1, 0)

        assert open_in(str(work)) == 0
        assert os.listdir(work) == []

    finally:
        indexer.close()


def test_scanned_collections_an_nzb_file_takes_are_let_go_of_in_chunks(
        paths, tmp_path, monkeypatch):
    """
    Taking articles from scanned collections works on a few articles (and
    collections) at a time:
    - each collection left with nothing is removed but one, which becomes
      the NZB file's collection
    - the one kept is the one with the NZB file's GID, else the one that
      lost the most articles
    """
    from newsreap import NNTPIndexer as module

    # A couple of articles, and collections, at a time
    chunked = module.chunked
    monkeypatch.setattr(module, 'chunked', lambda items, size=2: chunked(
        items, 2))
    monkeypatch.setattr(module, 'SORT_RUN_SIZE', 2)

    # The files (and collections) counted at once while articles are taken
    counted = []
    recount = NNTPIndexer._recount

    def noted(self, session, file_ids, collection_ids, dates=None):
        if dates is None:
            counted.append((len(list(file_ids)), len(list(collection_ids))))
        return recount(self, session, file_ids, collection_ids, dates)

    monkeypatch.setattr(NNTPIndexer, '_recount', noted)

    cache(paths[GROUP], [
        *release('Alpha', files=2, segments=2),
        *release('Beta', files=1, segments=2),
        *release('Gamma', files=3, segments=2),
        *release('Iota', files=1, segments=2),
        *release('Kappa', files=3, segments=2)])
    indexer = indexed(paths)
    try:
        scanned = {c.name: c.id for c in indexer.find()}
        assert sorted(scanned) == ['Alpha', 'Beta', 'Gamma', 'Iota', 'Kappa']

        def combined(name, *names, skip=()):
            # One NZB file listing what we scanned of each (but the files
            # whose subjects start with what skip has)
            nzb = NNTPnzb()
            for other in names:
                path = str(tmp_path / ('%s.nzb' % other))
                assert indexer.nzb(scanned[other], path) is True
                for post in NNTPnzb(path):
                    if not post.subject.startswith(skip):
                        nzb.add(post)

            path = str(tmp_path / name)
            assert nzb.save(path) is True
            return path

        # Its GID is Alpha's, which keeps a file of its own: of the ones
        # left with nothing, the one that lost the most is kept
        counted.clear()
        collection, added = indexer.import_nzb(combined(
            'all.nzb', 'Beta', 'Alpha', 'Gamma', skip=('Alpha [02', )))
        assert added is True
        assert collection.id == scanned['Gamma']
        assert (collection.files, collection.segments) == (5, 10)
        assert indexer.get(scanned['Beta']) is None
        assert indexer.get(scanned['Alpha']).files == 1
        assert counted
        assert max(files for files, _ in counted) <= 2
        assert max(collections for _, collections in counted) <= 2

        # The one with its GID is kept, even though it lost less
        collection, added = indexer.import_nzb(combined(
            'both.nzb', 'Iota', 'Kappa'))
        assert added is True
        assert collection.id == scanned['Iota']
        assert (collection.files, collection.segments) == (4, 8)
        assert indexer.get(scanned['Kappa']) is None

        indexer.settle()
        assert sorted(c.name for c in indexer.find()) == [
            'Alpha', 'all', 'both']

    finally:
        indexer.close()


def test_merging_compares_the_files_a_few_at_a_time(paths, tmp_path,
                                                    monkeypatch):
    """
    What merging an NZB file changed (files added and dropped) is worked
    out from their keys, read a page at a time and sorted in runs (see
    sorted_in_runs()), so a collection of any size is never held
    """
    from sqlalchemy import event
    from newsreap import NNTPIndexer as module

    monkeypatch.setattr(module, 'SORT_RUN_SIZE', 2)

    indexer = NNTPIndexer(paths['index'])
    try:
        first = nzb_file(tmp_path / 'first.nzb', 'Swap', range(1, 7),
                         total=9)
        collection_id = indexer.import_nzb(first)[0].id

        reads = []

        def note(conn, cursor, statement, *args):
            if statement.lstrip().upper().startswith('SELECT') and \
                    re.search(r'collection_file\."?key\b', statement) and \
                    'collection_file.collection_id =' in statement:
                reads.append(statement)

        engine = indexer.session().get_bind()
        event.listen(engine, 'before_cursor_execute', note)
        try:
            fixed = nzb_file(tmp_path / 'fixed.nzb', 'Swap', range(4, 10),
                             total=9)
            collection, added = indexer.import_nzb(fixed, merge=True)

        finally:
            event.remove(engine, 'before_cursor_execute', note)

        assert (collection.id, added) == (collection_id, True)
        assert (indexer.merged, indexer.dropped) == (3, 3)
        assert collection.files == 6

        # Every read of them was a page
        assert len(reads) >= 4
        assert all('LIMIT' in statement.upper() for statement in reads)

    finally:
        indexer.close()
