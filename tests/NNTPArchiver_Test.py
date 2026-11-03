# Test the archive of small articles (nr update archive)
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

import base64
import os
import re
from datetime import datetime
from datetime import timedelta

import pytest
from click.testing import CliRunner
from sqlalchemy import create_engine

from newsreap.NNTPArchivePost import NNTPArchivePost
from newsreap.NNTPArchiver import NNTPArchiver
from newsreap.decorators import hook
from newsreap.objects.index.ArchivedPost import ArchiveKind
from newsreap.objects.index.ArchivedPost import ArchivedPost
from newsreap.objects.index.ObjectBase import ObjectBase as IndexBase
from tests.FakeNNTPServer import FakeNNTPServer
from tests.FakeNNTPServer import Reply
from tests.SearchPlan import search_plan
from tests.conftest import config_for

GROUP = 'alt.discuss.test'
OTHER = 'alt.discuss.other'
POSTED = datetime(2020, 1, 1)

# Database servers the archive is tested on too (see NNTPIndexer_Test)
INDEX_SERVERS = [
    name for name in ('postgresql', 'mysql')
    if os.environ.get('NEWSREAP_TEST_%s' % name.upper())]


def article(body, **headers):
    """
    Returns an article (as a server sends it) with the headers given (a
    '_' in a name becomes '-') and body
    """
    headers.setdefault('From', 'someone@example.com')
    headers.setdefault('Subject', 'A subject')
    head = ''.join(
        '%s: %s\r\n' % (name.replace('_', '-'), value)
        for name, value in headers.items()).encode('utf-8')
    if isinstance(body, str):
        body = body.encode('utf-8')
    return head + b'\r\n' + body.replace(b'\r\n', b'\n').replace(
        b'\n', b'\r\n')


def converted(raw, hooks=None, default_hooks=True, **kwargs):
    """
    Returns the post an article becomes (with the archive hooks run over
    it)
    """
    archiver = NNTPArchiver(
        ':memory:', hooks=hooks, default_hooks=default_hooks)
    return archiver.convert(NNTPArchivePost('id@example.com', raw=raw,
                                            **kwargs))


#
# What is kept of an article
#
def test_plain_text_is_kept():
    """
    Plain text is kept as it is, decoded with the character set it says
    it uses
    """
    post = converted(article(
        'Caf\xe9 talk\nsecond line\n'.encode('latin-1'),
        Content_Type='text/plain; charset=iso-8859-1'))
    assert post.kind == ArchiveKind.Text
    assert post.text == 'Café talk\nsecond line'
    assert post.charset == 'iso-8859-1'
    assert post.files == []


def test_text_without_a_character_set():
    """
    Text that doesn't say which character set it uses is read as UTF-8,
    or else Windows-1252
    """
    assert converted(article('na\xefve'.encode())).text == 'naïve'
    post = converted(article(b'smart \x93quotes\x94'))
    assert post.text == 'smart “quotes”'
    assert post.charset == 'cp1252'


def test_bodies_without_a_header():
    """
    A body fetched on its own (BODY instead of ARTICLE) is kept too
    """
    post = converted(b'just a body\r\nwith two lines\r\n')
    assert post.kind == ArchiveKind.Text
    assert post.text == 'just a body\nwith two lines'


@pytest.mark.parametrize('encoding, body', [
    ('quoted-printable', 'Caf=C3=A9 =3D good=\n soup\n'),
    ('base64', base64.b64encode('Café = good soup\n'.encode()).decode()),
])
def test_encoded_text_is_decoded(encoding, body):
    """
    Text stored as quoted-printable or base64 is decoded so it can be
    searched
    """
    post = converted(article(
        body, MIME_Version='1.0',
        Content_Type='text/plain; charset=utf-8',
        Content_Transfer_Encoding=encoding))
    assert post.kind == ArchiveKind.Text
    assert post.text.replace('\n', '') == 'Café = good soup'
    assert post.encoding == [encoding]


def multipart(*parts):
    """
    Returns a multipart MIME article made of (headers, body) parts
    """
    body = ''.join(
        '--BOUNDARY\r\n%s\r\n%s\r\n' % (
            ''.join('%s: %s\r\n' % h for h in headers), part)
        for headers, part in parts) + '--BOUNDARY--\r\n'
    return article(
        body, MIME_Version='1.0',
        Content_Type='multipart/mixed; boundary="BOUNDARY"')


def test_attachments_are_only_noted():
    """
    The text of a MIME article is kept; its attachments are only noted
    (name, type and size)
    """
    picture = base64.b64encode(b'\x89PNG' + b'\0' * 96).decode()
    post = converted(multipart(
        ((('Content-Type', 'text/plain; charset=utf-8'), ), 'See the photo'),
        ((('Content-Type', 'image/png'),
          ('Content-Disposition', 'attachment; filename="cat.png"'),
          ('Content-Transfer-Encoding', 'base64')), picture),
    ))
    assert post.kind == ArchiveKind.Text
    assert post.text == 'See the photo'
    assert [(f['filename'], f['content_type'], f['size'])
            for f in post.files] == [('cat.png', 'image/png', 100)]


def test_attachments_on_their_own_are_binary():
    """
    A MIME article with nothing but attachments (HTML included, which
    isn't read yet) is a binary
    """
    post = converted(multipart(
        ((('Content-Type', 'text/html'), ), '<p>Hello</p>'),
        ((('Content-Type', 'application/zip'),
          ('Content-Disposition', 'attachment; filename=a.zip')), 'PK'),
    ))
    assert post.kind == ArchiveKind.Binary
    assert post.text is None
    assert [f['filename'] for f in post.files] == ['', 'a.zip']
    assert post.files[0]['content_type'] == 'text/html'


def test_yenc_files_are_only_noted():
    """
    A yEnc encoded file isn't kept; its name, size, part and checksum are
    (and any text written before it)
    """
    post = converted(article(
        'Here is part one\n\n'
        '=ybegin part=1 total=3 line=128 size=5000 name=photo 1.jpg\n'
        '=ypart begin=1 end=100\n'
        'kw\x7f\x88garbage\n'
        '=yend size=100 part=1 pcrc32=aaaaaaaa crc32=DEADBEEF\n'))
    assert post.kind == ArchiveKind.Binary
    assert post.text == 'Here is part one'
    assert post.files == [{
        'filename': 'photo 1.jpg', 'codec': 'yenc', 'content_type': '',
        'size': 5000, 'part': 1, 'total': 3, 'crc32': 'deadbeef'}]


def test_cut_off_yenc_parts_still_give_their_name():
    """
    A yEnc part that was cut off (no =yend) still gives its file's name,
    part, total and size; only the checksum (on =yend) is missing
    """
    post = converted(article(
        '=ybegin part=2 total=9 line=128 size=6000000 name=Movie.Part.mkv\n'
        '=ypart begin=750001 end=753000\n'
        'kw\x7f\x88garbage\nmore garbage that just stops'))
    assert post.kind == ArchiveKind.Binary
    assert [(f['filename'], f['part'], f['total'], f['size'], f['crc32'])
            for f in post.files] == [('Movie.Part.mkv', 2, 9, 6000000, '')]


def test_uuencoded_files_are_only_noted():
    """
    A uuencoded file isn't kept; its name is
    """
    post = converted(article(
        'begin 644 notes.dat\n'
        'M5&AI<R!I<R!A(&QI;F4@;V8@=&5X=\'1H870@:7,@9F%I<FQY(&QO;F<@86YD\n'
        '`\nend\n'))
    assert post.kind == ArchiveKind.Binary
    assert post.text is None
    assert [(f['filename'], f['codec']) for f in post.files] == [
        ('notes.dat', 'uuencode')]


def test_small_text_files_have_their_text_kept():
    """
    The text of a small text file posted on its own (ie: an .nfo or .sfv,
    yEnc encoded or uuencoded) is kept so it can be searched; .nfo files
    are read as DOS text
    """
    from zlib import crc32
    from newsreap.codecs.CodecYenc import yenc_encode

    def yenc(name, data):
        encoded, _ = yenc_encode(data)
        return article(
            b'=ybegin line=128 size=%d name=%s\r\n%s\r\n'
            b'=yend size=%d crc32=%08x\r\n' % (
                len(data), name.encode(), encoded.rstrip(b'\r\n'),
                len(data), crc32(data)))

    nfo = '╔═══════╗\r\n║ Holiday.Movie.2026 ║\r\n╚═══════╝\r\n'
    post = converted(yenc('release.nfo', nfo.encode('cp437')))
    assert post.kind == ArchiveKind.Binary
    assert post.files[0]['filename'] == 'release.nfo'
    assert post.text == nfo.replace('\r\n', '\n').strip()

    post = converted(yenc('release.sfv', b'a.rar 01234567\r\n'))
    assert post.text == 'a.rar 01234567'

    # Part of a bigger file, or not a kind of text file: only noted
    assert converted(yenc('movie.mkv', b'Holiday')).text is None

    post = converted(article(
        'begin 644 notes.txt\n'
        'M5&AI<R!I<R!A(&QI;F4@;V8@=&5X=\'1H870@:7,@9F%I<FQY(&QO;F<@86YD\n'
        '`\nend\n'))
    assert post.kind == ArchiveKind.Binary
    assert post.text == 'This is a line of textthat is fairly long and'


def test_pgp_blocks_are_noted():
    """
    A signed post keeps its text (and notes its signature); a post that is
    only an encrypted message is a file
    """
    from tests.CodecPGP_Test import sample

    post = converted(article(
        b'Before\n' + sample('clearsigned.asc') + b'After\n'))
    assert post.kind == ArchiveKind.Text
    assert post.text == \
        'Before\nAfter\nHello Usenet.\n- a dash line\nFrom here'
    assert [(f['codec'], f['content_type']) for f in post.files] == [
        ('pgp', 'application/pgp-signature')]

    post = converted(article(sample('encrypted.asc')))
    assert post.kind == ArchiveKind.Binary
    assert post.text is None
    assert [(f['codec'], f['content_type']) for f in post.files] == [
        ('pgp', 'application/pgp-encrypted')]

    post = converted(article(sample('signed.asc')))
    assert post.files[0]['filename'] == 'note.txt'

    # Only looks like one
    post = converted(article('-----BEGIN PGP nothing here'))
    assert post.kind == ArchiveKind.Text
    assert post.files == []


def test_text_mixing_character_sets_reads_right():
    """
    A UTF-8 post quoting latin-1 text comes out right on every line, even
    when it claims to be latin-1
    """
    post = converted(article(
        'Café — yes\n'.encode() + b'> caf\xe9\n',
        Content_Type='text/plain; charset=iso-8859-1'))
    assert post.text == 'Café — yes\n> café'


def test_sentences_starting_with_begin_stay_text():
    """
    A line like 'begin with this' isn't taken for a uuencoded file
    """
    post = converted(article('begin with this\nand carry on\n'))
    assert post.kind == ArchiveKind.Text
    assert post.text == 'begin with this\nand carry on'


def test_encoded_subjects_are_decoded():
    """
    A subject written with encoded words is kept readable
    """
    post = converted(article(
        'text', Subject='=?utf-8?b?%s?=' % base64.b64encode(
            'Caf\xe9 news'.encode()).decode()), subject='=?raw listing?=')
    assert post.subject == 'Café news'


def test_unreadable_articles_are_binary():
    """
    An article that is mostly control characters isn't kept as text
    """
    post = converted(article(bytes(range(32)) * 10))
    assert post.kind == ArchiveKind.Binary
    assert post.text is None


def test_missing_articles():
    """
    An article no server had is noted as missing
    """
    post = converted(None)
    assert post.kind == ArchiveKind.Missing
    assert post.text is None


#
# Hooks decide what is kept
#
def test_hooks_can_refuse_articles():
    """
    A pre_archive hook returning False keeps an article from being kept
    """
    @hook(name='pre_archive')
    def no_spam(post, **kwargs):
        return 'spam' not in post.subject.lower()

    assert converted(article('x', Subject='Buy SPAM'),
                     hooks=no_spam).kind == ArchiveKind.Refused
    assert converted(article('x', Subject='Hello'),
                     hooks=no_spam).kind == ArchiveKind.Text


def test_hooks_can_convert_articles():
    """
    A hook can convert an article before the built in ones (and stop them
    by marking it done)
    """
    @hook(name='archive_convert', priority=50)
    def shout(post, **kwargs):
        if post.header('X-Shout'):
            post.text = post.body.decode().upper()
            post.done = True

    post = converted(article('quiet words', X_Shout='yes'), hooks=shout)
    assert post.text == 'QUIET WORDS'

    # Without it, the built in ones carry on
    assert converted(article('quiet words'), hooks=shout).text == \
        'quiet words'


def test_built_in_hooks_can_be_left_out():
    """
    Without the built in hooks only the hooks given decide (nothing else
    is kept)
    """
    post = converted(article('words'), default_hooks=False)
    assert post.kind == ArchiveKind.Refused

    @hook(name='archive_convert')
    def everything(post, **kwargs):
        post.text = 'mine'

    assert converted(article('words'), hooks=everything,
                     default_hooks=False).text == 'mine'


def test_a_broken_hook_doesnt_stop_the_others():
    """
    A hook that fails is skipped; the others still run
    """
    @hook(name='archive_convert', priority=10)
    def broken(post, **kwargs):
        raise RuntimeError('oops')

    assert converted(article('still kept'), hooks=broken).text == \
        'still kept'


#
# Keeping and searching posts
#
@pytest.fixture(params=['sqlite', *INDEX_SERVERS])
def index_engine(request, tmp_path):
    """
    Where the archive is kept: an SQLite file, or an empty database on each
    database server given
    """
    if request.param == 'sqlite':
        return str(tmp_path / 'cache' / 'index.db')

    url = os.environ['NEWSREAP_TEST_%s' % request.param.upper()]
    engine = create_engine(url)
    IndexBase.metadata.drop_all(engine)
    engine.dispose()
    return url


def kept(index_engine, *posts):
    """
    Returns an archiver that has converted and kept the posts given
    ((message_id, raw, groups, posted, poster) each)
    """
    archiver = NNTPArchiver(index_engine)
    session = archiver.session(create=True)
    for message_id, raw, groups, posted, poster in posts:
        post = NNTPArchivePost(
            message_id, raw=raw, posted=posted, poster=poster,
            groups=groups, size=len(raw or b''))
        archiver.store(session, archiver.convert(post))
    session.commit()
    return archiver


@pytest.fixture
def archive(index_engine):
    """
    An archive holding a few posts
    """
    archiver = kept(
        index_engine,
        ('1@x', article('We met at the Café for a holiday chat',
                        Subject='Holiday plans'), {GROUP},
         POSTED, 'chris@example.com'),
        ('2@x', article('Nothing about trips here', Subject='Work'),
         {GROUP, OTHER}, POSTED + timedelta(days=1), 'boss@example.com'),
        ('3@x', article('=ybegin part=1 total=1 line=128 size=10 '
                        'name=holiday-photo.jpg\n=yend size=10 crc32=1\n',
                        Subject='[1/1] photo'), {OTHER},
         POSTED + timedelta(days=2), 'chris@example.com'),
        ('4@x', None, {GROUP}, POSTED, 'ghost@example.com'),
    )
    yield archiver
    archiver.close()


def ids(archiver, *keywords, **kwargs):
    """
    Returns the Message-IDs of the posts found
    """
    return [post.message_id for post in archiver.find(keywords, **kwargs)]


def test_posts_are_found_by_their_words(archive):
    """
    Posts are found by the words in their subject, text or file names;
    the last word can be the start of one
    """
    assert ids(archive, 'holiday') == ['3@x', '1@x']
    assert ids(archive, 'holid') == ['3@x', '1@x']
    assert ids(archive, 'chat') == ['1@x']
    assert ids(archive, 'holiday chat') == ['1@x']
    assert ids(archive, 'photo.jpg') == ['3@x']
    assert ids(archive, 'trips') == ['2@x']
    assert ids(archive, 'nothing at all') == []

    # Missing (and refused) posts aren't searched
    assert ids(archive) == ['3@x', '2@x', '1@x']


def test_post_searches_ignore_case_and_accents(archive):
    """
    Case and accents don't matter unless asked
    """
    assert ids(archive, 'CAFE') == ['1@x']
    assert ids(archive, 'café') == ['1@x']
    assert ids(archive, 'Café', case_sensitive=True) == ['1@x']
    assert ids(archive, 'café', case_sensitive=True) == []


def test_post_searches_narrow_down(archive):
    """
    Posts can be left out, searched by poster, and narrowed down to groups
    """
    assert ids(archive, 'holiday', '-photo') == ['1@x']
    assert ids(archive, '%pchris') == ['3@x', '1@x']
    assert ids(archive, '%p-chris') == ['2@x']
    assert ids(archive, groups={OTHER}) == ['3@x', '2@x']
    assert ids(archive, 'holiday', groups={GROUP}) == ['1@x']
    assert ids(archive, limit=1) == ['3@x']

    # Short words and symbols still work
    assert ids(archive, 'at') == ['1@x']
    assert ids(archive, '%') == []


def test_post_searches_use_the_search_index(archive):
    """
    Searching posts (by words or poster) uses the database's search index
    (never reading every post, which is far too slow on a big archive)
    """
    session = archive.session()
    dialect = session.get_bind().dialect.name

    # A real archive holds lots of posts, nearly all of them text (so
    # narrowing it down by kind doesn't help a database much)
    for no in range(500):
        archive.store(session, archive.convert(NNTPArchivePost(
            'filler.%d@x' % no, poster='someone.%d@example.com' % no,
            raw=article('nothing to see %d' % no, Subject='Filler'))))
    session.commit()

    for column, value, index in (
            ('subject', 'holiday', 'archive_post_search'),
            ('poster', 'chris', 'archive_post_poster_search')):
        # The search itself (what find() narrows the posts down with)
        plan = search_plan(session, lambda: session.query(
            ArchivedPost.id).filter(ArchivedPost.id.in_(archive._search(
                session, 0, column, value))).all())
        if dialect == 'sqlite':
            # Looked up with MATCH, not read row by row
            assert re.search(r'archive_search VIRTUAL TABLE INDEX \d+:M',
                             plan)

        else:
            assert index in plan

        if dialect == 'mysql':
            # The search runs once, not once for every post
            assert 'fulltext' in plan
            assert 'DEPENDENT SUBQUERY' not in plan


def test_kept_posts_can_be_read_back(archive):
    """
    A kept post has its text, groups and file details
    """
    post = archive.get('<1@x>')
    assert post.text == 'We met at the Café for a holiday chat'
    assert post.subject == 'Holiday plans'
    assert archive.groups('2@x') == [OTHER, GROUP]
    assert [f.filename for f in archive.files('3@x')] == [
        'holiday-photo.jpg']
    assert archive.get('4@x').kind == ArchiveKind.Missing
    assert archive.get('nope@x') is None

    # A short piece of it around what was searched for
    assert archive.snippet(post, ['chat']).endswith('holiday chat')


def test_posts_kept_again_are_searched_by_their_new_text(index_engine):
    """
    Keeping a post again (ie: after it was missing) replaces what is
    searched
    """
    archiver = kept(index_engine, ('1@x', article('old words'), {GROUP},
                                   POSTED, 'a@x'))
    archiver.close()
    archiver = kept(index_engine, ('1@x', article('new words'), {OTHER},
                                   POSTED, 'a@x'))
    assert ids(archiver, 'new') == ['1@x']
    assert ids(archiver, 'old') == []
    assert archiver.groups('1@x') == [OTHER, GROUP]
    archiver.close()


#
# Fetching (nr update archive)
#
def test_archive_from_the_command_line(tmp_path):
    """
    nr update archive fetches the small articles (once), and they can be
    searched and read
    """
    from newsreap.cli import cli

    group_list = tmp_path / 'group.list'
    group_list.write_text(
        '%s 0000000004 0000000001 y\n'
        '%s 0000000001 0000000001 y\n' % (GROUP, OTHER))

    articles = {
        'talk@x': article('Has anyone tried the new café?',
                          Subject='Coffee'),
        'reply@x': article('Yes, the coffee is great',
                           Subject='Re: Coffee', References='<talk@x>'),
        'file@x': article('=ybegin line=128 size=4 name=menu.pdf\n'
                          'data\n=yend size=4 crc32=0badf00d\n',
                          Subject='menu'),
    }

    # Articles are asked for by Message-ID (no group needed)
    with FakeNNTPServer(group_list=str(group_list),
                        join_group=False) as server:
        for no, (message_id, raw) in enumerate(articles.items(), 1):
            path = tmp_path / ('%d.msg' % no)
            path.write_bytes(raw)
            server.add_article(message_id, str(path), GROUP)

        # The articles listed (one too big to fetch, one no server has)
        server.add_group(GROUP, 0, 1, 1)
        for no, (message_id, size) in enumerate((
                ('talk@x', 200), ('reply@x', 200), ('file@x', 300),
                ('huge@x', 10 ** 6), ('gone@x', 200)), 1):
            server.add_overview(
                GROUP, no, 'Subject %d' % no, 'poster@example.com',
                POSTED + timedelta(minutes=no), message_id, size=size)

        # One of them was posted to another group too
        server.add_overview(
            OTHER, 1, 'Subject 1', 'poster@example.com', POSTED,
            'talk@x', size=200)

        config = config_for(server, tmp_path / 'config.yaml')
        runner = CliRunner()

        def nr(*args):
            return runner.invoke(cli, ['--config', config, *args], obj={})

        assert nr('db', 'init').exit_code == 0
        assert nr('update', 'groups').exit_code == 0

        # Nothing has been cached yet
        result = nr('update', 'archive', GROUP)
        assert result.exit_code == 1
        assert 'Nothing archived yet' in nr('search', '--posts', 'x').output

        assert nr('update', 'search', GROUP).exit_code == 0

        # The server fails one of them (it isn't missing; it is asked for
        # again next time)
        server.on(r'^ARTICLE <talk@x>$', Reply('503 Try again later'),
                  once=True)
        result = nr('update', 'archive', GROUP)
        assert result.exit_code == 0, result.output
        assert '%s: 2 article(s) kept (1 text, 1 binary), 1 missing, ' \
            '0 refused, 0 already kept, 1 failed' % GROUP in result.output

        result = nr('update', 'archive', GROUP)
        assert '%s: 1 article(s) kept (1 text, 0 binary), 0 missing' % \
            GROUP in result.output
        fetched = len([c for c in server.commands()
                       if c.startswith('ARTICLE')])
        assert fetched == 5

        # Nothing is fetched twice
        result = nr('update', 'archive', GROUP)
        assert '0 article(s) kept' in result.output
        assert len([c for c in server.commands()
                    if c.startswith('ARTICLE')]) == fetched

        # Posted to another group too: it is only noted there, not fetched
        # again
        assert nr('update', 'search', OTHER).exit_code == 0
        result = nr('update', 'archive', OTHER)
        assert '%s: 0 article(s) kept (0 text, 0 binary), 0 missing, ' \
            '0 refused, 1 already kept' % OTHER in result.output
        assert len([c for c in server.commands()
                    if c.startswith('ARTICLE')]) == fetched
        assert '<talk@x>' in nr('search', '--posts', 'cafe', '-g', OTHER).output

        # Unless asked to try the missing ones again
        result = nr('update', 'archive', GROUP, '--force')
        assert '1 missing, 0 refused, 3 already kept' in result.output

        # Search them (case and accents don't matter)
        result = nr('search', '--posts', 'CAFE')
        assert result.exit_code == 0
        assert '<talk@x>' in result.output
        assert 'Has anyone tried the new café?' in result.output
        assert '<talk@x>' not in nr('search', '--posts', '-c', 'CAFE').output
        assert '<file@x>' in nr('search', '--posts', 'menu.pdf').output
        assert nr('search', '--posts', '--raw', 'x').exit_code == 1

        # Read one
        result = nr('archive', 'show', 'reply@x', '<file@x>')
        assert result.exit_code == 0
        assert 'Subject:    Re: Coffee' in result.output
        assert 'References: <talk@x>' in result.output
        assert 'Yes, the coffee is great' in result.output
        assert 'Holds:      menu.pdf (yenc, 4.00B, crc32 0badf00d)' in \
            result.output
        assert nr('archive', 'show', 'nope@x').exit_code == 1
