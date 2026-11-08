# Test putting loose articles into batches of whole files
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

from newsreap.NNTPArticleBatches import NNTPArticleBatches
from newsreap.NNTPIndexer import NNTPIndexer

from tests.NNTPDownloader_Test import GROUP
from tests.NNTPFetcher_Test import indexed  # noqa: F401 (a fixture)


def article(message_id, subject, group=GROUP):
    return {'message_id': message_id, 'subject': subject, 'group': group}


def test_a_file_is_never_split_between_batches():
    """
    A file's parts come back together (even one posted again much later),
    files in the order they were first found, and a batch only ends
    between files
    """
    batches = NNTPArticleBatches(size=2)
    try:
        batches.add(article(*entry) for entry in (
            ('a1', '"a.rar" yEnc (1/3)'),
            ('lone', 'just some words'),
            ('b1', '"b.rar" yEnc (1/1)'),
            ('a2', '"a.rar" yEnc (2/3)'),
            # The same name in another group is another file
            ('other', '"a.rar" yEnc (1/1)', 'alt.other'),
            ('a3', '"a.rar" yEnc (3/3)')))

        found = [[entry['message_id'] for entry in batch]
                 for batch in batches]
        assert found == [['a1', 'a2', 'a3'], ['lone', 'b1'], ['other']]
        assert len(batches) == 3
        assert batches.articles == 6

        # What downloading a batch needs: its files, in their order
        files, known = batches.files(next(iter(batches)))
        assert known == {}
        assert [(f['name'], f['segments']) for f in files] == [
            ('a.rar', [(1, 'a1'), (2, 'a2'), (3, 'a3')])]

    finally:
        batches.close()


def test_files_of_the_same_name_from_other_posts_are_batched_apart():
    """
    Two posts that each hold a file of the same name (in the same group)
    are two files, not one
    """
    batches = NNTPArticleBatches(size=1)
    try:
        batches.add(article(*entry) for entry in (
            ('a', 'release-a "sample.mkv" (1/1)'),
            ('b', 'release-b "sample.mkv" (1/1)')))

        assert [[entry['message_id'] for entry in batch]
                for batch in batches] == [['a'], ['b']]

    finally:
        batches.close()


def test_batches_are_counted_the_way_they_come():
    """
    len() says how many batches iterating gives, whatever their size
    """
    for size in (1, 2, 3, 4, 7, 100):
        batches = NNTPArticleBatches(size=size)
        try:
            assert not batches
            assert len(batches) == 0
            batches.add(article('%d' % no, '"f%d" (1/1)' % (no // 3))
                        for no in range(20))
            assert batches
            assert len(batches) == len(list(batches))
            assert sum(len(batch) for batch in batches) == 20

        finally:
            batches.close()


def test_indexed_articles_are_batched_by_their_file(tmp_path, indexed):
    """
    Articles the index has are kept with the file it has them under, even
    when their subjects don't say so
    """
    indexer = NNTPIndexer(str(tmp_path / 'index.db'))
    batches = NNTPArticleBatches(indexer, size=1)
    try:
        batches.add([
            article('notes.txt.1@example.com', 'one'),
            article('readme.nfo.1@example.com', 'two'),
            article('notes.txt.3@example.com', 'three')])

        found = [[entry['message_id'] for entry in batch]
                 for batch in batches]
        assert found == [
            ['notes.txt.1@example.com', 'notes.txt.3@example.com'],
            ['readme.nfo.1@example.com']]

        files, _ = batches.files(next(iter(batches)))
        assert [(f['name'], f['segments']) for f in files] == [
            ('notes.txt', [(1, 'notes.txt.1@example.com'),
                           (3, 'notes.txt.3@example.com')])]

    finally:
        batches.close()
        indexer.close()


def test_reposts_are_batched_as_files_of_their_own():
    """
    A part seen again belongs to another copy of its file (a repost), as
    NNTPFetcher.files_of() sees them, so many reposts still make small
    batches of whole files
    """
    batches = NNTPArticleBatches(size=2)
    try:
        batches.add(article(*entry) for entry in (
            *(('r%d' % no, '"r.bin" yEnc (1/1)') for no in range(7)),
            ('a1', '"a.rar" yEnc (1/2)'),
            ('b1', '"a.rar" yEnc (1/2)'),
            ('a2', '"a.rar" yEnc (2/2)'),
            ('b2', '"a.rar" yEnc (2/2)')))

        found = [[entry['message_id'] for entry in batch]
                 for batch in batches]
        assert found == [
            ['r0', 'r1'], ['r2', 'r3'], ['r4', 'r5'], ['r6', 'a1', 'a2'],
            ['b1', 'b2']]
        assert len(batches) == 5

        # Each batch holds whole files
        files = [[f['segments'] for f in batches.files(batch)[0]]
                 for batch in batches]
        assert files[3] == [[(1, 'r6')], [(1, 'a1'), (2, 'a2')]]
        assert files[4] == [[(1, 'b1'), (2, 'b2')]]

    finally:
        batches.close()
