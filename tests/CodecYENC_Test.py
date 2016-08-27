# -*- encoding: utf-8 -*-
#
# Test the yEnc Codec
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

from io import BytesIO

try:
    from tests.TestBase import TestBase

except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

from newsreap.codecs.CodecYenc import CodecYenc
from newsreap.NNTPBinaryContent import NNTPBinaryContent
from newsreap.NNTPAsciiContent import NNTPAsciiContent
from newsreap.NNTPArticle import NNTPArticle


class CodecYENC_Test(TestBase):
    """
    A Unit Testing Class for yENC Files
    """

    def test_yenc_v1_1_headers(self):
        """
        Test that we can pick up the yenc headers correctly

        yenc Style v1.1
        """
        # Initialize Codec
        yd = CodecYenc()

        yenc_meta = yd.detect(
            "=ybegin part=1 line=128 size=500000 name=mybinary.dat",
        )

        assert yenc_meta is not None
        assert len(yenc_meta) == 5
        assert yenc_meta['key'] == 'begin'
        assert yenc_meta['part'] == 1
        assert yenc_meta['line'] == 128
        assert yenc_meta['size'] == 500000
        assert yenc_meta['name'] == 'mybinary.dat'

    def test_yenc_v1_2_headers(self):
        """
        Test that we can pick up the yenc headers correctly

        yenc Style v1.2
        """
        # Initialize Codec
        yd = CodecYenc()

        yenc_meta = yd.detect(
            "=ybegin line=128 size=123456 name=mybinary.dat",
        )
        assert yenc_meta is not None
        assert len(yenc_meta) == 4
        assert yenc_meta['key'] == 'begin'
        assert yenc_meta['line'] == 128
        assert yenc_meta['size'] == 123456
        assert yenc_meta['name'] == 'mybinary.dat'

        # support version types after keyword
        yenc_meta = yd.detect(
            "=ybegin2 line=128 size=123456 name=mybinary.dat",
        )
        assert yenc_meta is not None
        assert len(yenc_meta) == 4
        assert yenc_meta['key'] == 'begin'
        assert yenc_meta['line'] == 128
        assert yenc_meta['size'] == 123456
        assert yenc_meta['name'] == 'mybinary.dat'

        yenc_meta = yd.detect("=yend size=123456")
        # we aren't expecting an end; so this should fail
        assert yenc_meta is None

        # We turn off the relative flag and it works
        yenc_meta = yd.detect(
            "=yend size=123456",
            relative=False,
        )
        assert yenc_meta is not None
        assert len(yenc_meta) == 2
        assert yenc_meta['key'] == 'end'
        assert yenc_meta['size'] == 123456

        yenc_meta = yd.detect(
            "=yend size=123456 crc32=abcdef12",
            relative=False,
        )
        assert yenc_meta is not None
        assert len(yenc_meta) == 3
        assert yenc_meta['key'] == 'end'
        assert yenc_meta['size'] == 123456
        assert yenc_meta['crc32'] == 'abcdef12'

        yenc_meta = yd.detect(
            "=yend size=123456 pcrc32=adkfa987 crc32=abcdef12",
            relative=False,
        )
        assert yenc_meta is not None
        assert len(yenc_meta) == 4
        assert yenc_meta['key'] == 'end'
        assert yenc_meta['size'] == 123456
        assert yenc_meta['crc32'] == 'abcdef12'
        assert yenc_meta['pcrc32'] == 'adkfa987'

        yenc_meta = yd.detect("=ypart begin=1 end=100000")
        # we aren't expecting a ypart so this fails
        assert yenc_meta is None

        # however if we search without checking anything
        # relative to what we're expecting, it will work
        yenc_meta = yd.detect(
            "=ypart begin=1 end=100000",
            relative=False,
        )
        assert yenc_meta is not None
        assert len(yenc_meta) == 3
        assert yenc_meta['key'] == 'part'
        assert yenc_meta['begin'] == 1
        assert yenc_meta['end'] == 100000

        yenc_meta = yd.detect(
            "=yend size=100000 part=1 pcrc32=abcdef12",
        )
        assert yenc_meta is None
        yenc_meta = yd.detect(
            "=yend size=100000 part=1 pcrc32=abcdef12",
            relative=False,
        )
        assert yenc_meta is not None
        assert len(yenc_meta) == 4
        assert yenc_meta['key'] == 'end'
        assert yenc_meta['part'] == 1
        assert yenc_meta['pcrc32'] == 'abcdef12'

    def test_yenc_bad_headers(self):
        """
        Make sure we fail on bad headers
        """
        # Initialize Codec
        yd = CodecYenc()

        assert yd.detect(
            "=ybegin line=NotDigit size=BAD",
        ) == None

        # Make sure we don't pick up uuencoded content
        assert yd.detect(
            "begin 644 a.wonderful.uuencoded.file",
        ) == None

        # Bad ordering
        assert yd.detect(
            "=ybegin name=",
        ) == None

    def test_decoding_yenc_single_part(self):
        """
        This test was generated after visiting http://www.yenc.org and finding
        the examples they provide on their site.

            Downloaded the following zip file:
                http://www.yenc.org/yenc1.zip

            Then extracting it revealed 2 files:
                - 00000005.ntx
                    This is the yenc file as it would have been seen after
                    being downloaded from the NNTP server

                - testfile.txt
                    This is what the contents of the file should look like
                    after being decoded. This is what we use to test the file
                    against.
        """

        # A simple test for ensuring that the yenc
        # library exists; otherwise we want this test
        # to fail; the below line will handle this for
        # us; we'll let the test fail on an import error
        import yenc

        # Input File
        encoded_filepath = join(self.var_dir, '00000005.ntx')
        assert isfile(encoded_filepath)

        # Compare File
        decoded_filepath = join(self.var_dir, 'testfile.txt')
        assert isfile(decoded_filepath)

        # Python Solution
        fd_py = BytesIO()

        # C Solution
        fd_c = BytesIO()

        # Initialize Codec
        decoder = CodecYenc()

        # Force to operate in python (manual/slow) mode
        CodecYenc.FAST_YENC_SUPPORT = False
        with open(encoded_filepath, 'r') as fd_in:
            content_py = decoder.decode(fd_in)

        # our content should be valid
        assert isinstance(content_py, NNTPBinaryContent)

        # Force to operate with the C extension yenc
        # This require the extensions to be installed
        # on the system
        CodecYenc.FAST_YENC_SUPPORT = True
        with open(encoded_filepath, 'r') as fd_in:
            content_c = decoder.decode(fd_in)

        # our content should be valid
        assert isinstance(content_c, NNTPBinaryContent)

        # Verify the actual content itself reports itself
        # as being okay (structurally)
        assert content_py.is_valid() is True
        assert content_c.is_valid() is True

        # Confirm that our output from our python implimentation
        # matches that of our yenc C version.
        assert fd_py.tell() == fd_c.tell()

        with open(decoded_filepath, 'r') as fd_in:
            decoded = fd_in.read()

        # Compare our processed content with the expected results
        assert decoded == content_py.getvalue()
        assert decoded == content_c.getvalue()

    def test_decoding_yenc_multi_part(self):
        """
        This test was generated after visiting http://www.yenc.org and finding
        the examples they provide on their site.

            Downloaded the following zip file:
                http://www.yenc.org/yenc2.zip

            Then extracting it revealed 3 files:
                - 00000020.ntx
                    This is the yenc file as it would have been seen after
                    being downloaded from the NNTP server (part 1 of 2)

                - 00000021.ntx
                    This is the yenc file as it would have been seen after
                    being downloaded from the NNTP server (part 2 of 2)

                - joystick.jpg
                    This is what the contents of the file should look like
                    after being decoded (and assembled). This is what we use
                    to test the file against.
        """

        # A simple test for ensuring that the yenc
        # library exists; otherwise we want this test
        # to fail; the below line will handle this for
        # us; we'll let the test fail on an import error
        import yenc

        # Input File
        encoded_filepath_1 = join(self.var_dir, '00000020.ntx')
        encoded_filepath_2 = join(self.var_dir, '00000021.ntx')

        assert isfile(encoded_filepath_1)
        assert isfile(encoded_filepath_2)

        # Compare File
        decoded_filepath = join(self.var_dir, 'joystick.jpg')
        assert isfile(decoded_filepath)

        # Python Solution
        fd1_py = BytesIO()
        fd2_py = BytesIO()

        # C Solution
        fd1_c = BytesIO()
        fd2_c = BytesIO()

        # Initialize Codec
        decoder = CodecYenc()

        contents_py = []
        contents_c = []

        # Force to operate in python (manual/slow) mode
        CodecYenc.FAST_YENC_SUPPORT = False
        with open(encoded_filepath_1, 'r') as fd_in:
            contents_py.append(decoder.decode(fd_in))
        with open(encoded_filepath_2, 'r') as fd_in:
            contents_py.append(decoder.decode(fd_in))

        for x in contents_py:
            # Verify our data is good
            assert x.is_valid() is True

        # Force to operate with the C extension yenc
        # This require the extensions to be installed
        # on the system
        CodecYenc.FAST_YENC_SUPPORT = True
        with open(encoded_filepath_1, 'r') as fd_in:
            contents_c.append(decoder.decode(fd_in))
        with open(encoded_filepath_2, 'r') as fd_in:
            contents_c.append(decoder.decode(fd_in))

        for x in contents_c:
            # Verify our data is good
            assert x.is_valid() is True

        # Confirm that our output from our python implimentation
        # matches that of our yenc C version.
        assert fd1_py.tell() == fd1_c.tell()
        assert fd2_py.tell() == fd2_c.tell()

        with open(decoded_filepath, 'r') as fd_in:
            decoded = fd_in.read()

        # Assemble (TODO)
        contents_py.sort()
        contents_c.sort()

        content_py = NNTPBinaryContent(
            filepath=contents_py[0].filename,
            save_dir=self.out_dir,
        )
        content_c = NNTPBinaryContent(
            filepath=contents_c[0].filename,
            save_dir=self.out_dir,
        )

        # append() takes a list or another NNTPContent
        # and appends it's content to the end of the content
        content_py.append(contents_py)
        content_c.append(contents_py)

        assert len(content_py) == len(decoded)
        assert len(content_c) == len(decoded)

        # Compare our processed content with the expected results
        assert content_py.getvalue() == decoded
        assert content_c.getvalue() == decoded

    def test_yenc_v1_3_encoding(self):
        """
        Test the encoding of data; this is nessisary prior to a post
        """

        # A simple test for ensuring that the yenc
        # library exists; otherwise we want this test
        # to fail; the below line will handle this for
        # us; we'll let the test fail on an import error
        import yenc

        # First we take a binary file
        binary_filepath = join(self.var_dir, 'joystick.jpg')
        assert isfile(binary_filepath)

        # Initialize Codec
        encoder_c = CodecYenc()

        content_c = encoder_c.encode(binary_filepath)

        # We should have gotten an ASCII Content Object
        assert isinstance(content_c, NNTPAsciiContent) is True

        # We should actually have content associated with out data
        assert len(content_c) > 0

        # Now we should be able to perform the same tasks again without
        # using the C libraries
        CodecYenc.FAST_YENC_SUPPORT = False

        # Initialize Codec
        encoder_py = CodecYenc()

        content_py = encoder_py.encode(binary_filepath)

        # We should have gotten an ASCII Content Object
        assert isinstance(content_py, NNTPAsciiContent) is True

        # We should actually have content associated with out data
        assert len(content_py) > 0

        # We generate the same output
        assert content_py.crc32() == content_c.crc32()

    def test_yenc_v1_3_NNTPContent_encode(self):
        """
        Test the encoding of data; this is nessisary prior to a post
        """

        # A simple test for ensuring that the yenc
        # library exists; otherwise we want this test
        # to fail; the below line will handle this for
        # us; we'll let the test fail on an import error
        import yenc

        # First we take a binary file
        binary_filepath = join(self.var_dir, 'joystick.jpg')
        assert isfile(binary_filepath)

        # Initialize Codec
        encoder = CodecYenc()

        # Create an NNTPContent Object
        content = NNTPBinaryContent(binary_filepath)

        # Encode our content by object
        new_content_a = content.encode(encoder)

        # We should have gotten an ASCII Content Object
        assert isinstance(new_content_a, NNTPAsciiContent) is True

        # We should actually have content associated with out data
        assert len(new_content_a) > 0

        # Encode our content by type
        new_content_b = content.encode(CodecYenc)

        # We should have gotten an ASCII Content Object
        assert isinstance(new_content_b, NNTPAsciiContent) is True

        # We should actually have content associated with out data
        assert len(new_content_b) > 0

        # Our content should be the same when it was generated by both
        # methods
        assert new_content_a.md5() == new_content_b.md5()

        # Chain our encodings
        new_content = content.encode([CodecYenc, CodecYenc()])

        # We should have gotten an ASCII Content Object
        assert isinstance(new_content, NNTPAsciiContent) is True

        # We should actually have content associated with out data
        assert len(new_content) > 0

    def test_yenc_v1_3_NNTPArticle_encode(self):
        """
        Test the encoding of data; this is nessisary prior to a post
        """

        # A simple test for ensuring that the yenc
        # library exists; otherwise we want this test
        # to fail; the below line will handle this for
        # us; we'll let the test fail on an import error
        import yenc

        # First we take a binary file
        binary_filepath = join(self.var_dir, 'joystick.jpg')
        assert isfile(binary_filepath)

        # Initialize Codec
        encoder = CodecYenc()

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
        new_article_b = article.encode(CodecYenc)

        # We should have gotten an NNTPArticle Object
        assert isinstance(new_article_b, NNTPArticle) is True

        # We should actually have article associated with out data
        assert len(new_article_b) > 0

        # Our article should be the same when it was generated by both
        # methods
        assert new_article_a[0].md5() == new_article_b[0].md5()

        # Chain our encodings
        new_article = article.encode([CodecYenc, CodecYenc()])

        # We should have gotten an ASCII Content Object
        assert isinstance(new_article, NNTPArticle) is True

        # We should actually have article associated with out data
        assert len(new_article) > 0
