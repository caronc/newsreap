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
        content = b'newsreap'

        # Let's encrypt our content
        encrypted = obj.encrypt(content)

        # Decrypt it now:
        decrypted = obj.decrypt(encrypted)

        # Test it out
        assert(content == decrypted)

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
        decrypted = obj.decrypt(
            encrypted,
            alg=HashType.SHA512,
            mgf1=HashType.SHA512,
        )

        # It will succeed again
        assert(content == decrypted)

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
        for alg in list(CRYPTOGRAPHY_HASH_MAP.keys()):
            for mgf1 in list(CRYPTOGRAPHY_HASH_MAP.keys()):

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
                        assert(chunk == decrypted)

    def test_decode_encode_keys(self):
        """
        test decoding and encoding of keys.
        """
        # Create our Cryptography Object
        obj = NNTPCryptography()

        # We can't load our keys if we've got none
        assert (obj.encode_public_key() is None)
        assert (obj.encode_private_key() is None)

        # Generate our keys
        (prv, pub) = obj.genkeys()

        encoded_pub = obj.encode_public_key()
        encoded_prv = obj.encode_private_key()
        assert (obj.encode_public_key() is not None)
        assert (obj.encode_private_key() is not None)

        # Decoding
        assert (obj.decode_private_key(None) is False)
        assert (obj.decode_public_key(None) is False)
        assert (obj.decode_private_key('') is False)
        assert (obj.decode_public_key('') is False)

        assert (obj.decode_private_key(encoded_prv) is True)
        assert (obj.decode_public_key(encoded_pub) is True)

    def test_equality_compares_public_keys(self):
        """
        Two objects are equal only when they hold the same public key
        """
        obj_a = NNTPCryptography()
        obj_a.genkeys()

        obj_b = NNTPCryptography()
        obj_b.genkeys()

        assert obj_a == obj_a
        assert obj_a != obj_b
        assert obj_a != 'not a key'

        # Loading the same key makes them equal again
        obj_c = NNTPCryptography()
        assert obj_c.load(obj_a.private_key) is True
        assert obj_c == obj_a
        assert hash(obj_c) == hash(obj_a)
        assert len(set([obj_a, obj_b, obj_c])) == 2

    def test_bad_private_key_text_is_rejected(self):
        """
        Text that isn't a private key fails to load (without crashing)
        """
        obj = NNTPCryptography()
        assert obj.load('not a private key') is False
        assert obj.load(b'not a private key either') is False

    def test_keys_given_when_created_are_loaded(self):
        """
        Keys passed to NNTPCryptography() are loaded straight away; a
        public key that belongs to another private key is refused
        """
        import pytest

        obj_a = NNTPCryptography()
        obj_a.genkeys()
        obj_b = NNTPCryptography()
        obj_b.genkeys()

        obj = NNTPCryptography(private_key=obj_a.private_pem())
        assert obj == obj_a
        assert obj.verify() is True

        obj = NNTPCryptography(
            private_key=obj_a.private_key, public_key=obj_a.public_pem())
        assert obj == obj_a

        with pytest.raises(ValueError):
            NNTPCryptography(
                private_key=obj_a.private_key, public_key=obj_b.public_key)

        obj = NNTPCryptography()
        assert obj.load(obj_a.private_key, obj_b.public_key) is False
        assert obj.keys() == (None, None)
        assert obj.verify() is False

    def test_loading_keeps_the_hashes_chosen(self):
        """
        Loading a key keeps the hashes the object was made with, so what
        depends on them (ie: chunk_size()) still works
        """
        obj_a = NNTPCryptography()
        obj_a.genkeys()

        obj = NNTPCryptography(alg=HashType.SHA512, mgf1=HashType.SHA512)
        assert obj.load(obj_a.private_key) is True
        assert (obj.alg, obj.mgf1) == (HashType.SHA512, HashType.SHA512)
        assert obj.chunk_size() == \
            CRYPTOGRAPHY_HASH_MAP[HashType.SHA512]['max_chunk']
        assert obj.decrypt(obj.encrypt(b'hello')) == b'hello'

        # Or the ones asked for
        assert obj.load(obj_a.private_key, alg=HashType.SHA256) is True
        assert obj.alg == HashType.SHA256


def test_a_private_key_is_saved_for_our_eyes_only(tmp_path, monkeypatch):
    """
    A private key file can only be read by us (even one that was there
    before), ~ is our home, and a folder that can't be written is an error
    rather than a crash
    """
    import os
    import stat

    monkeypatch.setenv('HOME', str(tmp_path))
    old = tmp_path / 'key.pem'
    old.write_bytes(b'old')
    old.chmod(0o644)

    crypto = NNTPCryptography()
    crypto.genkeys()
    assert crypto.save('~/key.pem') is True
    assert stat.S_IMODE(os.stat(old).st_mode) == 0o600
    assert (tmp_path / 'key.pem.pub').is_file()
    assert crypto.save(str(tmp_path / 'missing' / 'key.pem')) is False


def test_keys_that_cant_be_read_are_refused(tmp_path):
    """
    A password given for a key that needs none, or an encoded key that
    isn't one, is refused instead of crashing
    """
    crypto = NNTPCryptography()
    prv, _ = crypto.genkeys()
    pem = crypto.private_pem()

    assert NNTPCryptography().load(private_key=pem, password='x') is False
    assert NNTPCryptography().decode_private_key('bm90IGd6aXA=') is False
    assert NNTPCryptography().decode_public_key('bm90IGd6aXA=') is False
    assert NNTPCryptography().decode_public_key('!!') is False
