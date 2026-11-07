# Test the yEnc Codec
#
# Copyright (C) 2015-2017 Chris Caron <lead2gold@gmail.com>
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
from os.path import dirname
from os.path import isfile
from os.path import abspath


try:
    from tests.TestBase import TestBase

except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

from newsreap.codecs.CodecYenc import CodecYenc
from newsreap.NNTPBinaryContent import NNTPBinaryContent
from newsreap.NNTPAsciiContent import NNTPAsciiContent
from newsreap.NNTPArticle import NNTPArticle
from newsreap.codecs.CodecYenc import yenc_encode
from newsreap.codecs.CodecYenc import yenc_decode
import newsreap.codecs.CodecYenc as CodecYencModule
from io import BytesIO


class CodecYENC_Test(TestBase):
    """
    A Unit Testing Class for yENC Files
    """

    def test_yenc_v1_1_headers(self):
        """
        Test that we can pick up the yEnc v1.1 headers correctly

        yenc Style v1.1
        """
        # Initialize Codec
        yd = CodecYenc(work_dir=self.test_dir)

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
        Test that we can pick up the yEnc v1.2 headers correctly

        yenc Style v1.2
        """
        # Initialize Codec
        yd = CodecYenc(work_dir=self.test_dir)

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
        yd = CodecYenc(work_dir=self.test_dir)

        assert yd.detect(
            "=ybegin line=NotDigit size=BAD",
        ) is None

        # Make sure we don't pick up uuencoded content
        assert yd.detect(
            "begin 644 a.wonderful.uuencoded.file",
        ) is None

        # Bad ordering
        assert yd.detect(
            "=ybegin name=",
        ) is None

    def test_decoding_yenc_single_part(self):
        """
        Test decoding of a yEnc single part

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

        # Input File
        encoded_filepath = join(self.var_dir, '00000005.ntx')
        assert isfile(encoded_filepath)

        # Compare File
        decoded_filepath = join(self.var_dir, 'testfile.txt')
        assert isfile(decoded_filepath)

        # Initialize Codec
        decoder = CodecYenc(work_dir=self.test_dir)

        with open(encoded_filepath, 'rb') as fd_in:
            content = decoder.decode(fd_in)

        # our content should be valid
        assert isinstance(content, NNTPBinaryContent)

        # The same codec can be used to decode the content again
        with open(encoded_filepath, 'rb') as fd_in:
            content_again = decoder.decode(fd_in)

        # our content should be valid
        assert isinstance(content_again, NNTPBinaryContent)

        # Verify the actual content itself reports itself
        # as being okay (structurally)
        assert content.is_valid() is True
        assert content_again.is_valid() is True

        with open(decoded_filepath, 'rb') as fd_in:
            decoded = fd_in.read()

        # Compare our processed content with the expected results
        assert decoded == content.getvalue()
        assert decoded == content_again.getvalue()

    def test_decoding_yenc_multi_part(self):
        """
        Test decoding of a yEnc multi-part

        This test was generated after visiting http://www.yenc.org and finding
        the examples they provide on their site.

            Downloaded the following zip file:
                http://www.yenc.org/yenc2.zip

            Then extracting it revealed 3 files:
                - 00000020.ntx
                    This is the yEnc file as it would have been seen after
                    being downloaded from the NNTP server (part 1 of 2)

                - 00000021.ntx
                    This is the yEnc file as it would have been seen after
                    being downloaded from the NNTP server (part 2 of 2)

                - joystick.jpg
                    This is what the contents of the file should look like
                    after being decoded (and assembled). This is what we use
                    to test the file against.
        """

        # Input File
        encoded_filepath_1 = join(self.var_dir, '00000020.ntx')
        encoded_filepath_2 = join(self.var_dir, '00000021.ntx')

        assert isfile(encoded_filepath_1)
        assert isfile(encoded_filepath_2)

        # Compare File
        decoded_filepath = join(self.var_dir, 'joystick.jpg')
        assert isfile(decoded_filepath)

        # Initialize Codec
        decoder = CodecYenc(work_dir=self.test_dir)

        contents = []
        with open(encoded_filepath_1, 'rb') as fd_in:
            contents.append(decoder.decode(fd_in))
        with open(encoded_filepath_2, 'rb') as fd_in:
            contents.append(decoder.decode(fd_in))

        for x in contents:
            # Verify our data is good
            assert x.is_valid() is True

        with open(decoded_filepath, 'rb') as fd_in:
            decoded = fd_in.read()

        # Assemble (TODO)
        contents.sort()

        content = NNTPBinaryContent(
            filepath=contents[0].filename,
            save_dir=self.out_dir,
        )

        # append() takes a list or another NNTPContent
        # and appends it's content to the end of the content
        content.append(contents)

        assert len(content) == len(decoded)

        # Compare our processed content with the expected results
        assert content.getvalue() == decoded

    def test_yenc_v1_3_encoding(self):
        """
        Test the yEnc (v1.3) encoding of data (via codec)

        Test the encoding of data; this is nessisary prior to a post
        """

        # First we take a binary file
        binary_filepath = join(self.var_dir, 'joystick.jpg')
        assert isfile(binary_filepath)

        # Initialize Codec
        encoder = CodecYenc(work_dir=self.test_dir)

        content = encoder.encode(binary_filepath)

        # We should have gotten an ASCII Content Object
        assert isinstance(content, NNTPAsciiContent) is True

        # We should actually have content associated with out data
        assert len(content) > 0

        # A second codec should produce the very same output
        encoder_again = CodecYenc(work_dir=self.test_dir)

        content_again = encoder_again.encode(binary_filepath)

        # We should have gotten an ASCII Content Object
        assert isinstance(content_again, NNTPAsciiContent) is True

        # We should actually have content associated with out data
        assert len(content_again) > 0

        # We generate the same output
        assert content_again.crc32() == content.crc32()

    def test_yenc_v1_3_NNTPContent_encode(self):
        """
        Test the yEnc (v1.3) encoding of data (via NNTPContent)

        this is nessisary prior to a post
        """

        # First we take a binary file
        binary_filepath = join(self.var_dir, 'joystick.jpg')
        assert isfile(binary_filepath)

        # Initialize Codec
        encoder = CodecYenc(work_dir=self.test_dir)

        # Create an NNTPContent Object
        content = NNTPBinaryContent(binary_filepath, work_dir=self.test_dir)

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
        new_content = content.encode(
            [CodecYenc, CodecYenc(work_dir=self.test_dir)],
        )

        # We should have gotten an ASCII Content Object
        assert isinstance(new_content, NNTPAsciiContent) is True

        # We should actually have content associated with out data
        assert len(new_content) > 0

    def test_yenc_v1_3_NNTPArticle_encode_01(self):
        """
        Test the encoding of data; this is nessisary prior to a post
        """

        # First we take a binary file
        binary_filepath = join(self.var_dir, 'joystick.jpg')
        assert isfile(binary_filepath)

        # Initialize Codec
        encoder = CodecYenc(work_dir=self.test_dir)

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
        new_article = article.encode(
            [CodecYenc, CodecYenc(work_dir=self.test_dir)],
        )

        # We should have gotten an ASCII Content Object
        assert isinstance(new_article, NNTPArticle) is True

        # We should actually have article associated with out data
        assert len(new_article) > 0

    def test_yenc_v1_3_NNTPArticle_encode_02(self):
        """
        Test the encoding of fresh new data
        """

        # Our private Key Location
        tmp_file = join(
            self.tmp_dir,
            'test_yenc_v1_3_NNTPArticle_encode_02.tmp',
        )

        # Create a larger file
        assert(self.touch(tmp_file, size='1M', random=True))

        # Create an NNTPContent Object pointing to our new data
        content = NNTPBinaryContent(tmp_file)

        # Create a Yenc Codec instance
        encoder = CodecYenc(work_dir=self.test_dir)

        # This should produce our yEnc object now
        encoded = encoder.encode(content)
        assert isinstance(encoded, NNTPAsciiContent) is True

        # Now we want to decode the content we just encoded
        decoded = encoder.decode(encoded)

        # We should get a Binary Object in return
        assert isinstance(decoded, NNTPBinaryContent) is True

        # Our original content should be the same as our decoded
        # content
        assert(decoded.crc32() == content.crc32())
        assert(decoded.md5() == content.md5())

    def test_partial_download(self):
        """
        Test the handling of a download that is explicitly ordered to abort
        after only some content is retrieved.  A way of 'peeking' if you will.

        This test is identicle to test_decoding_yenc_single_part defined in
        this same test file with the exception of testing the early abort.
        """

        # Input File
        encoded_filepath = join(self.var_dir, '00000005.ntx')
        assert isfile(encoded_filepath)

        # Compare File
        decoded_filepath = join(self.var_dir, 'testfile.txt')
        assert isfile(decoded_filepath)

        # Initialize Codec (restrict content to be no larger then 10 bytes)
        decoder = CodecYenc(work_dir=self.test_dir, max_bytes=10)

        with open(encoded_filepath, 'rb') as fd_in:
            content = decoder.decode(fd_in)

        # our content should be valid
        assert isinstance(content, NNTPBinaryContent)

        # The same codec can be used to decode the content again
        with open(encoded_filepath, 'rb') as fd_in:
            content_again = decoder.decode(fd_in)

        # our content should be valid
        assert isinstance(content_again, NNTPBinaryContent)

        # Our content is subject to an early exit so in all cases we should
        # not have valid content
        assert content.is_valid() is False
        assert content_again.is_valid() is False

        with open(decoded_filepath, 'rb') as fd_in:
            decoded = fd_in.read()

        # Compare our processed content with the expected results
        length = len(content.getvalue())
        length_again = len(content_again.getvalue())

        # Compare our processed content with the expected results
        assert decoded[0:length] == content.getvalue()
        assert decoded[0:length_again] == content_again.getvalue()

    def test_parse_article(self):
        """
        Test parse_article()

        """
        # Initialize Codec
        yd = CodecYenc(work_dir=self.test_dir)

        # filename wrapped in quotes and no quotes on description,
        # use of index/count values
        matches = yd.parse_article('description [1/2] - "filename" yEnc (3/4)')
        assert(isinstance(matches, dict) is True)
        assert('desc' in matches)
        assert(matches['desc'] == 'description')
        assert('fname' in matches)
        assert(matches['fname'] == 'filename')
        assert('index' in matches)
        assert(matches['index'] == 1)
        assert('count' in matches)
        assert(matches['count'] == 2)
        assert('yindex' in matches)
        assert(matches['yindex'] == 3)
        assert('ycount' in matches)
        assert(matches['ycount'] == 4)
        assert('size' not in matches)

        # Filename wrapped in quotes and no quotes on description
        matches = yd.parse_article('description - "filename" yEnc (1/2)')
        assert(isinstance(matches, dict) is True)
        assert('desc' in matches)
        assert(matches['desc'] == 'description')
        assert('fname' in matches)
        assert(matches['fname'] == 'filename')
        assert('index' not in matches)
        assert('count' not in matches)
        assert('yindex' in matches)
        assert(matches['yindex'] == 1)
        assert('ycount' in matches)
        assert(matches['ycount'] == 2)
        assert('size' not in matches)

        # no quotes on filename and no quotes on description
        matches = yd.parse_article('description - filename yEnc (3/4)')
        assert(isinstance(matches, dict) is True)
        assert('desc' in matches)
        assert(matches['desc'] == 'description')
        assert('fname' in matches)
        assert(matches['fname'] == 'filename')
        assert('index' not in matches)
        assert('count' not in matches)
        assert('yindex' in matches)
        assert(matches['yindex'] == 3)
        assert('ycount' in matches)
        assert(matches['ycount'] == 4)
        assert('size' not in matches)

        # not quotes around filename and quotes on description,
        # use of size object
        matches = yd.parse_article('"description" - filename yEnc (5/6) 13450')
        assert(isinstance(matches, dict) is True)
        assert('desc' in matches)
        assert(matches['desc'] == 'description')
        assert('fname' in matches)
        assert(matches['fname'] == 'filename')
        assert('index' not in matches)
        assert('count' not in matches)
        assert('yindex' in matches)
        assert(matches['yindex'] == 5)
        assert('ycount' in matches)
        assert(matches['ycount'] == 6)
        assert('size' in matches)
        assert(matches['size'] == 13450)

        # filename not in quotes and nquotes around description
        # no yindex value
        matches = yd.parse_article('"description" - filename yEnc (/1)')
        assert(isinstance(matches, dict) is True)
        assert('desc' in matches)
        assert(matches['desc'] == 'description')
        assert('fname' in matches)
        assert(matches['fname'] == 'filename')
        assert('index' not in matches)
        assert('count' not in matches)
        assert('yindex' not in matches)
        assert('ycount' in matches)
        assert(matches['ycount'] == 1)
        assert('size' not in matches)

        # just a filename in quotes and yindex and ycount
        matches = yd.parse_article('"filename" yEnc (1/2)')
        assert(isinstance(matches, dict) is True)
        assert('desc' not in matches)
        assert('fname' in matches)
        assert(matches['fname'] == 'filename')
        assert('index' not in matches)
        assert('count' not in matches)
        assert('yindex' in matches)
        assert(matches['yindex'] == 1)
        assert('ycount' in matches)
        assert(matches['ycount'] == 2)
        assert('size' not in matches)

        # just a filename in quotes and ycount
        matches = yd.parse_article('"filename" yEnc (/2)')
        assert(isinstance(matches, dict) is True)
        assert('desc' not in matches)
        assert('fname' in matches)
        assert(matches['fname'] == 'filename')
        assert('index' not in matches)
        assert('count' not in matches)
        assert('yindex' not in matches)
        assert('ycount' in matches)
        assert(matches['ycount'] == 2)
        assert('size' not in matches)

        # just a filename (no quotes) and yindex and ycount
        matches = yd.parse_article('filename yEnc (1/2)')
        assert(isinstance(matches, dict) is True)
        assert('desc' not in matches)
        assert('fname' in matches)
        assert(matches['fname'] == 'filename')
        assert('index' not in matches)
        assert('count' not in matches)
        assert('yindex' in matches)
        assert(matches['yindex'] == 1)
        assert('ycount' in matches)
        assert(matches['ycount'] == 2)
        assert('size' not in matches)

        # just a filename (no quotes) and ycount
        matches = yd.parse_article('filename yEnc (/2)')
        assert(isinstance(matches, dict) is True)
        assert('desc' not in matches)
        assert('fname' in matches)
        assert(matches['fname'] == 'filename')
        assert('index' not in matches)
        assert('count' not in matches)
        assert('yindex' not in matches)
        assert('ycount' in matches)
        assert(matches['ycount'] == 2)
        assert('size' not in matches)

    def test_yenc_escaping_rules(self):
        """
        Encoding matches the classic yEnc C library byte for byte
        """
        # These results were captured from the yenc (v0.4) C library
        assert yenc_encode(b'\x04x') == (b'=n\xa2', 3)
        assert yenc_encode(b'\xf6x') == (b'=`\xa2', 3)
        assert yenc_encode(b'\xdfx') == (b'=I\xa2', 3)
        assert yenc_encode(b'a\x04') == (b'\x8b.', 2)

        # Spaces are escaped at the end of a line
        encoded, column = yenc_encode(b'a' * 127 + b'\xf6')
        assert encoded.endswith(b'\x8b\x8b=`\r\n') and column == 0

        # ... but not just before the end
        encoded, column = yenc_encode(b'a' * 126 + b'\xf6b')
        assert encoded.endswith(b'\x8b \x8c\r\n') and column == 0

        # Dots are only escaped at the start of a line
        encoded, column = yenc_encode(b'a' * 127 + b'\x04')
        assert encoded.endswith(b'\x8b.\r\n') and column == 0
        encoded, column = yenc_encode(b'a' * 128 + b'\x04')
        assert encoded.endswith(b'\r\n=n') and column == 2

        # The column carries over between calls
        assert yenc_encode(b'\x04', column=5) == (b'.', 6)

        # Everything decodes back to what we started with
        data = bytes(range(256)) * 4
        encoded, _ = yenc_encode(data)
        assert yenc_decode(encoded) == (data, False)

    def test_yenc_escape_split_across_lines(self):
        """
        An escape character at the end of one block applies to the next
        """
        assert yenc_decode(b'abc=') == (b'789', True)
        assert yenc_decode(b'}x', escape=True) == (b'\x13N', False)
        assert yenc_decode(b'ab\r\ncd\n') == (b'789:', False)

    def test_encoded_lines_have_no_gaps(self):
        """
        Encoded content has no blank lines and respects the line length
        """
        binary_filepath = join(self.var_dir, 'joystick.jpg')
        for linelen in (128, 64):
            encoder = CodecYenc(work_dir=self.test_dir, linelen=linelen)
            content = encoder.encode(binary_filepath)

            with open(content.path(), 'rb') as fd:
                lines = fd.read().split(b'\r\n')

            # The very last entry is what follows the final line break
            assert lines[-1] == b''
            lines = lines[:-1]

            assert lines[0].startswith(b'=ybegin ')
            assert b' line=%d ' % linelen in lines[0]
            assert lines[1].startswith(b'=ypart ')
            assert lines[-1].startswith(b'=yend ')

            body = lines[2:-1]
            assert all(body)
            # An escape sequence can push a line one character over
            assert max(len(line) for line in body) <= linelen + 1

            # Decoding gives us our original file back
            with open(content.path(), 'rb') as fd:
                decoded = CodecYenc(work_dir=self.test_dir).decode(fd)

            with open(binary_filepath, 'rb') as fd:
                assert decoded.getvalue() == fd.read()
            assert decoded.is_valid() is True

    def _encoded(self, data, name='file.bin', part=None):
        """
        Returns yEnc encoded bytes (begin, part, data and end lines)
        """
        encoded, column = yenc_encode(data)
        if column:
            encoded += b'\r\n'

        head = b'=ybegin line=128 size=%d name=%s\r\n' % (
            len(data), name.encode())
        if part:
            head = b'=ybegin part=%d line=128 size=%d name=%s\r\n' % (
                part, len(data), name.encode())
            head += b'=ypart begin=1 end=%d\r\n' % len(data)

        return head + encoded + b'=yend size=%d\r\n' % len(data)

    def test_back_to_back_parts_in_one_stream(self):
        """
        Each decode() stops at the end of its part, leaving the rest
        """
        first = bytes(range(256)) * 50
        second = b'second' * 1000
        stream = BytesIO(
            self._encoded(first, 'first.bin') +
            self._encoded(second, 'second.bin') +
            b'-- \r\nA signature\r\n')

        decoded = CodecYenc(work_dir=self.test_dir).decode(stream)
        assert decoded.filename == 'first.bin'
        assert decoded.getvalue() == first
        assert decoded.is_valid() is True

        decoded = CodecYenc(work_dir=self.test_dir).decode(stream)
        assert decoded.filename == 'second.bin'
        assert decoded.getvalue() == second

        # Whatever follows is left for someone else
        assert stream.read() == b'-- \r\nA signature\r\n'

    def test_decoding_in_small_blocks(self):
        """
        Block sizes don't change the result (escapes can be split up)
        """
        data = bytes(range(256)) * 200
        encoded = self._encoded(data)

        original = CodecYencModule.YENC_DECODE_BLOCK_SIZE
        try:
            for size in (1, 7, 129, 4096):
                CodecYencModule.YENC_DECODE_BLOCK_SIZE = size
                decoder = CodecYenc(work_dir=self.test_dir)
                decoded = decoder.decode(BytesIO(encoded))
                assert decoded.getvalue() == data
                assert decoder.crc32() == decoded.crc32()

        finally:
            CodecYencModule.YENC_DECODE_BLOCK_SIZE = original

    def test_decoding_arrives_in_pieces(self):
        """
        Data can be handed to the decoder a piece at a time (as it arrives)
        """
        data = bytes(range(256)) * 100
        lines = self._encoded(data, part=1).split(b'\r\n')

        decoder = CodecYenc(work_dir=self.test_dir)
        stream = BytesIO()
        result = True
        for start in range(0, len(lines), 10):
            # Add a few more lines to our stream and decode what is there
            ptr = stream.tell()
            stream.seek(0, 2)
            stream.write(b''.join(
                line + b'\r\n' for line in lines[start:start + 10]))
            stream.seek(ptr)
            result = decoder.decode(stream)

        assert result.getvalue() == data
        assert result.is_valid() is True
