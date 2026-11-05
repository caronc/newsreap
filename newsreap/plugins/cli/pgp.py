# NewsReap PGP CLI Plugin
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

# The PGP keys found in archived posts (turn it on with 'pgp: yes' under
# processing in config.yaml; 'nr update <group> --content' then keeps
# them):
#   nr pgp ls [<name|key id>]
#   nr pgp show <name|key id|fingerprint> [...]
#   nr pgp import <file> [...]
#   nr pgp export <name|key id|fingerprint> [...]
#   nr pgp rm <key id|fingerprint> [...]
#
# Posts encrypted for you are decrypted as they are kept once your secret
# key is imported (gpg does the work):
#   nr pgp import my-secret-key.asc
#   nr pgp passphrase <name|key id>
#   nr pgp decrypt [<name|key id>] [--all]
#
# The ones that can't be decrypted yet (ie: no passphrase was known) are
# fetched again by the next 'nr update <group> --content', up to
# pgp_retries times; 'nr pgp decrypt' tries them all at once.
#
# 'nr search --key <name|key id>' finds the posts tied to a key.

import logging
import click
import sys

from os.path import abspath
from os.path import dirname

try:
    from newsreap.Logging import NEWSREAP_CLI
except ImportError:
    # Path
    sys.path.insert(0, dirname(dirname(dirname(dirname(abspath(__file__))))))
    from newsreap.Logging import NEWSREAP_CLI

from newsreap.NNTPArchiver import NNTPArchiver
from newsreap.NNTPIndexDatabase import NNTPIndexDatabase
from newsreap.NNTPKeyring import NNTPKeyring
from newsreap.codecs.CodecPGP import CodecPGP
from newsreap.objects.index.PGPKey import PGPKeySource
from newsreap.objects.index.PGPPost import PGP_ROLES

# initialize our logger
logger = logging.getLogger(NEWSREAP_CLI)

# The biggest key file read (key files are a few KB; a big keyring a few
# MB)
MAX_KEY_FILE = 16777216

# How many of a key's posts 'nr pgp show' lists by default
DEFAULT_KEY_POSTS = 20

NEWSREAP_CLI_PLUGINS = {
    # format:
    # cli short hand group: function prefix
    'pgp': {
        'prefix': 'pgp',
        'desc': 'The PGP keys found in archived posts',
    },
}


def open_index(ctx, create=False):
    """
    Returns the index database (None if nothing was ever kept, unless
    `create` is set)
    """
    path = ctx['NNTPSettings'].index_database()
    if not create and not NNTPIndexDatabase.exists(path):
        return None

    return NNTPIndexDatabase.from_engine(path)


def turned_off(ctx):
    """
    Says how to turn PGP keys on (when they are off)
    """
    settings = ctx['NNTPSettings']
    if not settings.pgp():
        click.echo(
            "PGP keys aren't kept from posts; add 'pgp: yes' under "
            "processing: in %s, then run 'nr update <group> --content'." % (
                settings.cfg_file or 'your configuration file'))


def secret_gpg(ctx):
    """
    Returns the NNTPGnuPG our secret keys are kept with; exits (saying
    why) if PGP is off or gpg isn't installed
    """
    gpg = ctx['NNTPSettings'].gpg()
    if gpg is None:
        logger.error(
            "Secret keys are only used with 'pgp: yes' under processing: in "
            "%s." % (ctx['NNTPSettings'].cfg_file or 'your configuration'))
        exit(1)

    if not gpg.available():
        logger.error(
            "gpg isn't installed; it is needed to decrypt posts (see "
            "https://gnupg.org).")
        exit(1)

    return gpg


def held(ctx):
    """
    Returns the fingerprints of the secret keys we hold (none without gpg,
    and gpg is never set up just to find that out)
    """
    gpg = ctx['NNTPSettings'].gpg()
    if gpg is None or not gpg.available():
        return set()

    return set(gpg.secret_keys().values())


def holds_secret(data):
    """
    Returns True if key file data (armored or binary) holds a secret key
    """
    if b'-----BEGIN PGP PRIVATE KEY BLOCK' in data:
        return True

    if data[:1] and data[0] & 0x80:
        # Binary
        return any(key.get('secret') for key in CodecPGP.packets(
            data)['keys'])

    return False


def waiting(session, gpg, fingerprint=None, final=False):
    """
    Returns the kept posts still waiting to be decrypted (only the ones
    one key can open when its fingerprint is given); with `final`, the ones
    given up on are included (ie: the server no longer has them)
    """
    return NNTPKeyring.locked(session, {
        key_id for key_id, key in gpg.secret_keys().items()
        if fingerprint is None or key == fingerprint}, final=final)


def renew(session, gpg, fingerprint):
    """
    Gives the posts a key may now open their tries back (it was just
    imported, or given its passphrase), so 'nr update' tries them again
    """
    NNTPKeyring.renew(session, waiting(session, gpg, fingerprint, final=True))
    session.commit()


def describe(key):
    """
    Returns a key's first name (or that it gives none)
    """
    return key.user_ids.splitlines()[0] if key.user_ids else '(no name)'


def keys_named(session, names):
    """
    Returns the keys named (by key ID, fingerprint or part of a name), in
    the order given and once each; a name nothing matches is logged
    """
    found = {}
    for name in names:
        keys = NNTPKeyring.find(session, name)
        if not keys:
            logger.error("No PGP key matches '%s'." % name)

        for key in keys:
            found.setdefault(key.fingerprint, key)

    return list(found.values())


@click.command(name='ls')
@click.argument('name', required=False)
@click.pass_obj
def pgp_list(ctx, name):
    """
    List the PGP keys kept.

    Give a key ID, a fingerprint or part of a name or email address to only
    list the keys it matches.

    """
    db = open_index(ctx)
    keys = []
    secret = held(ctx)
    try:
        if db is not None:
            session = db.session()
            keys = NNTPKeyring.find(session, name)
            for key in keys:
                click.echo('%s  %s  %-8s %5d post(s)  %s%s' % (
                    key.key_id, key.created.strftime('%Y-%m-%d')
                    if key.created else '????-??-??', key.algorithm,
                    NNTPKeyring.count_posts(session, key), describe(key),
                    '  (secret key held)' if key.fingerprint in secret
                    else ''))

    finally:
        if db is not None:
            db.close()

    if not keys:
        click.echo("No PGP keys%s kept." % (
            " matching '%s' are" % name if name else ' are'))
        turned_off(ctx)


@click.command(name='show')
@click.argument('names', nargs=-1, required=True)
@click.option('--limit', '-l', type=click.IntRange(min=1),
              default=DEFAULT_KEY_POSTS, show_default=True,
              help="How many of each key's posts are listed.")
@click.pass_obj
def pgp_show(ctx, names, limit):
    """
    Show everything known of PGP keys: their names, subkeys, where they
    were found and the posts tied to them.

    Name each key by its key ID, its fingerprint or part of a name or email
    address.  A key ID we don't have the key of still lists the posts
    naming it.

    """
    db = open_index(ctx)
    if db is None:
        click.echo('Nothing is kept yet.')
        turned_off(ctx)
        exit(1)

    # Track whether every name was found
    complete = True

    try:
        session = db.session()
        shown = 0
        for name in dict.fromkeys(names):
            keys = NNTPKeyring.find(session, name)
            if not keys:
                # Only posts naming it (we don't have the key)
                key_ids = NNTPKeyring.resolve(session, name)
                posts = NNTPKeyring.posts(session, key_ids, limit=limit)
                if not posts:
                    logger.error("No PGP key matches '%s'." % name)
                    complete = False
                    continue

                if shown:
                    click.echo('')
                shown += 1

                click.echo('Key ID:      %s (the key itself is not kept)' % (
                    ', '.join(sorted(key_ids))))
                show_posts(posts)
                continue

            for key in keys:
                if shown:
                    click.echo('')
                shown += 1
                show_key(session, key, limit, ctx['NNTPSettings'].gpg())

    finally:
        db.close()

    if not complete:
        exit(1)


def show_key(session, key, limit, gpg=None):
    """
    Shows everything known of a key
    """
    click.echo('Key ID:      %s' % key.key_id)
    click.echo('Fingerprint: %s' % NNTPKeyring.pretty(key.fingerprint))
    click.echo('Method:      %s (version %d)' % (key.algorithm, key.version))
    if key.created:
        click.echo('Created:     %s UTC' % key.created.strftime(
            '%Y-%m-%d %H:%M:%S'))

    for name in (key.user_ids or '').splitlines():
        click.echo('Name:        %s' % name)

    for subkey in NNTPKeyring.subkeys(session, key):
        click.echo('Subkey:      %s (%s%s)' % (
            subkey.key_id, subkey.algorithm, ', %s' % subkey.created.strftime(
                '%Y-%m-%d') if subkey.created else ''))

    seen = key.first_seen.strftime('%Y-%m-%d %H:%M UTC') \
        if key.first_seen else '?'
    if key.source == PGPKeySource.Imported:
        click.echo('Imported:    %s' % seen)

    else:
        click.echo('First seen:  %s%s' % (
            seen, ' in <%s>' % key.message_id if key.message_id else ''))

    if gpg is not None and gpg.available() and \
            key.fingerprint in set(gpg.secret_keys().values()):
        click.echo('Secret key:  held (%s; %d post(s) not decrypted yet)' % (
            'passphrase kept' if gpg.passphrase_file(key.fingerprint)
            else 'no passphrase kept',
            len(waiting(session, gpg, key.fingerprint))))

    show_posts(NNTPKeyring.posts(
        session, NNTPKeyring.resolve(session, key.fingerprint), limit=limit))


def show_posts(posts):
    """
    Lists posts tied to a key ((post, role) each)
    """
    click.echo('Posts:       %d%s' % (
        len(posts), ' (the newest)' if posts else ''))
    for post, role in posts:
        click.echo('  %s  %-13s <%s>  %s' % (
            post.posted.strftime('%Y-%m-%d %H:%M') if post.posted
            else '????-??-?? ??:??', PGP_ROLES.get(role, '?'),
            post.message_id, post.subject))


@click.command(name='import')
@click.argument('files', nargs=-1, required=True,
                type=click.File('rb', lazy=False))
@click.pass_obj
def pgp_import(ctx, files):
    """
    Keep the PGP public keys in files (someone sharing their key with you),
    or your own secret key.

    Files can be ASCII armored (.asc) or binary (.gpg); use - to read from
    what is piped in.

    A secret key is handed to gpg (never kept in the index), and posts
    encrypted for it are decrypted from then on as they are kept.  If it
    has a passphrase, give it with 'nr pgp passphrase'; 'nr pgp decrypt'
    reads the posts kept before.

    """
    db = open_index(ctx, create=True)

    # Track whether every file had a key
    complete = True

    try:
        session = db.session()
        for fp in files:
            data = fp.read(MAX_KEY_FILE + 1)
            if len(data) > MAX_KEY_FILE:
                logger.error('%s is too big to be a key file.' % fp.name)
                complete = False
                continue

            keys = NNTPKeyring.read_keys(data)
            secret = []
            if holds_secret(data):
                # Our own; gpg keeps it (and gives us its public half)
                gpg = secret_gpg(ctx)
                secret = gpg.import_keys(data)
                if not secret:
                    logger.error('gpg could not read the secret key in %s.' % (
                        fp.name))
                    complete = False
                    continue

                for fingerprint in secret:
                    keys.extend(NNTPKeyring.read_keys(gpg.export(fingerprint)))

            if not keys:
                logger.error('No PGP public keys were found in %s.' % (
                    fp.name))
                complete = False
                continue

            new = NNTPKeyring.save_keys(
                session, keys, source=PGPKeySource.Imported)
            session.commit()

            for key in keys:
                click.echo('Kept %s %s%s' % (
                    key['key_id'], key['user_ids'][0] if key['user_ids']
                    else '', ' (secret key)' if key['fingerprint'] in secret
                    else ''))

            click.echo('%s: %d key(s), %d new.' % (fp.name, len(keys), new))

            for fingerprint in secret:
                renew(session, gpg, fingerprint)
                found = len(waiting(session, gpg, fingerprint))
                if found:
                    click.echo(
                        "%d kept post(s) are encrypted for %s; 'nr pgp "
                        "decrypt' reads them%s." % (
                            found, fingerprint[-16:],
                            '' if gpg.passphrase_file(fingerprint) else
                            " (after 'nr pgp passphrase' if it has one)"))

    finally:
        db.close()

    if not complete:
        exit(1)


@click.command(name='export')
@click.argument('names', nargs=-1, required=True)
@click.option('--output', '-o', type=click.File('w'), default='-',
              help='Write them to this file (instead of showing them).')
@click.pass_obj
def pgp_export(ctx, names, output):
    """
    Write out PGP public keys (ASCII armored), ie: to hand them on or load
    them into gpg.

    Name each key by its key ID, its fingerprint or part of a name or email
    address.

    """
    db = open_index(ctx)
    if db is None:
        logger.error('No PGP keys are kept.')
        exit(1)

    try:
        session = db.session()
        keys = keys_named(session, names)
        for key in keys:
            output.write(key.armored or '')

        # Every name has to find a key (each one that didn't was said)
        missing = [name for name in names
                   if not NNTPKeyring.find(session, name)]

    finally:
        db.close()

    if not keys or missing:
        exit(1)


@click.command(name='rm')
@click.argument('names', nargs=-1, required=True)
@click.pass_obj
def pgp_forget(ctx, names):
    """
    Remove PGP keys (and their subkeys).

    Name each key by its key ID or fingerprint (a name matching more than
    one key is refused).  Which posts named it is kept, so 'nr search
    --key' still finds them by the key ID.

    """
    db = open_index(ctx)
    if db is None:
        logger.error('No PGP keys are kept.')
        exit(1)

    # Track whether every key was removed
    complete = True

    try:
        session = db.session()
        for name in dict.fromkeys(names):
            keys = NNTPKeyring.find(session, name)
            if not keys:
                logger.error("No PGP key matches '%s'." % name)
                complete = False
                continue

            if len(keys) > 1:
                logger.error(
                    "'%s' matches %d keys (%s); use a key ID." % (
                        name, len(keys), ', '.join(k.key_id for k in keys)))
                complete = False
                continue

            # Said before it is gone (its row can't be read after)
            text = 'Forgot %s %s' % (keys[0].key_id, describe(keys[0]))
            NNTPKeyring.forget(session, keys)
            session.commit()
            click.echo(text)

    finally:
        db.close()

    if not complete:
        exit(1)


@click.command(name='passphrase')
@click.argument('name')
@click.option('--forget', is_flag=True,
              help='Remove the passphrase kept for the key.')
@click.pass_obj
def pgp_passphrase(ctx, name, forget):
    """
    Keep the passphrase of one of your secret keys.

    Name the key by its key ID, its fingerprint or part of a name or email
    address (see 'nr pgp ls').  The passphrase is asked for (it isn't
    shown), checked against the key, and kept in a file only you can read,
    so posts encrypted for the key are decrypted without asking.

    """
    gpg = secret_gpg(ctx)
    secret = set(gpg.secret_keys().values())

    db = open_index(ctx, create=True)
    try:
        session = db.session()
        keys = [key for key in keys_named(session, [name])
                if key.fingerprint in secret]
        if not keys:
            logger.error(
                "You hold no secret key matching '%s'; add it with 'nr pgp "
                "import <file>'." % name)
            exit(1)

        if len(keys) > 1:
            logger.error("'%s' matches %d of your keys (%s); use a key ID." % (
                name, len(keys), ', '.join(k.key_id for k in keys)))
            exit(1)

        key = keys[0]
        if forget:
            if not gpg.forget_passphrase(key.fingerprint):
                click.echo('No passphrase was kept for %s.' % key.key_id)
                return

            click.echo('Forgot the passphrase of %s %s' % (
                key.key_id, describe(key)))
            return

        passphrase = click.prompt(
            'Passphrase for %s %s' % (key.key_id, describe(key)),
            hide_input=True, default='', show_default=False)

        if not gpg.check_passphrase(key.fingerprint, passphrase):
            logger.error("That passphrase doesn't unlock %s." % key.key_id)
            exit(1)

        gpg.save_passphrase(key.fingerprint, passphrase)
        click.echo('Kept the passphrase of %s %s' % (
            key.key_id, describe(key)))

        renew(session, gpg, key.fingerprint)

        found = len(waiting(session, gpg, key.fingerprint))
        if found:
            click.echo(
                "%d kept post(s) are waiting for it; 'nr pgp decrypt' "
                "reads them." % found)

    finally:
        db.close()


@click.command(name='decrypt')
@click.argument('names', nargs=-1)
@click.option('--all', 'everything', is_flag=True,
              help='Also try the posts given up on (the server no longer '
              'had them, or they weren\'t for your keys).')
@click.pass_obj
def pgp_decrypt(ctx, names, everything):
    """
    Decrypt the kept posts encrypted for your secret keys that weren't yet.

    A post encrypted for one of your keys is decrypted as it is kept ('nr
    update --content').  One that couldn't be (ie: its passphrase wasn't
    known yet, or the key was imported later) is fetched again by the next
    'nr update --content' of its group, up to pgp_retries times (1 unless
    set).  This tries them all now, however often they were tried.

    Posts are given up on at once when trying again can't help: the server
    no longer has them or won't let you have them, or none of your keys
    open them.  Use --all to try those too.  Name keys (by key ID,
    fingerprint or part of a name) to only read the posts for them.

    """
    gpg = secret_gpg(ctx)
    secret = gpg.secret_keys()
    if not secret:
        click.echo(
            "You hold no secret keys; add yours with 'nr pgp import "
            "<file>'.")
        exit(1)

    db = open_index(ctx)
    if db is None:
        click.echo('Nothing is kept yet.')
        exit(1)

    try:
        session = db.session()
        key_ids = set(secret)
        if names:
            wanted = {key.fingerprint for key in keys_named(session, names)}
            key_ids = {k for k, key in secret.items() if key in wanted}
            if not key_ids:
                logger.error('None of your secret keys were named.')
                exit(1)

        found = NNTPKeyring.locked(session, key_ids, final=everything)
        left = 0 if everything else \
            len(NNTPKeyring.locked(session, key_ids)) - len(found)

    finally:
        db.close()

    if left:
        click.echo(
            "%d post(s) given up on are left out (the server no longer has "
            "them, or they aren't for your keys); --all tries them too." % (
                left))

    if not found:
        click.echo('No kept post is waiting to be decrypted.')
        return

    settings = ctx['NNTPSettings']
    archiver = NNTPArchiver(
        settings.index_database(), pgp=True, gpg=gpg,
        retries=settings.pgp_retries())
    try:
        archiver.retry(found, ctx['NNTPManager'], work_dir=settings.work_dir)

    finally:
        archiver.close()

    click.echo('%d post(s) waiting: %d decrypted, %d still locked%s.' % (
        len(found), archiver.decrypted, archiver.locked,
        ', %d could not be fetched' % archiver.failed
        if archiver.failed else ''))

    if archiver.locked:
        click.echo(
            "A key that needs a passphrase can be given one with 'nr pgp "
            "passphrase <key>'.")

    if archiver.failed:
        exit(1)
