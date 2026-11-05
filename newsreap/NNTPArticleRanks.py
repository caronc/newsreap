# Where the scores and tags of articles are kept (see ArticleRank)
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

"""
An article's score and tags (see NNTPFilter) are kept in one place: the
index database's article_rank table, by Message-ID.  Every copy of the
article (in each group's header cache, the index and the archive) shares
them, and they outlive the header caches (see raw_retention).

Only articles with a score or tags have an entry, so the table stays small
however many articles there are.
"""

from sqlalchemy import func

from .NNTPFilterRule import tags_from_text
from .NNTPFilterRule import tags_to_text
from .objects.index.ArticleRank import ArticleRank

# How many Message-IDs are looked up (or saved) in one statement
RANK_CHUNK_SIZE = 500


def rank_key(message_id):
    """
    Returns a Message-ID the way ranks are kept (without its < and >, and
    no longer than its column), the same way every time it is saved or
    looked up
    """
    return (message_id or '').strip().strip('<>')[:250]


class NNTPArticleRanks:
    """
    Reads and writes the scores and tags of articles; see the module
    description
    """

    @staticmethod
    def load(session, message_ids):
        """
        Returns Message-ID -> (score, tags) of the articles given that have
        a score or tags (the rest score 0 and have no tags); the keys are
        the Message-IDs as given
        """
        wanted = {}
        for message_id in message_ids:
            wanted.setdefault(rank_key(message_id), []).append(message_id)

        found = {}
        keys = list(wanted)
        for start in range(0, len(keys), RANK_CHUNK_SIZE):
            for key, score, tags in session.query(
                    ArticleRank.message_id, ArticleRank.score,
                    ArticleRank.tags)\
                    .filter(ArticleRank.message_id.in_(
                        keys[start:start + RANK_CHUNK_SIZE])):
                for message_id in wanted[key]:
                    found[message_id] = (score, tags_from_text(tags))

        return found

    @staticmethod
    def save(session, ranks):
        """
        Saves the scores and tags given (Message-ID -> (score, tags)),
        replacing what the articles had; not committed

        An article with a score of 0 and no tags loses its entry.
        """
        rows = {}
        for message_id, (score, tags) in ranks.items():
            rows[rank_key(message_id)] = (score or 0, tags_to_text(tags))

        keys = list(rows)
        for start in range(0, len(keys), RANK_CHUNK_SIZE):
            chunk = keys[start:start + RANK_CHUNK_SIZE]

            # What they had is replaced
            session.query(ArticleRank)\
                .filter(ArticleRank.message_id.in_(chunk))\
                .delete(synchronize_session=False)

            kept = [{'message_id': key, 'score': rows[key][0],
                     'tags': rows[key][1]}
                    for key in chunk if rows[key][0] or rows[key][1]]
            if kept:
                session.execute(ArticleRank.__table__.insert(), kept)

    @staticmethod
    def forget(session, message_ids):
        """
        Removes the scores and tags of the articles given (ie: they were
        purged); not committed
        """
        keys = list({rank_key(m) for m in message_ids})
        for start in range(0, len(keys), RANK_CHUNK_SIZE):
            session.query(ArticleRank)\
                .filter(ArticleRank.message_id.in_(
                    keys[start:start + RANK_CHUNK_SIZE]))\
                .delete(synchronize_session=False)

    @staticmethod
    def known(session, message_ids, bodies=False, filenames=False):
        """
        Returns Message-ID -> {'body': ..., 'filenames': [...]} of what the
        index database knows about the articles given beyond their header:
        - bodies: their text (when archived)
        - filenames: the names of their files (from the archive, and the
          files of the collections they are in)

        Only what is asked for is read; articles nothing is known about are
        left out.
        """
        from .objects.index.ArchiveFile import ArchiveFile
        from .objects.index.ArchivedPost import ArchivedPost
        from .objects.index.CollectionFile import CollectionFile
        from .objects.index.Segment import Segment

        wanted = {}
        for message_id in message_ids:
            wanted.setdefault(rank_key(message_id), []).append(message_id)

        found = {}

        def note(key, name, value):
            for message_id in wanted.get(key, ()):
                entry = found.setdefault(message_id, {})
                if name == 'filenames':
                    entry.setdefault(name, []).append(value)
                else:
                    entry[name] = value

        keys = list(wanted)
        for start in range(0, len(keys), RANK_CHUNK_SIZE):
            chunk = keys[start:start + RANK_CHUNK_SIZE]
            if bodies:
                for key, text in session.query(
                        ArchivedPost.message_id, ArchivedPost.text)\
                        .filter(ArchivedPost.message_id.in_(chunk))\
                        .filter(ArchivedPost.text.isnot(None)):
                    note(key, 'body', text)

            if filenames:
                for key, name in session.query(
                        ArchiveFile.message_id, ArchiveFile.filename)\
                        .filter(ArchiveFile.message_id.in_(chunk))\
                        .filter(ArchiveFile.filename != ''):
                    note(key, 'filenames', name)

                for key, name in session.query(
                        Segment.message_id, CollectionFile.filename)\
                        .join(CollectionFile,
                              CollectionFile.id == Segment.file_id)\
                        .filter(Segment.message_id.in_(chunk))\
                        .filter(CollectionFile.filename != ''):
                    note(key, 'filenames', name)

        return found

    @staticmethod
    def matches(score, tags, tags_wanted=None, without_tags=None,
                min_score=None, max_score=None, scores=None, **kwargs):
        """
        Returns True if a score and tags pass what a search asked for (the
        same as rank_query() checks in SQL; see NNTPIndexer); anything else
        given (ie: posting times) is left to the query
        """
        from .NNTPFilterRule import SCORE_TESTS
        from .NNTPFilterRule import score_tests
        from .NNTPFilterRule import tag_logic
        from .NNTPFilterRule import tags_match

        logic = tag_logic(tags_wanted)
        if logic and not tags_match(logic, tags):
            return False

        logic = tag_logic(without_tags)
        if logic and tags_match(logic, tags):
            return False

        if min_score is not None and score < int(min_score):
            return False

        if max_score is not None and score > int(max_score):
            return False

        for value in scores or ():
            for op, number in score_tests(value, '--score'):
                if not SCORE_TESTS[op](score, number):
                    return False

        return True

    @staticmethod
    def joined(query, message_id_column):
        """
        Returns (query, score, tags): the query joined with the ranks of
        the articles in `message_id_column`, and the columns holding their
        score (0 when they have none) and tags ('' when they have none)
        """
        query = query.outerjoin(
            ArticleRank, ArticleRank.message_id == message_id_column)

        return query, func.coalesce(ArticleRank.score, 0), \
            func.coalesce(ArticleRank.tags, '')
