# Test the NNTP Header Codec
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
from os.path import join
from os.path import dirname
from os.path import isfile
from os.path import abspath

try:
    from tests.TestBase import TestBase
except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

from newsreap.codecs.CodecHeader import CodecHeader
from newsreap.NNTPHeader import NNTPHeader


class CodecUU_Header(TestBase):

    def test_bad_headers(self):
        """
        Make sure we fail on bad headers
        """
        # Initialize Codec
        ch = CodecHeader()
        assert ch.detect("Not A Valid Header: Entry") == None
        assert ch.detect("Another bad header") == None
        # Empty lines are not valid
        assert ch.detect("") == None


    def test_good_headers(self):
        """
        Make sure we are successful with good headers
        """

        # Initialize Codec
        ch = CodecHeader()

        assert ch.detect('A-Valid-Header:Entry') == {
            'key': 'A-Valid-Header',
            'value': 'Entry',
        }

        # some spaces are allowed (before any header is read)
        assert CodecHeader().detect(' AnotHER-Valid-HeaDER  : EnTRy') == {
            'key': 'AnotHER-Valid-HeaDER',
            'value': 'EnTRy',
        }

        # After a header, a line starting with a space continues it, even
        # when it holds a colon (RFC 5322 folding)
        ch = CodecHeader()
        assert ch.detect('Xref: news.example.com')['key'] == 'Xref'
        assert ch.detect(' alt.binaries.test:123') == {
            'key': 'Xref', 'value': 'alt.binaries.test:123', 'folded': True}
        assert ch.detect('Subject: Re:')['key'] == 'Subject'
        assert ch.detect(' Re: hello')['folded'] is True


    def test_decoding_01(self):
        """
        Open a stream to a file we can read for decoding; This test
        specifically focuses on var/headers.test01.msg
        """

        # Initialize Codec
        ch_py = CodecHeader()

        encoded_filepath = join(self.var_dir, 'headers.test01.msg')
        assert isfile(encoded_filepath)

        # Read data and decode it
        with open(encoded_filepath, 'rb') as fd_in:
            # Decodes content and stops when complete
            assert isinstance(ch_py.decode(fd_in), NNTPHeader)

            # Ensure that the next line of code we read is actually
            # the first line of text after the white space separater
            # after the headers
            assert fd_in.readline().strip() == b'First Line'

            # Calling decode again is futile because headers have already
            # been set
            ch_py.decode(fd_in)
            # Test that the next line read returns the next line of data
            assert fd_in.readline().strip() == b'Second Line'

        assert len(ch_py) == 16

        # with the white space + the 16 lines processed, our line_count
        # should be set to 11
        assert ch_py._total_lines == 17
        assert ch_py._lines == len(ch_py)

        # print('\n'.join(["assert ch_py['%s'] == '%s'" % (k,v) \
        #        for k, v in ch_py.items()]))

        # 01
        assert ch_py['From'] == 'Spyder McGurk <Smcgurk@veration.edu>'
        # 02
        assert ch_py['X-Received-Bytes'] == '226569'
        # 03
        assert ch_py['X-Newsreader'] == 'Forte Agent 6.00/32.1186'
        # 04
        assert ch_py['X-Received-Body-CRC'] == '1037710279'
        # 05
        assert ch_py['Lines'] == '1750'
        # 06
        assert ch_py['Bytes'] == '226506'
        # 07
        assert ch_py['References'] == '<rei7ka1vo93moj2kh7mpn3iion6v5tebtv@4ax.com>'
        # 08
        assert ch_py['X-Complaints-Info'] == 'Please be sure to forward a copy of ALL headers otherwise we will be unable to process your complaint properly.'
        # 09
        assert ch_py['Organization'] == 'Easynews - www.easynews.com'
        # 10
        assert ch_py['Newsgroups'] == 'alt.binaries.e-book'
        # 11
        assert ch_py['Date'] == 'Fri, 01 May 2015 14:43:53 -0500'
        # 12
        assert ch_py['Path'] == 'news.astraweb.com'
        # 13
        assert ch_py['Message-ID'] == '<5nl7kahf6idkev93a8qu5k9m4095nt5p2i@4ax.com>'
        # 14
        assert ch_py['X-Complaints-To'] == 'abuse@easynews.com'
        # 15
        assert ch_py['X-No-Archive'] == 'yes'
        # 16
        assert ch_py['Subject'] == 'Re: ATTN: Psychopath - "agent (Medium).jpg" 213.1 kBytes yEnc'


    def test_decoding_02(self):
        """
        Open a stream to a file we can read for decoding; This test
        specifically focuses on var/headers.test02.msg
        """

        # Initialize Codec
        ch_py = CodecHeader()

        encoded_filepath = join(self.var_dir, 'headers.test02.msg')
        assert isfile(encoded_filepath)

        # Read data and decode it
        with open(encoded_filepath, 'rb') as fd_in:
            # Decodes content and stops when complete
            assert isinstance(ch_py.decode(fd_in), NNTPHeader)

            # Read in the white space since it is actually the first line
            # after the end of headers delimiter
            assert fd_in.readline().strip() == b''

            # Ensure that the next line of code we read is actually
            # the first line of text after the white space separater
            # after the headers
            assert fd_in.readline().strip() == b'Second Line'


        # print('\n'.join(["assert ch_py['%s'] == '%s'" % (k, v) \
        #                 for k, v in ch_py.items()]))

        assert len(ch_py) == 10

        # with the white space + the 10 lines processed, our line_count
        # should be set to 11
        assert ch_py._total_lines == 11
        assert ch_py._lines == len(ch_py)

        # 01
        assert ch_py['From'] == 'Unknown User <unknown@unknown.com>'
        # 02
        assert ch_py['X-Newsreader'] == 'NNTP Testing v1.00'
        # 03
        assert ch_py['Lines'] == '1750'
        # 04
        assert ch_py['Bytes'] == '226506'
        # 05
        assert ch_py['References'] == '<id.com>'
        # 06
        assert ch_py['Newsgroups'] == 'alt.binaries.nntp.testing'
        # 07
        assert ch_py['Date'] == 'Fri, 01 January 1985 00:00:00 -0000'
        # 08
        assert ch_py['Path'] == 'news.nntp.testing'
        # 09
        assert ch_py['Message-ID'] == '<id.com>'
        # 10
        assert ch_py['Subject'] == 'Re: Short Subject'

        # assert False
        assert ch_py.is_valid() == True


    def test_decoding_03(self):
        """
        Open a stream to a file we can read for decoding; This test
        specifically focuses on var/headers.test03.msg
        """

        # Initialize Codec
        ch_py = CodecHeader()

        encoded_filepath = join(self.var_dir, 'headers.test03.msg')
        assert isfile(encoded_filepath)

        # Read data and decode it
        with open(encoded_filepath, 'rb') as fd_in:
            # Decodes content and stops when complete
            assert isinstance(ch_py.decode(fd_in), NNTPHeader)

            # Read in the white space since it is actually the first line
            # after the end of headers delimiter
            assert fd_in.readline().strip() == b'First Line without spaces'

        #print('\n'.join(["assert ch_py['%s'] == '%s'" % (k, v) \
        #                 for k, v in ch_py.items()]))

        assert len(ch_py) == 10

        # with the 10 lines processed, our line_count
        # should be set to 10
        assert ch_py._lines == 10

        # assert False
        assert ch_py.is_valid() == True


    def test_decoding_04(self):
        """
        Open a stream to a file we can read for decoding; This test
        specifically focuses on the following:
            var/headers.test04a.msg
            var/headers.test04b.msg

        This tests the fact that the first block of data only having
        some of the message header (but not all of it)... the decode
        can be consecutitively called until all of the data has been
        made avalable
        """

        # Initialize Codec
        ch_py = CodecHeader()

        encoded_filepath_a = join(self.var_dir, 'headers.test04a.msg')
        encoded_filepath_b = join(self.var_dir, 'headers.test04b.msg')
        assert isfile(encoded_filepath_a)
        assert isfile(encoded_filepath_b)

        # Read data and decode it
        with open(encoded_filepath_a, 'rb') as fd_in:
            # Returns true because we processed all we had found
            # and no end of header delimiter detected (or not header type)
            assert ch_py.decode(fd_in) is True

        # use our second file to finish the message
        with open(encoded_filepath_b, 'rb') as fd_in:
            # Decodes content and stops when complete
            assert isinstance(ch_py.decode(fd_in), NNTPHeader)

            # Read in the white space since it is actually the first line
            # after the end of headers delimiter
            assert fd_in.readline().strip() == b'First Line without spaces'

        #print('\n'.join(["assert ch_py['%s'] == '%s'" % (k, v) \
        #                 for k, v in ch_py.items()]))

        assert len(ch_py) == 10

        # with the 10 lines processed, our line_count
        # should be set to 10
        assert ch_py._lines == 10

        # We should be marked as valid
        assert ch_py.is_valid() == True


    def test_is_valid(self):
        """
        Tests different key combinations that would cause the different
        return types from is_valid()
        """

        # Initialize Codec
        ch_py = CodecHeader()

        encoded_filepath = join(self.var_dir, 'headers.test03.msg')
        assert isfile(encoded_filepath)

        # We haven't done any processing yet
        assert ch_py.is_valid() is None

        # Populate ourselves with some keys
        with open(encoded_filepath, 'rb') as fd_in:
            # Decodes content and stops when complete
            assert isinstance(ch_py.decode(fd_in), NNTPHeader)

        # keys should be good!
        assert ch_py.is_valid() is True

        for k in ( 'DMCA', 'Removed', 'Cancelled', 'Blocked' ):
            # Intentially create a bad key:
            ch_py['X-%s' % k] = 'True'

            # We should fail now
            assert ch_py.is_valid() is False

            # it will become valid again once we clear the key
            del ch_py['X-%s' % k]
            assert ch_py.is_valid() is True


def test_long_headers_continued_on_more_lines_are_joined(tmp_path):
    """
    A header continued on the lines after it (starting with spaces or a
    tab) is read whole, and an empty header doesn't end the header
    """
    from io import BytesIO

    codec = CodecHeader(work_dir=str(tmp_path))
    stream = BytesIO(
        b'Subject:\r\n'
        b'Content-Type: multipart/mixed;\r\n'
        b'  boundary="abc"\r\n'
        b'X-Long: first\r\n'
        b'\tsecond\r\n'
        b'From: someone@example.com\r\n'
        b'\r\n'
        b'the body\r\n')

    header = codec.decode(stream)
    assert header['Subject'] == ''
    assert header['Content-Type'] == 'multipart/mixed; boundary="abc"'
    assert header['X-Long'] == 'first second'
    assert header['From'] == 'someone@example.com'
    assert stream.read() == b'the body\r\n'

    # A header seen twice still joins with a comma
    codec.reset()
    header = codec.decode(BytesIO(b'X-A: 1\r\nX-A: 2\r\n\r\n'))
    assert header['X-A'] == '1,2'


def test_headers_in_other_character_sets_are_read(tmp_path):
    """
    UTF-8 is read as UTF-8; anything else with the server's character set
    """
    from io import BytesIO

    codec = CodecHeader(work_dir=str(tmp_path), encoding='ISO-8859-1')
    header = codec.decode(BytesIO(
        'X-A: café\r\n'.encode() + b'X-B: caf\xe9\r\n\r\n'))
    assert (header['X-A'], header['X-B']) == ('café', 'café')

    codec = CodecHeader(work_dir=str(tmp_path), encoding='utf-8')
    header = codec.decode(BytesIO(b'X-B: caf\xe9\r\n\r\n'))
    assert header['X-B'] == 'caf\ufffd'
