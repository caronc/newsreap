# Archive hook: yEnc encoded files
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

from io import BytesIO
from os.path import splitext

from newsreap.NNTPArchivePost import NNTPArchivePost
from newsreap.NNTPArchivePost import TEXT_FILES
from newsreap.codecs.CodecYenc import CodecYenc
from newsreap.decorators import hook
from newsreap.objects.index.ArchivedPost import ArchiveKind


@hook(name='archive_convert', priority=200)
def yenc(post, **kwargs):
    """
    Notes the files yEnc encoded in an article (their name, size, part and
    checksum) without keeping them; any text written before the first one
    is kept
    """
    if post.done:
        return

    codec = CodecYenc()
    found = []
    before = []
    for line in post.lines():
        meta = codec.detect(line, relative=False)
        if meta is None:
            if not found:
                # Text that comes before the file
                before.append(line)
            continue

        if meta['key'] == 'begin':
            found.append({
                'filename': meta.get('name', ''),
                'size': meta.get('size'),
                'part': meta.get('part'),
                'total': meta.get('total'),
                'crc32': '',
            })

        elif meta['key'] == 'end' and found:
            # The crc32 of the whole file (pcrc32 is only this part's)
            found[-1]['crc32'] = meta.get('crc32', '')

    if not found:
        # Not yEnc
        return

    for entry in found:
        post.add_file(codec='yenc', **entry)

    text = NNTPArchivePost.decode_text(b'\n'.join(before))[0].strip()
    post.text = text or None

    first = found[0]
    if len(found) == 1 and (first['total'] or 1) == 1 and \
            (first['part'] or 1) == 1 and \
            splitext(first['filename'])[1].lower() in TEXT_FILES:
        # A whole text file (ie: an .nfo); its text is kept too
        content = CodecYenc().decode(BytesIO(post.body))
        if hasattr(content, 'getvalue'):
            post.add_text(first['filename'], content.getvalue())

    post.kind = ArchiveKind.Binary
    post.done = True
