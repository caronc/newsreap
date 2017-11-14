# -*- coding: utf-8 -*-
#
# A Base Codec Class for deciphering data that is requiring an external file
#
# Copyright (C) 2016 Chris Caron <lead2gold@gmail.com>
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

import errno
from blist import sortedset

from os.path import isdir
from os.path import isfile
from os.path import exists
from os.path import join
from os.path import basename
from os.path import abspath
from os.path import expanduser

from os import X_OK
from os import access

from tempfile import mkdtemp

from newsreap.Utils import mkdir
from newsreap.NNTPContent import NNTPContent
from newsreap.NNTPArticle import NNTPArticle
from newsreap.NNTPBinaryContent import NNTPBinaryContent
from newsreap.NNTPSettings import DEFAULT_TMP_DIR
from newsreap.Utils import random_str
from newsreap.Utils import bytes_to_strsize
from newsreap.Utils import find
from os.path import splitext

# Logging
import logging
from newsreap.Logging import NEWSREAP_CODEC
logger = logging.getLogger(NEWSREAP_CODEC)


class CompressionLevel(object):
    """
    Support general compression level settings so that the calling user doesn't
    have to be aware of the different types supported by the actual executable.
    """

    # Maximum Compression will be slower to use and generate the most i/o
    # but will overall save the most disk space.
    Maximum = u'+++'

    # The average setting is what the actual executable being called would
    # have otherwise defaulted to.  It's not nessisarily the highest
    # compression level, but it's not the worst either.
    Average = u'~'

    # This will cause larger files to be generated (thus taking up more disk
    # space and posting space/time).  However the file generation itself will
    # be very fast (with respect to the other levels)
    Minimum = u'---'

# Tuple of supported Compression Levels
COMPRESSION_LEVELS = (
    CompressionLevel.Maximum,
    CompressionLevel.Average,
    CompressionLevel.Minimum,
)


class CodecFile(object):
    """
    CodecFile compliments CodecBase by wrapping the codecs that can only
    be accessed through an outside binary file located on the system.
    """

    def __init__(self, work_dir=None, name=None, password=None,
                 level=CompressionLevel.Average, cpu_cores=None,
                 *args, **kwargs):
        """
        The dir identfies the directory to store our sessions in
        until they can be properly handled.
        """

        # If the password is set to None then it is presumed that
        # you don't want to use it.  Keep in mind that setting this
        # to an empty string presumes that you want to set a blank
        # password (but a password none the less)
        self.password = password

        # Stores the name to associate with the archive being encoded or
        # decoded.
        self.name = name

        # The number of CPU cores should be set to whatever it is your
        # workstation can handle.  The more, the faster the processing will
        # be.

        # Linux users can do this:
        #  $> egrep '^processor' /proc/cpuinfo -c

        # If you set this to None, then the default options are used; thus cpu
        # core specifications (threading) are just simply not applied to the
        # command
        self.cpu_cores = cpu_cores

        # Compression Level
        self.level = level
        if self.level not in COMPRESSION_LEVELS:
            # Bad compression level specified
            logger.error(
                'Invalid CodecFile compression specified (%s)' % str(level),
            )
            raise AttributeError("Invalid compression level specified.")

        if work_dir is None:
            self.work_dir = DEFAULT_TMP_DIR

        else:
            self.work_dir = abspath(expanduser(work_dir))

        if not isdir(self.work_dir):
            # create directory
            if mkdir(self.work_dir):
                logger.info('Created directory: %s' % self.work_dir)

            else:
                logger.error('Failed to created directory: %s' % self.work_dir)
                # Should not continue under this circumstance
                raise IOError((
                    errno.EACCES,
                    'Failed to create directory: %s' % self.work_dir,
                ))

        # Contains a list of paths to be archived
        self.archive = set()

    def add(self, path):
        """
        Adds files, directories, NNTPContent() and NNTPArticle objects
        to archive.
        """
        _bcnt = len(self.archive)

        self.archive |= self.get_paths(path)

        return len(self.archive) > _bcnt

    def clear(self):
        """
        clears out all content added to our internal archive
        """
        self.archive.clear()

    def encode(self, content=None, *args, **kwargs):
        """
        Takes a specified content (dir or file) and compresses it. If this
        function is successful, it returns a set of NNTPBinaryContent()
        objects that are 'not' detached. Which means if they go out of scope,
        the compressed content will be lost.

        If this function fails, or there is nothing to encode, the function
        should return None.

        the content passed into should be passed into the self.add() call
        if it's not set to None otherwise. The content encoded is always
        that of what is in the self.archive sortedset.

        """
        raise NotImplementedError(
            "CodecFile() inheriting class is required to impliment compress()"
        )

    def decode(self, content, *args, **kwargs):
        """
        content must be a path containing rar files or at the very least
        NNTPContent() objects (or set of) containing rar files.

        """
        raise NotImplementedError(
            "CodecFile() inheriting class is required to impliment decompress()"
        )

    def can_exe(self, fpath):
        """
        Can test if path exists and is executable
        """
        if isinstance(fpath, basestring):
            return isfile(fpath) and access(fpath, X_OK)
        return False

    def mkstemp(self, content=None,  suffix='.tmp', prefix='_tmp_'):
        """
        A wrapper to mkstemp that only handles reference to the filepath/name
        itself. It creates a unique subdirectory that it generates the new
        temporary file within that can be referenced.

        If a content is specified, then the function parses out the directory
        infront of it and possibly a prefix at the end and swaps it with the
        prefix specified.  This is just an easier way of manipulating a
        filename or directory name that was recently pulled from an
        NNTPContent() object.

        This function returns both the temporary directory created and the
        temporary file prepared.

        """

        # Create a temporary directory to work in
        tmp_path = mkdtemp(prefix='_nr.codec-', dir=self.work_dir)
        tmp_file = None

        if isinstance(content, basestring):
            tmp_file = join(
                tmp_path,
                '%s%s' % (basename(content), suffix,
            ))

        elif isinstance(content, NNTPContent):
            # use the filename based on the path
            if content.filename:
                tmp_file = join(
                    tmp_path,
                    '%s%s' % (splitext(basename(content.filename))[0], suffix,
                ))

        elif isinstance(content, NNTPArticle):
            if len(content) > 0:
                if content[0].filename:
                    tmp_file = join(
                        tmp_path,
                        '%s%s' % (
                            splitext(basename(content[0].filename))[0],
                            suffix,
                    ))

        if tmp_file is None:
            # Fall back
            tmp_file = join(
                tmp_path,
                '%s%s' % (random_str(), suffix),
            )

        return tmp_path, tmp_file

    def get_paths(self, content):
        """
        When supplied content which can be a NNTPArticle(), NNTPContent()
        a directory, and/or file. get_paths() returns all of the results
        in a unique sortedset().  get_paths() also supports iterating over
        tuples, sets, sortedsets and lists to fetch this information.

        If a directory is passed in that maps against individual content
        within the directory; that content is removed from the list causing
        the directory to trump content within.

        This is a helper function that greatly makes the handling of multiple
        content types easier to work with. Ideally each Codec that inherits
        from this class should use this prior to the actual archiving to keep
        command line arguments to a minimum and consistent with the rules
        defined in this (where directories trump).

        """

        # Create a set to store our results in
        results = sortedset()

        if isinstance(content, (set, tuple, list, sortedset)):
            # Iterate over the entries passing them back into this function
            # recursively
            for v in content:
                results |= self.get_paths(v)

        elif isinstance(content, basestring):
            content = abspath(expanduser(content))
            if exists(content):
                results.add(content)

        elif isinstance(content, NNTPContent):
            if content.filepath and exists(content.filepath):
                results.add(content.filepath)

        elif isinstance(content, NNTPArticle):
            for c in content:
                if c.filepath and exists(c.filepath):
                    results.add(c.filepath)

        if len(results) <= 1:
            # Save ourselves some energy
            return results

        # Acquire a list of directories since these will trump any file
        # entries found that reside in them.
        _dirs = set([r for r in results if isdir(r)])

        if _dirs:
            # Adjust our results to eliminate any files that reside within
            # directories that have been queued as well.
            #
            # Basically we want to look for files that reside in a directory
            # we've already identified to include too and turf the files that
            # reside within them.  Thus directories trump!
            #
            # Hence if we find:
            #   - /path/to/data/dir/a/great/file
            #   - /path/to/data/dir/a.wonderful.file
            #   - /path/to/data/dir
            #   - /path/to/another.file
            #
            # We would keep:
            #   - /path/to/data/dir
            #   - /path/to/another.file
            #
            # We would turf the remaining files because they can be
            # found within the /path/to/data/dir
            results = sortedset([r for r in results if r not in _dirs and next(
                (False for d in _dirs \
                 if r.startswith(d, 0, len(d)) is True), True)])

            if len(_dirs) > 1:
                # Perform the same check with our directories (a directory
                # can not include another directory higher up) The shortest
                # directory trumps a larger one.
                # hence if we find:
                #   - /path/to/data/dir/
                #   - /path/to/data
                #
                # We would drop the /path/to/data/dir/ since the /path/to/data
                # already includes it
                _dirs = set([_d for _d in _dirs if next(
                    (True for d in _dirs if _d != d and \
                     d.startswith(_d, 0, len(d)) is True), False)])

            # Since we stripped out directories earlier for some pre-processing
            # we need to add them back here
            results |= _dirs

        # Return our results
        return results

    def watch_dir(self, path, regex=None, prefix=None, suffix=None,
            ignore=None, case_sensitive=True, seconds=15):
        """Monitors a directory for files that have been added/changed

            path: is the path to monitor
            ignore: is a sortedset of files already parsed
            seconds: is how long it takes a file to go untouched for before
              we presume it has been completely written to disk.
        """

        if ignore is None:
            ignore = sortedset()

        findings = find(
            path, fsinfo=True,
            regex_filter=regex,
            prefix_filter=prefix,
            suffix_filter=suffix,
            case_sensitive=case_sensitive,
        )

        findings = [
            (p, f['size'], f['created'], f['modified'])
                for p, f in findings.items()
                  if (f['modified'] - f['created']).total_seconds() >= seconds
                    and f['basename'] not in ignore
        ]

        # Sort list by created date
        findings.sort(key=lambda x: x[3])

        for f in findings:
            logger.info('Created %s (size=%s)' % (
                f, bytes_to_strsize(f[1]),
            ))
            # Add to our filter list
            ignore.add(f[0])

        # Return our ignore list (which is acutally also a found list)
        return ignore

    def __iter__(self):
        """
        Grants usage of the next()
        """

        # Ensure our stream is open with read
        return iter(self.archive)

    def __len__(self):
        """
        Returns the number of archive content entries found
        """
        return len(self.archive)

    def __str__(self):
        """
        Return a printable version of the codec
        """
        return repr(self)

    def __repr__(self):
        """
        Return an unambigious version of the objec
        """
        return '<CodecFile work_dir="%s" clevel="%s" archives="%d" />' % (
            self.work_dir,
            self.level,
            len(self.archive),
        )
