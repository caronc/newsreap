# Decrypts PGP posts with gpg (GnuPG)
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
Runs gpg (https://gnupg.org) to decrypt the posts encrypted for a secret
key you hold, and to keep those secret keys and their passphrases.

- gpg keeps the secret keys in its own folder (`home`): <base_dir>/pgp by
  default, or your own (ie: ~/.gnupg) with the pgp_home setting.
- A passphrase is kept in a file of its own next to them (only you can
  read it), named after the key's fingerprint.  It is handed to gpg as a
  file, so it never shows in the list of running programs.
- gpg never asks for anything: a key that needs a passphrase we don't
  have (or a wrong one) fails at once, and the post is left as it was so
  it can be tried again later ('nr pgp decrypt').  A gpg that stops
  answering is stopped after `timeout` seconds.
"""

import gevent.monkey
gevent.monkey.patch_all()

import re
import subprocess
from datetime import datetime
from datetime import UTC
from os import O_CREAT
from os import O_TRUNC
from os import O_WRONLY
from os import chmod
from os import makedirs
from os import open as os_open
from os import unlink
from os.path import isdir
from os.path import isfile
from os.path import join
from shutil import which
from tempfile import TemporaryDirectory

import gevent

from newsreap.objects.index.PGPPost import HIDDEN_KEY_ID

# Logging
import logging
from newsreap.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)

# How long gpg is given to answer (seconds)
DEFAULT_GPG_TIMEOUT = 30

# The most a decrypted post is let grow to (compressed data can unpack to
# far more than the post itself)
DEFAULT_GPG_MAX_SIZE = 4194304

# A status line gpg writes (see doc/DETAILS in the GnuPG sources)
STATUS_RE = re.compile(r'^\[GNUPG:\] (?P<key>[A-Z_]+) ?(?P<args>.*)$')

# A full fingerprint
FINGERPRINT_RE = re.compile(r'^[0-9A-F]{40}$|^[0-9A-F]{64}$')

# The kinds of data a PGP message can hold (the PLAINTEXT status); 'b' is
# a file, the others are text
BINARY_FORMAT = 0x62

# The signatures (statuses) that name who signed what was decrypted
SIGNED_STATUSES = frozenset((
    'GOODSIG', 'EXPSIG', 'EXPKEYSIG', 'REVKEYSIG', 'BADSIG', 'ERRSIG'))

# gpg's error codes (the low 16 bits; see 'gpg-error <code>') that mean a
# key needed a passphrase it didn't get: a bad passphrase, no passphrase
# given, no pinentry to ask with, or asking was cancelled.  Any other reason
# a key couldn't open a message (ie: a checksum error) means it wasn't the
# key it was for.
PASSPHRASE_ERRORS = frozenset((11, 85, 99, 114, 177, 198))


class GnuPGResult:
    """
    What decrypting a post came to
    - ok: True if it was decrypted
    - data: what it held (bytes)
    - binary: True if it held a file (`filename`) rather than text
    - signers: (key ID, when) for each signature inside it
    - reason: why it couldn't be decrypted: 'no key' (none of ours),
      'passphrase' (we have no passphrase for the key), 'bad passphrase',
      'too big', 'timeout' or 'failed'
    - key: the fingerprint of the key it was decrypted with (or needed)
    """

    def __init__(self):
        self.ok = False
        self.data = b''
        self.binary = False
        self.filename = ''
        self.signers = []
        self.reason = 'failed'
        self.key = ''

    def __bool__(self):
        return self.ok

    def __repr__(self):
        return '<GnuPGResult ok=%s reason="%s" />' % (self.ok, self.reason)


class NNTPGnuPG:
    """
    Decrypts PGP posts with gpg; see the module description
    """

    def __init__(self, home, passphrases=None, binary=None,
                 timeout=DEFAULT_GPG_TIMEOUT, max_size=DEFAULT_GPG_MAX_SIZE):
        """
        Prepares gpg

        - home: the folder gpg keeps its keys in (made when first needed)
        - passphrases: the folder passphrases are kept in (<home>/passphrases
          when not given)
        - binary: the gpg program (found on the PATH when not given)
        - timeout: how long gpg is given to answer (seconds)
        - max_size: the most a decrypted post is let grow to
        """
        self.home = home
        self.passphrases = passphrases or join(home, 'passphrases')
        self.binary = which(binary) if binary is not None \
            else which('gpg') or which('gpg2')
        self.timeout = timeout
        self.max_size = max_size

        # Our secret keys (worked out when first needed)
        self._secret = None

    def available(self):
        """
        Returns True if gpg is installed
        """
        return bool(self.binary)

    def _command(self, *args):
        """
        Returns a gpg command line that never asks for anything
        """
        return [
            self.binary, '--homedir', self.home, '--batch', '--no-tty',
            '--yes', '--quiet', '--pinentry-mode', 'loopback',
            '--no-auto-key-retrieve', '--no-permission-warning', *args]

    def _run(self, args, data=None, limit=None):
        """
        Runs gpg; returns (return code, what it wrote, its status lines as
        (key, args)).  The data given is handed to it as a file.

        - limit: the most it may write (it is stopped beyond that, with a
          return code of None)
        """
        if not self.available():
            return -1, b'', []

        if not isdir(self.home):
            makedirs(self.home, mode=0o700, exist_ok=True)

        with TemporaryDirectory(prefix='nr-gpg-') as work:
            status_file = join(work, 'status')
            command = self._command('--status-file', status_file, *args)
            if data is not None:
                source = join(work, 'input')
                with open(source, 'wb') as fp:
                    fp.write(data)
                command.append(source)

            code = None
            output = b''
            try:
                process = subprocess.Popen(
                    command, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

            except OSError as e:
                logger.warning('gpg could not be run: %s' % e)
                return -1, b'', []

            try:
                with gevent.Timeout(self.timeout):
                    output = process.stdout.read(
                        limit + 1 if limit else -1) or b''
                    if limit and len(output) > limit:
                        # Too much; we don't want the rest
                        process.kill()

                    else:
                        code = process.wait()

            except gevent.Timeout:
                logger.warning(
                    'gpg did not answer within %d second(s); it was '
                    'stopped.' % self.timeout)
                process.kill()
                code = 'timeout'

            finally:
                process.stdout.close()
                process.wait()

            status = []
            if isfile(status_file):
                with open(status_file, encoding='utf-8',
                          errors='replace') as fp:
                    for line in fp:
                        found = STATUS_RE.match(line.rstrip('\r\n'))
                        if found:
                            status.append(
                                (found.group('key'), found.group('args')))

        return code, output, status

    def secret_keys(self, refresh=False):
        """
        Returns {key ID: fingerprint of its key} for each secret key (and
        subkey) we hold, so a post naming any of them can be decrypted
        """
        if self._secret is not None and not refresh:
            return self._secret

        self._secret = {}
        if not self.available() or not isdir(self.home):
            # No keys yet (and nothing made just to find that out)
            return self._secret

        for kind, key_id, fingerprint, primary, _ in self._listing(
                '--list-secret-keys'):
            self._secret[key_id] = primary
            if len(fingerprint) == 64:
                # Newer (v5 and v6) keys are named by the start of theirs
                self._secret[fingerprint[:16]] = primary

        return self._secret

    def _listing(self, *args):
        """
        Yields (kind, key ID, fingerprint, fingerprint of its key, keygrip)
        for each key and subkey gpg lists ('sec' or 'ssb' for secret ones,
        'pub' or 'sub' for public ones)
        """
        _, output, _ = self._run(['--with-colons', '--with-keygrip', *args])

        # The key (or subkey) being read, and the key it belongs to
        kind = key_id = fingerprint = primary = None
        found = None
        for line in output.decode('utf-8', errors='replace').splitlines():
            fields = line.split(':')
            if len(fields) < 10:
                continue

            if fields[0] in ('sec', 'ssb', 'pub', 'sub'):
                if found:
                    yield found
                found = None
                kind, key_id, fingerprint = fields[0], fields[4].upper(), None

            elif fields[0] == 'fpr' and kind and fingerprint is None:
                fingerprint = fields[9].upper()
                if kind in ('sec', 'pub'):
                    primary = fingerprint
                found = (kind, key_id, fingerprint, primary, '')

            elif fields[0] == 'grp' and found and not found[4]:
                found = found[:4] + (fields[9].upper(),)

        if found:
            yield found

    def holds(self, key_ids):
        """
        Returns the fingerprints of our keys a post encrypted for the key
        IDs given can be decrypted with (each of them for a hidden
        recipient; see HIDDEN_KEY_ID), in the order given
        """
        secret = self.secret_keys()
        found = []
        for key_id in key_ids:
            key_id = key_id.upper()
            if key_id == HIDDEN_KEY_ID:
                # It doesn't say; each of ours is tried
                found.extend(secret.values())

            elif key_id in secret:
                found.append(secret[key_id])

        return list(dict.fromkeys(found))

    def decrypt(self, data, key_ids=(HIDDEN_KEY_ID, )):
        """
        Decrypts a PGP message (armored or binary) encrypted for the key
        IDs given; returns a GnuPGResult (false if it couldn't be)
        """
        result = GnuPGResult()
        keys = self.holds(key_ids)
        if not keys:
            result.reason = 'no key'
            return result

        for fingerprint in keys:
            args = ['--decrypt']
            passphrase = self.passphrase_file(fingerprint)
            if passphrase:
                args[:0] = ['--passphrase-file', passphrase]

            code, output, status = self._run(
                args, data=bytes(data), limit=self.max_size)

            result = self._read(code, output, status, bool(passphrase))
            result.key = result.key or fingerprint
            if result.ok:
                break

        if not result.ok:
            logger.info('A PGP message could not be decrypted (%s).' % (
                result.reason))

        return result

    @staticmethod
    def _read(code, output, status, passphrase):
        """
        Returns the GnuPGResult of a decryption (what gpg wrote and its
        status lines)
        - passphrase: True if a passphrase was given
        """
        result = GnuPGResult()
        keys = {key for key, _ in status}

        # Why our secret keys couldn't open it (gpg's error codes)
        failures = set()

        for key, args in status:
            fields = args.split()
            if key == 'PLAINTEXT' and fields:
                # What it holds: its format, when, and its file name
                try:
                    result.binary = int(fields[0], 16) == BINARY_FORMAT

                except ValueError:
                    pass

                if len(fields) > 2:
                    result.filename = unquote(' '.join(fields[2:]))

            elif key == 'DECRYPTION_KEY' and len(fields) > 1:
                result.key = fields[1].upper()

            elif key in SIGNED_STATUSES and fields:
                # ERRSIG says when (we may not have the key it names)
                created = fields[4] if key == 'ERRSIG' and len(fields) > 4 \
                    else None
                result.signers.append(
                    [fields[0].upper()[-16:], timestamp(created)])

            elif key == 'VALIDSIG' and len(fields) > 2 and result.signers:
                # When the last signature was made
                result.signers[-1][1] = timestamp(fields[2])

            elif key == 'ERROR' and len(fields) > 1 and \
                    fields[0] == 'pkdecrypt_failed':
                try:
                    failures.add(int(fields[1]) & 0xFFFF)

                except ValueError:
                    # We can't tell why; take it as a passphrase problem
                    failures.add(min(PASSPHRASE_ERRORS))

        result.signers = [tuple(s) for s in result.signers]

        if code is None:
            result.reason = 'too big'

        elif code == 'timeout':
            result.reason = 'timeout'

        elif 'DECRYPTION_OKAY' in keys and 'DECRYPTION_FAILED' not in keys:
            result.ok = True
            result.reason = ''
            result.data = output

        elif 'NEED_PASSPHRASE' in keys or 'BAD_PASSPHRASE' in keys or \
                failures & PASSPHRASE_ERRORS:
            result.reason = 'bad passphrase' if passphrase else 'passphrase'

        elif 'NO_SECKEY' in keys or failures:
            # Our key opened, but the message wasn't for it (ie: a hidden
            # recipient that wasn't us)
            result.reason = 'no key'

        return result

    def import_keys(self, data):
        """
        Adds the keys in a key file (armored or binary) to gpg; returns
        the fingerprints of the secret keys added (public ones are added
        too, but not returned)
        """
        _, _, status = self._run(['--import'], data=bytes(data))

        found = []
        for key, args in status:
            fields = args.split()
            if key == 'IMPORT_OK' and len(fields) > 1:
                try:
                    secret = int(fields[0]) & 16

                except ValueError:
                    continue

                if secret and FINGERPRINT_RE.match(fields[1].upper()):
                    found.append(fields[1].upper())

        if found:
            # Our secret keys changed
            self._secret = None

        return list(dict.fromkeys(found))

    def export(self, fingerprint):
        """
        Returns the public half of one of our keys (armored), so it can be
        kept with the others (see NNTPKeyring); empty if we don't have it
        """
        _, output, _ = self._run(['--armor', '--export', fingerprint])
        return output

    def passphrase_file(self, fingerprint):
        """
        Returns the file a key's passphrase is kept in (None if we don't
        have one)
        """
        path = join(self.passphrases, '%s.pass' % fingerprint.upper())
        return path if isfile(path) else None

    def save_passphrase(self, fingerprint, passphrase):
        """
        Keeps a key's passphrase (only you can read it)
        """
        makedirs(self.passphrases, mode=0o700, exist_ok=True)
        path = join(self.passphrases, '%s.pass' % fingerprint.upper())

        # Made unreadable to others before anything is written to it
        fd = os_open(path, O_WRONLY | O_CREAT | O_TRUNC, 0o600)
        with open(fd, 'w', encoding='utf-8') as fp:
            chmod(path, 0o600)
            fp.write(passphrase + '\n')

    def forget_passphrase(self, fingerprint):
        """
        Removes a key's passphrase; returns True if there was one
        """
        path = self.passphrase_file(fingerprint)
        if path:
            unlink(path)
            return True

        return False

    def check_passphrase(self, fingerprint, passphrase):
        """
        Returns True if the passphrase unlocks one of our keys (it signs
        nothing with it to find out)
        """
        # Anything gpg-agent remembers of this key would answer instead
        self._forget_cached(fingerprint)

        with TemporaryDirectory(prefix='nr-gpg-') as work:
            path = join(work, 'passphrase')
            fd = os_open(path, O_WRONLY | O_CREAT | O_TRUNC, 0o600)
            with open(fd, 'w', encoding='utf-8') as fp:
                fp.write(passphrase + '\n')

            code, _, status = self._run([
                '--passphrase-file', path, '--local-user', fingerprint + '!',
                '--output', join(work, 'signed'), '--detach-sign'],
                data=b'')

        return code == 0 and any(key == 'SIG_CREATED' for key, _ in status)

    def _forget_cached(self, fingerprint):
        """
        Makes gpg-agent forget a key's passphrase (if it remembers it); no
        other key's is touched
        """
        agent = which('gpg-connect-agent')
        if not agent:
            return

        for kind, _, _, primary, keygrip in self._listing(
                '--list-secret-keys', fingerprint):
            if primary != fingerprint.upper() or not keygrip:
                continue

            try:
                subprocess.run(
                    [agent, '--homedir', self.home, '--no-autostart',
                     'CLEAR_PASSPHRASE --mode=normal %s' % keygrip, '/bye'],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, timeout=self.timeout)

            except (OSError, subprocess.SubprocessError):
                pass

    def __str__(self):
        return self.home

    def __repr__(self):
        return '<NNTPGnuPG home="%s" />' % self.home


def timestamp(value):
    """
    Returns the UTC datetime of a gpg time (seconds since 1970, or an ISO
    date); None if it isn't one
    """
    if not value:
        return None

    try:
        if value.isdigit():
            return datetime.fromtimestamp(int(value), UTC)\
                .replace(tzinfo=None)

        return datetime.strptime(value.split('T')[0], '%Y%m%d')

    except (ValueError, OverflowError, OSError):
        return None


def unquote(text):
    """
    Returns a file name as gpg writes it in a status line (%XX for the
    characters it can't write as they are)
    """
    return re.sub(
        r'%([0-9A-Fa-f]{2})', lambda m: chr(int(m.group(1), 16)), text)
