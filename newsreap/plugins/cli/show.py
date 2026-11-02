# NewsReap Show/Fetch CLI Plugin
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

# Looks at articles by their Message-ID:
#   nr show <message-id> [<message-id> ...]    everything we know of them
#   nr fetch <message-id> [<message-id> ...]   save what they hold

import logging
import click
import sys

from os import listdir
from os.path import abspath
from os.path import dirname
from os.path import exists
from os.path import isdir
from os.path import join

try:
    from newsreap.Logging import NEWSREAP_CLI
except ImportError:
    # Path
    sys.path.insert(0, dirname(dirname(dirname(dirname(abspath(__file__))))))
    from newsreap.Logging import NEWSREAP_CLI

from newsreap.NNTPArchivePost import NNTPArchivePost
from newsreap.NNTPArchiver import NNTPArchiver
from newsreap.NNTPArticleRanks import NNTPArticleRanks
from newsreap.NNTPArticleRanks import rank_key
from newsreap.NNTPConnectionRequest import NNTPConnectionRequest
from newsreap.NNTPGroupDatabase import NNTPGroupDatabase
from newsreap.NNTPIndexDatabase import NNTPIndexDatabase
from newsreap.NNTPIndexer import NNTPIndexer
from newsreap.NNTPSettings import SQLITE_DATABASE_EXTENSION
from newsreap.Utils import bytes_to_strsize
from newsreap.Utils import mkdir
from newsreap.Utils import strsize_to_bytes
from newsreap.objects.group.Article import Article
from newsreap.objects.index.ArchivedPost import ArchiveKind
from newsreap.objects.index.Collection import Collection
from newsreap.objects.index.CollectionFile import CollectionFile
from newsreap.objects.index.Segment import Segment

# initialize our logger
logger = logging.getLogger(NEWSREAP_CLI)

NEWSREAP_CLI_PLUGINS = {
    # format:
    # cli short hand group: function prefix
    'show': 'show',
    'fetch': 'fetch',
}

# How each kind of post is described
KIND_NAMES = {
    ArchiveKind.Text: 'text',
    ArchiveKind.Binary: 'binary',
    ArchiveKind.Missing: 'missing (no server had it)',
    ArchiveKind.Refused: 'refused by a hook',
}

# How many lines of a big article's text are shown
DEFAULT_SHOW_LINES = 10


def ask(manager, message_id, work_dir, **kwargs):
    """
    Fetches an article (the options given go to NNTPConnection.get());
    returns (article, status): the article (None if it wasn't fetched) and
    how it went ('found', 'missing' or 'failed')
    """
    request = NNTPConnectionRequest(actions=[
        ('get', (message_id, work_dir), kwargs),
        ('fetch_status', (), {}),
    ])
    manager.put(request)
    request.wait()

    results = list(request.response) + [None, None]
    return results[0], results[1] or 'failed'


def cached_headers(settings, message_id):
    """
    Returns (group, header) for every group whose cache has the article
    """
    cache_dir = dirname(settings.group_database('_'))
    if not isdir(cache_dir):
        return []

    found = []
    for filename in sorted(listdir(cache_dir)):
        if not filename.endswith(SQLITE_DATABASE_EXTENSION):
            continue

        db = NNTPGroupDatabase.from_file(join(cache_dir, filename))
        try:
            header = db.session().query(Article).filter(
                Article.message_id.in_(
                    [message_id, '<%s>' % message_id])).first()
            if header is not None:
                db.session().expunge(header)
                found.append((
                    filename[:-len(SQLITE_DATABASE_EXTENSION)], header))

        finally:
            db.close()

    return found


def show_files(files):
    """
    Shows what an article's files say about themselves
    """
    for entry in files:
        details = [entry['codec']]
        if entry.get('content_type'):
            details.append(entry['content_type'])
        if entry.get('size'):
            details.append(bytes_to_strsize(entry['size']))
        if entry.get('total'):
            details.append('part %s/%s' % (entry['part'], entry['total']))
        elif entry.get('part'):
            details.append('part %s' % entry['part'])
        if entry.get('crc32'):
            details.append('crc32 %s' % entry['crc32'])

        click.echo('Holds:      %s (%s)' % (
            entry['filename'] or '(no name)', ', '.join(details)))


def show_text(text, lines=None):
    """
    Shows an article's text (only its first lines if `lines` is given)
    """
    if not text:
        return

    found = text.splitlines()
    click.echo('')
    click.echo('\n'.join(found[:lines] if lines else found))
    if lines and len(found) > lines:
        click.echo('... (%d more line(s))' % (len(found) - lines))


def show_one(ctx, archiver, index_session, message_id, local, max_size,
             lines):
    """
    Shows everything we know of an article (fetching what we don't have,
    unless local); returns False if nothing is known of it
    """
    settings = ctx['NNTPSettings']
    headers = cached_headers(settings, message_id)
    post = archiver.get(message_id)

    # The size its header gave (if we have it)
    size = next((h.size for _, h in headers if h.size), None)

    if post is None and not local:
        # Fetched whole: stopping part way through would mean dropping the
        # connection (and logging in again for the next one)
        article, status = ask(
            ctx['NNTPManager'], message_id, settings.work_dir,
            decoders=False)

        if article is not None:
            fetched = NNTPArchivePost(
                message_id, raw=article.body.getvalue(),
                subject=headers[0][1].subject if headers else '',
                poster=headers[0][1].poster if headers else '',
                posted=headers[0][1].posted_date if headers else None,
                size=size or len(article.body.getvalue()),
                groups={g for g, _ in headers})
            archiver.convert(fetched)

            if fetched.kind != ArchiveKind.Missing:
                # Kept (a file it holds is only noted), so it is never
                # fetched again
                session = archiver.session(create=True)
                archiver.store(session, fetched)
                session.commit()
                post = archiver.get(message_id)

        elif status == 'failed':
            logger.error('%s could not be fetched; try again later.' % (
                message_id))

    # Only the start of a big one's text is shown
    big = (size or (post.size if post else 0) or 0) > max_size

    if not (headers or post):
        logger.error('Nothing is known of <%s>%s.' % (
            message_id, ' (here)' if local else ''))
        return False

    # Its header: what we cached, else what was kept or fetched
    header = headers[0][1] if headers else None
    source = post
    click.echo('Message-ID: <%s>' % message_id)
    click.echo('Subject:    %s' % (
        source.subject if source else header.subject))
    click.echo('From:       %s' % (
        source.poster if source else header.poster))

    posted = header.posted_date if header else source.posted
    if posted:
        click.echo('Date:       %s UTC' % posted.strftime('%Y-%m-%d %H:%M:%S'))

    if header and header.size:
        click.echo('Size:       %s (%d line(s))' % (
            bytes_to_strsize(header.size), header.lines or 0))

    for group, entry in headers:
        click.echo('Group:      %s (article %d)' % (group, entry.article_no))

    if header and header.xref:
        click.echo('Xref:       %s' % header.xref)

    if post is not None:
        groups = [g for g in archiver.groups(message_id)
                  if g not in {name for name, _ in headers}]
        for group in groups:
            click.echo('Group:      %s (kept)' % group)

    if index_session is not None:
        ranks = NNTPArticleRanks.load(index_session, [message_id])
        score, tags = ranks.get(message_id, (0, frozenset()))
        if score or tags:
            click.echo('Score:      %+d%s' % (score, ' (%s)' % ' '.join(
                sorted(tags)) if tags else ''))

        for part, filename, subject, collection in index_session.query(
                Segment.part, CollectionFile.filename,
                CollectionFile.subject, Collection)\
                .join(CollectionFile, CollectionFile.id == Segment.file_id)\
                .join(Collection,
                      Collection.id == CollectionFile.collection_id)\
                .filter(Segment.message_id == message_id):
            click.echo('Indexed:    %s' % NNTPIndexer.summary(collection))
            click.echo('            part %d of %s' % (
                part, filename or subject))

    if post is not None:
        click.echo('Kind:       %s' % KIND_NAMES.get(post.kind, '?'))
        if post.references:
            click.echo('References: %s' % post.references)
        if post.encoding:
            click.echo('Encoding:   %s' % post.encoding)

        show_files([{
            'filename': f.filename, 'codec': f.codec,
            'content_type': f.content_type, 'size': f.size, 'part': f.part,
            'total': f.total, 'crc32': f.crc32}
            for f in archiver.files(message_id)])
        show_text(post.text, lines if big else None)

    return True


@click.command(name='show')
@click.argument('message_ids', nargs=-1)
@click.option('--local', '-L', is_flag=True,
              help="Only show what is kept here (fetch nothing).")
@click.option('--lines', '-l', type=click.IntRange(min=1),
              default=DEFAULT_SHOW_LINES, show_default=True,
              help='How much of a big article\'s text is shown.')
@click.option('--max-size', '-m', default=None, type=str,
              help='Articles bigger than this only have their first --lines '
                   'shown (defaults to the archive_max_size setting).')
@click.pass_obj
def show(ctx, message_ids, local, lines, max_size):
    """
    Show everything known of articles (by their Message-ID).

    What is kept here is shown: their headers (from each group they were
    cached in), score and tags, the collection and file they are part of,
    and what they say (only the first --lines of a big one).  An article
    whose content isn't kept is fetched (whole; it is never broken off part
    way, which would drop the connection) and kept, so it isn't fetched
    again; a file it holds is only noted.  Give as many Message-IDs as you
    like (with or without their < and >).

    """
    if not message_ids:
        click.echo(
            "Give the Message-IDs of the articles to show; 'nr search "
            "--posts' and 'nr search --raw' list them.")
        exit(1)

    settings = ctx['NNTPSettings']
    limit = strsize_to_bytes(
        max_size or settings.nntp_processing.get('archive_max_size') or
        '64KB')
    if not limit:
        logger.error("'%s' isn't a size (ie: 64KB)." % max_size)
        exit(1)

    index = None
    index_session = None
    if NNTPIndexDatabase.exists(settings.index_database()):
        index = NNTPIndexDatabase.from_engine(settings.index_database())
        index_session = index.session()

    archiver = NNTPArchiver(settings.index_database())

    # Track how many we found
    found = 0
    wanted = list(dict.fromkeys(rank_key(m) for m in message_ids))
    try:
        for message_id in wanted:
            if found:
                # Keep them apart
                click.echo('')

            if show_one(ctx, archiver, index_session, message_id, local,
                        limit, lines):
                found += 1

    finally:
        archiver.close()
        if index is not None:
            index.close()

    if found < len(wanted):
        exit(1)


def saved_name(output, name, force):
    """
    Returns where a file is saved in the output directory: its name as it
    is, or with a number added if one by that name is already there
    (replaced with force)
    """
    # Keep it in the output directory (whatever the name says)
    name = name.replace('\\', '/').split('/')[-1].lstrip('.') or 'article'
    path = join(output, name)
    if force or not exists(path):
        return path

    no = 1
    while exists('%s.%d' % (path, no)):
        no += 1

    return '%s.%d' % (path, no)


@click.command(name='fetch')
@click.argument('message_ids', nargs=-1)
@click.option('--output', '-o', default='.', show_default=True,
              help='The directory the files are saved in.')
@click.option('--raw', is_flag=True,
              help='Save each article exactly as the server sent it '
                   '(<message-id>.msg) instead of what it holds.')
@click.option('--force', is_flag=True,
              help='Replace files that are already there (instead of '
                   'saving under a new name).')
@click.pass_obj
def fetch(ctx, message_ids, output, raw, force):
    """
    Download articles (by their Message-ID) and save what they hold.

    The files an article holds (yEnc, uuencode, MIME attachments) are
    decoded and saved under their own names; one that is only part of a
    file is saved as that part (ie: name.rar.023).  An article holding no
    file is saved as it is (<message-id>.msg), as is every one with --raw.
    The name of each file saved is shown.  Give as many Message-IDs as you
    like (with or without their < and >).

    """
    if not message_ids:
        click.echo(
            "Give the Message-IDs of the articles to fetch; 'nr show' "
            "tells you about them first.")
        exit(1)

    output = abspath(output)
    if not mkdir(output):
        logger.error("Could not create '%s'." % output)
        exit(1)

    work_dir = ctx['NNTPSettings'].work_dir

    # Track how many we saved
    saved = 0
    wanted = list(dict.fromkeys(rank_key(m) for m in message_ids))
    for message_id in wanted:
        article, status = ask(
            ctx['NNTPManager'], message_id, work_dir,
            decoders=False if raw else None)

        if article is None:
            logger.error(
                'No server has <%s>.' % message_id if status == 'missing'
                else '<%s> could not be fetched; try again later.' % (
                    message_id))
            continue

        contents = [] if raw else [
            c for c in article.decoded if getattr(c, 'filename', None)]

        if not contents and not raw:
            # It holds no file; it is kept as the server sent it
            article, status = ask(
                ctx['NNTPManager'], message_id, work_dir, decoders=False)
            if article is None:
                logger.error(
                    '<%s> could not be fetched; try again later.' % (
                        message_id))
                continue

        if not contents:
            path = saved_name(output, '%s.msg' % message_id, force)
            with open(path, 'wb') as fp:
                fp.write(article.body.getvalue())

            click.echo(path)
            saved += 1
            continue

        for content in contents:
            name = content.filename
            if (getattr(content, 'total_parts', 0) or 0) > 1:
                # Only a part of it
                name = '%s.%03d' % (name, content.part)

            path = saved_name(output, name, force)
            if not content.save(path, copy=True):
                logger.error("Could not save '%s'." % path)
                continue

            click.echo(path)
            saved += 1

    if saved == 0 or saved < len(wanted):
        exit(1)
