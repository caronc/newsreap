# Archive hook: MIME articles (attachments, base64 and quoted-printable)
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

from newsreap.NNTPArchivePost import NNTPArchivePost
from newsreap.decorators import hook
from newsreap.objects.index.ArchivedPost import ArchiveKind

# How the body of an article is stored when it isn't plain text
MIME_ENCODINGS = ('base64', 'quoted-printable')


@hook(name='archive_convert', priority=100)
def mime(post, **kwargs):
    """
    Reads MIME articles: the text parts are decoded and kept, attachments
    are only noted (name, type and size)
    """
    if post.done or post.message is None:
        return

    message = post.message
    encoding = post.header('Content-Transfer-Encoding').lower()
    if not message.is_multipart() and encoding not in MIME_ENCODINGS:
        # Plain text (or something another hook knows about)
        return

    texts = []
    for part in message.walk():
        if part.is_multipart():
            # Its parts come next
            continue

        content_type = part.get_content_type()
        filename = part.get_filename()

        try:
            # Undoes base64 / quoted-printable
            payload = part.get_payload(decode=True) or b''

        except (ValueError, TypeError):
            # Broken beyond repair; use it as it is
            payload = part.get_payload().encode('latin-1', 'replace')

        if content_type == 'text/plain' and not filename and \
                part.get_content_disposition() != 'attachment':
            # Text we can search
            text, charset = NNTPArchivePost.decode_text(
                payload, part.get_content_charset())
            texts.append(text)

            if not post.charset:
                post.charset = charset

            part_encoding = str(
                part.get('Content-Transfer-Encoding', '')).strip().lower()
            if part_encoding in MIME_ENCODINGS and \
                    part_encoding not in post.encoding:
                post.encoding.append(part_encoding)

            continue

        # An attachment (or a kind of text we don't read yet, ie: HTML)
        post.add_file(
            filename=filename or '', codec='mime',
            content_type=content_type, size=len(payload))

    if texts:
        post.text = '\n'.join(texts).strip('\n')
        post.kind = ArchiveKind.Text

    else:
        # Nothing but attachments
        post.kind = ArchiveKind.Binary

    post.done = True
