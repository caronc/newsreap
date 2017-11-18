# -*- coding: utf-8 -*-
#
# An example post processing hook file
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
from pprint import pformat
from newsreap.decorators import hook

import logging
from newsreap.Logging import NEWSREAP_CLI
logger = logging.getLogger(NEWSREAP_CLI)


@hook()
def pre_prepare(*args, **kwargs):
    """
    this function gets called prior to an issued prepare

    If you return False here, you will skip the preparation entirely
    """
    logger.info(
        'DEBUG HOOK pre_prep()\n{args}\n{kwargs}'.format(
            args=pformat(args, indent=4, depth=2),
            kwargs=pformat(kwargs, indent=4, depth=2),
        )
    )


@hook(name="pre_prepare", priority=1000)
def pre_prepare_second_call(*args, **kwargs):
    """
    this function gets called prior to an issued prepare but gets
    called 'after' the first pre_prepare() call identified above

    If you return False here, you will skip the preparation entirely
    """
    logger.info(
        'DEBUG HOOK pre_prep()\n{args}\n{kwargs}'.format(
            args=pformat(args, indent=4, depth=2),
            kwargs=pformat(kwargs, indent=4, depth=2),
        )
    )


@hook
def post_prepare(*args, **kwargs):
    """
    this function gets called after to an issued prepare

    """
    logger.info(
        'DEBUG HOOK post_prep()\n{args}\n{kwargs}'.format(
            args=pformat(args, indent=4, depth=2),
            kwargs=pformat(kwargs, indent=4, depth=2),
        )
    )


@hook()
def pre_stage(*args, **kwargs):
    """
    this function gets called prior to an issued stage

    If you return False here, you will skip the staging entirely
    """
    logger.info(
        'DEBUG HOOK pre_stage()\n{args}\n{kwargs}'.format(
            args=pformat(args, indent=4, depth=2),
            kwargs=pformat(kwargs, indent=4, depth=2),
        )
    )


@hook()
def post_encoded_filename(*args, **kwargs):
    """
    this function gets called just before the encoding of a filename.

    If you return a new filename here then it will be used instead
    of the one passed in

    """
    logger.info(
        'DEBUG HOOK post_encoded_filename()\n{args}\n{kwargs}'.format(
            args=pformat(args, indent=4, depth=2),
            kwargs=pformat(kwargs, indent=4, depth=2),
        )
    )


@hook()
def post_staged_segment(*args, **kwargs):
    """
    this function gets called after a segment has been staged for posting

    """
    logger.info(
        'DEBUG HOOK post_staged_segment()\n{args}\n{kwargs}'.format(
            args=pformat(args, indent=4, depth=2),
            kwargs=pformat(kwargs, indent=4, depth=2),
        )
    )


@hook()
def post_staged_nzb(*args, **kwargs):
    """
    this function gets called after a NZB-File has been staged for saving

    """
    logger.info(
        'DEBUG HOOK post_staged_nzb()\n{args}\n{kwargs}'.format(
            args=pformat(args, indent=4, depth=2),
            kwargs=pformat(kwargs, indent=4, depth=2),
        )
    )


@hook()
def post_stage(session, *args, **kwargs):
    """
    this function gets called prior to an issued stage

    """
    logger.info(
        'DEBUG HOOK post_stage()\n{args}\n{kwargs}'.format(
            args=pformat(args, indent=4, depth=2),
            kwargs=pformat(kwargs, indent=4, depth=2),
        )
    )


@hook()
def pre_upload(*args, **kwargs):
    """
    this function gets called prior to an issued upload

    If you return False here, you will skip the upload entirely

    """
    logger.info(
        'DEBUG HOOK pre_upload()\n{args}\n{kwargs}'.format(
            args=pformat(args, indent=4, depth=2),
            kwargs=pformat(kwargs, indent=4, depth=2),
        )
    )


@hook()
def upload_article(*args, **kwargs):
    """
    this function gets called prior to an actual article upload

    If you return False from this function, you 'will' prevent
    the article from being uploaded.

    """
    logger.info(
        'DEBUG HOOK upload_article()\n{args}\n{kwargs}'.format(
            args=pformat(args, indent=4, depth=2),
            kwargs=pformat(kwargs, indent=4, depth=2),
        )
    )


@hook()
def post_upload(*args, **kwargs):
    """
    this function gets called after an upload has completed

    """
    logger.info(
        'DEBUG HOOK post_upload()\n{args}\n{kwargs}'.format(
            args=pformat(args, indent=4, depth=2),
            kwargs=pformat(kwargs, indent=4, depth=2),
        )
    )


@hook()
def pre_verify(*args, **kwargs):
    """
    this function gets called prior to running a verification check

    If you return False here, you will skip the verify entirely

    """
    logger.info(
        'DEBUG HOOK pre_verify()\n{args}\n{kwargs}'.format(
            args=pformat(args, indent=4, depth=2),
            kwargs=pformat(kwargs, indent=4, depth=2),
        )
    )


@hook()
def post_verify(*args, **kwargs):
    """
    this function gets called after running a verification check

    """
    logger.info(
        'DEBUG HOOK pre_upload()\n{args}\n{kwargs}'.format(
            args=pformat(args, indent=4, depth=2),
            kwargs=pformat(kwargs, indent=4, depth=2),
        )
    )


@hook()
def pre_clean(*args, **kwargs):
    """
    this function gets called prior to running a cleanup

    If you return False here, you will skip the clean-up entirely

    """
    logger.info(
        'DEBUG HOOK pre_clean()\n{args}\n{kwargs}'.format(
            args=pformat(args, indent=4, depth=2),
            kwargs=pformat(kwargs, indent=4, depth=2),
        )
    )


@hook()
def post_clean(*args, **kwargs):
    """
    this function gets called after running a cleanup

    """
    logger.info(
        'DEBUG HOOK pre_upload()\n{args}\n{kwargs}'.format(
            args=pformat(args, indent=4, depth=2),
            kwargs=pformat(kwargs, indent=4, depth=2),
        )
    )
