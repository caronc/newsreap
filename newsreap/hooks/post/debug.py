# -*- coding: utf-8 -*-
from pprint import pformat

import logging
from newsreap.Logging import NEWSREAP_CLI
logger = logging.getLogger(NEWSREAP_CLI)


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


def staged_segment(*args, **kwargs):
    """
    this function gets called after a segment has been staged for posting

    """
    logger.info(
        'DEBUG HOOK staged_segment()\n{args}\n{kwargs}'.format(
            args=pformat(args, indent=4, depth=2),
            kwargs=pformat(kwargs, indent=4, depth=2),
        )
    )


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
