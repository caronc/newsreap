# -*- encoding: utf-8 -*-
#
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
from blist import sortedset

try:
    from tests.TestBase import TestBase

except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

from tests.NNTPSocketServer import NNTPSocketServer
from tests.NNTPSocketServer import NNTP_TEST_VAR_PATH as VAR_PATH

from newsreap.NNTPConnection import NNTPConnection
from newsreap.NNTPIOStream import NNTPIOStream
from newsreap.NNTPArticle import NNTPArticle
from newsreap.NNTPBinaryContent import NNTPBinaryContent


class NNTPYencArticle_Test(TestBase):
    def setUp(self):
        """
        Grab a few more things from the config
        """
        super(NNTPYencArticle_Test, self).setUp()

        # Secure NNTP Server
        self.nntps = NNTPSocketServer(
            secure=True,
            join_group=True,
        )

        # Insecure NNTP Server
        self.nntp = NNTPSocketServer(
            secure=False,
            join_group=True,
        )

        # Common Group Name
        self.common_group = 'alt.binaries.test'

        # Map Articles (to groups) for fetching
        self.nntp.map(
            article_id='5',
            groups=(self.common_group, ),
            filepath=join(VAR_PATH, '00000005.ntx'),
        )
        self.nntps.map(
            article_id='5',
            groups=(self.common_group, ),
            filepath=join(VAR_PATH, '00000005.ntx'),
        )

        self.nntp.map(
            article_id='20',
            groups=(self.common_group, ),
            filepath=join(VAR_PATH, '00000020.ntx'),
        )
        self.nntp.map(
            article_id='21',
            groups=(self.common_group, ),
            filepath=join(VAR_PATH, '00000021.ntx'),
        )
        self.nntps.map(
            article_id='20',
            groups=(self.common_group, ),
            filepath=join(VAR_PATH, '00000020.ntx'),
        )
        self.nntps.map(
            article_id='21',
            groups=(self.common_group, ),
            filepath=join(VAR_PATH, '00000021.ntx'),
        )

        # Exit the server thread when the main thread terminates
        self.nntps.daemon = True
        self.nntp.daemon = True

        # Start Our Server Threads
        self.nntps.start()
        self.nntp.start()

        # Acquire our configuration
        self.nttp_ipaddr, self.nntp_portno = \
                self.nntp.local_connection_info()
        self.nttps_ipaddr, self.nntps_portno = \
                self.nntps.local_connection_info()

    def tearDown(self):
        # Shutdown NNTP Dummy Servers Daemons
        self.nntps.shutdown()
        self.nntp.shutdown()

        super(NNTPYencArticle_Test, self).tearDown()

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
        assert isinstance(iter(article.decoded).next(), NNTPBinaryContent)
        assert iter(article.decoded).next().is_valid() is True

        # Compare File
        decoded_filepath = join(self.var_dir, 'testfile.txt')
        assert isfile(decoded_filepath)
        with open(decoded_filepath, 'r') as fd_in:
            decoded = fd_in.read()

        assert decoded == iter(article.decoded).next().getvalue()

        # Close up our socket
        sock.close()

        # Our temporary cached file
        assert isfile(iter(article.decoded).next().filepath)

        # Save our file to disk
        new_filepath = join(self.tmp_dir, 'copied.txt')
        assert iter(article.decoded).next().save(new_filepath, copy=True) \
                is True
        assert isfile(new_filepath) is True
        assert iter(article.decoded).next().filepath != new_filepath

        try:
            # Makes sure the file doesn't already exist
            unlink(new_filepath)
        except:
            pass

        old_filepath = iter(article.decoded).next().filepath
        assert iter(article.decoded).next().save(new_filepath) is True
        assert isfile(old_filepath) is False
        assert isfile(new_filepath) is True
        assert iter(article.decoded).next().filepath == new_filepath

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
            filepath=iter(iter(articles).next().decoded).next().filename,
            work_dir=self.tmp_dir,
        )

        for article in articles:
            assert isinstance(article, NNTPArticle) is True
            assert len(article.decoded) == 1
            assert isinstance(iter(article.decoded).next(), NNTPBinaryContent)
            assert iter(article.decoded).next().is_valid() is True

            # Build on new file
            newfile.append(iter(article.decoded).next())
            # keep open file count low
            iter(article.decoded).next().close()

        # Compare File
        decoded_filepath = join(self.var_dir, 'joystick.jpg')
        assert isfile(decoded_filepath)
        with open(decoded_filepath, 'r') as fd_in:
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
            old_filepath = iter(article.decoded).next().filepath
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
