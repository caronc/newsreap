# -*- coding: utf-8 -*-
#
# NNTPPostFactory is an object that generates NNTP postable articles
#
# Copyright (C) 2017 Chris Caron <lead2gold@gmail.com>
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

import re
import weakref

from os.path import isfile
from os.path import exists
from os.path import join
from os.path import isdir
from os.path import abspath
from os.path import expanduser
from os.path import basename
from os.path import dirname
from datetime import datetime

from .objects.post.StagedArticle import StagedArticle
from .objects.post.StagedArticleGroup import StagedArticleGroup
from .objects.post.StagedArticleHeader import StagedArticleHeader

from .Utils import find
from .Utils import mkdir
from .Utils import rm
from .Utils import dirsize
from .Utils import strsize_to_bytes

# Our codecs
from .codecs.CodecPar import CodecPar
from .codecs.CodecRar import CodecRar
from .codecs.CodecYenc import CodecYenc

from .NNTPGroup import NNTPGroup
from .NNTPnzb import NNTPnzb
from .NNTPSegmentedPost import NNTPSegmentedPost
from .NNTPArticle import NNTPArticle
from .NNTPPostDatabase import NNTPPostDatabase
from .NNTPConnection import NNTPConnection
from .NNTPManager import NNTPManager
from .NNTPHeader import NNTPHeader
from .NNTPResponse import NNTPResponseCode
from .Utils import scan_pylib
from .Utils import load_pylib
from .Utils import parse_paths

# Logging
import logging
from newsreap.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)

# The workspace we can stage our content in. It's always relative to the
# content being posted nrws (short for NewsReap Work Space)
STAGE_DIR_SUFFIX = '.nrws'

# The directory we can find our prep'ed content in
PREP_DIR = 'prep'

# Defines the auto keyword we accept to which we detect our archive split
# size based on the size of the content we're archiving.
# See detect_split_size() below for details
PREP_AUTO_ARCHIVE_RE = re.compile('\s*auto\s*', re.I)

# Staging
STAGE_DIR = 'staged'


class NNTPPostFactory(object):
    """
    Simplifies posting content to an NNTP Server

    You can point this object to a directory containing binary files and it
    will generate your NNTPArticles that you can directly post to an NNTP
    server with.
    """

    # The name (based on the directory of the original path)
    path = None

    # The absolute path to the content to be posted
    path = None

    # The root path content will be staged
    staging_root = None

    # The path to where we zip/rar/par etc
    # content is still in a binary format in this dir.  If no
    # prepping occurs, then the reference directory is used instead
    # Simply specify --skip-prep if you like things the way they are
    prep_path = None

    # The path to where all files are prior to posting; content residing
    # in this directory is already in a text format
    stage_path = None

    # The path to our database for managing our staged content
    db_path = None

    # The sqlalchemy engine reference
    engine = None

    # our Database object
    _db = None

    # The default size to split our content up as.  This defines the maximum
    # size of each of the uploaded articles
    split_size = '760KB'

    # If archiving is performed on grouped content, the following defines the
    # maximum size of an archived file before we split it up.  This value is
    # passed directly into zip, 7z, rar, etc
    archive_size = 'auto'

    # A boolean that allows us to enable/disable certain parts of our factory
    # depending on whether or not we loaded content successfully
    _loaded = False

    # Defines hook(s) allowing us to over-ride sections of the
    # process. This grants the user to ability to manipulate
    # content prior to it being posted and/or staged
    _hooks = None

    # A link to either an NNTPManager or NNTPConnection object
    # This only referenced for the upload and verify stages
    connection = None

    # A pointer to an NZB-File object if it exists
    nzb = None

    def __init__(self, connection=None, hooks=None, *args, **kwargs):
        """
        Initializes an NNTPPost object

        The hooks can be a name of a file found in hooks/post/ or it can be
        a path to a hook file you've defined elsewhere. Hook files allow you to
        declare your own handling (assuming you include the decorator) ie:

            # include the module
            from newsreap.decorators import post_staging

            @post_staging
            def my_function(article, *args, **kwargs):

               # mangle the article now in any way you want. If you want to
               # skip over this file, you can just return False and the file
               # will not be processed/posted
               #
               # If you return a different NNTPArticle, NNTPSegmentedFile or a
               # set of, then the article passed in will be ignored and the
               # returned content will be used instead.

        """

        # Setup our hooks
        self._hooks = self.get_hooks(hooks)

        # Set up our connection object
        self.connection = connection

    def load(self, path, hooks=True, *args, **kwargs):
        """
        Takes a path and loads it into the Post Factory

        If hooks is set to True, it will continue using whatever hooks are
        already in place.  Otherwise you can define your own hooks here

        """

        if hooks is not True:
            self._hooks = self.get_hooks(hooks)

        # Ensure we're not loaded
        self._loaded = False

        if not exists(path):
            # Some simple error checking
            logger.warning("Could not locate '%s'." % path)
            return False

        # Get our filename and/or dir absolute path
        self.path = abspath(expanduser(path))

        # Record our name
        self.name = basename(self.path)

        # The root path content will be staged
        # .nrs is short for Newsreap staged
        self.staging_root = '{0}{1}'.format(self.path, STAGE_DIR_SUFFIX)

        # NZB-File (if one is loaded)
        self.nzb = None

        # The path to where we zip/rar/par etc
        # content is still in a binary format in this dir.  If no
        # prepping occurs, then the reference directory is used instead
        # Simply specify --skip-prep if you like things the way they are
        self.prep_path = self.path

        prep_path = join(self.staging_root, PREP_DIR)
        if isdir(prep_path):
            # Adust our prepare pointer
            self.prep_path = prep_path

        # The path to where all files are prior to posting; content residing
        # in this directory is already in a text format
        self.stage_path = join(self.staging_root, STAGE_DIR)

        # The path to our database for managing our staged content
        self.db_path = join(self.staging_root, '{}.db'.format(STAGE_DIR))
        self.engine = 'sqlite:///%s' % self.db_path
        self._db = None

        # Prepare our content working_dir
        if not isdir(self.staging_root):
            if not mkdir(self.staging_root):
                logger.error(
                    "Could not create staging root directory '%s'." %
                    self.staging_root)
                return False

        # It's safe to toggle our flag now
        self._loaded = True
        return True

    def prepare(self, archive_size=None, *args, **kwargs):
        """
        A Wrapper to _prepare() as this allows us to call our post_hooks
        properly each time.

        """
        if self._loaded is False:
            # Content must be loaded!
            return False

        # Initialize our return state
        status = False

        try:
            response = self.call_hook(
                'pre_prepare',
                name=self.name,
                path=self.prep_path,
            )

            if False not in [r for (_, r) in response.iteritems()]:
                status = self._prepare(
                    archive_size=archive_size, *args, **kwargs)

            else:
                logger.warning("Preparation aborted by pre_prepare() hook.")
                # abort specified; set status to None
                status = None

        finally:
            self.call_hook(
                'post_prepare',
                name=self.name,
                path=self.prep_path,
                status=status,
            )
        return status

    def _prepare(self, archive_size=None, *args, **kwargs):
        """
        Prepares our content

        """

        if self._loaded is False:
            # Content must be loaded!
            return False

        if archive_size is None:
            archive_size = self.archive_size

        # Adust our prepare pointer
        self.prep_path = join(self.staging_root, PREP_DIR)

        # we ONLY remove the prep_path if we're performing
        # a prep ourselves
        if not rm(self.prep_path):
            logger.error(
                "Could not remove a stale prep directory '%s'." %
                self.prep_path)
            return False

        if not mkdir(self.prep_path):
            logger.error(
                "Could not create prep directory '%s'." %
                self.prep_path)
            return False

        # Get our entries (if any)
        entries = find(self.path, min_depth=1, max_depth=1)
        if not entries:
            # Nothing more to do if there isn't any entries
            logger.error("There is no content to prepare in '%s'." % self.path)
            return False

        # Ensure our database does not exist
        if not rm(self.db_path):
            logger.error(
                "Could not remove stale database '%s'." %
                self.db_path)
            return False

        # ensure our stage path does not exist
        if not rm(self.stage_path):
            logger.error(
                "Could not remove stale staging path '%s'." %
                self.stage_path)
            return False

        if isinstance(archive_size, basestring) and \
                PREP_AUTO_ARCHIVE_RE.search(archive_size):

            # Automatically detect our archive split size
            size = dirsize(self.path)
            if size is None:
                # There is content we don't have access to in this directory;
                # We might as well fail now instead of later...
                logger.error(
                    "Could not detect the total size of content "
                    "prepared in %s" % self.path)
                return False

            # Store our new size
            archive_size = self.detect_split_size(size)

        logger.info("Preparing %s for posting." % (self.path))

        # Initialize Codecs
        crar = CodecRar(work_dir=self.staging_root, volume_size=archive_size)
        cpar = CodecPar(work_dir=self.staging_root)

        # For each entry, prep our stage environment
        for entry in sorted(entries):
            crar.add(entry)

        # Archive our content
        archived_content = crar.encode(name=basename(self.path))
        if archived_content is None:
            logger.error("Could not prepare archive")

        # We want to create par files now
        logger.debug("Preparing Parchive")
        for archive in archived_content:
            if not archive.save(filepath=self.prep_path):
                # We failed to save the content
                logger.error("Could not write '%s'." % archive.path())

                # Safety cleanup
                rm(self.prep_path)
                return False

            cpar.add(archive.path())

        par2_content = cpar.encode()
        for par2 in par2_content:
            if not par2.save(filepath=self.prep_path):
                # We failed to save the content
                logger.error("Could not write '%s'." % archive.path())

                # Safety cleanup
                rm(self.prep_path)
                return False

        self.call_hook('post_prep', path=self.prep_path)

        return True

    def stage(self, groups, split_size=None, poster=None, subject=None,
              *args, **kwargs):
        """
        A Wrapper to _stage() as this allows us to call our post_hooks
        properly each time.

        """

        if self._loaded is False:
            # Content must be loaded!
            return False

        groups = NNTPGroup.split(groups)
        if not groups:
            logger.warning("No groups defined.")

        # Initialize our return state
        status = False

        # we ONLY remove the stage_path if we're performing
        # a stage ourselves
        if not rm(self.stage_path):
            logger.error(
                "Could not remove a stale stage directory '%s'." %
                self.stage_path)
            return False

        if not rm(self.db_path):
            logger.error(
                "Could not remove a stale stage db '%s'." %
                self.db_path)
            return False

        # Create our staging directory if it doesn't already exist
        if not mkdir(self.stage_path):
            logger.error(
                "Could not create staging directory '%s'." % self.stage_path)
            return False

        try:
            response = self.call_hook(
                'pre_stage',
                name=self.name,
                path=self.prep_path,
            )

            if False not in [r for (_, r) in response.iteritems()]:
                status = self._stage(
                     groups=groups,
                     split_size=split_size,
                     poster=poster,
                     subject=subject,
                     *args, **kwargs)

            else:
                logger.warning("Staging aborted by pre_stage() hook.")
                # abort specified; set status to None
                status = None

        finally:
            self.call_hook(
                'post_stage',
                name=self.name,
                path=self.prep_path,
                status=status,
            )

        return status

    def _stage(self, groups, split_size=None, poster=None, subject=None,
               *args, **kwargs):
        """
        Stages our content so that it can be posted to the NNTP Server

        if split_size is set to None, then the defaults are used instead.
        Specifying zero (0) is a valid option as well if you don't want any
        splitting to occur.

        """

        if not isdir(self.stage_path):
            return False

        if split_size is None:
            split_size = self.split_size

        # Find our content
        entries = find(self.prep_path, min_depth=1, max_depth=1)
        if not entries:
            # Nothing more to do if there isn't any entries
            logger.error(
                "There is no content to stage with in '%s'." % self.prep_path)
            return False

        # Our encoder
        encoder = CodecYenc(work_dir=self.stage_path)

        # Acquire our session
        session = self.session()
        if not session:
            logger.error(
                "Staging {} could not be accessed.".format(
                    self.engine,
                ),
            )
            return False

        logger.info("Staging %s for posting." % self.path)

        # For each entry, we need to encode it
        for sort_no, entry in enumerate(sorted(entries)):
            if not isfile(entry):
                logger.warning(
                    "The entry '{}' is not file and therefore can not be "
                    "staged.".format(entry))
                continue

            # Create a post object
            post = NNTPSegmentedPost(
                entry,
                work_dir=self.stage_path,
                groups=groups,
                poster=poster,
                subject=subject,
            )

            # Split our content up based on our split-size
            if strsize_to_bytes(split_size):
                if not post.split(size=split_size):
                    logger.error(
                        "Could not split content '%s' (split-size=%s)." % (
                            split_size, entry))
                    return False

            # Encode our content so that it's post-able
            if not post.encode((encoder, )):
                logger.error(
                    "Could not encode (%d) article(s)." % (len(post)))
                return False

            if not post.apply_template():
                logger.error("Could not apply posting template.")
                return False

            # Save our content (written to our work_dir)
            if not post.save():
                logger.error("Could write yEncoded content '%s'." % entry)
                return False

            # The best hook of them all
            self.call_hook(
                'staged_segment',
                name=self.name,
                path=self.prep_path,
                segment=weakref.proxy(post),
            )

            self.save_segment(post, sort_no=sort_no+1, commit=False)

        # Pass along some meta information we can use as part of the NZB-File
        self._db.set('filename', basename(self.path))
        session.commit()

        return True

    def upload(self, groups=None, *args, **kwargs):
        """
        A Wrapper to _upload() as this allows us to call our post_hooks
        properly each time.

        """
        if self._loaded is False:
            # Content must be loaded!
            return False

        # Initialize our return state
        status = False

        # Acquire our session
        session = self.session()
        if not session:
            logger.error(
                "{} could not be accessed.".format(
                    self.engine,
                ),
            )
            return False

        try:
            response = self.call_hook(
                'pre_upload',
                name=self.name,
                path=self.prep_path,
            )

            if False not in [r for (_, r) in response.iteritems()]:
                status = self._upload(groups=groups, *args, **kwargs)

            else:
                logger.warning("Upload aborted by pre_upload() hook.")
                # abort specified; set status to None
                status = None

        finally:
            self.call_hook(
                'post_upload',
                name=self.name,
                path=self.prep_path,
                status=status,
                nzb=weakref.proxy(self.nzb),
            )

        return status

    def _upload(self, groups=None, *args, **kwargs):
        """
        Uploading is only possible:
          - if content has been properly staged.
          - if each element of the staged content has at least 1 group to post
             to with.
          - if content has been marked as uploaded already from a previously
             staged setting.

        """
        # Acquire our session
        session = self.session()
        if not session:
            logger.error(
                "{} could not be accessed.".format(
                    self.engine,
                ),
            )
            return False

        if not isinstance(self.connection, (NNTPConnection, NNTPManager)):
            logger.error("No connection object defined for upload.")
            return False

        # Our NZB-File
        self.nzb = NNTPnzb(work_dir=self.staging_root)

        # using the database we rebuild NNTPSegmentedPost objects and do
        # our upload.
        sa_query = session.query(StagedArticle)\
            .order_by(
                StagedArticle.sort_no.asc(),
                StagedArticle.sequence_no.asc()).all()

        # Simply maps the filename to a response
        upload_map = {}

        # A flag we'll use to track the upload status
        upload_status = True

        # Our segment
        segment = None

        # Track our pending updates
        pending_updates = 0

        # Get default groups
        groups = NNTPGroup.split(groups)

        for entry in sa_query:
            # Assemble our expected file
            path = join(self.stage_path, entry.localfile)

            if not isfile(path):
                # Local file is missing; we can't post
                logger.error("Missing article '%s'." % entry.localfile)
                return False

            # Our Groups
            article_groups = [
                x.name for x in session.query(StagedArticleGroup)
                .filter(StagedArticleGroup.article_id == entry.id).all()]

            if not article_groups:
                # Fall back to default specified
                article_groups = groups

            # Our Headers
            headers = {
                x.key: x.value for x in session.query(StagedArticleHeader)
                .filter(StagedArticleHeader.article_id == entry.id).all()}

            if segment is None or entry.sort_no != segment.sort_no:
                if segment is not None:
                    # Add our old entry
                    self.nzb.add(segment)

                # a new file; only index 1 is important for our SegmentedPost
                # Entry
                segment = NNTPSegmentedPost(
                    filename=entry.localfile,
                    subject=entry.subject,
                    poster=entry.poster,
                    groups=article_groups,
                    work_dir=self.staging_root,
                    sort_no=entry.sort_no,
                )

            # Prepare our article
            article = NNTPArticle(
                id=entry.message_id,
                work_dir=self.staging_root,
                subject=entry.subject,
                poster=entry.poster,
                body=entry.body,
                groups=article_groups,
                no=entry.sequence_no,
            )

            # Store our headers
            for key, value in headers.iteritems():
                article.header[key] = value

            if not article.add(path):
                # Could not add our file
                logger.error(
                    "Could not append article '%s'." % entry.localfile)
                return False

            if article[0].sha1() != entry.sha1:
                # Local file is missing; we can't post
                logger.error(
                    "Article '%s' fails checksum." % entry.localfile)
                return False

            article[0].filename = entry.remotefile

            # Add our article to our segment
            segment.add(article)

            # Only upload our content if we have a group associated with it
            if len(article.groups) == 0:
                logger.warning("Skipping '%s' Reason: No groups defined." % (
                    entry.localfile,
                ))
                continue

            if entry.verified_date is not None:
                # Nothing more to do; content has been pushed
                logger.info(
                    "Skipping '%s' Reason: Already uploaded+verified." % (
                        entry.localfile))
                continue

            if entry.posted_date is None:
                # Verify the article isn't already posted
                response = self.connection.stat(
                    article.msgid(),
                    full=False,
                    group=next(iter(article.groups)),
                )

                if response is None:
                    logger.warning("Could not pre-verify Message-ID '%s'." % (
                        article.msgid(),
                    ))
                    continue

                elif response:
                    logger.warning("Message-ID exists already '%s'." % (
                        article.msgid(),
                    ))
                    # Update our Message-ID
                    article.msgid(reset=True)

                # capture our response
                response = self.call_hook(
                    'upload_article',
                    name=self.name,
                    path=self.prep_path,
                    article=weakref.proxy(article),
                )

                if False not in [r for (_, r) in response.iteritems()]:
                    # else response is False (this is a good thing, the message
                    # just simply does not exist on the server)
                    logger.info("Uploading '%s' to '%s'." % (
                        entry.localfile,
                        ', '.join(x.name for x in article.groups),
                    ))

                    # Perform (async) post
                    upload_map[entry.id] = self.connection.post(
                        # our payload
                        article,
                        # We must update our headers because there is a
                        # possiblity our Message-ID changed (above) if the
                        # item already appeared to exist on our NNTP Server.
                        update_headers=True,
                        # We want the result of all of our posted content, not
                        # just the ones that were successful.
                        success_only=False,
                        # Never block
                        block=False,
                    )[0]

        if segment is not None:
            # Add our segment
            self.nzb.add(segment)

        # At this stage we have an NZB-File created; save it
        if not self.nzb.save('{0}.nzb'.format(self.path)):
            logger.warning("Could not save NZB-File: %s.nzb." % (
                basename(self.path)))
            return False

        # Block until our uploads have finished and report them accordingly
        for article_id, _connection in upload_map.iteritems():
            # Ensure we're done
            _connection.wait()

            # Update the pointer to our response object
            response = _connection.response[0][0]
            article = _connection.response[0][0].body

            if NNTPResponseCode.SUCCESS not in response:
                upload_status = False
                logger.warning("Could not post Message-ID: %s." % article_id)
                continue

            # Mark our content as posted
            if not session\
                    .query(StagedArticle)\
                    .filter(StagedArticle.id == article_id)\
                    .update({
                        # Our posted date
                        StagedArticle.posted_date: response.created,
                    }):
                logger.warning("Failed to mark upload successful.")
                continue

            # Save the rest of our article details (in case they changed)
            if not self.save_article(article, id=article_id, commit=False):
                logger.warning("Failed to update article details.")

            pending_updates += 1

        if pending_updates:
            session.commit()

        return upload_status

    def verify(self, *args, **kwargs):
        """
        A Wrapper to _verify() as this allows us to call our post_hooks
        properly each time.

        """

        if self._loaded is False:
            # Content must be loaded!
            return False

        # Initialize our return state
        status = False

        # Acquire our session
        session = self.session()
        if not session:
            logger.error(
                "{} could not be accessed.".format(
                    self.engine,
                ),
            )
            return False

        try:
            response = self.call_hook(
                'pre_verify',
                name=self.name,
                path=self.prep_path,
            )

            if False not in [r for (_, r) in response.iteritems()]:
                status = self._verify(*args, **kwargs)

            else:
                logger.warning("Verification aborted by pre_verify() hook.")
                # abort specified; set status to None
                status = None

        finally:
            self.call_hook(
                'post_verify',
                name=self.name,
                path=self.prep_path,
                status=status,
            )

        return status

    def _verify(self, *args, **kwargs):
        """
        Verifies if the content is posted to usenet properly or not

        """

        # Simply maps the filename to a response
        verification_map = {}

        # A flag we'll use to track the verification status
        verification_status = True

        # Acquire our session
        session = self.session()
        if not session:
            logger.error(
                "{} could not be accessed.".format(
                    self.engine,
                ),
            )
            return False

        if not isinstance(self.connection, (NNTPConnection, NNTPManager)):
            logger.error("No connection object defined for upload.")
            return False

        # Verification
        # using the database we rebuild NNTPSegmentedPost objects and do
        # our upload.
        sa_query = session.query(StagedArticle)\
            .order_by(
                StagedArticle.sort_no.asc(),
                StagedArticle.sequence_no.asc()).all()

        pending_updates = 0

        for entry in sa_query:

            if entry.verified_date is not None:
                # Nothing more to do; content has been pushed
                logger.info('Successfully verified %s (%s)' % (
                    entry.localfile,
                    entry.verified_date.strftime('%Y-%m-%d %H:%M:%S'),
                ))
                continue

            if entry.posted_date is None:
                # Nothing more to do; content has been pushed
                logger.warning('Content not posted: %s' % entry.localfile)
                continue

            # Our Groups
            groups = [x.name for x in
                      session.query(StagedArticleGroup)
                      .filter(StagedArticleGroup.article_id == entry.id).all()]

            for group in groups:
                key = '{group}::{id}'.format(
                    group=group,
                    id=entry.message_id,
                )
                # Verify that our article got posted
                verification_map[key] = {
                    'response': self.connection.stat(
                            entry.message_id,
                            full=True,
                            group=group,
                            block=False,
                        ),
                    'id': entry.id,
                    'msgid': entry.message_id,
                    'group': group,
                }

        # Update our verification map
        for key, meta in verification_map.items():
            # Ensure we're done
            meta['response'].wait()

            response = meta['response'].response[0]
            if not isinstance(response, NNTPHeader):
                logger.warning("Could not verify Message-ID %s." % (
                    meta['msgid']))

                # Toggle flag
                verification_status = False
                continue

            # we're good!
            # Mark our content as verified
            reference = datetime.now()
            if not session\
                    .query(StagedArticle)\
                    .filter(StagedArticle.id == meta['id'])\
                    .update({StagedArticle.verified_date: reference}):
                logger.warning("Failed to mark verification successful.")

            else:
                logger.info("Successful verification of Message-ID %s." % (
                    meta['msgid'],
                ))
                pending_updates += 1

        if pending_updates > 0:
            session.commit()

        return verification_status

    def clean(self, *args, **kwargs):
        """
        A Wrapper to _clean() as this allows us to call our post_hooks
        properly each time.
        """
        if self._loaded is False:
            # Content must be loaded!
            return False

        # Initialize our return state
        status = False

        try:
            response = self.call_hook(
                'pre_clean',
                name=self.name,
                path=self.prep_path,
            )

            if False not in [r for (_, r) in response.iteritems()]:
                status = self._clean(*args, **kwargs)

            else:
                logger.warning("Cleanup aborted by pre_clean() hook.")
                # abort specified; set status to None
                status = None

        finally:
            self.call_hook(
                'post_clean',
                name=self.name,
                path=self.prep_path,
                status=status,
            )

        return status

    def _clean(self, *args, **kwargs):
        """
        Eliminate all content in the temporary working directory for a
        given prepable path
        """
        if not rm(self.staging_root):
            logger.error(
                "Could not remove staging root directory '%s'." %
                self.staging_root)

            return False
        return True

    def save_segment(self, segment, sort_no=1, commit=True):
        """
        saves a segmented post to the database

        """

        session = self.session()
        if not session:
            logger.error(
                "{} could not be accessed.".format(
                    self.engine,
                ),
            )
            return False

        for sequence_no, article in enumerate(segment):
            # prepare our database object
            if not self.save_article(
                    article,
                    sequence_no=sequence_no+1,
                    sort_no=sort_no,
                    commit=False,
                    ):
                logger.warning(
                    "Could not save new article %s" % article.msgid())

        if commit:
            session.commit()

        return True

    def save_article(self, article, sequence_no=1, sort_no=1, id=None,
                     commit=True):
        """
        Takes an article and saves it back to the database over-writing what
        is there. If no id is specified, then a new record is saved.

        """
        if not self._loaded:
            return False

        # Acquire our session
        session = self.session()
        if not session:
            logger.error(
                "{} could not be accessed.".format(
                    self.engine,
                ),
            )
            return False

        if id:
            # get our id
            sa = session.query(StagedArticle)\
                .filter(StagedArticle.id == id).one()
            if not sa:
                # Does not exist
                return None

            # Perform update
            session.query(StagedArticleGroup)\
                .filter(StagedArticleGroup.article_id == sa.id).delete()

            session.query(StagedArticleHeader)\
                .filter(StagedArticleHeader.article_id == sa.id).delete()

            session\
                .query(StagedArticle)\
                .filter(StagedArticle.id == sa.id)\
                .update({
                    # The localfile is the path on our disk (stage path)
                    # This should never change or our post will fail,
                    # This is also our primary key
                    StagedArticle.localfile: article[0].filename,
                    # The sha1() of our content
                    StagedArticle.sha1: article[0].sha1(),

                    # Our Message-ID could have changed, be sure to
                    # Include it in our update
                    StagedArticle.message_id: article.msgid(),
                    StagedArticle.subject: article.subject,
                    StagedArticle.body: unicode(article.body),
                    StagedArticle.poster: article.poster,
                    StagedArticle.remotefile: article[0].filename,
                    StagedArticle.size: article.size(),
                    StagedArticle.sequence_no: sequence_no,
                    StagedArticle.sort_no: sort_no,
                })

        else:
            # Perform Insert
            sa = StagedArticle(
                # The localfile is the path on our disk (stage path)
                # This should never change or our post will fail,
                # This is also our primary key
                localfile=article[0].filename,
                # The sha1() of our content
                sha1=article[0].sha1(),

                # The below is for anyone to manipulate prior to
                # a post to adjust where content is sent to
                message_id=article.msgid(),
                subject=article.subject,
                body=unicode(article.body),
                poster=article.poster,
                remotefile=article[0].filename,
                size=article.size(),
                sequence_no=sequence_no,
                sort_no=sort_no,
            )

            session.add(sa)

            # Flush so we have access to to our primary key created
            # from above isert.
            session.flush()

        # Store our groups associated with the article now
        for _group in article.groups:
            session.add(
                StagedArticleGroup(
                    name=str(_group),
                    article_id=sa.id,
                )
            )

        # Store our header(s) associated with the article now
        for _key, _value in article.header.items():
            session.add(
                StagedArticleHeader(
                    key=str(_key),
                    value=str(_value),
                    article_id=sa.id,
                )
            )

        if commit:
            session.commit()

        return True

    def get_hooks(self, hooks=None):
        """
        Initializes any hooks set

        """
        if hooks is True:
            # No change
            return self._hooks

        if hooks is None:
            # Nothing to do
            return None

        # Get all of the entries from what was specified
        paths = parse_paths(hooks)

        here = scan_pylib('.')
        there = scan_pylib(join(
            dirname(abspath(__file__)),
            'hooks', 'post'
        ))

        _loaded = list()

        for hook in paths:
            if isfile(hook):
                # We were referenced a (python) file directly
                # Attempt to load it
                result = load_pylib(hook)

            elif hook in here:
                # We found our hook
                result = load_pylib(hook, next(iter(here[hook])))

            elif hook in there:
                # We found our hook
                result = load_pylib(hook, next(iter(there[hook])))

            else:
                # not a supported entry
                logger.warning("Could not locate hook '{}'.".format(hook))
                continue

            if result is not None:
                _loaded.append(result)

        return _loaded

    def call_hook(self, function_name, *args, **kwargs):
        """
        wrapper to call posting hooks

        """
        if not self._hooks:
            return dict()

        response = dict()
        for hook in self._hooks:
            if not hasattr(hook, function_name):
                continue

            try:
                # Execute our function
                response[hook] = getattr(hook, function_name)(*args, **kwargs)

            except:
                logger.warning(
                    "Hook Exception calling {0}.{1}'."
                    .format(hook, function_name))
                # We don't care, we're moving on anyway
                pass

        # Return all of our responses
        return response

    def session(self, reset=False):
        """
        Returns a database session
        """
        if not self._loaded:
            return False

        if not isfile(self.db_path):
            reset = True

        if self._db and reset is True:
            self._db = None

        if self._db is None:
            # Reset our database
            self._db = NNTPPostDatabase(engine=self.engine, reset=reset)

        # Acquire our session
        return self._db.session()

    def detect_split_size(self, size):
        """
        Takes a given size and returns what archives should split on. The
        rules are as follows:
           0-100MB     ->   5MB/archive
           100MB-1GB   ->  15MB/archive
           1GB-5GB     ->  50MB/archive
           5GB-15GB    -> 100MB/archive
           15GB-25GB   -> 200MB/archive
           25GB+       -> 400MB/archive

        If you pass in garbage, expect garbage in return. the function will
        return False.  This is intentional because codecs that recieve a
        archive split_size of False interpret it as 'do not' split our content

        """
        size = strsize_to_bytes(size)
        if size is None:
            # An unknown size; so return no-split
            return False

        if size < 104857600:
            return 5242880

        elif size < 1073741824:
            return 15728640

        elif size < 5368709120:
            return 52428800

        elif size < 16106127360:
            return 104857600

        elif size < 26843545600:
            return 209715200

        return 419430400
