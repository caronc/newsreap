# -*- encoding: utf-8 -*-
# A base testing class/library to test the NNTP Cryptography class
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

import sys
if 'threading' in sys.modules:
    #  gevent patching since pytests import
    #  the sys library before we do.
    del sys.modules['threading']

import gevent.monkey
gevent.monkey.patch_all()

# Import threading after monkey patching
# see: http://stackoverflow.com/questions/8774958/\
#        keyerror-in-module-threading-after-a-successful-py-test-run
import threading

from os.path import join
from os.path import isfile
from os.path import dirname
from os.path import abspath

from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

try:
    from tests.TestBase import TestBase

except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

from newsreap.NNTPCryptography import NNTPCryptography
from newsreap.NNTPCryptography import CRYPTOGRAPHY_HASH_MAP
from newsreap.NNTPCryptography import HashType
from newsreap.NNTPBinaryContent import NNTPBinaryContent
from newsreap.codecs.CodecUU import CodecUU


class NNTPCryptography_Test(TestBase):
    """
    Test the NNTPCryptography class
    """

    def test_key_creation(self):
        """
        Test Key Generation

        """

        # Create our Cryptography Object
        obj = NNTPCryptography()

        # We can't save if we haven't created keys yet
        assert(obj.save() is False)

        # Generate our keys
        (prv, pub) = obj.genkeys()

        # Check that they're stored
        assert (prv, pub) == obj.keys()

        # Our private Key Location
        private_keyfile = join(
            self.tmp_dir,
            'NNTPCryptography.test_key_creation-id_rsa'
        )

        # Our private Key Location
        public_keyfile = join(
            self.tmp_dir,
            'NNTPCryptography.test_key_creation-id_rsa.pub'
        )

        # Simple testing
        assert(isinstance(prv, RSAPrivateKey))
        assert(isinstance(pub, RSAPublicKey))

        # Just verify the files don't already exist (to avoid
        # skewing our testing)
        assert(isfile(private_keyfile) is False)
        assert(isfile(public_keyfile) is False)

        # Let's create them now
        assert(obj.save(private_keyfile, public_keyfile) is True)
        assert(isfile(private_keyfile) is True)
        assert(isfile(public_keyfile) is True)

        # Create another Cryptography Object
        obj2 = NNTPCryptography()

        # Load our content
        assert(obj2.load(private_keyfile, public_keyfile) is True)

        # Check that they're still what we had kept with us from
        # earlier
        assert(str(obj) == str(obj2))

    def test_key_creation_with_password(self):
        """
        Test Key Generation with password

        """

        # Set ourselves a password
        password = 'l2g-nuxref.com'

        # Create our Cryptography Object
        obj = NNTPCryptography()

        # We can't save if we haven't created keys yet
        assert(obj.save() is False)

        # Generate our keys (with our password)
        (prv, pub) = obj.genkeys(password=password)

        # Check that they're stored
        assert((prv, pub) == obj.keys())

        # Our private Key Location
        private_keyfile = join(
            self.tmp_dir,
            'NNTPCryptography.test_key_creation_withpw-id_rsa'
        )

        # Our private Key Location
        public_keyfile = join(
            self.tmp_dir,
            'NNTPCryptography.test_key_creation_withpw-id_rsa.pub'
        )

        # Simple testing
        assert(isinstance(prv, RSAPrivateKey))
        assert(isinstance(pub, RSAPublicKey))

        # Just verify the files don't already exist (to avoid
        # skewing our testing)
        assert(isfile(private_keyfile) is False)
        assert(isfile(public_keyfile) is False)

        # Let's create them now
        assert(obj.save(private_keyfile, public_keyfile) is True)
        assert(isfile(private_keyfile) is True)
        assert(isfile(public_keyfile) is True)

        # Create another Cryptography Object
        obj2 = NNTPCryptography()

        # Our content can't load with a bad password
        assert(obj2.load(private_keyfile, public_keyfile, 'bad') is False)

        # Load our content
        assert(obj2.load(private_keyfile, public_keyfile, password) is True)

        # Our data should still be the same
        assert(str(obj) == str(obj2))

    def test_encrytion(self):
        """
        Test te encryption and decryption of data

        """

        # Create our Cryptography Object
        obj = NNTPCryptography()

        # We can't save if we haven't created keys yet
        assert(obj.save() is False)

        # Generate our keys
        (prv, pub) = obj.genkeys()

        # Check that they're stored
        assert (prv, pub) == obj.keys()

        # Test small content first
        content = 'newsreap'

        # Let's encrypt our content
        encrypted = obj.encrypt(content)

        # Decrypt it now:
        decrypted = obj.decrypt(encrypted)

        # Test it out
        assert(str(content) == str(decrypted))

        # Note that the Hash value is important as encryption
        # and decryption will fail otherwise
        encrypted = obj.encrypt(
            content,
            alg=HashType.SHA512,
            mgf1=HashType.SHA512,
        )
        # Returns None in all cases below because either the alg
        assert(obj.decrypt(encrypted,
            alg=HashType.SHA256, mgf1=HashType.SHA512) is None)
        assert(obj.decrypt(encrypted,
            alg=HashType.SHA512, mgf1=HashType.SHA256) is None)
        assert(obj.decrypt(encrypted,
            alg=HashType.SHA384, mgf1=HashType.SHA1) is None)

        # However if we use the right hash
        decrypted = obj.decrypt(encrypted,
            alg=HashType.SHA512, mgf1=HashType.SHA512)

        # It will succeed again
        assert(str(content) == str(decrypted))

        # Our private Key Location
        tmp_file = join(
            self.tmp_dir,
            'NNTPCryptography.test_encrytion.tmp'
        )

        # Let's create a slightly larger file; one we'll need to process
        # in chunks
        assert(self.touch(tmp_file, size='128KB', random=True))

        # We'll yEnc the file since we can't deal with binary
        # Create an NNTPContent Object
        content = NNTPBinaryContent(tmp_file)

        # We need to iterate over all of our possible compression types
        # so that we can test that the chunk sizes are valid in all cases
        # This big O(n2) will test all of our supported operations
        for alg in CRYPTOGRAPHY_HASH_MAP.keys():
            for mgf1 in CRYPTOGRAPHY_HASH_MAP.keys():

                # Create our Cryptography Object
                obj = NNTPCryptography(alg=alg, mgf1=mgf1)

                # We can't save if we haven't created keys yet
                assert(obj.save() is False)

                # Generate our keys
                (prv, pub) = obj.genkeys()

                encoder = CodecUU(work_dir=self.test_dir)
                response = encoder.encode(content)

                # We should have gotten an ASCII Content Object
                assert(len(response) > 0)

                with open(response.filepath, 'rb') as f:
                    # Any chunk size higher then 190 doesn't seem to work
                    for chunk in iter(lambda: f.read(obj.chunk_size()), b''):
                        # Let's encrypt our content
                        encrypted = obj.encrypt(chunk)
                        assert(encrypted is not None)

                        # Decrypt it now:
                        decrypted = obj.decrypt(encrypted)

                        # Test it out
                        assert(str(chunk) == str(decrypted))
