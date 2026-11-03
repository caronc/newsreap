# The PGP keys found in posts (and which posts each one is tied to)
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
Keeps the PGP keys found in archived posts (or imported by hand), and
which posts each key signed, was encrypted for, or was carried by.  It is
only used when the `pgp` setting is on (see config.yaml and 'nr pgp').

- Only public details are kept; secret keys and passphrases never are.
- No key is needed: what a post says about its keys is read straight from
  it (see CodecPGP.packets()).
- A key can be named by its key ID, its fingerprint, or part of a name or
  email address it gives.
"""

import re

from sqlalchemy import func
from sqlalchemy import or_
from sqlalchemy import select
from sqlalchemy.orm import aliased

from .Utils import utcnow
from .codecs.CodecPGP import CodecPGP
from .objects.index.ArchiveFile import ArchiveFile
from .objects.index.ArchiveGroup import ArchiveGroup
from .objects.index.ArchivedPost import ArchivedPost
from .objects.index.PGPKey import PGPKey
from .objects.index.PGPKey import PGPKeySource
from .objects.index.PGPPost import HIDDEN_KEY_ID
from .objects.index.PGPPost import PGPPost
from .objects.index.PGPPost import PGPRole
from .objects.index.PGPWait import PGPWait
from .objects.index.PGPWait import WAIT_FINAL
from .objects.index.PGPWait import WAIT_SERVER

# A key ID or fingerprint (in hex; spaces are taken out first, and a
# leading 0x is allowed)
HEX_RE = re.compile(r'^(0x)?(?P<hex>[0-9a-f]{8,64})$', re.IGNORECASE)

# The kind of block that holds public keys
PUBLIC_KEY_BLOCK = 'PUBLIC KEY BLOCK'

# How many Message-IDs are looked up (or removed) in one statement
KEYRING_CHUNK_SIZE = 500

# How a message still encrypted is noted (see ArchiveFile)
ENCRYPTED_TYPE = 'application/pgp-encrypted'

# The most tries a waiting post keeps count of
MAX_TRIES = 9999


class NNTPKeyring:
    """
    Reads and writes the PGP keys kept; see the module description
    """

    @staticmethod
    def read_keys(data):
        """
        Returns the public keys in the data given: ASCII armored blocks
        (with any text around them) or a binary key file.  Each is a dict:
        - fingerprint, key_id, version, algorithm, created
        - user_ids: the names it gives (ie: 'Name <email>')
        - subkeys: its subkeys (fingerprint, key_id, version, algorithm,
          created)
        - armored: the key on its own, ASCII armored

        Secret keys, and blocks that fail their checksum, are left out.
        """
        if isinstance(data, str):
            data = data.encode('utf-8', 'replace')

        blocks = []
        if data[:1] and data[0] & 0x80:
            # A binary key file (it starts with a packet)
            blocks.append(data)

        else:
            _, contents = CodecPGP().read(data)
            for content in contents:
                if content.pgp.get('kind') == PUBLIC_KEY_BLOCK and \
                        content.pgp.get('checksum') is not False:
                    blocks.append(content.getvalue())

        keys = []
        for block in blocks:
            for key in CodecPGP.split_keys(block):
                found = CodecPGP.packets(key)
                parts = [k for k in found['keys'] if k['fingerprint']]
                if not parts or parts[0]['subkey']:
                    # Damaged (or not a key after all)
                    continue

                keys.append({
                    'fingerprint': parts[0]['fingerprint'],
                    'key_id': parts[0]['key_id'],
                    'version': parts[0]['version'],
                    'algorithm': parts[0]['algorithm'],
                    'created': parts[0]['created'],
                    'user_ids': list(dict.fromkeys(
                        u.strip() for u in found['user_ids'] if u.strip())),
                    'subkeys': [{
                        'fingerprint': k['fingerprint'],
                        'key_id': k['key_id'],
                        'version': k['version'],
                        'algorithm': k['algorithm'],
                        'created': k['created'],
                    } for k in parts[1:] if k['subkey']],
                    'armored': CodecPGP.armor(key),
                })

        return keys

    @staticmethod
    def key_text(text):
        """
        Returns what a key was named by: ('hex', key ID or fingerprint in
        upper case) or ('name', the text given)
        """
        text = (text or '').strip()
        found = HEX_RE.match(re.sub(r'\s+', '', text))
        if found:
            return 'hex', found.group('hex').upper()

        return 'name', text

    @staticmethod
    def pretty(fingerprint):
        """
        Returns a fingerprint in groups of 4 (the way PGP tools show them)
        """
        return ' '.join(
            fingerprint[i:i + 4] for i in range(0, len(fingerprint), 4))

    @staticmethod
    def _save(session, key, parent='', message_id=None,
              source=PGPKeySource.Post):
        """
        Keeps one key (or subkey); returns True if it is new
        """
        entry = session.query(PGPKey)\
            .filter(PGPKey.fingerprint == key['fingerprint']).first()

        new = entry is None
        if new:
            entry = PGPKey(
                fingerprint=key['fingerprint'], source=source,
                first_seen=utcnow(),
                message_id=message_id[:250] if message_id else None)
            session.add(entry)

        entry.key_id = key['key_id']
        entry.parent = parent
        entry.version = key['version']
        entry.algorithm = str(key['algorithm'])[:16]
        entry.created = key['created']

        if not parent:
            # Names are added to the ones we had (never taken away)
            names = (entry.user_ids or '').splitlines()
            names.extend(key['user_ids'])
            entry.user_ids = '\n'.join(dict.fromkeys(names)) or None

            # The fullest copy of the key is kept (a newer one carries more
            # signatures and subkeys)
            if not entry.armored or \
                    len(key['armored']) >= len(entry.armored):
                entry.armored = key['armored']

        return new

    @staticmethod
    def save_keys(session, keys, message_id=None, source=PGPKeySource.Post):
        """
        Keeps the keys given (see read_keys()) and their subkeys; a key we
        already have gets any new names.  Returns how many keys were new
        (not committed).

        - message_id: the post they were found in
        - source: where they came from (see PGPKeySource)
        """
        new = 0
        for key in keys:
            if NNTPKeyring._save(session, key, message_id=message_id,
                                 source=source):
                new += 1

            for subkey in key['subkeys']:
                NNTPKeyring._save(
                    session, subkey, parent=key['fingerprint'],
                    message_id=message_id, source=source)

            session.flush()

        return new

    @staticmethod
    def store_post(session, post):
        """
        Keeps what an archived post (NNTPArchivePost) says about PGP keys,
        replacing what it had: the keys it carries and the ones it names.
        Returns how many keys were new (not committed).
        """
        NNTPKeyring.forget_posts(session, [post.message_id])

        # Key ID and role -> when (a post can name a key more than once)
        links = {}
        for key_id, role, created in post.pgp:
            links.setdefault((key_id.upper()[:16], role), created)

        new = 0
        for data in post.pgp_keys:
            keys = NNTPKeyring.read_keys(data)
            new += NNTPKeyring.save_keys(
                session, keys, message_id=post.message_id)

            for key in keys:
                links.setdefault((key['key_id'], PGPRole.Carries), None)

        session.add_all(
            PGPPost(message_id=post.message_id, key_id=key_id, role=role,
                    created=created)
            for (key_id, role), created in links.items())
        return new

    @staticmethod
    def forget_posts(session, message_ids):
        """
        Lets go of which keys the posts given named (ie: they were purged);
        the keys themselves are kept.  Not committed.
        """
        ids = list(message_ids)
        for start in range(0, len(ids), KEYRING_CHUNK_SIZE):
            session.query(PGPPost)\
                .filter(PGPPost.message_id.in_(
                    ids[start:start + KEYRING_CHUNK_SIZE]))\
                .delete(synchronize_session=False)

    @staticmethod
    def unread(session, message_ids):
        """
        Returns the posts given that hold PGP blocks but name no keys yet
        (ie: they were kept before PGP keys were); they are fetched again
        to read them
        """
        found = set()
        ids = list(message_ids)
        for start in range(0, len(ids), KEYRING_CHUNK_SIZE):
            chunk = ids[start:start + KEYRING_CHUNK_SIZE]
            found.update(m for m, in session.query(ArchiveFile.message_id)
                         .filter(ArchiveFile.message_id.in_(chunk))
                         .filter(ArchiveFile.codec == 'pgp')
                         .filter(ArchiveFile.message_id.not_in(
                             select(PGPPost.message_id)
                             .where(PGPPost.message_id.in_(chunk)))))
        return found

    @staticmethod
    def locked(session, key_ids, message_ids=None, retries=None,
               final=True, group=None):
        """
        Returns the posts still encrypted (not decrypted yet) that the key
        IDs given (ours; see NNTPGnuPG.secret_keys()) can open: the ones
        encrypted for them, and the ones whose recipient is hidden.

        - message_ids: only look at these posts (every post when None)
        - retries: only the ones tried again fewer times than this
        - final: include the ones given up on (an answer trying again
          won't change, ie: the server no longer has it; see WAIT_FINAL)
        - group: only the ones found in this group
        """
        key_ids = {k.upper() for k in key_ids}
        if not key_ids:
            return set()

        key_ids.add(HIDDEN_KEY_ID)
        query = session.query(PGPPost.message_id).distinct()\
            .join(ArchiveFile, ArchiveFile.message_id == PGPPost.message_id)\
            .filter(PGPPost.role == PGPRole.EncryptedFor)\
            .filter(PGPPost.key_id.in_(key_ids))\
            .filter(ArchiveFile.codec == 'pgp')\
            .filter(ArchiveFile.content_type == ENCRYPTED_TYPE)

        if retries is not None or not final:
            # How each was tried before (a post without a note wasn't)
            query = query.outerjoin(
                PGPWait, PGPWait.message_id == PGPPost.message_id)

            if retries is not None:
                query = query.filter(or_(
                    PGPWait.tries.is_(None), PGPWait.tries < retries))

            if not final:
                query = query.filter(or_(
                    PGPWait.reason.is_(None),
                    PGPWait.reason.notin_(list(WAIT_FINAL))))

        if group is not None:
            query = query.join(
                ArchiveGroup, ArchiveGroup.message_id == PGPPost.message_id)\
                .filter(ArchiveGroup.name == group[:128])

        if message_ids is None:
            return {m for m, in query}

        found = set()
        ids = list(message_ids)
        for start in range(0, len(ids), KEYRING_CHUNK_SIZE):
            found.update(m for m, in query.filter(PGPPost.message_id.in_(
                ids[start:start + KEYRING_CHUNK_SIZE])))
        return found

    @staticmethod
    def note_waiting(session, message_id, reason, create=True):
        """
        Notes why a post is still waiting to be decrypted (see WaitReason).
        A post noted before is counted as tried again; one that wasn't is
        only noted when `create` is set.  Returns its PGPWait (None if it
        wasn't noted).  Not committed.
        """
        entry = session.get(PGPWait, message_id)
        if entry is None:
            if not create:
                return None

            entry = PGPWait(message_id=message_id, tries=0)
            session.add(entry)

        else:
            entry.tries = min((entry.tries or 0) + 1, MAX_TRIES)

        entry.reason = (reason or '')[:16]
        entry.tried = utcnow()
        return entry

    @staticmethod
    def done_waiting(session, message_ids):
        """
        Lets go of why the posts given were waiting (ie: they were
        decrypted, or purged).  Not committed.
        """
        ids = list(message_ids)
        for start in range(0, len(ids), KEYRING_CHUNK_SIZE):
            session.query(PGPWait)\
                .filter(PGPWait.message_id.in_(
                    ids[start:start + KEYRING_CHUNK_SIZE]))\
                .delete(synchronize_session=False)

    @staticmethod
    def renew(session, message_ids):
        """
        Gives the waiting posts given their tries back (ie: a secret key or
        passphrase that may open them was just added), so 'nr update' tries
        them again.  The ones the server answered for (gone, or not ours to
        have) are left as they are.  Returns how many were.  Not committed.
        """
        renewed = 0
        ids = list(message_ids)
        for start in range(0, len(ids), KEYRING_CHUNK_SIZE):
            renewed += session.query(PGPWait)\
                .filter(PGPWait.message_id.in_(
                    ids[start:start + KEYRING_CHUNK_SIZE]))\
                .filter(PGPWait.reason.notin_(list(WAIT_SERVER)))\
                .update({PGPWait.tries: 0, PGPWait.reason: ''},
                        synchronize_session=False)
        return renewed

    @staticmethod
    def waits(session, message_ids):
        """
        Returns how the waiting posts given were tried: Message-ID ->
        PGPWait (posts that aren't waiting are left out)
        """
        found = {}
        ids = list(message_ids)
        for start in range(0, len(ids), KEYRING_CHUNK_SIZE):
            found.update((entry.message_id, entry) for entry in session.query(
                PGPWait).filter(PGPWait.message_id.in_(
                    ids[start:start + KEYRING_CHUNK_SIZE])))
        return found

    @staticmethod
    def find(session, text=None):
        """
        Returns the keys (not subkeys) named by `text`, oldest first: a key
        ID (the short 8 character form too), a fingerprint, or part of a
        name or email address (case doesn't matter).  A key whose subkey is
        named is returned too.  Every key is returned without `text`.
        """
        query = session.query(PGPKey).filter(PGPKey.parent == '')
        if text:
            kind, value = NNTPKeyring.key_text(text)
            if kind == 'hex':
                # The key itself, or the key one of its subkeys belongs to
                subkey = aliased(PGPKey)
                query = query.filter(or_(
                    NNTPKeyring._match(PGPKey, value),
                    PGPKey.fingerprint.in_(
                        select(subkey.parent)
                        .where(subkey.parent != '')
                        .where(NNTPKeyring._match(subkey, value)))))

            else:
                query = query.filter(func.lower(PGPKey.user_ids).contains(
                    value.lower(), autoescape=True))

        return query.order_by(PGPKey.first_seen, PGPKey.id).all()

    @staticmethod
    def _match(table, value):
        """
        Returns the condition for a key (in the table given) having the key
        ID or fingerprint given (in upper case hex)
        """
        if len(value) == 16:
            return table.key_id == value

        if len(value) == 8:
            # The short form of a key ID is its end
            return table.key_id.endswith(value)

        return table.fingerprint == value

    @staticmethod
    def subkeys(session, key):
        """
        Returns the subkeys of a key (a PGPKey)
        """
        return session.query(PGPKey)\
            .filter(PGPKey.parent == key.fingerprint)\
            .order_by(PGPKey.created, PGPKey.id).all()

    @staticmethod
    def resolve(session, text):
        """
        Returns the key IDs `text` stands for (see find()): the keys it
        names and all of their subkeys.  A full key ID or fingerprint we
        don't have still stands for itself (posts can name keys never
        seen).
        """
        keys = NNTPKeyring.find(session, text)
        ids = {key.key_id for key in keys}
        if keys:
            ids.update(key_id for key_id, in session.query(PGPKey.key_id)
                       .filter(PGPKey.parent.in_(
                           [key.fingerprint for key in keys])))

        kind, value = NNTPKeyring.key_text(text)
        if kind == 'hex':
            if len(value) == 16:
                ids.add(value)

            elif len(value) == 40:
                # A version 4 key's ID is the end of its fingerprint
                ids.add(value[-16:])

            elif len(value) == 64:
                # A newer key's ID is the start of it
                ids.add(value[:16])

        return ids

    @staticmethod
    def posts(session, key_ids, limit=None):
        """
        Returns (post, role) for the archived posts naming any of the key
        IDs given, newest first (see PGPRole)
        """
        if not key_ids:
            return []

        query = session.query(ArchivedPost, PGPPost.role)\
            .join(PGPPost, PGPPost.message_id == ArchivedPost.message_id)\
            .filter(PGPPost.key_id.in_(list(key_ids)))\
            .order_by(ArchivedPost.posted.desc(), ArchivedPost.id.desc(),
                      PGPPost.role)
        return query.limit(limit).all() if limit else query.all()

    @staticmethod
    def count_posts(session, key):
        """
        Returns how many archived posts name a key (a PGPKey) or one of its
        subkeys
        """
        ids = [key.key_id, *(k for k, in session.query(PGPKey.key_id)
                             .filter(PGPKey.parent == key.fingerprint))]
        return session.query(func.count(func.distinct(PGPPost.message_id)))\
            .filter(PGPPost.key_id.in_(ids)).scalar() or 0

    @staticmethod
    def links(session, message_id):
        """
        Returns (role, key ID, created, key) for each key a post names; key
        is the PGPKey it is (or whose subkey it is), None if we don't have
        it
        """
        found = []
        for key_id, role, created in session.query(
                PGPPost.key_id, PGPPost.role, PGPPost.created)\
                .filter(PGPPost.message_id == message_id)\
                .order_by(PGPPost.role, PGPPost.key_id):
            key = session.query(PGPKey)\
                .filter(PGPKey.key_id == key_id)\
                .order_by(PGPKey.parent).first()

            if key is not None and key.parent:
                # A subkey: the key it belongs to is the one people know
                key = session.query(PGPKey)\
                    .filter(PGPKey.fingerprint == key.parent).first()

            found.append((role, key_id, created, key))

        return found

    @staticmethod
    def forget(session, keys):
        """
        Removes the keys given (PGPKey objects) and their subkeys; which
        posts named them is kept.  Not committed.
        """
        fingerprints = [key.fingerprint for key in keys]
        if not fingerprints:
            return

        session.query(PGPKey)\
            .filter(or_(PGPKey.fingerprint.in_(fingerprints),
                        PGPKey.parent.in_(fingerprints)))\
            .delete(synchronize_session=False)

    @staticmethod
    def counts(session):
        """
        Returns (keys, posts): how many keys are kept, and how many archived
        posts name one
        """
        return (
            session.query(func.count(PGPKey.id))
            .filter(PGPKey.parent == '').scalar() or 0,
            session.query(func.count(func.distinct(PGPPost.message_id)))
            .filter(PGPPost.key_id != HIDDEN_KEY_ID).scalar() or 0)
