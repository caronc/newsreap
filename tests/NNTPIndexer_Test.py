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

from datetime import datetime
from datetime import timedelta
from os.path import join

import pytest
from click.testing import CliRunner

from newsreap.NNTPGroupDatabase import NNTPGroupDatabase
from newsreap.NNTPIndexer import NNTPIndexer
from newsreap.NNTPIndexer import make_gid
from newsreap.NNTPIndexer import parse_subject
from newsreap.NNTPIndexer import release_name
from newsreap.NNTPnzb import NNTPnzb
from newsreap.NNTPnzb import NZBSegmentNumber
from newsreap.objects.group.Article import Article
from newsreap.objects.index.Collection import CollectionState

from tests.FakeNNTPServer import FakeNNTPServer
from tests.conftest import config_for

GROUP = 'alt.binaries.test'
OTHER = 'alt.binaries.other'
POSTER = 'poster@example.com'
POSTED = datetime(2020, 1, 1)

# Long after everything above was posted (so it has all settled)
LATER = datetime(2021, 1, 1)


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


@pytest.fixture
def paths(tmp_path):
    """
    Where our index and our test groups' articles are kept
    """
    return {
        'index': str(tmp_path / 'cache' / 'index.db'),
        GROUP: str(tmp_path / 'cache' / 'search' / 'test.db'),
        OTHER: str(tmp_path / 'cache' / 'search' / 'other.db'),
    }


def indexed(paths, *groups, **kwargs):
    """
    Returns an indexer that has indexed (and settled) the groups given
    """
    now = kwargs.pop('now', LATER)
    indexer = NNTPIndexer(paths['index'], **kwargs)
    for group in groups or (GROUP, ):
        indexer.index(group, paths[group])
    indexer.settle(now=now)
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
    assert indexer.settle(now=LATER) == 3

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
    assert indexer.settle(now=LATER) == 0
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
    A collection missing parts forms once nothing new arrived for a while,
    and completes when the missing parts turn up later
    """
    missing = {(2, 3), (3, 1)}
    next_no = cache(paths[GROUP], release('Late', skip=missing))

    # Just after it was posted it is still forming
    indexer = indexed(paths, now=POSTED + timedelta(hours=1))
    assert indexer.find() == []
    late = indexer.find(forming=True)[0]
    assert late.state == CollectionState.Forming
    assert late.gid is None

    # Hours later it has settled (as complete as it will get)
    assert indexer.settle(now=POSTED + timedelta(hours=5)) == 1
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
    indexer.settle(now=LATER)
    late = indexer.get(late.id)
    assert (late.segments, late.segments_expected) == (12, 12)
    assert late.state == CollectionState.Complete
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
    assert nzb.meta == {'name': 'Holiday'}


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
# Deleting
#
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
    indexer.settle(now=LATER)
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

        # We already have what it describes
        result = nr('nzb', 'import', nzb_file)
        assert result.exit_code == 0
        assert '(already indexed)' in result.output

        # Delete it; it doesn't come back
        result = nr('delete', '1')
        assert result.exit_code == 0
        assert 'Removed [1] Holiday.Photos' in result.output
        assert nr('delete', '1').exit_code == 1
        assert nr('update', 'index', 'both').exit_code == 0
        assert 'Holiday.Photos' not in nr('nzb', 'list').output
        assert nr('nzb', 'import', nzb_file).exit_code == 1
