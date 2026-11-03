# Archive hook: uuencoded files
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
from io import BytesIO
from os.path import splitext

from newsreap.NNTPArchivePost import NNTPArchivePost
from newsreap.NNTPArchivePost import TEXT_FILES
from newsreap.codecs.CodecUU import CodecUU
from newsreap.decorators import hook
from newsreap.objects.index.ArchivedPost import ArchiveKind

# A line of uuencoded data: its length character followed by the data
# ('M' is a full line of 45 bytes, written as 60 characters)
UU_LINE_RE = re.compile(br'^[!-`][!-`]{0,60}$')


@hook(name='archive_convert', priority=300)
def uuencode(post, **kwargs):
    """
    Notes the files uuencoded in an article (their name) without keeping
    them; any text written before the first one is kept
    """
    if post.done:
        return

    codec = CodecUU()
    lines = post.body.splitlines()
    found = []
    before = []
    for no, line in enumerate(lines):
        meta = codec.detect(line, relative=False)
        if meta is not None and meta.get('key') == 'begin' and \
                meta.get('perm') is not None and \
                no + 1 < len(lines) and UU_LINE_RE.match(lines[no + 1]):
            # 'begin 644 name' followed by data (a sentence starting with
            # 'begin' isn't)
            found.append(meta.get('name', ''))

        elif not found:
            # Text that comes before the file
            before.append(line)

    if not found:
        # Not uuencoded
        return

    for filename in found:
        post.add_file(filename=filename, codec='uuencode')

    text = NNTPArchivePost.decode_text(b'\n'.join(before))[0].strip()
    post.text = text or None

    if len(found) == 1 and splitext(found[0])[1].lower() in TEXT_FILES:
        # A text file (ie: an .nfo); its text is kept too
        content = CodecUU().decode(BytesIO(post.body))
        if hasattr(content, 'getvalue'):
            post.add_text(found[0], content.getvalue())
    post.kind = ArchiveKind.Binary
    post.done = True
