# Archive hook: plain text
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

# More than this share of control characters (other than tabs and line
# breaks) means it isn't text a person wrote
MAX_CONTROL_SHARE = 0.10

# The control characters text is allowed to have
TEXT_CONTROLS = frozenset('\t\n\r\f')


@hook(name='archive_convert', priority=1000)
def text(post, **kwargs):
    """
    Keeps what no other hook dealt with as text (decoded with the
    character set the article says it uses), unless it is mostly
    unreadable
    """
    if post.done:
        return

    charset = None
    if post.message is not None:
        charset = post.message.get_content_charset()

    body, charset = NNTPArchivePost.decode_text(post.body, charset)

    controls = sum(
        1 for c in body if ord(c) < 32 and c not in TEXT_CONTROLS)
    if body and controls > len(body) * MAX_CONTROL_SHARE:
        # Some kind of file we don't know about
        post.kind = ArchiveKind.Binary
        post.text = None

    else:
        post.kind = ArchiveKind.Text
        post.text = body.strip('\n')
        post.charset = charset

    post.done = True
