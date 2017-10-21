# -*- coding: utf-8 -*-
#
# Common Logging Parameters and Defaults
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

# The first part of the file defines all of the namespacing
# used by this application

import sys
import logging

# We intentionally import this module so it preconfigures it's logging
# From there we can choose to manipulate it later without worrying about
# it's configuration over-riding ours; This creates a lint warning
# that we're importing a module we're not using; but this is intended.
# do not comment out or remove this entry
import sqlalchemy

# The default logger identifier used for general logging
NEWSREAP_LOGGER = 'newsreap'

# The default logger which displays backend engine and
# NNTP Server Interaction
NEWSREAP_ENGINE = '%s.engine' % NEWSREAP_LOGGER

# Codec Manipulation such as yEnc, uuencoded, etc
NEWSREAP_CODEC = '%s.codec' % NEWSREAP_LOGGER

# Command Line Interface Logger
NEWSREAP_CLI = '%s.cli' % NEWSREAP_LOGGER

# For a common reference point, we include the static logging
# Resource at the time for this information was:
# - http://docs.sqlalchemy.org/en/rel_1_0/core/engines.html#dbengine-logging
#
# namespaces used by SQLAlchemy
SQLALCHEMY_LOGGER = 'sqlalchemy'

# Defines the logger for the SQLAlchemy Engine
SQLALCHEMY_ENGINE = '%s.engine' % SQLALCHEMY_LOGGER

# Controls SQLAlchemy's connection pool logging.
SQLALCHEMY_POOL = '%s.pool' % SQLALCHEMY_LOGGER

# Controls SQLAlchemy's various Object Relational Mapping (ORM) logging.
SQLALCHEMY_ORM = '%s.orm' % SQLALCHEMY_LOGGER

# The number of bytes reached before automatically rotating the log file
# if this option was specified
# 5000000 bytes == 5 Megabytes
LOG_ROTATE_FILESIZE_BYTES = 5000000

def add_handler(logger, sendto=True, backupCount=5):
    """
    Add handler to idenfied logger
        sendto == None then logging is disabled
        sendto == True then logging is put to stdout
        sendto == False then logging is put to stderr
        sendto == <string> then logging is routed to the filename specified

        if sendto is a <string>, then backupCount defines the number of logs
        to keep around.  Set this to 0 or None if you don't wish the python
        logger to backupCount the files ever. By default logs are rotated
        once they reach 5MB

    """
    if sendto is True:
        # redirect to stdout
        handler = logging.StreamHandler(sys.stdout)

    elif sendto is False:
        # redirect to stderr
        handler = logging.StreamHandler(sys.stderr)

    elif sendto is None:
        # redirect to null
        try:
            handler = logging.NullHandler()
        except AttributeError:
            # Python <= v2.6
            class NullHandler(logging.Handler):
                def emit(self, record):
                    pass
            handler = NullHandler()

        # Set data to NOTSET just to eliminate the
        # extra checks done internally
        if logger.level != logging.NOTSET:
            logger.setLevel(logging.NOTSET)

    elif isinstance(sendto, basestring):
        if backupCount is None:
            handler = logging.FileHandler(filename=sendto)

        elif isinstance(backupCount, int):
            handler = logging.RotatingFileHandler(
                filename=sendto,
                maxBytes=LOG_ROTATE_FILESIZE_BYTES,
                backupCount=backupCount,
            )

    else:
        # We failed to add a handler
        return False

    # Setup Log Format
    handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s %(name)s %(message)s'))

    # Add Handler
    logger.addHandler(handler)

    return True


def init(verbose=2, sendto=True, backupCount=5):
    """
    Set's up some simple default handling to make it
    easier for those wrapping this library.

    You do not need to call this function if you
    don't wnat to; ideally one might want to set up
    things their own way.
    """
    # Add our handlers at the parent level
    add_handler(
        logging.getLogger(SQLALCHEMY_LOGGER),
        sendto=True,
        backupCount=backupCount,
    )
    add_handler(
        logging.getLogger(NEWSREAP_LOGGER),
        sendto=True,
        backupCount=backupCount,
    )

    if verbose:
        set_verbosity(verbose=verbose)


def set_verbosity(verbose):
    """
    A simple function one can use to set the verbosity of
    the app.
    """
    # Default
    logging.getLogger(SQLALCHEMY_LOGGER).setLevel(logging.ERROR)
    logging.getLogger(SQLALCHEMY_ENGINE).setLevel(logging.ERROR)
    logging.getLogger(NEWSREAP_LOGGER).setLevel(logging.ERROR)
    logging.getLogger(NEWSREAP_CLI).setLevel(logging.ERROR)
    logging.getLogger(NEWSREAP_CODEC).setLevel(logging.ERROR)
    logging.getLogger(NEWSREAP_ENGINE).setLevel(logging.ERROR)

    # Handle Verbosity
    if verbose > 0:
        logging.getLogger(NEWSREAP_CLI).setLevel(logging.INFO)
        logging.getLogger(NEWSREAP_ENGINE).setLevel(logging.INFO)

    if verbose > 1:
        logging.getLogger(NEWSREAP_CLI).setLevel(logging.DEBUG)
        logging.getLogger(NEWSREAP_ENGINE).setLevel(logging.DEBUG)

    if verbose > 2:
        logging.getLogger(SQLALCHEMY_ENGINE).setLevel(logging.INFO)
        logging.getLogger(NEWSREAP_CODEC).setLevel(logging.INFO)

    if verbose > 3:
        logging.getLogger(NEWSREAP_CODEC).setLevel(logging.DEBUG)

    if verbose > 4:
        logging.getLogger(SQLALCHEMY_ENGINE).setLevel(logging.DEBUG)

# set initial level to WARN.
rootlogger = logging.getLogger(NEWSREAP_LOGGER)
if rootlogger.level == logging.NOTSET:
    set_verbosity(-1)
