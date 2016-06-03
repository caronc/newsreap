# -*- coding: utf-8 -*-
#
# A container for controlling content found within an article
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

from os import unlink
from os import fdopen
from os.path import join
from os.path import getsize
from os.path import basename
from os.path import abspath
from os.path import expanduser
from os.path import isdir
from os.path import isfile
from tempfile import mkstemp
from shutil import move as _move
from shutil import copy as _copy
from shutil import Error as ShutilError

from lib.codecs.CodecBase import DEFAULT_TMP_DIR
from lib.Utils import mkdir
from lib.Utils import bytes_to_strsize
from lib.Utils import SEEK_SET
from lib.Utils import SEEK_END
from lib.NNTPSettings import DEFAULT_BLOCK_SIZE as BLOCK_SIZE

# Logging
import logging
from lib.Logging import NEWSREAP_LOGGER
logger = logging.getLogger(NEWSREAP_LOGGER)


class NNTPFileMode(object):
    """
    This class makes the detction of file modes easier since
    we can compare what is set to a variable/object name
    """
    BINARY_RO = 'rb'
    BINARY_WO = 'wb'
    BINARY_RW = 'r+b'
    BINARY_RW_TRUNCATE = 'w+b'
    ASCII_R = 'r'
    ASCII_RW = 'w+'

class NNTPContent(object):
    """
    An object for maintaining retrieved article content. There can only
    be 1 article, however that 1 article can have a lot of content
    found within it.

    This identifies the content found internally within an article.

    This function does it's best to behave like a stream. But provides
    some functions to make manipulating and merging with other articles
    easier to do.

    Articles by default assume a roll of 'attached'.  This means that
    the files written to disk are removed the second the object is
    destroyed.  This is intentional!  You can call detach() at any
    time you want but now you are responsible for cleaning up the
    filename.

    """

    def __init__(self, filename=None, part=0, tmp_dir=None, sort_no=10000, *args, **kwargs):
        """
        Initialize NNTP Content

        If a filepath is specified, it can be either a stream (already opened file
        or ByteIO or StringIO class is fine too), or it can be a path to a filename
        which will be open in 'wb' mode.
        """

        # The sort is used with sorting; different filetypes/content types
        # should be processed before otheres.

        # For example, the NNTPHeader() and NNTPMetaContent() area always kept
        # at the head where as NNTPAsciiContent() and NNTPBinaryContent() are
        # kept at the back.

        # The lower sort no is always processe first; but default we choose
        # a rather large sort value. Grouped content should share the same sort
        # value so that they sort their content together.
        self.sort_no = sort_no

        # Store part
        try:
            self.part = int(part)

        except (ValueError, TypeError):
            self.part = 0

        # The filepath is automatically set up when the temporary file is
        # created
        self.filepath = None

        # used to track the filemode (saves on time from opening and closing
        # un-nessisarily).  These flags are set during an open and a close
        self.filemode = None

        # Prepare temporary folder we intend to use by default
        if tmp_dir:
            self.tmp_dir = abspath(expanduser(tmp_dir))
        else:
            self.tmp_dir = DEFAULT_TMP_DIR

        # A Stream object
        self.stream = None

        # Detached prevents the article from cleaning up all of
        # the data it otherwise tracks (such as the article stored
        # on disk)
        #
        # If set to None, then it hasn't been initalized yet
        self._detached = None

        # Dirty flag is set to true if a write is made; all data
        # is considered dirty until flush() is called (forcing
        # contents out of cache and onto disk)
        self._dirty = False

        # The name is used to describe the file
        if not filename:
            self.filename = ''

        else:
            self.filename = basename(filename)

        # A flag that can be toggled if the data stored is
        # corrupted in some way. Such as through CRC Failing
        # or part construction (a part missing perhaps, etc)
        # if all is good, then we just leave the flag as is
        self._is_valid = True


    def getvalue(self):
        """
        This is mostly just used for unit testing, but it
        greatly makes life easier anyway.

        Effectively we put the pointer at the head of our
        file and read back the entire chunk into memory
        and return it.
        """

        if not self.open(mode=NNTPFileMode.BINARY_RO, eof=False):
            # Error
            return None

        # Head of data
        self.stream.seek(0L, SEEK_SET)

        return self.read()


    def is_valid(self):
        """
        A simple function that returns whether the article is valid or not

        The function returns True if it is valid, and False if it isn't
        and None if there isn't enough information to make a valid guess.

        The basic version (no overloading) just returns what the flag
        was set to.
        """
        return self._is_valid


    def open(self, filepath=None, mode=None, eof=False):
        """
        Opens a filepath specified and re-attaches to it.
        You can also pass in an already open stream which
        causes it to operate in a detached state

        if a filepath is specified, mode default to BINARY_RW

        if no filepath is specified, then the filepath saved
        in the article is used (self.filepath) and mode
        default to 'rb' (NNTPFileMode.BINARY_RO).

        if eof is set to True, then the file is opened and the
        pointer is placed at the end of the file (oppose to
        the head)
        """

        if not mode:
            # Read and write
            mode = NNTPFileMode.BINARY_RW

        if self.stream is not None:
            if self.filemode is not None and self.filemode == mode:
                # ensure we're at the head of the file
                if not eof:
                    self.stream.seek(0L, SEEK_SET)
                else:
                    self.stream.seek(0L, SEEK_END)

                return True

        if not filepath and self.filepath:
            # Update filepath
            filepath = self.filepath

        elif not filepath:
            if not isdir(self.tmp_dir):
                # create directory
                mkdir(self.tmp_dir)

            # Create a Temporary File
            fileno, self.filepath = mkstemp(dir=self.tmp_dir)
            try:
                self.stream = fdopen(fileno, mode)
                if self._detached is None:
                    self._detached = False

                # save the last mode the file was opened as
                self.filemode = mode

                logger.debug(
                    'Opened %s (mode=%s)' % \
                    (self.filepath, mode),
                )

            except OSError:
                logger.error(
                    'Could not open %s (mode=%s)' % \
                    (self.filepath, mode),
                )
                return False

            return True

        if isinstance(filepath, basestring):

            # expand our path to be absolute
            filepath = abspath(expanduser(filepath))

            # Create our stream
            try:
                self.stream = open(filepath, mode)
                self.filepath = filepath
                if self._detached is None:
                    self._detached = True

                # save the last mode the file was opened as
                self.filemode = mode

                logger.debug(
                    # D flag for Detached
                    'Opened %s (mode=%s) (flag=D)' % \
                    (self.filepath, mode),
                )

            except OSError:
                logger.error(
                    'Could not open %s (mode=%s) (flag=D)' % \
                    (self.filepath, mode),
                )
                return False

        elif hasattr(filepath, 'seek'):
            # assume we're dealing with an already open stream and therefore
            # we work in a detached state
            self.stream = filepath
            self.filepath = None
            self.filemode = None

            # You can never have an attached file without a filepath
            self._detached = False

            # Reset dirty flag
            self._dirty = False

        else:
            logger.error(
                'Could not open object %s' % \
                (type(self.filepath)),
            )
            return False

        if not eof:
            # Ensure we're at the head of the file
            self.stream.seek(0L, SEEK_SET)

        else:
            # Ensure we're at the end of the file
            self.stream.seek(0L, SEEK_END)

        return True


    def load(self, filepath, detached=True):
        """
        This causes the function to point to the file specified and acts in a
        detached manner to it.

        By identifing the detached flag, to true we don't try to remove the
        file when this object is destroyed.  If you want this script to handle
        the file afterwards, then make sure to set detached to False.
        """

        if not isfile(filepath):
            return False

        if self.stream is not None:
            # Close any existing open file
            self.close()

        if not self._detached and self.filepath:
            # We're changing so it's better we unlink this (but only
            # if we're attached to it)
            try:
                unlink(self.filepath)
                logger.debug('Removed file %s' % self.filepath)
            except:
                pass

        # Set Detached flag
        self._detached = detached

        # Assign new file
        self.filepath = filepath

        return True


    def save(self, filepath=None, copy=False, append=False):
        """
        This function writes the content to disk using the filename
        specified.

        If copy is False, then the content detaches itself from internal
        management and is moved to the new path specified by filepath.
        If copy is True, then content is written to the new location
        and remains in it's current detached state (whatever it was).

        If no filepath is specified, then the detected filename and
        tmp_dir specified during the objects initialization is used instead.

        The function returns the filepath the content was successfully
        written (saved) to, otherwise None is returned.

        If append is set to True, the content is appended to the file
        (if one already exists)

        """
        if filepath is None:
            if not isdir(self.tmp_dir):
                # create directory
                mkdir(self.tmp_dir)

            if self.filename:
                filepath = join(self.tmp_dir, self.filename)
            else:
                filepath = join(self.tmp_dir, basename(self.filepath))

        elif isdir(filepath):
            if self.filename:
                filepath = join(
                    abspath(expanduser(filepath)),
                    basename(self.filename),
                )
            else:
                if not isdir(self.tmp_dir):
                    # create directory
                    mkdir(self.tmp_dir)

                filepath = join(self.tmp_dir, basename(self.filepath))

        elif isfile(filepath):
            # It's a file; just make sure we're dealing with a full pathname
            filepath = abspath(expanduser(filepath))


        if isfile(filepath):
            if not append:
                try:
                    unlink(filepath)
                    logger.warning('%s already existed (removed).' % (
                        filepath,
                    ))
                except:
                    logger.error(
                        '%s already existed (and could not be removed).' % (
                        filepath,
                    ))
                    return None
            else:
                # we're not moving or copying, we're appending
                with open(filepath, NNTPFileMode.BINARY_RW) as target:
                    self.stream.seek(0L, SEEK_END)
                    logger.debug('Appending %s to %s.' % (
                        self,
                        filepath,
                    ))
                    for chunk in self:
                        # TODO: Handle out of disk space here
                        target.write(chunk)

                return filepath

        # else: treat it as full path and filename included

        if self.stream:
            # close the file if it's open
            self.close()

        # Function Wrapping
        if copy:
            action = _copy
            action_str = "copy"

        else:
            action = _move
            action_str = "move"

        try:
            action(self.filepath, filepath)
            if not copy:
                # Detach File
                self._detached = True
                # Update filepath
                self.filepath = filepath

            logger.debug('%s(%s, %s)' % (
                action_str, self.filepath, filepath,
            ))

            return filepath

        except ShutilError, e:
            logger.debug('%s(%s, %s) exception %s' % (
                action_str, self.filepath, filepath, str(e),
            ))

        return None


    def write(self, data, eof=True):
        """
        Writes data to stream

        eof is only considered if the file wasn't open prior to the write()
        call. Otherwise the pointer remains where it last was. If set to True
        and the file wasn't previously open, the pointer is automatically
        placed at the end of the stream.

        """
        if self.stream is None:
            # open the file if it's not already open
            self.open(mode=NNTPFileMode.BINARY_RW, eof=eof)

        self.stream.write(data)

        # Set dirty flag
        self._dirty = True


    def read(self, n=-1):
        """
        read up to n bytes from the stream
        """
        if self.stream is None:
            # open the file if it's not already open
            self.open(mode=NNTPFileMode.BINARY_RO, eof=False)

        return self.stream.read(n)


    def close(self):
        """
        Closes the file but retains any attachment to it.
        """
        if self.stream is not None:
            try:
                self.stream.close()
                if self.filepath:
                    logger.debug('Closed %s' % (self.filepath))
                else:
                    logger.debug('Closed stream.')
            except:
                pass

            self.stream = None
            self.filemode = None

            # A closed file can't be dirty as content is
            # flushed to disk at this point; therefore reset the flag
            # back to False
            self._dirty = False

        return


    def append(self, content):
        """
        This function takes a content object (or list of content objects) and
        appends them to `this` object.

        """
        if isinstance(content, NNTPContent):
            content = [ content ]

        if not self.open(mode=NNTPFileMode.BINARY_RW, eof=True):
            return False

        for entry in content:
            if isinstance(entry, NNTPContent):
                # Just append the current content
                if not entry.open(mode=NNTPFileMode.BINARY_RO, eof=False):
                    logger.debug('Error handling content: %s' % entry)
                    continue

                logger.debug('Appending content %s' % entry)

                while True:
                    buf = entry.stream.read(BLOCK_SIZE)
                    if not buf:
                        # Set dirty flag
                        self._dirty = True
                        break
                    self.stream.write(buf)

                entry.close()

        return True


    def detach(self, close=True):
        """
        Detach the article stored on disk from being further managed by this class
        """
        if close:
            self.close()

        self._detached = True
        return


    def key(self):
        """
        Returns a key that can be used for sorting with:
            lambda x : x.key()
        """
        return '%.5d/%s/%.5d' % (self.sort_no, self.filename, self.part)


    def next(self):
        """
        Python 2 support
        Support stream type functions and iterations
        """
        data = self.stream.read(BLOCK_SIZE)
        if not data:
            self.close()
            raise StopIteration()

        return data


    def __next__(self):
        """
        Python 3 support
        Support stream type functions and iterations
        """
        data = self.stream.read(BLOCK_SIZE)
        if not data:
            self.close()
            raise StopIteration()

        return data


    def __iter__(self):
        """
        Grants usage of the next()
        """

        # Ensure our stream is open with read
        self.open(mode=NNTPFileMode.BINARY_RO)
        return self


    def __len__(self):
        """
        Returns the length of the articles
        """
        if not self.filepath:
            # If there is no filepath, then we're probably dealing with a
            # stream in memory like a StringIO or BytesIO stream.
            if self.stream:
                # Advance to the end of the file
                ptr = self.stream.tell()
                # Advance to the end of the file and get our length
                length = self.stream.seek(0L, SEEK_END)
                if length != ptr:
                    # Return our pointer
                    self.stream.seek(ptr, SEEK_SET)
            else:
                # No Stream or Filepath; nothing has been initialized
                # yet at all so just return 0
                length = 0
        else:
            if self.stream and self._dirty:
                self.stream.flush()

            # Get the size
            length = getsize(self.filepath)

        return length


    def __del__(self):
        """
        Gracefully remove the file retrieved as it was removed
        from scope for a good reason. We can easily avoid
        having this step called by calling the detach() function
        """
        if self.stream is not None:
            self.close()

        if not self._detached and self.filepath:
            try:
                unlink(self.filepath)
                logger.debug('Removed file %s' % self.filepath)
            except:
                pass


    def __lt__(self, other):
        """
        Support Less Than (<) operator for sorting
        """
        return self.key() < self.key()


    def __cmp__(self, content):
        """
        Support comparative checks
        """
        return cmp(self.key(), content.key())


    def __str__(self):
        """
        Return a printable version of the file being read
        """
        if self.part > 0:
            return '%s.%.5d' % (self.filename, self.part)
        return self.filename


    def __repr__(self):
        """
        Return a printable version of the file being read
        """
        return '<NNTPContent sort=%d filename="%s" part=%d len=%s />' % (
            self.sort_no,
            self.filename,
            self.part,
            bytes_to_strsize(len(self)),
        )
