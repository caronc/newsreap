# -*- coding: utf-8 -*-
#
# Used for NNTP Encoding/Decoding of Ecrypted Content
#
# Copyright (C) 2015 Chris Caron <lead2gold@gmail.com>
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

# OpenSSL will only write 16K at a time
SSL_WRITE_BLOCKSIZE = 16384

class NNTPCryptography(object):
    def __init__(self, public_key=None, private_key=None):
        """
        Initialize NNTP Cryptography class
        """

        # STUB: TODO Open files and keep them ready to use in
        #       memory

    def get_public_key(self, share_key):
        """
        Based on spotnab, this is the zipped version of the key
        with base64 applied to it.  We read in this string
        and return the key.

        """
        # STUB: TODO don't set global public key and/or private
        #       key; but return the same string that can be
        #       passed into the decrypt() function

    def get_share_key(self, public_key=None):
        """
        Based on spotnab, this is the zipped version of the key
        with base64 applied to it.  We read in a key and return
        the shareable key

        """
        # STUB: TODO use the loaded public key if none set
        #       return the string you can share so that others
        #       can decrypt the content

    def encrypt(self, payload, private_key=None):
        """
        Encrypts the payload using keys and returns the
        encrypted key
        """
        # STUB: TODO
        pass


    def decrypt(self, payload, public_key=None):
        """
        Decrypts the payload using keys and returns the
        encrypted key
        """
        # STUB: TODO
        pass


    def genkeys(self):
        """
        Generates a Public/Private Key set
        """
        # STUB: TODO
        pass
