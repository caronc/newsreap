# Test the NNTPPostFactory Object
#
# Copyright (C) 2017 Chris Caron <lead2gold@gmail.com>
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

import os
import sys
from os.path import dirname
from os.path import abspath
from os.path import join

try:
    from tests.TestBase import TestBase

except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

from newsreap.NNTPSettings import SERVER_LIST_KEY
from newsreap.NNTPSettings import PROCESSING_KEY
from newsreap.NNTPSettings import NNTPSettings
from newsreap.NNTPManager import NNTPManager
from newsreap.NNTPPostFactory import NNTPPostFactory
from newsreap.NNTPArticle import NNTPArticle
from newsreap.NNTPnzb import NNTPnzb
from newsreap.decorators import hook
from newsreap.Utils import strsize_to_bytes


class NNTPPostFactory_Test(TestBase):
    """
    A Class for testing NNTPPostFactory

    """

    def test_detect_split_size(self):
        """
        Test detect_split_size()

        """
        cfg_file = join(self.tmp_dir, 'NNTPManager.config.yaml')

        server = {
            'username': 'valid',
            'password': 'valid',
            'host': 'localhost',
            'port': 119,
            'secure': 'False',
            'compress': 'False',
            'priority': '1',
            'join_group': 'False',
        }

        processing = {
            # Our test server only supports one connection at this
            # time
            'threads': 1,
            'header_batch_size': 8000,
        }

        # Create a yaml configuration entry we can test with
        # The output is invalid (formatting)
        with open(cfg_file, 'w') as fp:
            fp.write('%s:\n' % PROCESSING_KEY)
            fp.write('   %s' % ('   '.join(
                ['%s: %s\n' % (k, v) for (k, v) in list(processing.items())])))

            fp.write('%s:\n' % SERVER_LIST_KEY)
            fp.write(' - %s' % ('   '.join(
                ['%s: %s\n' % (k, v) for (k, v) in list(server.items())])))

        # Settings Object
        setting = NNTPSettings(cfg_file=cfg_file)

        # Create our NNTP Manager Instance
        mgr = NNTPManager(setting)

        pf = NNTPPostFactory(connection=mgr)
        assert(pf.detect_split_size(None) is False)
        assert(pf.detect_split_size('') is False)
        assert(pf.detect_split_size('garbage') is False)

        # 0-100MB     ->   5MB/archive
        assert(pf.detect_split_size(0) == strsize_to_bytes('5MB'))
        assert(pf.detect_split_size(strsize_to_bytes('5MB') - 1) ==
               strsize_to_bytes('5MB'))

        # 100MB-1GB   ->  15MB/archive
        assert(pf.detect_split_size('100M') == strsize_to_bytes('15MB'))
        assert(pf.detect_split_size(strsize_to_bytes('1GB') - 1) ==
               strsize_to_bytes('15MB'))

        # 1GB-5GB     ->  50MB/archive
        assert(pf.detect_split_size('1G') == strsize_to_bytes('50MB'))
        assert(pf.detect_split_size(strsize_to_bytes('5GB') - 1) ==
               strsize_to_bytes('50MB'))

        # 5GB-15GB    ->  100MB/archive
        assert(pf.detect_split_size('5G') == strsize_to_bytes('100MB'))
        assert(pf.detect_split_size(strsize_to_bytes('15GB') - 1) ==
               strsize_to_bytes('100MB'))

        # 15GB-25GB   ->  200MB/archive
        assert(pf.detect_split_size('15G') == strsize_to_bytes('200MB'))
        assert(pf.detect_split_size(strsize_to_bytes('25GB') - 1) ==
               strsize_to_bytes('200MB'))

        # 25GB+       ->  400MB/archive
        assert(pf.detect_split_size('25G') == strsize_to_bytes('400MB'))
        assert(pf.detect_split_size('50G') == strsize_to_bytes('400MB'))
        assert(pf.detect_split_size('100G') == strsize_to_bytes('400MB'))

    def test_custom_hooks_keep_transfer_tracking(self):
        """
        Loading custom hooks keeps the factory's transfer progress hooks
        """
        @hook(name='socket_write')
        def custom(**kwargs):
            return 'custom'

        mgr = NNTPManager(self.nntp_settings('post_hooks'))
        pf = NNTPPostFactory(connection=mgr)

        path = join(self.tmp_dir, 'NNTPPostFactory.hooks', 'My.Content')
        assert self.touch(join(path, 'file.txt'), size='1K')
        assert pf.load(path, hooks=custom) is True

        def functions(name):
            return [
                meta['function'].__name__
                for h in mgr.hooks for meta in (h[name] or [])]

        assert 'custom' in functions('socket_write')
        assert 'transfer_rates' in functions('socket_write')
        assert 'transfer_count' in functions('post_post')
        mgr.close()

    def test_saving_a_missing_article_returns_none(self):
        """
        save_article() returns None when the article id doesn't exist
        """
        mgr = NNTPManager(self.nntp_settings('post_missing'))
        pf = NNTPPostFactory(connection=mgr)

        path = join(self.tmp_dir, 'NNTPPostFactory.missing', 'My.Content')
        assert self.touch(join(path, 'file.txt'), size='1K')
        assert pf.load(path) is True

        assert pf.save_article(NNTPArticle(), id=9999) is None
        mgr.close()


def test_a_single_connection_can_post_and_verify(tmp_path):
    """
    A post can be uploaded and verified with one NNTPConnection (not only
    an NNTPManager); an article is only verified once every group it was
    posted to has it
    """
    import re
    from tests.FakeNNTPServer import FakeNNTPServer
    from tests.NNTPPostPassword_Test import content
    from tests.conftest import connect_to

    groups = ['alt.binaries.test', 'alt.binaries.other']
    with FakeNNTPServer(join_group=True) as server:
        for group in groups:
            server.add_group(group)

        pf = NNTPPostFactory(connection=connect_to(server, join_group=True))
        path = content(tmp_path)
        assert pf.load(path) is True
        assert pf.stage(groups=groups, subject='Holiday photos',
                        poster='me@example.com') is True
        assert pf.upload(groups=groups) is True
        assert server.posted

        def make_available(*names):
            for no, article in enumerate(server.posted):
                found = re.search(rb'^Message-ID: <([^>]+)>', article, re.M)
                stored = tmp_path / ('posted.%d.eml' % no)
                stored.write_bytes(article)
                server.add_article(
                    found.group(1).decode(), str(stored), names)

        # Only one of its groups has it, so it isn't marked verified (a
        # later run with nothing on the server still finds it unverified)
        make_available(groups[0])
        assert pf.verify() is False
        server.articles.clear()
        assert pf.verify() is False

        # Now both do
        make_available(*groups)
        assert pf.verify() is True
        pf.connection.close()


def posted_file(posted, work_dir):
    """
    Puts the file the yEnc articles posted hold back together
    """
    import re
    from io import BytesIO
    from newsreap.codecs.CodecYenc import CodecYenc

    parts = []
    for article in posted:
        body = article.split(b'\r\n\r\n', 1)[1]
        begin = int(re.search(rb'=ypart begin=(\d+)', body).group(1))
        content = CodecYenc(work_dir=work_dir).decode(BytesIO(body))
        parts.append((begin, content.getvalue()))
    return b''.join(data for _, data in sorted(parts))


def test_a_file_bigger_than_an_article_is_posted_in_parts(tmp_path):
    """
    A file split over several articles is posted a part per article (each
    its own piece, nothing in front of its yEnc data) and the parts put
    back together give the file again
    """
    import os
    from tests.FakeNNTPServer import FakeNNTPServer
    from tests.conftest import connect_to

    path = tmp_path / 'Holiday'
    path.mkdir()
    data = os.urandom(25000)
    (path / 'photo.jpg').write_bytes(data)

    with FakeNNTPServer(join_group=True) as server:
        server.add_group('alt.binaries.test')
        pf = NNTPPostFactory(connection=connect_to(server, join_group=True))
        assert pf.load(str(path)) is True
        assert pf.stage(groups='alt.binaries.test', split_size=10000,
                        subject='Holiday', poster='me@example.com') is True
        assert pf.upload(groups='alt.binaries.test') is True
        pf.connection.close()

    assert len(server.posted) == 3
    for article in server.posted:
        body = article.split(b'\r\n\r\n', 1)[1]
        assert body.startswith(b'=ybegin ')
        assert b' size=25000 ' in body.split(b'\r\n', 1)[0]

    assert posted_file(server.posted, str(tmp_path / 'decoded')) == data


def test_an_article_that_isnt_posted_fails_the_upload(tmp_path):
    """
    An article a hook stops from going up fails the upload and its
    verification, and no NZB-File is written
    """
    from tests.FakeNNTPServer import FakeNNTPServer
    from tests.NNTPPostPassword_Test import content
    from tests.conftest import connect_to

    @hook(name='upload_article')
    def refuse(**kwargs):
        return False

    with FakeNNTPServer(join_group=True) as server:
        server.add_group('alt.binaries.test')
        pf = NNTPPostFactory(connection=connect_to(server, join_group=True))
        path = content(tmp_path)
        assert pf.load(path, hooks=refuse) is True
        assert pf.stage(groups='alt.binaries.test', subject='Holiday',
                        poster='me@example.com') is True

        assert pf.upload(groups='alt.binaries.test') is False
        assert not server.posted
        assert not os.path.exists(path + '.nzb')
        assert pf.verify() is False
        pf.connection.close()


def test_an_upload_a_hook_stops_returns_none(tmp_path):
    """
    An upload a pre_upload hook stops returns None (its post_upload hook is
    told there is no NZB-File)
    """
    from newsreap.NNTPConnection import NNTPConnection
    from tests.NNTPPostPassword_Test import content

    told = []

    @hook(name='pre_upload')
    def stop(**kwargs):
        return False

    @hook(name='post_upload')
    def finished(**kwargs):
        told.append(kwargs['nzb'])

    # Never connected
    pf = NNTPPostFactory(connection=NNTPConnection(host='localhost'))
    assert pf.load(content(tmp_path), hooks=stop) is True
    pf.hooks.add(finished)
    assert pf.upload(groups='alt.binaries.test') is None
    assert told == [None]


def test_nothing_staged_isnt_uploaded(tmp_path):
    """
    Uploading what was never staged fails (rather than writing an empty
    NZB-File)
    """
    from newsreap.NNTPConnection import NNTPConnection
    from tests.NNTPPostPassword_Test import content

    # Never connected
    pf = NNTPPostFactory(connection=NNTPConnection(host='localhost'))
    path = content(tmp_path)
    assert pf.load(path) is True
    assert pf.upload(groups='alt.binaries.test') is False
    assert not os.path.exists(path + '.nzb')


def test_post_upload_hooks_see_the_nzb_file_written(tmp_path):
    """
    A post_upload hook is handed the NZB-File just written, every file of
    it (read back from the file), with what a pre_nzb hook added
    """
    from newsreap.NNTPEmptyContent import NNTPEmptyContent
    from newsreap.NNTPSegmentedPost import NNTPSegmentedPost
    from tests.FakeNNTPServer import FakeNNTPServer
    from tests.conftest import connect_to

    path = tmp_path / 'Holiday'
    path.mkdir()
    (path / 'a.jpg').write_bytes(os.urandom(1500))
    (path / 'b.jpg').write_bytes(os.urandom(1500))

    seen = []

    @hook(name='pre_nzb')
    def extra(**kwargs):
        kwargs['nzb'].meta['title'] = 'Holiday'
        post = NNTPSegmentedPost('extra.txt', groups=['alt.binaries.test'])
        article = NNTPArticle(id='extra@example.com', no=1)
        article.add(NNTPEmptyContent(filepath='extra.txt', part=1,
                                     total_size=10))
        post.add(article)
        kwargs['nzb'].add(post)

    @hook(name='post_upload')
    def finished(**kwargs):
        seen.extend([a.msgid() for a in post] for post in kwargs['nzb'])

    with FakeNNTPServer(join_group=True) as server:
        server.add_group('alt.binaries.test')
        pf = NNTPPostFactory(connection=connect_to(server, join_group=True))
        assert pf.load(str(path), hooks=finished) is True
        pf.hooks.add(extra)
        assert pf.stage(groups='alt.binaries.test', split_size=1000,
                        subject='Holiday', poster='me@example.com') is True
        assert pf.upload(groups='alt.binaries.test') is True
        pf.connection.close()

    assert [len(ids) for ids in seen] == [2, 2, 1]
    assert seen[-1] == ['extra@example.com']
    assert len({i for ids in seen for i in ids}) == 5
    assert NNTPnzb('%s.nzb' % path).meta == {'title': 'Holiday'}


def test_nzb_file_hooks_can_change_a_file_or_leave_it_out(tmp_path):
    """
    An nzb_file hook is handed each staged file as it is written into the
    NZB-File; it can change it, or answer False to leave it out
    """
    from tests.FakeNNTPServer import FakeNNTPServer
    from tests.conftest import connect_to

    path = tmp_path / 'Holiday'
    path.mkdir()
    for name in 'abc':
        (path / ('%s.jpg' % name)).write_bytes(os.urandom(1500))

    @hook(name='nzb_file')
    def tidy(post, **kwargs):
        if post.sort_no == 2:
            return False
        post.subject = 'File %d' % post.sort_no

    with FakeNNTPServer(join_group=True) as server:
        server.add_group('alt.binaries.test')
        pf = NNTPPostFactory(connection=connect_to(server, join_group=True))
        assert pf.load(str(path), hooks=tidy) is True
        assert pf.stage(groups='alt.binaries.test', split_size=1000,
                        subject='Holiday', poster='me@example.com') is True
        assert pf.upload(groups='alt.binaries.test') is True
        pf.connection.close()

    # Every file still went up
    assert len(server.posted) == 6
    assert [(post.subject, len(post)) for post in NNTPnzb(
        '%s.nzb' % path)] == [('File 1', 2), ('File 3', 2)]


def test_a_pre_nzb_hook_can_stop_the_nzb_file_being_written(tmp_path):
    """
    A pre_nzb hook answering False means no NZB-File is written; the
    upload itself still worked
    """
    from tests.FakeNNTPServer import FakeNNTPServer
    from tests.conftest import connect_to

    path = tmp_path / 'Holiday'
    path.mkdir()
    (path / 'a.jpg').write_bytes(os.urandom(1500))

    told = []

    @hook(name='pre_nzb')
    def skip(**kwargs):
        return False

    @hook(name='post_upload')
    def finished(**kwargs):
        told.append(kwargs['nzb'])

    with FakeNNTPServer(join_group=True) as server:
        server.add_group('alt.binaries.test')
        pf = NNTPPostFactory(connection=connect_to(server, join_group=True))
        assert pf.load(str(path), hooks=skip) is True
        pf.hooks.add(finished)
        assert pf.stage(groups='alt.binaries.test', split_size=1000,
                        subject='Holiday', poster='me@example.com') is True
        assert pf.upload(groups='alt.binaries.test') is True
        pf.connection.close()

    assert len(server.posted) == 2
    assert not os.path.exists('%s.nzb' % path)
    assert told == [None]


def test_the_nzb_file_lists_the_sizes_noted_when_posting(tmp_path):
    """
    The NZB-File is made from what the staging database noted (the staged
    files aren't read again, so it doesn't matter if they are gone)
    """
    import re
    from tests.FakeNNTPServer import FakeNNTPServer
    from tests.conftest import connect_to

    path = tmp_path / 'Holiday'
    path.mkdir()
    (path / 'a.jpg').write_bytes(os.urandom(2500))

    with FakeNNTPServer(join_group=True) as server:
        server.add_group('alt.binaries.test')
        pf = NNTPPostFactory(connection=connect_to(server, join_group=True))
        assert pf.load(str(path)) is True
        assert pf.stage(groups='alt.binaries.test', split_size=1000,
                        subject='Holiday', poster='me@example.com') is True
        staged = sorted(os.path.getsize(os.path.join(pf.stage_path, name))
                        for name in os.listdir(pf.stage_path))
        assert pf.upload(groups='alt.binaries.test') is True
        pf.connection.close()

    for name in os.listdir(pf.stage_path):
        os.remove(os.path.join(pf.stage_path, name))

    again = str(tmp_path / 'again.nzb')
    assert NNTPnzb().save(again, posts=pf._nzb_posts(pf.session())) is True
    listed = sorted(int(b) for b in re.findall(
        r'bytes="(\d+)"', open(again).read()))
    assert listed == staged


def test_a_hook_can_choose_the_name_a_file_is_posted_under(tmp_path):
    """
    The name a post_encoded_filename hook answers with is the one a file
    is posted under
    """
    from tests.FakeNNTPServer import FakeNNTPServer
    from tests.NNTPPostPassword_Test import content
    from tests.conftest import connect_to

    @hook(name='post_encoded_filename')
    def rename(**kwargs):
        return 'a1b2c3.bin'

    with FakeNNTPServer(join_group=True) as server:
        server.add_group('alt.binaries.test')
        pf = NNTPPostFactory(connection=connect_to(server, join_group=True))
        assert pf.load(content(tmp_path), hooks=rename) is True
        assert pf.stage(groups='alt.binaries.test', subject='Holiday',
                        poster='me@example.com') is True
        assert pf.upload(groups='alt.binaries.test') is True
        pf.connection.close()

    assert len(server.posted) == 1
    assert b' name=a1b2c3.bin\r\n' in server.posted[0]


def test_an_upload_run_again_keeps_its_files_apart(tmp_path):
    """
    Running an upload again (ie: after it was stopped) writes the same
    NZB-File: each file with its own parts, in order
    """
    import os
    from tests.FakeNNTPServer import FakeNNTPServer
    from tests.conftest import connect_to

    path = tmp_path / 'Holiday'
    path.mkdir()
    (path / 'a.jpg').write_bytes(os.urandom(15000))
    (path / 'b.jpg').write_bytes(os.urandom(15000))

    def files():
        nzb = NNTPnzb('%s.nzb' % path)
        return sorted(
            (len(post), [a.no for a in post]) for post in nzb)

    with FakeNNTPServer(join_group=True) as server:
        server.add_group('alt.binaries.test')
        pf = NNTPPostFactory(connection=connect_to(server, join_group=True))
        assert pf.load(str(path)) is True
        assert pf.stage(groups='alt.binaries.test', split_size=10000,
                        subject='Holiday', poster='me@example.com') is True
        assert pf.upload(groups='alt.binaries.test') is True
        first = files()
        assert first == [(2, [1, 2]), (2, [1, 2])]

        assert pf.upload(groups='alt.binaries.test') is True
        pf.connection.close()

    assert len(server.posted) == 4
    assert files() == first


def test_articles_are_only_checked_for_when_an_upload_runs_again(tmp_path):
    """
    A first upload posts without asking the server about each article
    first; an upload run again (ie: after it was stopped) checks the ones
    not yet marked as posted
    """
    import os
    from tests.FakeNNTPServer import FakeNNTPServer
    from tests.conftest import connect_to
    from newsreap.objects.post.StagedArticle import StagedArticle

    path = tmp_path / 'Holiday'
    path.mkdir()
    (path / 'photo.jpg').write_bytes(os.urandom(20000))

    def stats(server):
        return sum(1 for c in server.received if c.startswith('STAT'))

    with FakeNNTPServer(join_group=True) as server:
        server.add_group('alt.binaries.test')
        pf = NNTPPostFactory(connection=connect_to(server, join_group=True))
        assert pf.load(str(path)) is True
        assert pf.stage(groups='alt.binaries.test', split_size=10000,
                        subject='Holiday', poster='me@example.com') is True
        assert pf.upload(groups='alt.binaries.test') is True
        assert stats(server) == 0

        # The run stopped before its last article was marked as posted
        session = pf.session()
        last = session.query(StagedArticle).order_by(
            StagedArticle.sort_no.desc(),
            StagedArticle.sequence_no.desc()).first()
        last.posted_date = None
        session.commit()

        assert pf.upload(groups='alt.binaries.test') is True
        assert stats(server) == 1

        # Staging it again starts afresh
        assert pf.stage(groups='alt.binaries.test', split_size=10000,
                        subject='Holiday', poster='me@example.com') is True
        before = stats(server)
        assert pf.upload(groups='alt.binaries.test') is True
        assert stats(server) == before
        pf.connection.close()


def test_a_verify_that_cant_be_noted_fails():
    """
    An article found on the server that can't be marked as verified (its
    staged row is gone) fails the check rather than passing it
    """
    from types import SimpleNamespace
    from newsreap.NNTPConnection import NNTPConnection
    from newsreap.NNTPHeader import NNTPHeader

    # Never connected
    pf = NNTPPostFactory(connection=NNTPConnection(host='localhost'))
    session = SimpleNamespace(query=lambda *a: SimpleNamespace(
        filter=lambda *a: SimpleNamespace(update=lambda *a, **k: 0)))
    found = SimpleNamespace(wait=lambda: True, response=[NNTPHeader()])

    assert pf._verified(
        session, SimpleNamespace(id=1, message_id='a@x'),
        [('alt.binaries.test', found)]) is False


def test_uploading_holds_only_a_few_articles_at_once(tmp_path, monkeypatch):
    """
    The articles posted are let go as their posts finish (the NZB-File is
    written from the staging database afterwards), so a post of any size
    takes the same memory (the NZB-File is written a file at a time); it
    still lists every part in order
    """
    import os
    import weakref
    from newsreap import NNTPPostFactory as module
    from tests.FakeNNTPServer import FakeNNTPServer
    from tests.conftest import connect_to

    # Read a few staged articles at a time, so paging is used too
    monkeypatch.setattr(module, 'STAGED_CHUNK', 3)

    live = weakref.WeakSet()
    most = [0]

    class Tracked(module.NNTPArticle):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            live.add(self)
            most[0] = max(most[0], len(live))

    monkeypatch.setattr(module, 'NNTPArticle', Tracked)

    path = tmp_path / 'Holiday'
    path.mkdir()
    for name in 'abcde':
        (path / ('%s.jpg' % name)).write_bytes(os.urandom(3500))

    with FakeNNTPServer(join_group=True) as server:
        server.add_group('alt.binaries.test')
        pf = NNTPPostFactory(connection=connect_to(server, join_group=True))
        assert pf.load(str(path)) is True
        assert pf.stage(groups='alt.binaries.test', split_size=1000,
                        subject='Holiday', poster='me@example.com') is True
        most[0] = 0
        assert pf.upload(groups='alt.binaries.test') is True
        pf.connection.close()

    assert len(server.posted) == 20
    assert most[0] < 10

    nzb = NNTPnzb('%s.nzb' % path)
    assert [[a.no for a in post] for post in nzb] == [[1, 2, 3, 4]] * 5


def test_posting_leaves_no_files_open(tmp_path):
    """
    Uploading doesn't keep each article's file open (a post of a few
    thousand articles would otherwise run out of them)
    """
    import os
    from tests.FakeNNTPServer import FakeNNTPServer
    from tests.conftest import connect_to

    path = tmp_path / 'Holiday'
    path.mkdir()
    (path / 'photo.jpg').write_bytes(os.urandom(20000))

    def open_files():
        return len(os.listdir('/proc/self/fd'))

    with FakeNNTPServer(join_group=True) as server:
        server.add_group('alt.binaries.test')
        pf = NNTPPostFactory(connection=connect_to(server, join_group=True))
        assert pf.load(str(path)) is True
        assert pf.stage(groups='alt.binaries.test', split_size=1000,
                        subject='Holiday', poster='me@example.com') is True

        before = open_files()
        assert pf.upload(groups='alt.binaries.test') is True
        assert open_files() - before < 5
        pf.connection.close()

    assert len(server.posted) == 20


def test_an_upload_the_server_refuses_writes_no_nzb_file(tmp_path):
    """
    An upload the server won't take fails and writes no NZB-File; run
    again once the server takes posts, it finishes the post
    """
    from tests.FakeNNTPServer import FakeNNTPServer
    from tests.conftest import connect_to
    from newsreap.objects.post.StagedArticle import StagedArticle

    path = tmp_path / 'Holiday'
    path.mkdir()
    (path / 'a.jpg').write_bytes(os.urandom(2500))

    with FakeNNTPServer(join_group=True) as server:
        server.add_group('alt.binaries.test')
        pf = NNTPPostFactory(connection=connect_to(server, join_group=True))
        assert pf.load(str(path)) is True
        assert pf.stage(groups='alt.binaries.test', split_size=1000,
                        subject='Holiday', poster='me@example.com') is True

        server.posting = False
        assert pf.upload(groups='alt.binaries.test') is False
        assert not os.path.exists('%s.nzb' % path)
        session = pf.session()
        assert session.query(StagedArticle).filter(
            StagedArticle.posted_date.isnot(None)).count() == 0

        server.posting = True
        assert pf.upload(groups='alt.binaries.test') is True
        pf.connection.close()

    assert len(server.posted) == 3
    assert len(list(NNTPnzb('%s.nzb' % path))) == 1


def test_an_upload_that_cant_be_noted_as_started_posts_nothing(tmp_path):
    """
    If the staging database can't note that the upload started, nothing
    is posted (a later run couldn't tell what to check for)
    """
    from tests.FakeNNTPServer import FakeNNTPServer
    from tests.conftest import connect_to

    path = tmp_path / 'Holiday'
    path.mkdir()
    (path / 'a.jpg').write_bytes(os.urandom(1500))

    with FakeNNTPServer(join_group=True) as server:
        server.add_group('alt.binaries.test')
        pf = NNTPPostFactory(connection=connect_to(server, join_group=True))
        assert pf.load(str(path)) is True
        assert pf.stage(groups='alt.binaries.test', split_size=1000,
                        subject='Holiday', poster='me@example.com') is True
        pf._db.set = lambda *args, **kwargs: False
        assert pf.upload(groups='alt.binaries.test') is False
        pf.connection.close()

    assert server.posted == []


def test_an_nzb_file_that_cant_be_written_fails_the_upload(tmp_path):
    """
    The upload fails when its NZB-File can't be written (ie: a folder is
    in its place); what was there is left alone and nothing half written
    is left behind
    """
    from tests.FakeNNTPServer import FakeNNTPServer
    from tests.conftest import connect_to

    path = tmp_path / 'Holiday'
    path.mkdir()
    (path / 'a.jpg').write_bytes(os.urandom(1500))
    (tmp_path / 'Holiday.nzb').mkdir()
    (tmp_path / 'Holiday.nzb' / 'keep.txt').write_text('mine')
    (tmp_path / 'Holiday.nzb.new').mkdir()

    with FakeNNTPServer(join_group=True) as server:
        server.add_group('alt.binaries.test')
        pf = NNTPPostFactory(connection=connect_to(server, join_group=True))
        assert pf.load(str(path)) is True
        assert pf.stage(groups='alt.binaries.test', split_size=1000,
                        subject='Holiday', poster='me@example.com') is True
        assert pf.upload(groups='alt.binaries.test') is False
        pf.connection.close()

    assert len(server.posted) == 2
    assert (tmp_path / 'Holiday.nzb' / 'keep.txt').read_text() == 'mine'
    assert sorted(p.name for p in tmp_path.iterdir()
                  if p.name.startswith('Holiday.nzb')) == [
        'Holiday.nzb', 'Holiday.nzb.new']
