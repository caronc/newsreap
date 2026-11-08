# Test keeping the scores and tags of articles in one place
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


from datetime import timedelta

from click.testing import CliRunner

from newsreap.NNTPArticleRanks import NNTPArticleRanks
from newsreap.NNTPGroupDatabase import NNTPGroupDatabase
from newsreap.NNTPIndexDatabase import NNTPIndexDatabase
from newsreap.NNTPIndexer import NNTPIndexer
from newsreap.objects.group.Article import Article
from newsreap.objects.index.ArticleRank import ArticleRank
from newsreap.objects.index.Collection import Collection

from tests.FakeNNTPServer import FakeNNTPServer
from tests.conftest import config_for
from tests.NNTPIndexer_Test import GROUP
from tests.NNTPIndexer_Test import OTHER
from tests.NNTPIndexer_Test import POSTED
from tests.NNTPIndexer_Test import POSTER
from tests.NNTPIndexer_Test import cache
from tests.NNTPIndexer_Test import index_engine  # noqa: F401
from tests.NNTPIndexer_Test import indexed
from tests.NNTPIndexer_Test import paths  # noqa: F401
from tests.NNTPIndexer_Test import release


def rows(session):
    """
    Returns Message-ID -> (score, tags) of every rank kept
    """
    return {r.message_id: (r.score, r.tags)
            for r in session.query(ArticleRank)}


def test_ranks_are_kept_once_per_article(paths):
    """
    An article has one rank, found by its Message-ID (with or without its
    < and >); a score of 0 with no tags keeps nothing
    """
    indexer = NNTPIndexer(paths['index'])
    session = indexer.session(create=True)

    NNTPArticleRanks.save(session, {
        '<a@x>': (5, {'video', 'hd'}), 'b@x': (0, set()),
        'c@x': (-3, set())})
    session.commit()
    assert rows(session) == {'a@x': (5, 'hd video'), 'c@x': (-3, '')}

    # Found whichever way it is written
    assert NNTPArticleRanks.load(session, ['a@x', '<c@x>', 'b@x']) == {
        'a@x': (5, frozenset({'hd', 'video'})), '<c@x>': (-3, frozenset())}

    # Saved again: replaced (never added to)
    NNTPArticleRanks.save(session, {'a@x': (1, {'x'}), 'c@x': (0, ())})
    session.commit()
    assert rows(session) == {'a@x': (1, 'x')}

    NNTPArticleRanks.forget(session, ['<a@x>'])
    session.commit()
    assert rows(session) == {}
    indexer.close()


def test_collections_take_the_best_score_of_their_articles(paths):
    """
    A collection's score is the best of its articles' (an article without
    a rank scores 0), and it has every tag they have
    """
    cache(paths[GROUP], [*release('Good', files=1), *release('Bad', files=1)])
    indexer = indexed(paths)
    session = indexer.session()
    ids = {c.name: c.id for c in session.query(Collection)}

    NNTPArticleRanks.save(session, {
        # One article of Good scores 7, another -2 (the rest 0)
        'good.1.1@example.com': (7, {'video'}),
        'good.1.2@example.com': (-2, {'hd'}),
        # Every article of Bad scores below 0
        **{'bad.1.%d@example.com' % p: (-5 - p, {'junk'})
           for p in range(1, 5)}})

    best = indexer.rank_collections(session, list(ids.values()))
    session.commit()
    assert best[ids['Good']] == (7, {'video', 'hd'})
    assert best[ids['Bad']] == (-6, {'junk'})

    session.expire_all()
    found = {c.name: (c.score, c.tags) for c in session.query(Collection)}
    assert found == {'Good': (7, 'hd video'), 'Bad': (-6, 'junk')}

    # One article of Bad without a rank: it scores 0 then
    NNTPArticleRanks.forget(session, ['bad.1.4@example.com'])
    assert indexer.rank_collections(session, [ids['Bad']])[ids['Bad']] == (
        0, {'junk'})
    indexer.close()


def test_crossposted_articles_are_judged_the_same_everywhere(tmp_path):
    """
    A crossposted article is judged in every group its Xref lists (so a
    rule for one of them reaches it wherever it is scanned), its groups are
    kept, and it has one rank however many groups it was cached in
    """
    from newsreap.cli import cli

    group_list = tmp_path / 'group.list'
    group_list.write_text('%s 0000000002 0000000001 y\n'
                          '%s 0000000002 0000000001 y\n' % (GROUP, OTHER))

    with FakeNNTPServer(group_list=str(group_list)) as server:
        for group in (GROUP, OTHER):
            for no, (subject, poster, message_id) in enumerate((
                    ('Good [1/1] - "good.rar" yEnc (1/1)', POSTER, 'g@x'),
                    ('Cheap [1/1] - "buy.exe" yEnc (1/1)',
                     'bot@spam.example', 's@x')), 1):
                server.add_overview(
                    group, no, subject, poster,
                    POSTED + timedelta(minutes=no), message_id,
                    extras=['Xref: news %s:%d %s:%d' % (
                        GROUP, no, OTHER, no)])

        config = config_for(server, tmp_path / 'config.yaml')
        (tmp_path / 'filters.yaml').write_text(
            'filters:\n'
            '  - name: other-only\n    groups: [%s]\n'
            '    subject: {glob: "good*"}\n    score: 5\n    tag: other\n'
            '  - name: spam\n    groups: [%s]\n'
            '    poster: "@spam.example"\n' % (OTHER, OTHER))

        runner = CliRunner()

        def nr(*args):
            return runner.invoke(cli, ['--config', config, *args], obj={})

        assert nr('db', 'init').exit_code == 0
        assert nr('group', 'refresh').exit_code == 0

        # Scanned in the group the rules don't name, but posted to it too
        result = nr('update', GROUP)
        assert result.exit_code == 0, result.output
        assert '1 article(s) cached, 1 dropped by filters' in result.output

        db = NNTPGroupDatabase.from_file(
            str(tmp_path / 'cache' / 'search' / ('%s.db' % GROUP)))
        found = {a.message_id: a.xref for a in db.session().query(Article)}
        db.close()
        assert found == {'g@x': '%s %s' % (OTHER, GROUP)}

        result = nr('update', OTHER)
        assert result.exit_code == 0, result.output

        index = NNTPIndexDatabase.from_engine(
            str(tmp_path / 'index.db'))
        assert rows(index.session()) == {'g@x': (5, 'other')}
        index.close()

        # Searched the same way from either group
        for group in (GROUP, OTHER):
            result = nr('search', '--raw', '-g', group, '-t', 'other')
            assert 'good.rar' in result.output
            assert '{+5 other}' in result.output
