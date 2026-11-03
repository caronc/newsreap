# Archive hook: PGP blocks (signatures, keys and encrypted messages)
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

from mimetypes import guess_type
from os.path import splitext

from newsreap.NNTPArchivePost import NNTPArchivePost
from newsreap.NNTPArchivePost import TEXT_FILES
from newsreap.codecs.CodecPGP import CodecPGP
from newsreap.decorators import hook
from newsreap.objects.index.ArchivedPost import ArchiveKind
from newsreap.objects.index.PGPPost import PGPRole

# What every PGP block starts with
PGP_BEGIN = b'-----BEGIN PGP '


# How much of decrypted data is looked at to tell text from a file
TEXT_SAMPLE = 8192


@hook(name='archive_convert', priority=150)
def pgp(post, gpg=None, **kwargs):
    """
    Notes the PGP blocks in an article (signatures, keys and encrypted
    messages) without keeping them; the text of a signed message and any
    text around the blocks is kept.

    The keys it names (and carries) are noted even when another hook dealt
    with the article (ie: a signed MIME message); see NNTPKeyring.

    A message encrypted for a secret key we hold is decrypted (with gpg, an
    NNTPGnuPG) and what it says is kept instead.  One that can't be yet (ie:
    its passphrase isn't known) stays noted as encrypted, so it can be
    tried again later ('nr pgp decrypt').
    """
    if PGP_BEGIN not in post.body:
        return

    outside, contents = CodecPGP().read(post.body)
    if not contents:
        # Only looked like one
        return

    for content in contents:
        meta = content.pgp
        if meta.get('checksum') is False:
            # Damaged; what it says can't be trusted
            continue

        if meta.get('kind') == 'PUBLIC KEY BLOCK':
            # Someone sharing their key (the signatures inside it vouch for
            # the key, not for the post)
            post.pgp_keys.append(content.getvalue())
            continue

        for key_id in meta.get('signers', ()):
            post.add_pgp(key_id, PGPRole.Signed, meta.get('created'))

        for key_id in meta.get('recipients', ()):
            post.add_pgp(key_id, PGPRole.EncryptedFor)

    if post.done:
        # Another hook already worked out what it says
        return

    texts = [NNTPArchivePost.decode_text(outside)[0]]
    for content in contents:
        if content.content_type == 'text/plain':
            # The text of a signed message
            texts.append(NNTPArchivePost.decode_text(
                content.getvalue(), content.charset)[0])
            continue

        if gpg is not None and content.pgp.get('encrypted') and \
                content.pgp.get('checksum') is not False and \
                decrypt(post, gpg, content, texts):
            continue

        post.add_file(
            filename=content.pgp.get('filename', ''), codec='pgp',
            content_type=content.content_type, size=len(content))

    text = '\n'.join(t.strip('\n') for t in texts if t.strip())
    post.text = text or None
    post.kind = ArchiveKind.Text if text else ArchiveKind.Binary
    post.done = True


def decrypt(post, gpg, content, texts):
    """
    Decrypts an encrypted message (with gpg) if it is for one of our secret
    keys; returns True if it was.  Its text is added to `texts`; a file it
    holds is noted (and its text kept if it is a small text file, as a
    posted one would be).
    """
    meta = content.pgp
    if not meta.get('recipients'):
        # Opened with a passphrase, not a key
        return False

    result = gpg.decrypt(content.getvalue(), meta['recipients'])
    if not result:
        # Noted so it can be tried again (or given up on)
        post.locked = result.reason
        return False

    # Read with one of our keys
    post.encoding.append('pgp')

    for key_id, created in result.signers:
        post.add_pgp(key_id, PGPRole.Signed, created)

    data = result.data
    filename = result.filename if result.filename != '_CONSOLE' else ''
    if b'\0' not in data[:TEXT_SAMPLE] and (
            not filename or splitext(filename)[1].lower() in TEXT_FILES):
        # Text (a message, or a text file)
        text = NNTPArchivePost.decode_text(data, filename=filename)[0]
        if text.strip():
            texts.append(text)

    if filename or b'\0' in data[:TEXT_SAMPLE]:
        # A file (only noted, as any other)
        content_type = guess_type(filename)[0] if filename else None
        post.add_file(
            filename=filename, codec='pgp',
            content_type=content_type or 'application/octet-stream',
            size=len(data))

    return True
