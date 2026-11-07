# Test find(1) style searching of collections and archived posts
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


import re
from datetime import timedelta

import pytest

from newsreap.NNTPArchivePost import NNTPArchivePost
from newsreap.NNTPArchiver import NNTPArchiver
from newsreap.NNTPArticleRanks import NNTPArticleRanks
from newsreap.NNTPSearchTerm import NNTPSearchTerm
from newsreap.objects.index.Collection import Collection

from tests.SearchPlan import search_plan
from tests.NNTPIndexer_Test import GROUP
from tests.NNTPIndexer_Test import POSTED
from tests.NNTPIndexer_Test import POSTER
from tests.NNTPIndexer_Test import cache
from tests.NNTPIndexer_Test import index_engine  # noqa: F401
from tests.NNTPIndexer_Test import indexed
from tests.NNTPIndexer_Test import paths  # noqa: F401
from tests.NNTPIndexer_Test import release

CHRIS = 'Chris <chris@example.com>'


def terms(**options):
    """
    Returns the search conditions command line options would give
    """
    return NNTPSearchTerm.from_options(
        **{k: v if isinstance(v, list) else [v] for k, v in options.items()})


@pytest.fixture
def indexer(paths):
    """
    An index holding a few collections
    """
    cache(paths[GROUP], [
        *release('Holiday.Photos'),
        *release('Sample.Holiday', poster=CHRIS),
        *release('Café.Trip', files=1)])

    indexer = indexed(paths)
    yield indexer
    indexer.close()


def names(indexer, keywords=(), **options):
    """
    Returns the names of the collections a search finds
    """
    return {c.name for c in indexer.find(keywords, terms=terms(**options))}


def test_collections_are_found_by_their_whole_subject(indexer):
    """
    A subject condition must match all of a collection's name or of one of
    its file subjects; * and ? are wildcards
    """
    assert names(indexer, isubject='*holiday*') == {
        'Holiday.Photos', 'Sample.Holiday'}
    assert names(indexer, isubject='holiday*') == {'Holiday.Photos'}
    assert names(indexer, isubject='holiday') == set()
    assert names(indexer, isubject='holiday.photos') == {'Holiday.Photos'}
    assert names(indexer, isubject='h?liday.*') == {'Holiday.Photos'}

    # Case matters without the i
    assert names(indexer, subject='Holiday*') == {'Holiday.Photos'}
    assert names(indexer, subject='holiday*') == set()

    # Accents don't matter with the i
    assert names(indexer, isubject='cafe*') == {'Café.Trip'}
    assert names(indexer, subject='Cafe*') == set()


def test_every_condition_narrows_the_results_down(indexer):
    """
    Each condition given has to match too; a leading ! leaves out what
    matches
    """
    assert names(indexer, isubject=['*holiday*', '*photos*']) == {
        'Holiday.Photos'}
    assert names(indexer, isubject=['*holiday*', '!sample*']) == {
        'Holiday.Photos'}
    assert names(indexer, isubject='*holiday*', iposter='chris*') == {
        'Sample.Holiday'}
    assert names(indexer, isubject='*holiday*', iposter='nobody*') == set()

    # Along with plain keywords
    assert names(indexer, ['photos'], isubject='*holiday*') == {
        'Holiday.Photos'}


def test_collections_are_found_by_file_name_and_poster(indexer):
    """
    Name conditions look at the file names, poster conditions at who
    posted them
    """
    assert names(indexer, ifilename='*.part03.rar') == {
        'Holiday.Photos', 'Sample.Holiday'}
    assert names(indexer, ifilename='CAFE.TRIP.PART01.RAR') == {'Café.Trip'}
    assert names(indexer, filename='*.PART01.RAR') == set()

    assert names(indexer, iposter='chris*') == {'Sample.Holiday'}
    assert names(indexer, poster='Chris*') == {'Sample.Holiday'}
    assert names(indexer, poster='chris*') == set()


def test_collections_are_found_by_regular_expressions(indexer):
    """
    A regular expression must match the whole subject
    """
    assert len(names(indexer, iregex_subject=r'.*\(1/4\).*')) == 3
    assert names(indexer, iregex_subject='sample[.].*') == {'Sample.Holiday'}
    assert names(indexer, regex_subject='sample[.].*') == set()
    assert names(indexer, iregex_subject='holiday') == set()
    assert names(indexer, iregex_poster='chris <.*') == {
        'Sample.Holiday'}


def test_collections_are_found_by_message_id(indexer):
    """
    A Message-ID condition finds the collection holding the article
    """
    assert names(indexer, message_id='holiday.photos.1.1@example.com') == {
        'Holiday.Photos'}
    assert names(indexer, message_id='HOLIDAY.photos.1.1@example.com') == \
        set()
    assert names(indexer, imessage_id='HOLIDAY.photos.1.1@example.com') == \
        {'Holiday.Photos'}
    assert names(indexer, imessage_id='*.3.4@example.com') == {
        'Holiday.Photos', 'Sample.Holiday'}
    assert names(indexer, imessage_id='!*.3.4@example.com') == {'Café.Trip'}
    assert names(indexer, iregex_message_id=r'caf.\.trip\.1\.\d@.*') == {
        'Café.Trip'}


def test_keywords_can_hold_wildcards(indexer):
    """
    A keyword with * or ? in it is found anywhere, wildcards and all
    """
    assert names(indexer, ['holi?ay']) == {'Holiday.Photos', 'Sample.Holiday'}
    assert names(indexer, ['holi*photos']) == {'Holiday.Photos'}
    assert names(indexer, ['holiday', '-sam?le']) == {'Holiday.Photos'}
    assert names(indexer, ['%pchr?s']) == {'Sample.Holiday'}


def test_a_keyword_can_give_choices(indexer):
    """
    'a|b' finds what has either; a - in front leaves out what has any of
    them, a choice can hold wildcards, and %p carries on to the choices
    after it
    """
    assert names(indexer, ['photos|trip']) == {'Holiday.Photos', 'Café.Trip'}
    assert names(indexer, ['holiday', 'photos|sample']) == {
        'Holiday.Photos', 'Sample.Holiday'}
    assert names(indexer, ['-photos|trip']) == {'Sample.Holiday'}
    assert names(indexer, ['pho?os|tr*p']) == {'Holiday.Photos', 'Café.Trip'}
    assert names(indexer, ['%pchris|nobody']) == {'Sample.Holiday'}

    # Empty choices are skipped, and \| is a | itself
    assert names(indexer, ['trip|']) == {'Café.Trip'}
    assert names(indexer, ['a\\|b']) == set()


def test_collections_have_no_body_to_search(indexer):
    """
    Collections can't be searched by a post's text
    """
    with pytest.raises(ValueError, match='only works when searching posts'):
        names(indexer, ibody='*x*')


def test_subject_searches_use_the_search_index(indexer):
    """
    A subject pattern is narrowed down with the database's search index
    (only what holds its text is checked against the pattern)
    """
    session = indexer.session()
    dialect = session.get_bind().dialect.name
    term = terms(isubject='*holiday*')[0]

    plan = search_plan(session, lambda: session.query(Collection.id).filter(
        indexer._term(session, 0, term)).all())

    if dialect == 'sqlite':
        assert re.search(
            r'collection_search VIRTUAL TABLE INDEX \d+:[ML]', plan)

    elif dialect == 'postgresql':
        assert 'collection_name_search' in plan
        assert 'collection_file_subject_search' in plan

    else:
        assert 'collection_name_search' in plan
        assert 'DEPENDENT SUBQUERY' not in plan


@pytest.fixture
def archiver(paths):
    """
    An archive holding a few posts
    """
    archiver = NNTPArchiver(paths['index'], default_hooks=False)
    session = archiver.session(create=True)
    for message_id, subject, poster, text, filename in (
            ('talk@x', 'Coffee in Paris', POSTER,
             'Has anyone tried the new café?', None),
            ('reply@x', 'Re: Coffee in Paris', CHRIS,
             'Yes, the coffee is great', None),
            ('file@x', 'menu', POSTER, 'See attached', 'menu.pdf')):

        post = NNTPArchivePost(
            message_id, raw=b'Subject: x\r\n\r\nx\r\n', subject=subject,
            poster=poster, posted=POSTED, size=100, groups={GROUP})
        post.text = text
        if filename:
            post.add_file(filename=filename, codec='mime')
        archiver.store(session, post)

    session.commit()
    yield archiver
    archiver.close()


def posts(archiver, keywords=(), **options):
    """
    Returns the Message-IDs of the posts a search finds
    """
    return {p.message_id
            for p in archiver.find(keywords, terms=terms(**options))}


def test_posts_are_found_by_subject_and_body(archiver):
    """
    Posts can be searched by their subject and their text, on their own or
    together
    """
    assert posts(archiver, isubject='*coffee*') == {'talk@x', 'reply@x'}
    assert posts(archiver, ibody='*coffee*') == {'reply@x'}
    assert posts(archiver, isubject='*coffee*', ibody='*great') == {'reply@x'}
    assert posts(archiver, isubject='re:*') == {'reply@x'}
    assert posts(archiver, subject='Re:*') == {'reply@x'}
    assert posts(archiver, subject='re:*') == set()
    assert posts(archiver, isubject='!re:*') == {'talk@x', 'file@x'}

    # Accents don't matter with the i
    assert posts(archiver, ibody='*cafe?') == {'talk@x'}
    assert posts(archiver, ibody='*CAFÉ?') == {'talk@x'}


def test_posts_are_found_by_file_name_poster_and_expressions(archiver):
    """
    Posts can be searched by the names of their files, their poster, and
    by regular expressions
    """
    assert posts(archiver, ifilename='*.PDF') == {'file@x'}
    assert posts(archiver, filename='*.PDF') == set()
    assert posts(archiver, iposter='chris*') == {'reply@x'}
    assert posts(archiver, iregex_body='yes, .*') == {'reply@x'}
    assert posts(archiver, regex_body='yes, .*') == set()
    assert posts(archiver, iregex_subject='(re: )?coffee in paris') == {
        'talk@x', 'reply@x'}

    assert posts(archiver, message_id='talk@x') == {'talk@x'}
    assert posts(archiver, imessage_id='*@X') == {
        'talk@x', 'reply@x', 'file@x'}
    assert posts(archiver, message_id='!talk@x') == {'reply@x', 'file@x'}

    # Plain keywords can hold wildcards (and look in subjects, text and file
    # names)
    assert posts(archiver, ['caf?']) == {'talk@x'}
    assert posts(archiver, ['men?.pdf']) == {'file@x'}


def test_the_snippet_follows_what_the_body_needs(archiver):
    """
    The piece of a post's text shown is around what a body condition looks
    for
    """
    post = next(iter(archiver.find(terms=terms(ibody='*great'))))
    text = 'A long beginning that goes on and on and on. ' * 3 + post.text
    post.text = text
    assert 'great' in archiver.snippet(
        post, terms=terms(ibody='*coffee is great'))


def rank(indexer, **kwargs):
    """
    Returns the names of the collections a search narrowed down by tag,
    score or posting time finds
    """
    return {c.name for c in indexer.find(**kwargs)}


def test_collections_are_narrowed_down_by_tag_score_and_time(indexer):
    """
    Tags (! for what mustn't be there), score comparisons and posting
    times narrow collections down; every one given must hold
    """
    session = indexer.session()
    for name, score, tags in (('Holiday.Photos', 10, 'hd video'),
                              ('Sample.Holiday', -5, 'sample video'),
                              ('Café.Trip', 0, '')):
        session.query(Collection).filter(Collection.name == name).update(
            {Collection.score: score, Collection.tags: tags})
    session.commit()

    everything = {'Holiday.Photos', 'Sample.Holiday', 'Café.Trip'}
    assert rank(indexer) == everything

    # Tags
    assert rank(indexer, tags=['video']) == {
        'Holiday.Photos', 'Sample.Holiday'}
    assert rank(indexer, tags=['!sample']) == {'Holiday.Photos', 'Café.Trip'}
    assert rank(indexer, tags=['video, !sample']) == {'Holiday.Photos'}
    assert rank(indexer, tags=['!video', 'hd']) == {
        'Holiday.Photos', 'Café.Trip'}
    assert rank(indexer, without_tags=['sample']) == {
        'Holiday.Photos', 'Café.Trip'}

    # Scores
    assert rank(indexer, scores=['<0']) == {'Sample.Holiday'}
    assert rank(indexer, scores=['>=0']) == {'Holiday.Photos', 'Café.Trip'}
    assert rank(indexer, scores=['>-10, <10']) == {
        'Sample.Holiday', 'Café.Trip'}
    assert rank(indexer, scores=['>-10', '<10', '>-1']) == {'Café.Trip'}
    assert rank(indexer, scores=['=10'], tags=['hd']) == {'Holiday.Photos'}

    with pytest.raises(ValueError, match='takes comparisons'):
        rank(indexer, scores=['10'])

    # When they were posted (a minute apart, in that order)
    split = POSTED + timedelta(minutes=12)
    assert rank(indexer, after=split) == {'Sample.Holiday', 'Café.Trip'}
    assert rank(indexer, before=split) == {'Holiday.Photos'}
    assert rank(indexer, after=split, before=split + timedelta(
        minutes=12)) == {'Sample.Holiday'}
    assert rank(indexer, after='2019-12-31') == everything
    assert rank(indexer, before='2019-12-31') == set()
    assert rank(indexer, after='30d') == set()

    # Along with everything else
    assert {c.name for c in indexer.find(
        ['holiday'], terms=terms(iposter='!chris*'), tags=['video'],
        scores=['>0'], before='1d')} == {'Holiday.Photos'}


def test_posts_are_narrowed_down_by_tag_score_and_time(archiver):
    """
    Posts are narrowed down the same way
    """
    from newsreap.objects.index.ArchivedPost import ArchivedPost

    session = archiver.session()
    for no, (message_id, score, tags) in enumerate((
            ('talk@x', 3, 'chat'), ('reply@x', -2, 'chat reply'),
            ('file@x', 0, 'file'))):
        session.query(ArchivedPost).filter(
            ArchivedPost.message_id == message_id).update({
                ArchivedPost.posted: POSTED + timedelta(days=no)})
        NNTPArticleRanks.save(session, {message_id: (score, tags.split())})
    session.commit()

    def found(**kwargs):
        return {p.message_id for p in archiver.find(**kwargs)}

    assert found(tags=['chat, !reply']) == {'talk@x'}
    assert found(tags=['!chat']) == {'file@x'}
    assert found(scores=['<0']) == {'reply@x'}
    assert found(scores=['>=0'], tags=['chat']) == {'talk@x'}
    assert found(after=POSTED + timedelta(days=1)) == {'reply@x', 'file@x'}
    assert found(before=POSTED + timedelta(days=1)) == {'talk@x'}
    assert found(terms=terms(isubject='*coffee*'),
                 after=POSTED + timedelta(hours=1)) == {'reply@x'}


#
# Acting on what a search found (--delete, --download)
#
def test_found_results_keep_only_what_is_needed_once_shown(capsys):
    """
    Once a result is printed, its lines are let go of; removing keeps only
    what each result is and where it is kept
    """
    from newsreap.plugins.cli.search import Found
    from newsreap.plugins.cli.search import shown

    def found():
        yield Found(None, ['alt.binaries.test:'], None)
        yield Found('a@x', ['  [00001] first', '    snippet'], {
            'kind': 'article', 'group': 'alt.binaries.test',
            'subject': 'first', 'size': 10})

    kept = shown(found(), fields=('kind', 'group'))
    assert kept == [Found('a@x', (), {
        'kind': 'article', 'group': 'alt.binaries.test'})]
    assert capsys.readouterr().out.splitlines() == [
        'alt.binaries.test:', '  [00001] first', '    snippet']

    # Downloading keeps every detail (it puts articles together by them),
    # and a quiet run prints nothing
    kept = shown(found(), show=False)
    assert kept[0].data['subject'] == 'first'
    assert kept[0].lines == ()
    assert capsys.readouterr().out == ''


def test_what_a_search_found_is_downloaded_and_removed(
        tmp_path, index_engine):
    """
    --delete removes the collections, posts and articles a search found,
    and -z saves the NZB files of collections, on every index database
    """
    from click.testing import CliRunner

    from newsreap.cli import cli
    from tests.FakeNNTPServer import FakeNNTPServer
    from tests.NNTPArchiver_Test import article
    from tests.conftest import config_for

    group_list = tmp_path / 'group.list'
    group_list.write_text('%s 0000000004 0000000001 y\n' % GROUP)

    with FakeNNTPServer(group_list=str(group_list),
                        join_group=False) as server:
        for message_id, text in (('talk@x', 'Holiday photos are up'),
                                 ('more@x', 'More holiday photos')):
            path = tmp_path / ('%s.msg' % message_id)
            path.write_bytes(article(text, Subject='Holiday talk'))
            server.add_article(message_id, str(path), GROUP)

        for no, (subject, poster, message_id) in enumerate(
                [*release('Holiday', files=1, segments=2),
                 ('Holiday talk', POSTER, 'talk@x'),
                 ('Holiday talk', POSTER, 'more@x')], 1):
            server.add_overview(
                GROUP, no, subject, poster, POSTED + timedelta(minutes=no),
                message_id, size=100)

        # The index on the database being tested
        config = config_for(server, tmp_path / 'config.yaml')
        with open(config) as fp:
            content = fp.read()
        assert content.count('\ndatabase:\n') == 1
        with open(config, 'w') as fp:
            fp.write(content.replace(
                '\ndatabase:\n',
                "\ndatabase:\n   index_engine: '%s'\n" % index_engine))

        runner = CliRunner()

        def nr(*args, **kwargs):
            return runner.invoke(cli, ['--config', config, *args], obj={},
                                 **kwargs)

        assert nr('db', 'init').exit_code == 0
        assert nr('update', 'groups').exit_code == 0
        result = nr('update', GROUP, '--content')
        assert result.exit_code == 0, result.output

        # An NZB file for each collection found
        collections = nr('search', 'holiday', '-q').output.split()
        assert collections
        out = tmp_path / 'out'
        result = nr('-y', 'search', 'holiday', '-z', '-o', str(out))
        assert result.exit_code == 0, result.output
        assert sorted(path.suffix for path in out.iterdir()) == \
            ['.nzb'] * len(collections)

        # Posts: shown, asked, then removed
        result = nr('search', '--posts', 'more', '--delete', input='y\n')
        assert '<more@x>' in result.output
        assert 'Removed 1 post(s).' in result.output
        assert nr('search', '--posts', 'photos', '-q').output.split() == [
            'talk@x']

        # Articles (from their group's cache) and collections
        result = nr('-y', 'search', '--articles', '-g', GROUP, 'talk',
                    '--delete')
        assert 'Removed 2 article(s).' in result.output, result.output
        assert nr('search', '--articles', '-g', GROUP, 'talk',
                  '-q').output.strip() == ''

        result = nr('-y', 'search', 'holiday', '--delete')
        assert 'Removed %d collection(s).' % len(collections) in \
            result.output, result.output
        assert nr('search', 'holiday', '-q').output.strip() == ''
