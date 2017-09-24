# -*- coding: utf-8 -*-
#
# A simple collection of general functions
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

import re
import errno
from blist import sortedset
from os import listdir
from os import makedirs
from os import access
from os import chmod
from os import lstat
from os import error as _OSError

from os.path import isdir
from os.path import exists
from os.path import join
from os.path import islink
from os.path import isfile
from os.path import dirname
from os.path import abspath
from os.path import basename
from os.path import splitext
from os.path import expanduser

# for pushd() popd() context
from contextlib import contextmanager
from os import getcwd
from os import chdir
from os import unlink
from os import rmdir

from urlparse import urlparse
from urlparse import parse_qsl
from urllib import quote
from urllib import unquote

from random import choice
from string import ascii_uppercase
from string import digits
from string import ascii_lowercase
from string import ascii_letters
from string import punctuation

from datetime import datetime

# File Stats
from stat import ST_ATIME
from stat import ST_CTIME
from stat import ST_MTIME
from stat import ST_SIZE
from stat import S_IRWXU
from stat import S_ISDIR

from os import W_OK
from os import stat as os_stat

# MIME Libraries
from mimetypes import guess_type
from urllib import pathname2url

# Python 3 Support
try:
    from importlib.machinery import SourceFileLoader
    PYTHON_3 = True

except ImportError:
    from imp import load_source
    PYTHON_3 = False

# Logging
import logging
from newsreap.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)

# delimiters used to separate values when content is passed in by string
# This is useful when turning a string into a list
STRING_DELIMITERS = r'[\[\]\;,\s]+'

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
    '^(.+[^:][^%s])[\s%s]*$' % (
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


# This table allows us to support MIMEs that may not have otherwise
# been detected by our system.  It purely bases it's result on the
# filename extension and is only referenced if the built in mime
# applications didn't work.
NEWSREAP_MIME_TABLE = (
    (re.compile(r'\.r(ar|[0-9]{2})\s*$', re.IGNORECASE),
     'application/x-rar-compressed'),
    (re.compile(r'\.z(ip|[0-9]{2})\s*$', re.IGNORECASE),
     'application/zip'),
)

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

# Sub commands are identified by they're filename
PYTHON_MODULE_RE = re.compile(r'^(?P<fname>[^_].+)\.py?$')

# URL Indexing Table for returns via parse_url()
VALID_URL_RE = re.compile(r'^[\s]*([^:\s]+):[/\\]*([^?]+)(\?(.+))?[\s]*$')
VALID_HOST_RE = re.compile(r'^[\s]*([^:/\s]+)')
VALID_QUERY_RE = re.compile(r'^(.*[/\\])([^/\\]*)$')

# The maximum number of recursive calls that can be made to rm
RM_RECURSION_LIMIT = 100


def strsize_to_bytes(strsize):
    """
    This function returns the byte size equivalent (as an integer) that best
    represents the string passed in.  the string passed in is intended to be
    in the format as:
        value <unit>

        example: 4TB, 3GB, 10MB, 25KB, etc

    """
    #strsize = re.sub('[^0-9BKMGT]+', '', strsize, re.IGNORECASE)

    if isinstance(strsize, int):
        # Nothing further to do
        return strsize

    try:
        strsize_re = re.match(
            r'\s*(?P<size>(0|[1-9][0-9]*)(\.(0+|[1-9][0-9]*))?)\s*((?P<unit>.)(?P<type>[bB])?)?\s*',
            strsize,
        )
    except TypeError:
        return None

    if not strsize_re:
        # Not good
        return None

    size = int(strsize_re.group('size'))
    unit = strsize_re.group('unit')
    use_bytes = strsize_re.group('type') != 'b'

    if not unit:
        return size

    # convert unit to uppercase
    unit = unit.upper()

    if use_bytes:
        if unit == 'B':
            # We're going to assume bytes and return now
            return size

        elif unit == 'K':
            # We're dealing with Kilobytes
            return size*1024

        elif unit == 'M':
            # We're dealing with Megabytes
            return size*1048576

        elif unit == 'G':
            # We're dealing with Gigabytes
            return size*1073741824

        elif unit == 'T':
            # We're dealing with Terabytes
            return size*1073741824*1024

    # In Bits
    if unit == 'K':
        # We're dealing with Kilobytes
        return size*1e3

    elif unit == 'M':
        # We're dealing with Megabytes
        return size*1e6

    elif unit == 'G':
        # We're dealing with Gigabytes
        return size*1e9

    elif unit == 'T':
        # We're dealing with Terabytes
        return size*1e12

    # Unsupported Type
    return None


def bytes_to_strsize(byteval):
    """
    This function returns the string size equivalent (as a string) that best
    represents the integer (in bytes) passed in.

    """
    unit = 'B'
    try:
        byteval = float(byteval)

    except(ValueError, TypeError):
        return None

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

    return '%.2f%s' % (byteval, unit)


def stat(path, fsinfo=True, mime=True):
    """
    A wrapper to the stat() python class that handles exceptions and
    converts all file stats into datetime objects.

    A simple response would look like this:
        {
            'basename': 'filename.rar',
            'dirname': '/path/to/location/',
            'extension': 'rar',
            'filename': 'filename',

            # the below appears if you specified fsinfo=True
            'created': datetime(),      # file creation time
            'modified': datetime(),     # file last modified time
            'accessed': datetime(),     # file last access time
            'size': 3002423,            # size is in bytes

            # Mime Type (if boolean set)
            'mime': 'application/rar'
        }
    """
    # If we reach here, we store the file found
    _abspath = abspath(path)
    _basename = basename(path)

    try:
        stat_obj = os_stat(_abspath)

    except OSError:
        # File was not found or recently removed
        return None

    nfo = {
        'basename': _basename,
        'dirname': dirname(_abspath),
        'extension': splitext(_basename)[1].lower(),
        'filename': splitext(_basename)[0],
    }

    if fsinfo:
        # Extend file information
        try:
            nfo['modified'] = \
                datetime.fromtimestamp(stat_obj[ST_MTIME])

        except ValueError:
            nfo['modified'] = \
                    datetime(1980, 1, 1, 0, 0, 0, 0)

        try:
            nfo['accessed'] = \
                datetime.fromtimestamp(stat_obj[ST_ATIME])

        except ValueError:
            nfo['accessed'] = \
                    datetime(1980, 1, 1, 0, 0, 0, 0)

        try:
            nfo['created'] = \
                datetime.fromtimestamp(stat_obj[ST_CTIME])

        except ValueError:
            nfo['created'] = \
                    datetime(1980, 1, 1, 0, 0, 0, 0)

        nfo['size'] = stat_obj[ST_SIZE]

    if mime:
        url = pathname2url(_basename)
        mime_type = guess_type(url)[0]

        if mime_type is None:
            # Default MIME Type if nothing found in our backup list
            mime_type = next((m[1] for m in NEWSREAP_MIME_TABLE \
                  if m[0].search(_basename)), 'application/octet-stream')

        nfo['mime'] = mime_type

    return nfo


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
                return isdir(name)

            logger.debug('Created directory %s exception: %s' % (
                name, e,
            ))

        # racing condition; just try again
        attempt -= 1

    # To many attempts... fail
    # ... fall through...
    return False


def rm(path, *args, **kwargs):
    """
    A  rmtree reimplimentation. More importantly, Microsoft Windows
    can't recursively delete a directory if it contains read-only files
    with rmtree().  This rm() attempts to handle this. Symbolic links
    are not supported

    See http://stackoverflow.com/a/2656405 for a partial basis of
    why this rewrite exists.  The on_error refernced in this function didn't
    really satisfy my needs, so a rewrite of the rmtree() function was required.

    """
    if not exists(path):
        # Nothing more to do
        return True

    # We track our recursion level so we don't go in some unexitable loop
    recursion_count = kwargs.get('__recursion_count', 0)
    if recursion_count == 0:
        # We're on our first iteration
        logger.debug('Attempting to remove %s' % path)

    elif recursion_count >= RM_RECURSION_LIMIT:
        # Recursive Limit reached
        return False

    if not access(path, W_OK):
        # Is the error an access error ?
        chmod(path, S_IRWXU)

    if islink(path):
        # symlinks to directories are forbidden (taken from shutils.rmtree()
        # see: https://bugs.python.org/issue1669

        # We'll attempt to treat the link as a file and NOT a directory. If we
        # fail then we're done
        try:
            unlink(path)
            logger.debug('Removed link %s' % path)
            return True

        except:
            logger.warning(
                'Failed to remove link %s' % path)
            return False

    if isdir(path):
        names = []
        try:
            names = listdir(path)

        except _OSError:
            # Failed
            return False

        for name in names:
            fullname = join(path, name)
            try:
                mode = lstat(fullname).st_mode

            except _OSError:
                mode = 0

            if S_ISDIR(mode):
                if not access(fullname, W_OK):
                    # Make sure we can access 'this' path
                    chmod(fullname, S_IRWXU)

                try:
                    # Attempt to remove directory
                    rmdir(fullname)

                except _OSError:
                    # It's not empty, so recursively enter it
                    if not rm(fullname, __recursion_level=recursion_count+1):
                        return False
            else:
                # We're dealing with file/link
                try:
                    unlink(fullname)
                    logger.debug('Removed file %s' % fullname)

                except:
                    logger.warning('Failed to remove file %s' % fullname)
                    return False

        try:
            rmdir(path)
            logger.debug('Removed file %s' % path)

        except _OSError:
            logger.warning('Failed to remove file %s' % path)
            return False

    else:
        # We're dealing with file/link
        try:
            unlink(path)
            logger.debug('Removed file %s' % path)

        except:
            logger.warning('Failed to remove file %s' % path)
            return False

    return True

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
        paths = [paths]

    # Filter dirs to those that exist
    paths = [d for d in paths if isdir(d) is True]
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
        result['qsd'] = dict([(k.lower().strip(), v.strip()) \
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


def parse_list(*args):
    """
    Take a string list and break it into a delimited
    list of arguments. This funciton also supports
    the processing of a list of delmited strings and will
    always return a unique set of arguments. Duplicates are
    always combined in the final results.

    You can append as many items to the argument listing for
    parsing.

    Hence: parse_list('.mkv, .iso, .avi') becomes:
        ['.mkv', '.iso', '.avi']

    Hence: parse_list('.mkv, .iso, .avi', ['.avi', '.mp4']) becomes:
        ['.mkv', '.iso', '.avi', '.mp4']

    The parsing is very forgiving and accepts spaces, slashes, commas
    semicolons, and pipes as delimiters
    """

    result = []
    for arg in args:
        if isinstance(arg, basestring):
            result += re.split(STRING_DELIMITERS, arg)

        elif isinstance(arg, (list, tuple, set, sortedset)):
            for _arg in arg:
                if isinstance(arg, basestring):
                    result += re.split(STRING_DELIMITERS, arg)

                # A list inside a list? - use recursion
                elif isinstance(_arg, (list, tuple, set, sortedset)):
                    result += parse_list(_arg)

                else:
                    # Convert whatever it is to a string and work with it
                    result += parse_list(str(_arg))
        else:
            # Convert whatever it is to a string and work with it
            result += parse_list(str(arg))

    # apply as well as make the list unique by converting it
    # to a set() first. filter() eliminates any empty entries
    return filter(bool, list(set(result)))


def find(search_dir, regex_filter=None, prefix_filter=None,
                suffix_filter=None, fsinfo=False, mime=False,
               followlinks=False, min_depth=None, max_depth=None,
              case_sensitive=False, *args, **kwargs):
    """Returns a dict object of the files found in the download
       directory. You can additionally pass in filters as a list or
       string) to filter the results returned.

          ex:
          {
             '/full/path/to/file.mkv': {
                 'basename': 'file.mkv',
                 'dirname': '/full/path/to',
                 # identify the filename (without applied extension)
                 'filename': 'file',
                 # always tolower() applied to:
                 'extension': 'mkv',

                 # If fullstatus == True then the following additional
                 # content is provided.

                 # file size is in bytes
                 'size': 10000,
                 # accessed date
                 'accessed': datetime(),
                 # created date
                 'created': datetime(),
                 # created date
                 'modified': datetime(),
             }
          }

          the function returns None if something bad happens

    """

    # Internal Tracking of Directory Depth
    current_depth = kwargs.get('__current_depth', 1)
    if current_depth == 1:
        # General Error Checking for first iteration through
        if min_depth and max_depth and min_depth > max_depth:
            return None

        # Translate to full absolute path
        search_dir = abspath(expanduser(search_dir))

    root_dir = kwargs.get('__root_dir', search_dir)

    # Build file list
    files = {}
    if isinstance(search_dir, (sortedset, set, list, tuple)):
        for _dir in search_dir:
            # use recursion to build a master (unique) list
            files = dict(files.items() + find(
                search_dir=_dir,
                regex_filter=regex_filter,
                prefix_filter=prefix_filter,
                suffix_filter=suffix_filter,
                fsinfo=fsinfo,
                mime=mime,
                followlinks=followlinks,
                min_depth=min_depth,
                max_depth=max_depth,
                case_sensitive=case_sensitive,
                # Internal Current Directory Depth tracking
                __current_depth=current_depth,
                __root_dir=root_dir,
            ).items())
        return files

    elif not isinstance(search_dir, basestring):
        # Unsupported
        return {}

    # Change all filters strings lists (if they aren't already)
    if regex_filter is None:
        regex_filter = tuple()
    if isinstance(regex_filter, basestring):
        regex_filter = (regex_filter,)
    elif isinstance(regex_filter, re._pattern_type):
        regex_filter = (regex_filter,)
    if suffix_filter is None:
        suffix_filter = tuple()
    if isinstance(suffix_filter, basestring):
        suffix_filter = (suffix_filter, )
    if prefix_filter is None:
        prefix_filter = tuple()
    if isinstance(prefix_filter, basestring):
        prefix_filter = (prefix_filter, )

    # clean prefix list
    if prefix_filter:
        prefix_filter = parse_list(prefix_filter)

    # clean up suffix list
    if suffix_filter:
        suffix_filter = parse_list(suffix_filter)

    # Precompile any defined regex definitions
    if regex_filter:
        _filters = []
        for f in regex_filter:
            if not isinstance(f, re._pattern_type):
                flags = 0x0
                if not case_sensitive:
                    flags = re.IGNORECASE

                try:
                    _filters.append(re.compile(f, flags=flags))

                except:
                    logger.error(
                        'Invalid regular expression: "%s"' % f,
                    )
                    return None
            else:
                # precompiled already
                _filters.append(f)
        # apply
        regex_filter = _filters

    if current_depth == 1:
        # noise reduction; only display this notice once (but not on
        # each recursive call)
        logger.debug("get_files('%s') with %d filter(s)" % (
            search_dir,
            len(prefix_filter) + len(suffix_filter) + len(regex_filter),
        ))

    if isfile(search_dir):
        fname = basename(search_dir)
        filtered = False
        if regex_filter:
            filtered = True
            for regex in regex_filter:
                if regex.search(fname):
                    logger.debug('Allowed %s (regex)' % fname)
                    filtered = False
                    break

        if not filtered and prefix_filter:
            filtered = True
            for prefix in prefix_filter:
                if case_sensitive:
                    # We use slicing and not startswith() because slicing is
                    # faster
                    if fname[0:len(prefix)] == prefix:
                        logger.debug('Allowed %s (prefix)' % fname)
                        filtered = False
                        break
                else:
                    # Not Case Sensitive
                    if fname[0:len(prefix)].lower() == prefix.lower():
                        logger.debug('Allowed %s (prefix)' % fname)
                        filtered = False
                        break

        if not filtered and suffix_filter:
            filtered = True
            for suffix in suffix_filter:
                if case_sensitive:
                    # We use slicing and not endswith() because slicing is
                    # faster
                    if fname[-len(suffix):] == suffix:
                        # Allowed
                        filtered = False
                        break
                else:
                    # Not Case Sensitive
                    if fname[-len(suffix):].lower() == suffix.lower():
                        logger.debug('Allowed %s (suffix)' % fname)
                        filtered = False
                        break

        if filtered:
            # File does not meet implied filters
            return {}

        # If we reach here, we can prepare a file using the data
        # we fetch
        _file = {
            search_dir: stat(search_dir, fsinfo=fsinfo, mime=mime),
        }
        if _file[search_dir] is None:
            del files[search_dir]
            logger.warning(
                'The file %s became inaccessible' % fname,
            )
            return {}

        return _file

    elif not isdir(search_dir):
        return {}

    # Get Directory entries
    dirents = [d for d in listdir(search_dir) \
              if d not in ('..', '.')]

    for dirent in dirents:
        # Store Path
        fullpath = join(search_dir, dirent)

        if isdir(fullpath):
            # Max Depth Handling
            if max_depth and max_depth <= current_depth:
                continue

            if not followlinks and islink(fullpath):
                # honor followlinks
                continue

            # use recursion to build a master (unique) list
            files = dict(files.items() + find(
                search_dir=fullpath,
                regex_filter=regex_filter,
                prefix_filter=prefix_filter,
                suffix_filter=suffix_filter,
                fsinfo=fsinfo,
                mime=mime,
                followlinks=followlinks,
                min_depth=min_depth,
                max_depth=max_depth,
                case_sensitive=case_sensitive,
                # Internal Current Directory Depth tracking
                __current_depth=current_depth+1,
                __root_dir=root_dir,
            ).items())
            continue

        elif not isfile(fullpath):
            # Unknown Type
            logger.debug('Skipping %s (unknown)' % dirent)
            continue

        # Min depth handling
        if min_depth and min_depth > current_depth:
            continue

        # Apply filters to match filed
        if regex_filter:
            filtered = True
            for regex in regex_filter:
                if regex.search(dirent):
                    logger.debug('Allowed %s (regex)' % dirent)
                    filtered = False
                    break

            if filtered:
                # Denied
                continue

        if prefix_filter:
            filtered = True
            for prefix in prefix_filter:
                if case_sensitive:
                    # We use slicing and not startswith() because slicing is
                    # faster
                    if dirent[0:len(prefix)] == prefix:
                        logger.debug('Allowed %s (prefix)' % dirent)
                        filtered = False
                        break
                else:
                    # Not Case Sensitive
                    if dirent[0:len(prefix)].lower() == prefix.lower():
                        logger.debug('Allowed %s (prefix)' % dirent)
                        filtered = False
                        break

            if filtered:
                # Denied
                continue

        if suffix_filter:
            filtered = True
            for suffix in suffix_filter:
                if case_sensitive:
                    # We use slicing and not endswith() because slicing is
                    # faster
                    if dirent[-len(suffix):] == suffix:
                        logger.debug('Allowed %s (suffix)' % dirent)
                        filtered = False
                        break
                else:
                    # Not Case Sensitive
                    if dirent[-len(suffix):].lower() == suffix.lower():
                        logger.debug('Allowed %s (suffix)' % dirent)
                        filtered = False
                        break

            if filtered:
                # Denied
                continue

        # If we reach here, we store the file found
        files[fullpath] = stat(fullpath, fsinfo=fsinfo, mime=mime)
        if files[fullpath] is None:
            # File was not found or recently removed
            del files[fullpath]
            logger.warning(
                'The file %s became inaccessible' % dirent,
            )
            continue

    # Return all files
    return files


@contextmanager
def pushd(newdir, create_if_missing=False, perm=0775):
    """
    # A pushd/popd implimentation
    # Based on : http://stackoverflow.com/questions/6194499/\
                    pushd-through-os-system

    # It's use is pretty straight forward:
    # with pushd('somewhere'):
    #     # somewhere
    #     print os.getcwd()
    #
    # # wherever you started
    # print os.getcwd()

    """
    prevdir = getcwd()
    if not isdir(newdir) and create_if_missing:
        # Don't bother checking the success or not
        # we'll find out soon enough with chdir()
        mkdir(newdir, perm)

    chdir(newdir)
    try:
        yield

    finally:
        # Fall back to previous directory popd()
        chdir(prevdir)


def random_str(count=16, seed=ascii_uppercase + digits + ascii_lowercase):
    """
    Generates a random string. This code is based on a great stackoverflow
    post here: http://stackoverflow.com/questions/2257441/\
                    random-string-generation-with-upper-case-\
                    letters-and-digits-in-python
    """
    return ''.join(choice(seed) for _ in range(count))


def parse_bool(arg, default=False):
    """
    Parses strings such as 'yes' and 'no' as well as other strings such as
    'on' or 'off' , 'enable' or 'disable', etc.

    This method can just simplify checks to these variables.

    If the content could not be parsed, then the default is
    returned.
    """

    if isinstance(arg, basestring):
        # no = no - False
        # of = short for off - False
        # 0  = int for False
        # fa = short for False - False
        # f  = short for False - False
        # n  = short for No or Never - False
        # ne  = short for Never - False
        # di  = short for Disable(d) - False
        # de  = short for Deny - False
        if arg.lower()[0:2] in ('de', 'di', 'ne', 'f', 'n', 'no', 'of',
                                '0', 'fa'):
            return False
        # ye = yes - True
        # on = short for off - True
        # 1  = int for True
        # tr = short for True - True
        # t  = short for True - True
        # al = short for Always (and Allow) - True
        # en  = short for Enable(d) - True
        elif arg.lower()[0:2] in ('en', 'al', 't', 'y', 'ye', 'on', '1',
                                  'tr'):
            return True
        # otherwise
        return default

    # Handle other types
    return bool(arg)


def hexdump(src, length=16, sep='.'):
    """
    Displays a hex output of the content it is passed.

    This was based on https://gist.github.com/7h3rAm/5603718 with some
    minor modifications
    """
    allowed = digits + ascii_letters + punctuation + ' '

    print_map = ''.join(((x if x in allowed else '.') \
        for x in map(chr, range(256))))
    lines = []

    for c in xrange(0, len(src), length):
        chars = src[c:c+length]
        hex = ' '.join(["%02x" % ord(x) for x in chars])
        if len(hex) > 24:
            hex = "%s %s" % (hex[:24], hex[24:])
        printable = ''.join(["%s" % (
            (ord(x) <= 127 and print_map[ord(x)]) or sep) for x in chars])
        lines.append("%08x:  %-*s  |%s|" % (c, length*3, hex, printable))
    return '\n'.join(lines)
