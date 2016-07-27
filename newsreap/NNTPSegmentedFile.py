# -*- coding: utf-8 -*-
#
# A container of NNTPArticles which together forms an NNTPSegmentedFile.
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

from blist import sortedset
from newsreap.NNTPArticle import NNTPArticle

# Logging
import logging
from newsreap.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)


class NNTPSegmentedFile(object):
    """
    An object for maintaining retrieved nzb content. Large files need
    to be split across multiple Articles in order to be posted.

    When combined into one, They create a SegmentedFile

    """

    def __init__(self, *args, **kwargs):
        """
        Initialize NNTP Segmented File

        """
        # The File
        self.filename = kwargs.get('filename', '')

        # A sorted set of articles
        self.articles = sortedset(key=lambda x: x.key())


    def __str__(self):
        """
        Return a printable version of the article
        """
        return '%s' % self.filename


    def __unicode__(self):
        """
        Return a printable version of the article
        """
        return u'%s' % self.filename


    def __repr__(self):
        """
        Return an unambigious version of the object
        """

        return '<NNTPSegmentedFile filename="%s" segments=%d />' % (
            self.filename,
            len(self.articles),
        )

