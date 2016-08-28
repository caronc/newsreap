# -*- encoding: utf-8 -*-
#
# Test the UU Codec
#
# Copyright (C) 2015-2016 Chris Caron <lead2gold@gmail.com>
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

from os.path import join
from os.path import dirname
from os.path import isfile
from os.path import abspath

try:
    from tests.TestBase import TestBase

except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

from newsreap.codecs.CodecUU import CodecUU
from newsreap.NNTPBinaryContent import NNTPBinaryContent
from newsreap.NNTPAsciiContent import NNTPAsciiContent
from newsreap.NNTPArticle import NNTPArticle


class CodecUU_Test(TestBase):

    def test_uu_bad_headers(self):
        """
        Make sure we fail on bad headers
        """
        # Initialize Codec
        ud = CodecUU(work_dir=self.tmp_dir, out_dir=self.out_dir)

        # Make sure we don't pick up on yenc content
        assert ud.detect(
            "=ybegin line=1024 size=12345",
        ) == None

        yenc_meta = ud.detect(
            "begin BDP FILENAME",
        )
        # The BDP (Bad Permission) assumes to be part of the filename
        assert len(yenc_meta) == 2
        assert yenc_meta['key'] == 'begin'
        assert yenc_meta['name'] == 'BDP FILENAME'


    def test_uu_headers(self):
        """
        Test that we can pick up the uu headers correctly
        """
        # Initialize Codec
        ud = CodecUU(work_dir=self.tmp_dir, out_dir=self.out_dir)
        uu_meta = ud.detect("begin 775 mybinary.dat")
        assert uu_meta is not None
        assert len(uu_meta) == 3
        assert uu_meta['key'] == 'begin'
        assert uu_meta['perm'] == 0775
        assert uu_meta['name'] == 'mybinary.dat'

        # Whitespace has no bearing
        uu_meta = ud.detect("  begin    775   mybinary.dat   ")
        assert uu_meta is not None
        assert len(uu_meta) == 3
        assert uu_meta['key'] == 'begin'
        assert uu_meta['perm'] == 0775
        assert uu_meta['name'] == 'mybinary.dat'

        # End fails because we're processing content relative
        # to the decoder (which is expecing a begin at this time)
        uu_meta = ud.detect("  end ")
        assert uu_meta is None

        # end also doesn't care about whitespace
        uu_meta = ud.detect("  end ", relative=False)
        assert uu_meta is not None
        assert len(uu_meta) == 1
        assert uu_meta['key'] == 'end'

        # The '`' tilda character is used on the line
        # prior to the 'end' keyword.  we ignore this
        # entry in most cases, but treat it as a key
        # none the less.
        uu_meta = ud.detect("`", relative=False)
        assert uu_meta is not None
        assert len(uu_meta) == 1
        assert uu_meta['key'] == '`'


    def test_decoding_uuenc_single_part(self):
        """
        Decodes a single UUEncoded message
        """
        # Input File
        encoded_filepath = join(self.var_dir, 'uuencoded.tax.jpg.msg')
        assert isfile(encoded_filepath)

        # Compare File
        decoded_filepath = join(self.var_dir, 'uudecoded.tax.jpg')
        assert isfile(decoded_filepath)

        # Initialize Codec
        ud_py = CodecUU()

        # Read data and decode it
        with open(encoded_filepath, 'r') as fd_in:
            article = ud_py.decode(fd_in)

        # our content should be valid
        assert isinstance(article, NNTPBinaryContent)

        # Verify the actual article itself reports itself
        # as being okay (structurally)
        assert article.is_valid() is True

        with open(decoded_filepath, 'r') as fd_in:
            decoded = fd_in.read()

        # Compare our processed content with the expected results
        assert decoded == article.getvalue()

    def test_uu_encoding(self):
        """
        Test the encoding of data; this is nessisary prior to a post
        """

        # First we take a binary file
        binary_filepath = join(self.var_dir, 'joystick.jpg')
        assert isfile(binary_filepath)

        # Initialize Codec
        encoder = CodecUU()

        content = encoder.encode(binary_filepath)

        # We should have gotten an ASCII Content Object
        assert isinstance(content, NNTPAsciiContent) is True

        # We should actually have content associated with out data
        assert len(content) > 0


    def test_yenc_v1_3_NNTPContent_encode(self):
        """
        Test the encoding of data; this is nessisary prior to a post
        """

        # First we take a binary file
        binary_filepath = join(self.var_dir, 'joystick.jpg')
        assert isfile(binary_filepath)

        # Initialize Codec
        encoder = CodecUU()

        # Create an NNTPContent Object
        content = NNTPBinaryContent(binary_filepath)

        # Encode our content by object
        new_content_a = content.encode(encoder)

        # We should have gotten an ASCII Content Object
        assert isinstance(new_content_a, NNTPAsciiContent) is True

        # We should actually have content associated with out data
        assert len(new_content_a) > 0

        # Encode our content by type
        new_content_b = content.encode(CodecUU)

        # We should have gotten an ASCII Content Object
        assert isinstance(new_content_b, NNTPAsciiContent) is True

        # We should actually have content associated with out data
        assert len(new_content_b) > 0

        # Our content should be the same when it was generated by both
        # methods
        assert new_content_a.md5() == new_content_b.md5()

        # Chain our encodings
        new_content = content.encode([CodecUU, CodecUU()])

        # We should have gotten an ASCII Content Object
        assert isinstance(new_content, NNTPAsciiContent) is True

        # We should actually have content associated with out data
        assert len(new_content) > 0

    def test_NNTPArticle_UU_encode(self):
        """
        Test the encoding of data; this is nessisary prior to a post
        """

        # First we take a binary file
        binary_filepath = join(self.var_dir, 'joystick.jpg')
        assert isfile(binary_filepath)

        # Initialize Codec
        encoder = CodecUU()

        # Create an NNTPArticle Object
        article = NNTPArticle()
        # Add our file
        article.add(binary_filepath)

        # Encode our article by object
        new_article_a = article.encode(encoder)

        # We should have gotten an NNTPArticle Object
        assert isinstance(new_article_a, NNTPArticle) is True

        # We should actually have article associated with out data
        assert len(new_article_a) > 0

        # Encode our article by type
        new_article_b = article.encode(CodecUU)

        # We should have gotten an NNTPArticle Object
        assert isinstance(new_article_b, NNTPArticle) is True

        # We should actually have article associated with out data
        assert len(new_article_b) > 0

        # Our article should be the same when it was generated by both
        # methods
        assert new_article_a[0].md5() == new_article_b[0].md5()

        # Chain our encodings
        new_article = article.encode([CodecUU, CodecUU()])

        # We should have gotten an ASCII Content Object
        assert isinstance(new_article, NNTPArticle) is True

        # We should actually have article associated with out data
        assert len(new_article) > 0
