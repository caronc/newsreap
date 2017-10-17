# -*- encoding: utf-8 -*-
#
# A base testing class/library to test the workings of an NNTPArticle
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
if 'threading' in sys.modules:
    #  gevent patching since pytests import
    #  the sys library before we do.
    del sys.modules['threading']

import gevent.monkey
gevent.monkey.patch_all()
# Import threading after monkey patching
# see: http://stackoverflow.com/questions/8774958/\
#        keyerror-in-module-threading-after-a-successful-py-test-run

import re
from blist import sortedset
from os.path import dirname
from os.path import abspath
from os.path import join
from os.path import isfile

try:
    from tests.TestBase import TestBase

except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

from newsreap.NNTPArticle import NNTPArticle
from newsreap.NNTPBinaryContent import NNTPBinaryContent
from newsreap.NNTPHeader import NNTPHeader
from newsreap.NNTPResponse import NNTPResponse
from newsreap.Utils import strsize_to_bytes


class NNTPArticle_Test(TestBase):

    def test_loading_response(self):
        """
        Tests the load() function of the article
        """

        # Prepare a Response
        response = NNTPResponse(200, 'Great Data')
        response.decoded.add(NNTPBinaryContent(work_dir=self.tmp_dir))

        # Prepare Article
        article = NNTPArticle(id='random-id', work_dir=self.tmp_dir)

        # There is no data so our article can't be valid
        assert(article.is_valid() is False)

        # Load and Check
        assert(article.load(response) is True)
        assert(article.header is None)
        assert(len(article.decoded) == 1)
        assert(len(article.decoded) == len(article.files()))
        assert(str(article) == 'random-id')
        assert(unicode(article) == u'random-id')
        assert(article.size() == 0)

        # Now there is data, but it's an empty Object so it can't be valid
        assert(article.is_valid() is False)

        result = re.search(' Message-ID=\"(?P<id>[^\"]+)\"', repr(article))
        assert(result is not None)
        assert(result.group('id') == str(article))

        result = re.search(' attachments=\"(?P<no>[^\"]+)\"', repr(article))
        assert(result is not None)
        assert(int(result.group('no')) == len(article))

        # Prepare Article
        article_a = NNTPArticle(id='a', work_dir=self.tmp_dir)
        article_b = NNTPArticle(id='b', work_dir=self.tmp_dir)
        assert((article_a < article_b) is True)

        # playing with the sort order however alters things
        article_a.no += 1
        assert((article_a < article_b) is False)

        # Prepare a Response (with a Header)
        response = NNTPResponse(200, 'Great Data')
        response.decoded.add(NNTPHeader(work_dir=self.tmp_dir))
        response.decoded.add(NNTPBinaryContent(work_dir=self.tmp_dir))

        # Prepare Article
        article = NNTPArticle(id='random-id', work_dir=self.tmp_dir)

        # Load and Check
        assert(article.load(response) is True)
        assert(isinstance(article.header, NNTPHeader))
        assert(len(article.decoded) == 1)

        for no, decoded in enumerate(article.decoded):
            # Test equality
            assert(article[no] == decoded)

        # We can also load another article ontop of another
        # This used when associating downloaded articles with ones
        # found in NZB-Files
        new_article = NNTPArticle(
            msgid='brand-new-id',
            no=article.no+1,
            groups='a.b.c,d.e.f',
            work_dir=self.tmp_dir,
        )
        new_article.subject = 'test-subject-l2g'
        new_article.poster = 'test-poster-l2g'
        new_article.header = 'test-header-l2g'

        assert(article.load(new_article) is True)
        assert(article.id == new_article.id)
        assert(article.no == new_article.no)
        assert(article.groups == new_article.groups)
        assert(article.poster == new_article.poster)
        assert(article.subject == new_article.subject)
        assert(article.header == new_article.header)
        assert(article.body == new_article.body)
        assert(article.decoded == new_article.decoded)
        assert(article.groups == new_article.groups)

    def test_group(self):
        """
        Tests the group variations
        """

        # Test String
        article = NNTPArticle(
            id='random-id',
            work_dir=self.tmp_dir,
        )
        assert(isinstance(article.groups, set))
        assert(len(article.groups) == 0)

        # Test String
        article = NNTPArticle(
            id='random-id',
            groups='convert.lead.2.gold',
            work_dir=self.tmp_dir,
        )
        assert(isinstance(article.groups, set))
        assert(len(article.groups) == 1)
        assert('convert.lead.2.gold' in article.groups)

        # Support Tuples
        article = NNTPArticle(
            id='random-id',
            groups=(
                'convert.lead.2.gold',
                'convert.lead.2.gold.again',
            ),
            work_dir=self.tmp_dir,
        )

        assert(isinstance(article.groups, set))
        assert(len(article.groups) == 2)
        assert('convert.lead.2.gold' in article.groups)
        assert('convert.lead.2.gold.again' in article.groups)

        # Support Lists
        article = NNTPArticle(
            id='random-id',
            groups=[
                'convert.lead.2.gold',
                'convert.lead.2.gold.again',
            ],
            work_dir=self.tmp_dir,
        )
        assert(isinstance(article.groups, set))
        assert(len(article.groups) == 2)
        assert('convert.lead.2.gold' in article.groups)
        assert('convert.lead.2.gold.again' in article.groups)

        # Support Sets
        article = NNTPArticle(
            id='random-id',
            groups=set([
                'convert.lead.2.gold',
                'convert.lead.2.gold.again',
            ]),
            work_dir=self.tmp_dir,
        )
        assert(isinstance(article.groups, set))
        assert(len(article.groups) == 2)
        assert('convert.lead.2.gold' in article.groups)
        assert('convert.lead.2.gold.again' in article.groups)

        try:
            # Throw an exception if the group is invalid
            article = NNTPArticle(
                id='random-id',
                groups=4,
                work_dir=self.tmp_dir,
            )
            assert(False)

        except Exception, e:
            assert(isinstance(e, AttributeError) is True)

        # Duplicates groups are are removed automatically
        article = NNTPArticle(
            id='random-id',
            groups=[
                'convert.lead.2.gold.again',
                'ConVert.lead.2.gold',
                'convert.lead.2.gold',
                'convert.lead.2.gold.again',
            ],
            work_dir=self.tmp_dir,
        )
        assert(isinstance(article.groups, set))
        assert(len(article.groups) == 2)
        assert('convert.lead.2.gold' in article.groups)
        assert('convert.lead.2.gold.again' in article.groups)

    def test_article_splitting(self):
        """
        Tests that articles can split
        """
        # Duplicates groups are are removed automatically
        article = NNTPArticle(
            work_dir=self.tmp_dir,
            subject='split-test',
            poster='<noreply@newsreap.com>',
            groups='alt.binaries.l2g',
        )

        # Nothing to split gives an error
        assert(article.split() is None)

        tmp_file = join(self.tmp_dir, 'NNTPArticle_Test.chunk', '1MB.rar')
        # The file doesn't exist at first
        assert(isfile(tmp_file) is False)
        # Create it
        assert(self.touch(tmp_file, size='1MB', random=True) is True)
        # Now it does
        assert(isfile(tmp_file) is True)

        # Now we want to load it into a NNTPContent object
        content = NNTPBinaryContent(filepath=tmp_file, work_dir=self.tmp_dir)

        # Add our object to our article
        assert(article.add(content) is True)

        # No size to split on gives an error
        assert(article.split(size=0) is None)
        assert(article.split(size=-1) is None)
        assert(article.split(size=None) is None)
        assert(article.split(size='bad_string') is None)

        # Invalid Memory Limit
        assert(article.split(mem_buf=0) is None)
        assert(article.split(mem_buf=-1) is None)
        assert(article.split(mem_buf=None) is None)
        assert(article.split(mem_buf='bad_string') is None)

        # We'll split it in 2
        results = article.split(strsize_to_bytes('512K'))

        # Tests that our results are expected
        assert(isinstance(results, sortedset) is True)
        assert(len(results) == 2)

        # Test that the parts were assigned correctly
        for i, content in enumerate(results):
            # We should only have one content object
            assert(len(content) == 1)
            # Our content object should correctly have the part and
            # total part contents populated correctly
            assert(content[0].part == (i+1))
            assert(content[0].total_parts == len(results))

    def test_article_append(self):
        """
        Test article append()

        Appending effectively takes another's article and appends it's
        content to the end of the article doing the appending.
        Consider:
            - test.rar.000 (ArticleA)
            - test.rar.001 (ArticleB)
            - test.rar.002 (Articlec)

            # The following would assemble the entire article
            ArticleA.append(ArticleB)
            ArticleA.append(ArticleC)

        """
        # Create a temporary file we can use
        tmp_file = join(self.tmp_dir, 'NNTPArticle_Test.append', '1MB.rar')

        # The file doesn't exist at first
        assert(not isfile(tmp_file))

        # Create it
        assert(self.touch(tmp_file, size='1MB', random=True))

        # Now it does
        assert(isfile(tmp_file))

        # Duplicates groups are are removed automatically
        article_a = NNTPArticle(
            work_dir=self.tmp_dir,
            subject='split-test-a',
            poster='<noreply@newsreap.com>',
            groups='alt.binaries.l2g',
        )

        # No size at this point
        assert(article_a.size() == 0)

        # Add our file to our article
        assert(article_a.add(tmp_file) is True)

        # We should be equal to the size we created our content with
        assert(article_a.size() == strsize_to_bytes('1M'))

        # We'll split it in 2
        results = article_a.split(strsize_to_bytes('512K'))

        # Size doesn't change even if we're split
        assert(article_a.size() == strsize_to_bytes('1M'))

        # Tests that our results are expected
        assert(isinstance(results, sortedset) is True)
        assert(len(results) == 2)

        # We'll create another article
        article_b = NNTPArticle(
            subject='split-test-b',
            poster='<noreply@newsreap.com>',
            groups='alt.binaries.l2g',
            work_dir=self.tmp_dir,
        )

        # Now we'll join the contents using append
        assert(article_b.size() == 0)
        for article in results:
            assert(isinstance(article, NNTPArticle) is True)
            assert(article_b.append(article) is True)

        assert(article_b.size() == article_a.size())
        assert(article_b[0].md5() == article_a[0].md5())

        # Cleanup still occurs as expected
        # fname = article_a[0].path()
        # assert(isfile(fname) is True)
        # del article_a
        # assert(isfile(fname) is False)

        # fname = article_b[0].path()
        # assert(isfile(fname) is True)
        # del article_b
        # assert(isfile(fname) is False)

        # for i in reversed(range(len(results))):
        #     fname = results[i][0].path()
        #     assert(isfile(fname) is True)
        #     del results[i]
        #     assert(isfile(fname) is False)

    def test_posting_content(self):
        """
        Tests the group variations
        """
        # Duplicates groups are are removed automatically
        article = NNTPArticle(
            subject='woo-hoo',
            poster='<noreply@newsreap.com>',
            id='random-id',
            groups='alt.binaries.l2g',
            work_dir=self.tmp_dir,
        )

        # First we create a 512K file
        tmp_file = join(
            self.tmp_dir, 'NNTPArticle_Test.posting', 'file.tmp')

        # File should not already exist
        assert(isfile(tmp_file) is False)
        # Create a random file
        assert(self.touch(tmp_file, size='512K', random=True) is True)
        # File should exist now
        assert(isfile(tmp_file) is True)

        # Now we want to load it into a NNTPContent object
        content = NNTPBinaryContent(filepath=tmp_file, work_dir=self.tmp_dir)
        assert(article.add(content) is True)

        # Now we want to split the file up
        results = article.split('128K')
        # Tests that our results are expected
        assert(isinstance(results, sortedset) is True)
        assert(len(results) == 4)

    def test_article_copy(self):
        """
        The copy() function built into the article allows you
        to create a duplicate copy of the original article without
        obstructing the content from within.
        """

        tmp_dir = join(self.tmp_dir, 'NNTPArticle_Test.test_article_copy')
        # First we create a 512K file
        tmp_file_01 = join(tmp_dir, 'file01.tmp')
        tmp_file_02 = join(tmp_dir, 'file02.tmp')

        # Allow our files to exist
        assert(self.touch(tmp_file_01, size='512K', random=True) is True)
        assert(self.touch(tmp_file_02, size='512K', random=True) is True)

        # Duplicates groups are are removed automatically
        article = NNTPArticle(
            subject='woo-hoo',
            poster='<noreply@newsreap.com>',
            id='random-id',
            groups='alt.binaries.l2g',
            work_dir=self.tmp_dir,
        )

        # Store some content
        content = NNTPBinaryContent(
            filepath=tmp_file_01, part=1, work_dir=self.tmp_dir)
        assert(article.add(content) is True)
        content = NNTPBinaryContent(
            filepath=tmp_file_02, part=2, work_dir=self.tmp_dir)
        assert(article.add(content) is True)

        # Detect our 2 articles
        assert(len(article) == 2)

        # Set a few header entries
        article.header['Test'] = 'test'
        article.header['Another-Entry'] = 'test2'

        # Create a copy of our object
        article_copy = article.copy()

        assert(len(article_copy) == len(article))
        assert(len(article_copy.header) == len(article.header))

        # Make sure that if we obstruct 1 object it doesn't
        # effect the other (hence we should have a pointer to
        # the same location in memory
        article.header['Yet-Another-Entry'] = 'test3'
        assert(len(article_copy.header)+1 == len(article.header))
