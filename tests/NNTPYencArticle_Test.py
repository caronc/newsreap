# A base testing class/library to test a yEnc file trnansfer
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
# Import threading after monkey patching
# see: http://stackoverflow.com/questions/8774958/\
#        keyerror-in-module-threading-after-a-successful-py-test-run
import threading

from os import unlink
from os.path import join
from os.path import dirname
from os.path import abspath
from os.path import isfile
from newsreap.SortedSet import sortedset

try:
    from tests.TestBase import TestBase

except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

from tests.FakeNNTPServer import FakeNNTPServer
from tests.FakeNNTPServer import VAR_PATH

from newsreap.NNTPConnection import NNTPConnection
from newsreap.NNTPIOStream import NNTPIOStream
from newsreap.NNTPArticle import NNTPArticle
from newsreap.NNTPBinaryContent import NNTPBinaryContent


class NNTPYencArticle_Test(TestBase):
    def setUp(self):
        """
        Grab a few more things from the config
        """
        super().setUp()

        # Secure NNTP Server
        self.nntps = FakeNNTPServer(secure=True, join_group=True)

        # Insecure NNTP Server
        self.nntp = FakeNNTPServer(secure=False, join_group=True)

        # Common Group Name
        self.common_group = 'alt.binaries.test'

        # Map Articles (to groups) for fetching
        self.nntp.add_article(
            '5', join(VAR_PATH, '00000005.ntx'), groups=self.common_group)
        self.nntps.add_article(
            '5', join(VAR_PATH, '00000005.ntx'), groups=self.common_group)

        self.nntp.add_article(
            '20', join(VAR_PATH, '00000020.ntx'), groups=self.common_group)
        self.nntp.add_article(
            '21', join(VAR_PATH, '00000021.ntx'), groups=self.common_group)
        self.nntps.add_article(
            '20', join(VAR_PATH, '00000020.ntx'), groups=self.common_group)
        self.nntps.add_article(
            '21', join(VAR_PATH, '00000021.ntx'), groups=self.common_group)

        # Start our servers
        self.nntps.start()
        self.nntp.start()

        # Acquire our configuration
        self.nttp_ipaddr, self.nntp_portno = self.nntp.host, self.nntp.port
        self.nttps_ipaddr, self.nntps_portno = \
            self.nntps.host, self.nntps.port

    def tearDown(self):
        # Shutdown our test servers
        self.nntps.stop()
        self.nntp.stop()

        super().tearDown()

    def test_yenc_message(self):
        """
        Tests the handling of a yenc message
        """

        # Create a non-secure connection
        sock = NNTPConnection(
            host=self.nttp_ipaddr,
            port=self.nntp_portno,
            username='valid',
            password='valid',
            secure=False,
            join_group=True,
        )

        assert sock.connect() is True
        assert sock._iostream == NNTPIOStream.RFC3977_GZIP
        article = sock.get('5', work_dir=self.tmp_dir, group=self.common_group)
        assert sock.group_name == self.common_group
        assert isinstance(article, NNTPArticle) is True
        assert len(article.decoded) == 1
        assert isinstance(next(iter(article.decoded)), NNTPBinaryContent)
        assert next(iter(article.decoded)).is_valid() is True

        # Compare File
        decoded_filepath = join(self.var_dir, 'testfile.txt')
        assert isfile(decoded_filepath)
        with open(decoded_filepath, 'rb') as fd_in:
            decoded = fd_in.read()

        assert decoded == next(iter(article.decoded)).getvalue()

        # Close up our socket
        sock.close()

        # Our temporary cached file
        assert isfile(next(iter(article.decoded)).filepath)

        # Save our file to disk
        new_filepath = join(self.tmp_dir, 'copied.txt')
        assert next(iter(article.decoded)).save(new_filepath, copy=True) \
                is True
        assert isfile(new_filepath) is True
        assert next(iter(article.decoded)).filepath != new_filepath

        try:
            # Makes sure the file doesn't already exist
            unlink(new_filepath)
        except:
            pass

        old_filepath = next(iter(article.decoded)).filepath
        assert next(iter(article.decoded)).save(new_filepath) is True
        assert isfile(old_filepath) is False
        assert isfile(new_filepath) is True
        assert next(iter(article.decoded)).filepath == new_filepath

        # cleanup our file
        unlink(new_filepath)

        # Create a secure connection
        # sock = NNTPConnection(
        #     host=self.nttps_ipaddr,
        #     port=self.nntps_portno,
        #     username='valid',
        #     password='valid',
        #     secure=True,
        #     join_group=True,
        # )
        # assert sock.connect() is True
        # assert sock._iostream == NNTPIOStream.RFC3977_GZIP
        # assert sock.get('5', work_dir=self.tmp_dir, group='alt.binaries.test')

        # Invalid Password
        # assert sock.connect() is False

    def test_yenc_multi_message(self):
        """
        Tests the handling of a yenc multi-message
        """

        # Create a non-secure connection
        sock = NNTPConnection(
            host=self.nttp_ipaddr,
            port=self.nntp_portno,
            username='valid',
            password='valid',
            secure=False,
            join_group=True,
        )

        assert sock.connect() is True
        assert sock._iostream == NNTPIOStream.RFC3977_GZIP

        articles = sortedset(key=lambda x: x.key())

        # We intententionally fetch the content out of order
        # ideally we'd want 20 followed by 21
        articles.add(sock.get(id='21', work_dir=self.tmp_dir, group=self.common_group))
        assert sock.group_name == self.common_group
        articles.add(sock.get(id='20', work_dir=self.tmp_dir))
        assert sock.group_name == self.common_group

        newfile = NNTPBinaryContent(
            # This looks rough;
            # we're basically looking at the first article stored (since our
            # set is sorted, and then we're looking at the first content entry

            # TODO: update the article function so it's much easier to get
            # an iterator to decoded list
            filepath=next(iter(next(iter(articles)).decoded)).filename,
            work_dir=self.tmp_dir,
        )

        for article in articles:
            assert isinstance(article, NNTPArticle) is True
            assert len(article.decoded) == 1
            assert isinstance(next(iter(article.decoded)), NNTPBinaryContent)
            assert next(iter(article.decoded)).is_valid() is True

            # Build on new file
            newfile.append(next(iter(article.decoded)))
            # keep open file count low
            next(iter(article.decoded)).close()

        # Compare File
        decoded_filepath = join(self.var_dir, 'joystick.jpg')
        assert isfile(decoded_filepath)
        with open(decoded_filepath, 'rb') as fd_in:
            decoded = fd_in.read()

        assert isfile(newfile.filepath) is True
        old_filepath = newfile.filepath
        newfile.save()
        new_filepath = newfile.filepath
        assert old_filepath != new_filepath
        assert isfile(old_filepath) is False
        assert isfile(new_filepath) is True

        assert decoded == newfile.getvalue()

        # Close up our socket
        sock.close()

        while len(articles):
            article = articles.pop()
            # length hasn't changed
            assert len(article.decoded) == 1
            old_filepath = next(iter(article.decoded)).filepath
            assert isfile(old_filepath) is True

            # If we remove the article, we automatically destroy
            # all associated decoded with it (that aren't detached)
            del article

            # Since there is only 1 attachment per article in this test
            # we can see that the file is now gone
            assert isfile(old_filepath) is False

        # Remove the file
        del newfile

        # We called save() so the file has been detached and will still exist!
        assert isfile(new_filepath) is True

        # cleanup our file
        unlink(new_filepath)

    def test_partial_yenc_get(self):
        """
        Tests the handling of a partial yenc message
        """

        # Create a non-secure connection
        sock = NNTPConnection(
            host=self.nttp_ipaddr,
            port=self.nntp_portno,
            username='valid',
            password='valid',
            secure=False,
            join_group=True,
        )

        assert sock.connect() is True
        assert sock._iostream == NNTPIOStream.RFC3977_GZIP
        article = sock.get(
            '5',
            work_dir=self.tmp_dir,
            group=self.common_group,
            max_bytes=10,
        )
        assert isinstance(article, NNTPArticle) is True
        assert len(article.decoded) == 1
        assert isinstance(next(iter(article.decoded)), NNTPBinaryContent)
        # Our content isn't valid because it's only a subset/partial download
        assert next(iter(article.decoded)).is_valid() is False

        # Compare File
        decoded_filepath = join(self.var_dir, 'testfile.txt')
        assert isfile(decoded_filepath)
        with open(decoded_filepath, 'rb') as fd_in:
            decoded = fd_in.read()

        # Compare our processed content (which is a subset because we
        # specified the max_bytes argument) with the expected results
        length = len(next(iter(article.decoded)).getvalue())
        assert decoded[0:length] == next(iter(article.decoded)).getvalue()

        # Close up our socket
        sock.close()
