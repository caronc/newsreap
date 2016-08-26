# -*- coding: utf-8 -*-
#
# Defines a base struture for wrapping Posts to NNTP Servers
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

"""
The idea is to allow the community to over-ride how content is sent to the
NNTP Server by allowing them a location they can write their own hook.

The PostHook is called whenever an NNTPArticle() is about to be posted to
a NNTP Server.  The function pre() is called just prior to the post while
post() is called upon completion.

If pre() returns False then the post of that specific article will be aborted.
Otherwise pre() should return the NNTPArticle() (or set of) to be posted.

After the NNTPArticle() post has completed (either successfully or a fail) a
a post() will be called. A post allows you to decide if the article should
be reposted or if you'll just abort.

You can also use this time to free up disk space or perform any other
type of automation you'd like here. Perhaps send the info to a database
of some sort or notify an external application.

"""

# Logging
import logging
from newsreap.Logging import NEWSREAP_LOGGER
from newsreap import __version__
logger = logging.getLogger(NEWSREAP_LOGGER)
from time import time


class NNTPPostStatus(object):
    """
    Since there aren't a whole lot of reasons for an NNTP Post to fail, all
    possible errors are masked for those intersted in an exact reason.

    Alternatively it's also safe to say that if the post status is 0 (Zero)
    all went well, otherwise a non-zero indicates a failure. If the type
    of failure can't be determined, at the very least the FAILED flag will be
    set.
    """
    # Post was successful
    OKAY = 0x0
    # Post had a failure
    FAILED = 0x01
    # Posting Privilieges are not present
    POSTING_DENIED = 0x10
    # Post timeout out while being sent
    TIMEOUT = 0x100


class PostHookBase(object):
    """
    The base class all Posting Hooks should inherit from
    """

    def __init__(self, *args, **kwargs):
        """
        Initialize PostHook
        """
        return

    def pre(self, article, *args, **kwargs):
        """
        This function is called against every article prior to it being posted
        to the NNTP Server.

        The article represents the article about to be posted to the NNTP
        Server.

        Possible return values are:
            True: This is the same as returning the article; it presumes you
                    want to continue procesessing.
            False: You want to skip the posting of this article but move on
                    to the next (you'll come back into the pre() call of it
            None:   A hard abort; you want to skip the posting of this article
                    in addition to not posting anything anymore.

            NNTPArticle(): Return a new NNTPArticle that you which to process
                    instead of the article passed into the functon.  The
                    NNTPArticle() you create will be used instead.

            set(), list or tuple of NNTPArticle objects:
                    You may want to return a list of articles that you
                    generated from the one passe into this function.
                    Any NNTPArticle objects returned are processed instead of
                    the one passed into this function.

                    Keep in mind that returning a list of articles will not
                    cause this function's pre() to be issued on each of the
                    new items you generated (to avoid potential recursive
                    loops). So if you split an article into more then one here
                    be sure to leave the articles you generate ready for
                    posting.

                    Each new article you generate and return will still cause
                    a post() call to be made. This can allow you to monitor
                    the success of your actions.

        """
        if 'From' not in article.header:
            article.header['From'] =  'nr@unknown.com'
        if 'Newsgroups' not in article.header:
            article.header['Newsgroups'] =  'unknown'
        if 'Subject' not in article.header:
            article.header['Subject'] = article.subject

        if 'Message-ID' not in article.header:
            article.header['Message-ID'] = '<%.5f.%d@%s>' % (time(), article.part, 'unknown')

        article.header['X-Newsposter'] = \
                'NewsReap %s - https://github.com/caronc/newsreap' % \
                __version__

        # TODO: Apply template to Subject/Poster

        return article

    def post(self, status, article, retries=3, *args, **kwargs):
        """
        Similar to pre(), this is executed for every file posted to an NNTP
        Server. However the difference is that post() is executed 'after'
        the content has been processed.  It's important to check the `status`
        variable to determine if the post was succcessful or not.

        It is through the post() you can issue a post retry of the article. Or
        just move on.

        The function returns work as follows:
            True:   You're content with the situation, move on to the next
                    article if there is one.

            False:  Abort remaining posts.

            NNTPArticle(): Return a new NNTPArticle that you wish to post next.
                    It's important to note that upon completion of this post (
                    successful or not) this post() call will be called again.


            set(), list or tuple of NNTPArticle objects:
                    Return multple NNTPArticles you wish to post next. Each
                    post will cause this post() call to be made again (per
                    article).

        """
        if status is NNTPPostStatus.OKAY:
            logger.info('NNTP POST of %s completed.' % article)
            return True

        if not hasattr(article, '__retry_post'):
            article.__retry_post = 0

        if status & NNTPPostStatus.TIMEOUT:
            if article.__retry_post < retries:
                logger.warning('NNTP POST timeout occured, retrying...')
                article.__retry_post += 1
                return article

            logger.error('NNTP POST timeout occured and retry limit reached.')

        elif status & NNTPPostStatus.POSTING_DENIED:
            logger.error('NNTP POST denied.')

        else:
            if article.__retry_post < retries:
                logger.warning('NNTP POST timeout occured, retrying...')
                article.__retry_post += 1
                return article

            logger.error('NNTP POST failed.')

        return False
