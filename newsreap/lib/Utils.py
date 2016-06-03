# -*- coding: utf-8 -*-
#
# A simple collection of general functions
#
# Copyright (C) 2015 Chris Caron <lead2gold@gmail.com>
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
import errno
from os import listdir
from os import makedirs
from os.path import isdir
from os.path import join
from urlparse import urlparse
from urlparse import parse_qsl
from urllib import quote
from urllib import unquote
from os.path import expanduser

# Pre-Escape content since we reference it so much
ESCAPED_PATH_SEPARATOR = re.escape('\\/')
ESCAPED_WIN_PATH_SEPARATOR = re.escape('\\')
ESCAPED_NUX_PATH_SEPARATOR = re.escape('/')

TIDY_WIN_PATH_RE = re.compile(
    '(^[%s]{2}|[^%s\s][%s]|[\s][%s]{2}])([%s]+)' % (
        ESCAPED_WIN_PATH_SEPARATOR,
        ESCAPED_WIN_PATH_SEPARATOR,
        ESCAPED_WIN_PATH_SEPARATOR,
        ESCAPED_WIN_PATH_SEPARATOR,
        ESCAPED_WIN_PATH_SEPARATOR,
))
TIDY_WIN_TRIM_RE = re.compile(
    '^(.+[^:][^%s])[\s%s]*$' %(
        ESCAPED_WIN_PATH_SEPARATOR,
        ESCAPED_WIN_PATH_SEPARATOR,
))

TIDY_NUX_PATH_RE = re.compile(
    '([%s])([%s]+)' % (
        ESCAPED_NUX_PATH_SEPARATOR,
        ESCAPED_NUX_PATH_SEPARATOR,
))
TIDY_NUX_TRIM_RE = re.compile(
    '([^%s])[\s%s]+$' % (
        ESCAPED_NUX_PATH_SEPARATOR,
        ESCAPED_NUX_PATH_SEPARATOR,
))

try:
    from importlib.machinery import SourceFileLoader
    PYTHON_3 = True

except ImportError:
    from imp import load_source
    PYTHON_3 = False

DEFAULT_PYLIB_IGNORE_LIST = (
    # Any item begining with an underscore
    re.compile(r'^_.*'),
)
# Stream `whence` variables were introduced in Python 2.7 but to remaing
# compatible with Python 2.6, we define their values here which when
# referenced make our code easier to read. These are used for stream
# manipulation only.

# Start of the stream (the default); offset should be zero or positive
# ie: stream.seek(0L, SEEK_SET)
SEEK_SET = 0
# Current stream position; offset may be negative or possitive
SEEK_CUR = 1
# End of the stream; offset should be zero or negative
SEEK_END = 2

# Logging
import logging
from lib.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)

# Sub commands are identified by they're filename
PYTHON_MODULE_RE = re.compile(r'^(?P<fname>[^_].+)\.py?$')

# URL Indexing Table for returns via parse_url()
VALID_URL_RE = re.compile(r'^[\s]*([^:\s]+):[/\\]*([^?]+)(\?(.+))?[\s]*$')
VALID_HOST_RE = re.compile(r'^[\s]*([^:/\s]+)')
VALID_QUERY_RE = re.compile(r'^(.*[/\\])([^/\\]*)$')

def strsize_to_bytes(strsize):
    """
    This function returns the byte size equivalent (as an integer) that best
    represents the string passed in.  the string passed in is intended to be
    in the format as:
        value <unit>

        example: 4TB, 3GB, 10MB, 25KB, etc

    """
    strsize = re.sub('[^0-9BKMGT]+', '', strsize, re.IGNORECASE)
    strsize_re = re.search('0*(?P<size>[1-9][0-9]*)(?P<unit>.)?.*', strsize)
    if not strsize_re:
        # Not good
        return 0

    size = int(strsize_re.group('size'))
    unit = strsize_re.group('unit')

    if not unit or unit == 'B':
        # We're going to assume bytes and return now
        return size

    if unit == 'K':
        # We're dealing with Kilobytes
        return size*1024

    if unit == 'M':
        # We're dealing with Megabytes
        return size*1048576

    if unit == 'G':
        # We're dealing with Gigabytes
        return size*1073741824

    if unit == 'T':
        # We're dealing with Terabytes
        return size*1073741824*1024


def bytes_to_strsize(byteval):
    """
    This function returns the string size equivalent (as a string) that best
    represents the integer (in bytes) passed in.

    """
    if not byteval:
        # Not good
        return '0.00B'

    unit = 'B'
    try:
        byteval = float(byteval)
    except(ValueError, TypeError):
        return '0.00B'

    if byteval >= 1024.0:
        byteval = byteval/1024.0
        unit = 'KB'
    if byteval >= 1024.0:
        byteval = byteval/1024.0
        unit = 'MB'
    if byteval >= 1024.0:
        byteval = byteval/1024.0
        unit = 'GB'
    if byteval >= 1024.0:
        byteval = byteval/1024.0
        unit = 'TB'

    return'%.2f%s' % (byteval, unit)


def mkdir(name, perm=0775):
    """
    A more contained wrapper to directory management
    """
    attempt = 3
    if isdir(name):
        return True

    while attempt > 0:
        try:
            makedirs(name, perm)
            logger.debug('Created directory: %s' % name)
            return True

        except OSError, e:
            if e[0] == errno.EEXIST:
                # directory exists; this is okay
                return True

            logger.debug('Created directory %s exception: %s' % (
                name, e,
            ))

        # racing condition; just try again
        attempt -= 1

    # To many attempts... fail
    # ... fall through...
    return False


def scan_pylib(paths, ignore_re=DEFAULT_PYLIB_IGNORE_LIST):
    """
    A simple function that scans specified paths for .pyc files
    it returns a dictionary of files it scanned using the paths
    specified.

    You can optionally specify a list of items you wish to
    ignore from the matched results.  This allows you to filter
    content. By default, anything starting with an underscore
    is skipped.

    The list of module names found is returned:
        {
            'foo' : (
               '/absolute/path/to/foo.py',
               '/another_path/to/another/foo.py',
            ),
            'bob' : (
                '/absolute/path/to/bob.py',
            ),
        }
    """
    # Module paths
    rpaths = {}

    if isinstance(paths, basestring):
        paths = [ paths ]

    # Filter dirs to those that exist
    paths = [ d for d in paths if isdir(d) is True ]
    for pd in paths:
        for filename in listdir(pd):
            result = PYTHON_MODULE_RE.match(filename)
            if not result:
                # Not our type of file
                continue

            if next((True for r in ignore_re \
                     if r.match(result.group('fname')) is not None), False):
                # We already loaded an alike file
                # we don't wnat to over-ride it or we matched an element
                # from our ignore list
                continue

            if result.group('fname') not in rpaths:
                rpaths[result.group('fname')] = set()

            # Store unique entry
            rpaths[result.group('fname')].add(join(pd, filename))

    return rpaths


def load_pylib(module_name, filepath):
    """
    Loads a python module presumable retreived by the
    scan_pylib() function call.

    module_name should be the name of the module path
    you want to import from within the file.

    ie:
        load_pylib('plugin.test', '/path/to/plugin.py')

    """
    if PYTHON_3:
        return SourceFileLoader(module_name, filepath).load_module()
    return load_source(module_name, filepath)


def tidy_path(path):
    """take a filename and or directory and attempts to tidy it up by removing
    trailing slashes and correcting any formatting issues.

    For example: ////absolute//path// becomes:
        /absolute/path

    """
    # Windows
    path = TIDY_WIN_PATH_RE.sub('\\1', path.strip())
    # Linux
    path = TIDY_NUX_PATH_RE.sub('\\1', path.strip())

    # Linux Based Trim
    path = TIDY_NUX_TRIM_RE.sub('\\1', path.strip())
    # Windows Based Trim
    path = expanduser(TIDY_WIN_TRIM_RE.sub('\\1', path.strip()))
    return path


def parse_url(url, default_schema='http'):
    """A function that greatly simplifies the parsing of a url
    specified by the end user.

     Valid syntaxes are:
        <schema>://<user>@<host>:<port>/<path>
        <schema>://<user>:<passwd>@<host>:<port>/<path>
        <schema>://<host>:<port>/<path>
        <schema>://<host>/<path>
        <schema>://<host>

     Argument parsing is also supported:
        <schema>://<user>@<host>:<port>/<path>?key1=val&key2=val2
        <schema>://<user>:<passwd>@<host>:<port>/<path>?key1=val&key2=val2
        <schema>://<host>:<port>/<path>?key1=val&key2=val2
        <schema>://<host>/<path>?key1=val&key2=val2
        <schema>://<host>?key1=val&key2=val2

     The function returns a simple dictionary with all of
     the parsed content within it and returns 'None' if the
     content could not be extracted.
    """

    if not isinstance(url, basestring):
        # Simple error checking
        return None

    # Default Results
    result = {
        # The username (if specified)
        'user': None,
        # The password (if specified)
        'password': None,
        # The port (if specified)
        'port': None,
        # The hostname
        'host': None,
        # The full path (query + path)
        'fullpath': None,
        # The path
        'path': None,
        # The query
        'query': None,
        # The schema
        'schema': None,
        # The schema
        'url': None,
        # The arguments passed in (the parsed query)
        # This is in a dictionary of {'key': 'val', etc }
        # qsd = Query String Dictionary
        'qsd': {}
    }

    qsdata = ''
    match = VALID_URL_RE.search(url)
    if match:
        # Extract basic results
        result['schema'] = match.group(1).lower().strip()
        host = match.group(2).strip()
        try:
            qsdata = match.group(4).strip()
        except AttributeError:
            # No qsdata
            pass
    else:
        match = VALID_HOST_RE.search(url)
        if not match:
            return None
        result['schema'] = default_schema
        host = match.group(1).strip()

    if not result['schema']:
        result['schema'] = default_schema

    if not host:
        # Invalid Hostname
        return None

    # Now do a proper extraction of data
    parsed = urlparse('http://%s' % host)

    # Parse results
    result['host'] = parsed[1].strip()
    result['fullpath'] = quote(unquote(tidy_path(parsed[2].strip())))
    try:
        # Handle trailing slashes removed by tidy_path
        if result['fullpath'][-1] not in ('/', '\\') and \
           url[-1] in ('/', '\\'):
            result['fullpath'] += url.strip()[-1]
    except IndexError:
        # No problem, there simply isn't any returned results
        # and therefore, no trailing slash
        pass

    # Parse Query Arugments ?val=key&key=val
    # while ensureing that all keys are lowercase
    if qsdata:
        result['qsd'] = dict([ (k.lower().strip(), v.strip()) \
                              for k, v in parse_qsl(
            qsdata,
            keep_blank_values=True,
            strict_parsing=False,
        )])

    if not result['fullpath']:
        # Default
        result['fullpath'] = None
    else:
        # Using full path, extract query from path
        match = VALID_QUERY_RE.search(result['fullpath'])
        if match:
            result['path'] = match.group(1)
            result['query'] = match.group(2)
            if not result['path']:
                result['path'] = None
            if not result['query']:
                result['query'] = None
    try:
        (result['user'], result['host']) = \
                re.split('[\s@]+', result['host'])[:2]

    except ValueError:
        # no problem then, host only exists
        # and it's already assigned
        pass

    if result['user'] is not None:
        try:
            (result['user'], result['password']) = \
                    re.split('[:\s]+', result['user'])[:2]

        except ValueError:
            # no problem then, user only exists
            # and it's already assigned
            pass

    try:
        (result['host'], result['port']) = \
                re.split('[\s:]+', result['host'])[:2]

    except ValueError:
        # no problem then, user only exists
        # and it's already assigned
        pass

    if result['port']:
        try:
            result['port'] = int(result['port'])
        except (ValueError, TypeError):
            # Invalid Port Specified
            return None
        if result['port'] == 0:
            result['port'] = None

    # Re-assemble cleaned up version of the url
    result['url'] = '%s://' % result['schema']
    if isinstance(result['user'], basestring):
        result['url'] += result['user']
        if isinstance(result['password'], basestring):
            result['url'] += ':%s@' % result['password']
        else:
            result['url'] += '@'
    result['url'] += result['host']

    if result['port']:
        result['url'] += ':%d' % result['port']

    if result['fullpath']:
        result['url'] += result['fullpath']

    return result
