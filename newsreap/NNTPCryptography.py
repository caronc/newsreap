# -*- coding: utf-8 -*-
#
# Used for NNTP Encoding/Decoding of Ecrypted Content
#
# Copyright (C) 2017 Chris Caron <lead2gold@gmail.com>
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

# Cryptography is a fantastic up and coming replacement to OpenSSL and GnuTLS
# Reference: https://github.com/pyca/cryptography
#
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.exceptions import UnsupportedAlgorithm

from os.path import isfile

from base64 import b64encode
from base64 import b64decode
from StringIO import StringIO
from gzip import GzipFile

from newsreap.Utils import SEEK_SET

# Logging
import logging
from newsreap.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)

# OpenSSL will only write 16K at a time
SSL_WRITE_BLOCKSIZE = 16384

# The default (private) keyfile we'll create if you don't specify one.
# The default public keyfile (unless otherwise specified) is always
# the same name as the private key but with the .pub extension added to the
# end
DEFAULT_KEYFILE = "~/newsreap-rsa_id"


class KeySize(object):
    """
    The KeySize is used when generating a new private/public key pair.
    It plays a signifigant role in the security of the data that will
    eventually be encrypted using the new key pair.

    The larger the key size, the more likely your encrypted content
    will never be cracked by some third party but it comes at a cost of
    being slower to encrypt/decrypt the content.

    It adds an layer of abstraction so that this class can eventually
    use different libraries to do keygens, encryptions and decryptions
    allowing the user to not have to know the way each system
    accomplishes this feat with.
    """

    # 1024 bytes make for a faster system performance when performing
    # encryption/decryption of content but it's security is considered
    # hackable through brute force.
    WEAK = 1024

    # 2048 Bytes is still kinda/sorta hackable (unlikely unless someone
    # really has a vendeta against you), it's a bit slower to generate content
    # with but is the ideal choice in most situations.
    NORMAL = 2048

    # 4096 Bytes is not crackable in today's times (2017 at the time this
    # entry was created).
    # The trade off is much slower system performance when encrypting and
    # decrypting content.
    STRONG = 4096

    # 8192 Bytes is very, very, very secure.  The price is system performance
    SECURE = 8192


class HashType(object):
    """
    This just spells it out for people so they know what is available to them

    Hashes are a 1 way encryption that always produces the same result when
    provided the same content. It's incredibly useful for verifying the
    integrity of any kind of content. Encryption relies on this!

    The list identified below is (for the most part) in order from best to
    worst with the trade off being the better choice you make it will
    cost you extra cpu cycles (a longer processing time).

    """
    SHA1 = 'sha1'

    SHA224 = 'sha224'

    SHA256 = 'sha256'

    SHA384 = 'sha384'

    SHA512 = 'sha512'


# We map our HashTypes to they're respected function
# the max_chunk defines the maximum chunk_size value we can
# correctly encrypt().  Anything larger isn't possible
CRYPTOGRAPHY_HASH_MAP = {
    HashType.SHA1: {
        'function': hashes.SHA1,
        'max_chunk': 214,
    },
    HashType.SHA224: {
        'function': hashes.SHA224,
        'max_chunk': 198,
    },
    HashType.SHA256: {
        'function': hashes.SHA256,
        'max_chunk': 190,
    },
    HashType.SHA384: {
        'function': hashes.SHA384,
        'max_chunk': 158,
    },
    HashType.SHA512: {
        'function': hashes.SHA512,
        'max_chunk': 126,
    },
}

# The default Hash to use
CRYPTOGRAPHY_DEFAULT_HASH = HashType.SHA256


class NNTPCryptography(object):
    def __init__(self, private_key=None, public_key=None, password=None,
                 alg=HashType.SHA256, mgf1=HashType.SHA256):
        """
        Initialize NNTP Cryptography class

        """

        # Initialize our Private Key
        self.private_key = None

        # Initialize our Public Key
        self.public_key = None

        # Hang onto any password specified (if provided)
        self.password = None

        # Hang onto any specified hash value (used for encryption and
        # decryption)
        self.alg = alg

        # Mask generation functions
        self.mgf1 = mgf1

        if self.private_key is not None:
            if not self.load(private_key, private_key, password):
                raise ValueError('Could not load specified keys')

    def encode_public_key(self):
        """
        Based on spotnab, this is the gzipped version of the key
        with base64 applied to it. We encode it as such and
        return it.

        """
        fileobj = StringIO()
        with GzipFile(fileobj=fileobj, mode="wb") as f:
            try:
                f.write(self.public_pem())
            except TypeError:
                # It wasn't initialized yet
                return None

        return b64encode(fileobj.getvalue())

    def encode_private_key(self):
        """
        Based on spotnab, this is the gzipped version of the key
        with base64 applied to it. We encode it as such and
        return it.

        """
        fileobj = StringIO()
        with GzipFile(fileobj=fileobj, mode="wb") as f:
            try:
                f.write(self.private_pem())
            except TypeError:
                # It wasn't initialized yet
                return None
        return b64encode(fileobj.getvalue())

    def decode_private_key(self, encoded):
        """
        Based on spotnab, this is the gzipped version of the key
        with base64 applied to it.  We decode it and load it.

        """

        fileobj = StringIO()
        try:
            fileobj.write(b64decode(encoded))
        except TypeError:
            return False

        fileobj.seek(0L, SEEK_SET)
        private_key = None
        with GzipFile(fileobj=fileobj, mode="rb") as f:
            private_key = f.read()

        if not private_key:
            return False

        # We were successful
        if not self.load(private_key=private_key):
            return False

        return True

    def decode_public_key(self, encoded):
        """
        Based on spotnab, this is the gzipped version of the key
        with base64 applied to it.  We decode it and load it.

        """
        fileobj = StringIO()
        try:
            fileobj.write(b64decode(encoded))
        except TypeError:
            return False

        fileobj.seek(0L, SEEK_SET)
        self.public_key = None
        with GzipFile(fileobj=fileobj, mode="rb") as f:
            try:
                self.public_key = serialization.load_pem_public_key(
                    f.read(),
                    backend=default_backend()
                )
            except ValueError:
                # Could not decrypt content
                return False

        if not self.public_key:
            return False

        return True

    def encrypt(self, payload, alg=None, mgf1=None):
        """
        Encrypts the payload using keys and returns the encrypted content

        """

        if not payload:
            # Nothing more to do
            return ''

        if not self.public_key:
            return None

        if alg is None:
            # Assign default algorithm
            alg = self.alg

        if mgf1 is None:
            # Assign default algorithm
            mgf1 = self.mgf1

        # Get our Algorithm hash value
        _alg = self.__get_hash_func(alg)
        _mgf1 = self.__get_hash_func(mgf1)

        try:
            return self.public_key.encrypt(
                payload,
                # OAEP (Optimal Asymmetric Encryption Padding) is a padding
                # scheme defined in RFC 3447. It provides probabilistic
                # encryption and is proven secure against several attack
                # types. This is the recommended padding algorithm for RSA
                # encryption.
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=_mgf1()),
                    algorithm=_alg(),
                    label=None,
                )
            )

        except TypeError:
            # Decryption Failed
            logger.error(
                'Cryptography / encryption failed '
                '(size=%d, alg=%s, mgf1=%s)' % (
                    len(payload), str(alg), str(mgf1),
                )
            )

        except ValueError as e:
            logger.error(
                'Cryptography / encryption failed '
                '(size=%d, alg=%s, mgf1=%s)' % (
                    len(payload), alg, mgf1,
                )
            )
            logger.debug(
                'Cryptography / encryption exception: %s' % (
                    str(e),
                )
            )

        # We failed if we reach here
        return None

    def decrypt(self, payload, alg=None, mgf1=None):
        """
        Decrypts the payload using keys and returns the
        encrypted key.

        """
        if not payload:
            # Nothing more to do
            return ''

        if not self.private_key:
            return None

        if alg is None:
            alg = self.alg

        if mgf1 is None:
            mgf1 = self.mgf1

        # Get our Algorithm hash value
        _alg = self.__get_hash_func(alg)
        _mgf1 = self.__get_hash_func(mgf1)

        try:
            return self.private_key.decrypt(
                payload,
                # OAEP (Optimal Asymmetric Encryption Padding) is a padding
                # scheme defined in RFC 3447. It provides probabilistic
                # encryption and is proven secure against several attack
                # types. This is the recommended padding algorithm for RSA
                # encryption.
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=_mgf1()),
                    algorithm=_alg(),
                    label=None
                )
            )

        except UnsupportedAlgorithm as e:
            # Decryption Failed
            logger.error(
                'Cryptography / decryption failed '
                '(size=%d, alg=%s, mgf1=%s)' % (
                    len(payload), alg, mgf1,
                )
            )
            logger.debug(
                'Cryptography / Unsupported Algorithm: %s' % (
                    str(e),
                )
            )

        except TypeError:
            # Decryption Failed
            logger.error(
                'Cryptography / decryption failed '
                '(size=%d, alg=%s, mgf1=%s)' % (
                    len(payload), str(alg), str(mgf1),
                )
            )

        except ValueError as e:
            # Decryption Failed
            logger.error(
                'Cryptography / decryption failed '
                '(size=%d, alg=%s, mgf1=%s)' % (
                    len(payload), alg, mgf1,
                )
            )
            logger.debug(
                'Cryptography / decryption exception: %s' % (
                    str(e),
                )
            )

        # We failed if we reach here
        return None

    def keys(self):
        """
        Simply return our Private and Public key in a tuple as such:
        (private, public)
        """

        return (self.private_key, self.public_key)

    def genkeys(self, key_size=KeySize.NORMAL, password=None):
        """
        Generates a Private and Public Key set and returns them in a tuple
        (private, public)

        """

        self.private_key = rsa.generate_private_key(
            # The public exponent of the new key. Usually one of the small
            # Fermat primes 3, 5, 17, 257, 65537. If in doubt you should use
            # 65537. See http://www.daemonology.net/blog/2009-06-11-\
            #  cryptographic-right-answers.html
            public_exponent=65537,
            key_size=key_size,
            backend=default_backend()
        )

        # Generate our Public Key
        self.public_key = self.private_key.public_key()

        # Store our password; this will be used when we save our content
        # via it's searialized value later on
        self.password = password

        # Returns a (RSAPrivateKey, RSAPublicKey)
        return (self.private_key, self.public_key)

    def private_pem(self, password=None):
        """
        Returns the private key PEM. This is a base64 format with delimiters.

        This function returns None if the private pem information could
        not be acquired.
        """
        if not isinstance(self.private_key, RSAPrivateKey):
            return None

        if password is None:
            password = self.password

        if password:
            return self.private_key.private_bytes(
               encoding=serialization.Encoding.PEM,
               format=serialization.PrivateFormat.PKCS8,
               encryption_algorithm=serialization
                       .BestAvailableEncryption(password)
            )

        return self.private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )

    def public_pem(self):
        """
        Returns the public key PEM. This is a base64 format with delimiters.

        This function returns None if the public pem information could
        not be acquired.

        """
        if not isinstance(self.public_key, RSAPublicKey):
            if not isinstance(self.private_key, RSAPrivateKey):
                return None
            self.public_key = self.private_key.public_key()

        return self.public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    def load(self, private_key, public_key=None, password=None,
             alg=None, mgf1=None):
        """
        Loads a private key from disk (and public if specified)
        """

        # Reset our internal variables
        self.private_key = None
        self.public_key = None
        self.password = None
        self.alg = None
        self.mgf1 = None

        if isinstance(private_key, RSAPrivateKey):
            # Easy-Peasy
            self.private_key = private_key

        elif isfile(private_key):
            try:
                # Attempt to load our file and create and RSAPrivateKey from it
                with open(private_key, "rb") as key_file:
                    self.private_key = serialization.load_pem_private_key(
                        key_file.read(),
                        password=password,
                        backend=default_backend()
                    )

            except ValueError as e:
                # specified password was inevitably bad
                logger.error(
                    'Cryptography / Could not load private key file %s' % (
                        private_key,
                    )
                )
                logger.debug(
                    'Cryptography / private key load exception: %s' % (
                        str(e),
                    )
                )
                return False

        elif isinstance(private_key, basestring):
            try:
                # Treat our content as a private key in a serialized form
                self.private_key = serialization.load_pem_private_key(
                    private_key,
                    password=password,
                    backend=default_backend()
                )

            except ValueError as e:
                # specified password was inevitably bad
                logger.error(
                    'Cryptography / Could not load specified private key.' % (
                        private_key,
                    )
                )
                logger.debug(
                    'Cryptography / private key load exception: %s' % (
                        str(e),
                    )
                )
                return False

        else:
            # We just don't support this feature
            logger.error(
                'Cryptography / Could not load private key (%s)' % (
                    str(private_key),
                )
            )
            return False

        # Store our specified password
        self.password = password

        if not public_key:
            # Generate our Public Key
            self.public_key = self.private_key.public_key()

            # We're done!
            return True

        # If a public key was specified, we need to verify it against our
        if isinstance(public_key, RSAPublicKey):
            # Easy-Peasy
            self.public_key = public_key

        elif isfile(public_key):
            # Attempt to load our file and create and RSAPrivateKey from it
            with open(public_key, "rb") as key_file:
                try:
                    self.public_key = serialization.load_pem_public_key(
                        key_file.read(),
                        backend=default_backend()
                    )
                except ValueError as e:
                    # We could not load the public key
                    logger.error(
                        'Cryptography / Public Key '
                        'from %s could not be loaded.' % (
                            public_key,
                        )
                    )
                    logger.debug(
                        'Cryptography / Public Key loading exception: %s' % (
                            str(e),
                        )
                    )
                    return False

        elif isinstance(public_key, basestring):
            # Treat our content as a public key in a serialized form
            try:
                self.public_key = serialization.load_pem_public_key(
                    public_key,
                    backend=default_backend()
                )
            except ValueError as e:
                # We could not load the public key
                logger.error(
                    'Cryptography / Public Key string could not be loaded.' % (
                    )
                )
                logger.debug(
                    'Cryptography / Public Key loading exception: %s' % (
                        str(e),
                    )
                )
                return False

        else:
            # We just don't support this feature
            raise AttributeError('Invalid public key specified.')

        return self.verify()

    def verify(self):
        """
        Verifies that our public/private keys are the correct match

        """

        # TODO

        # Verification Passed!
        return True

    def save(self, private_keyfile=DEFAULT_KEYFILE, public_keyfile=None,
             password=None):
        """
        Write our content to disk.

        It's during the serialization of content can we password protect
        it. So it is here we can specify a password. If one isn't specified
        then we use whatever was defined when we initalized the keyfile to
        begin with. It's perfectly fine to just not have a password at all
        either.

        """

        # First generate our keys if they're not already created
        if not isinstance(self.private_key, RSAPrivateKey):
            logger.error(
                'Cryptography / No keys defined to save. operation aborted.',
            )
            return False

        if not self.public_key:
            # Generate our Public Key
            self.public_key = self.private_key.public_key()

        try:
            with open(private_keyfile, "wb") as key_file:
                key_file.write(self.private_pem(password=password))

        except (OSError, IOError) as e:
            logger.error(
                'Cryptography / Private Key %s could not be written.' % (
                    key_file,
                )
            )
            logger.debug(
                'Cryptography / Private Key creation exception: %s' % (
                    str(e),
                )
            )
            return False

        if public_keyfile is None:
            # Safety
            public_keyfile = '%s.pub' % private_keyfile

        try:
            with open(public_keyfile, "wb") as key_file:
                key_file.write(self.public_pem())

        except (OSError, IOError) as e:
            logger.error(
                'Cryptography / Public Key %s could not be written.' % (
                    key_file,
                )
            )
            logger.debug(
                'Cryptography / Public Key creation exception: %s' % (
                    str(e),
                )
            )
            return False

        return True

    def chunk_size(self):
        """
        Returns the maximum chunk size given the configuration.

        """
        return CRYPTOGRAPHY_HASH_MAP[self.alg]['max_chunk']

    def __get_hash_func(self, htype):
        """
        Simply returns the hash value function

        """
        if htype not in CRYPTOGRAPHY_HASH_MAP:
            htype = CRYPTOGRAPHY_DEFAULT_HASH
        return CRYPTOGRAPHY_HASH_MAP[htype]['function']

    def __eq__(self, other):
        """
        Handles equality

        """
        return str(self) == str(self)

    def __str__(self):
        """
        Print the public pem information

        """
        return self.public_pem()

    def __unicode__(self):
        """
        Return the public pem information
        """
        return unicode(self.public_pem())
