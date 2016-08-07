# -*- coding: utf-8 -*-
#
# A wrapper to greatly simplify manipulation of NZB Files
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
from os.path import join
from os.path import dirname

from newsreap.NNTPContent import NNTPContent
from newsreap.NNTPArticle import NNTPArticle
from newsreap.NNTPSegmentedPost import NNTPSegmentedPost
from HTMLParser import HTMLParser

# Logging
import logging
from newsreap.Logging import NEWSREAP_LOGGER
logger = logging.getLogger(NEWSREAP_LOGGER)

# XML Parsing
from lxml import etree
from lxml.etree import XMLSyntaxError
import hashlib
# Some Common Information for the NZB Construction
NZB_XML_VERSION = "1.0"
NZB_XML_ENCODING = "UTF-8"
NZB_XML_DOCTYPE = 'nzb'
NZB_XML_DTD = 'http://www.newzbin.com/DTD/nzb/nzb-1.1.dtd'
NZB_XML_NAMESPACE = 'http://www.newzbin.com/DTD/2003/nzb'
# If set to PUBLIC then XML_DTD is expected to be on a remote location
# If set to SYSTEM then the XML_DTD is expected to be local to the system
NZB_XML_DTD_TYPE = 'PUBLIC'

# Path to LOCAL DTD Validation
NZB_XML_DTD_FILE = join(dirname(__file__), 'var', 'nzb-1.1.dtd')

# http://stackoverflow.com/questions/7007427/does-a-valid-xml\
#       -file-require-an-xml-declaration
# If XML_STANDALONE is set to True, then no DTD is provided and the
# standalone="yes" flag is specified instead. Otherwise standalone is set to
# "no"
NZB_XML_STANDALONE = True

# Defined globally for namespace lookups with lxml calls
NZB_LXML_NAMESPACES = {'ns': NZB_XML_NAMESPACE}


class NNTPnzb(NNTPContent):
    """
    This class reads and writes to nzb files.

    It can also be passed into a NNTPConnection() get()
    call for the retrieval of articles identified.

    """

    def __init__(self, nzbfile=None, tmp_dir=None, *args, **kwargs):
        """
        Initialize NNTP NZB object

        A nzbfile can optionally be specified identifying the location of the
        nzb file to open/read or even eventually write to if you're generating
        one.
        """

        # File lazy counter; it is only populated on demand
        self._lazy_file_count = None
        self._lazy_is_valid = None
        self._lazy_gid = None

        # XML Stream/Iter Pointer
        self.xml_iter = None
        self.xml_root = None
        self.xml_itr_count = 0

        # Meta information placed into (or read from) the <head/> tag

        self.meta = None

        # Track segmented files when added
        # TODO: add method for adding to segmented files list
        self.segmented_files = sortedset(key=lambda x: x.key())

        # Initialize our parent
        super(NNTPnzb, self).__init__(tmp_dir=tmp_dir, *args, **kwargs)

        # Used for it's ability to convert to and
        self._htmlparser = HTMLParser()

        # NNTPContent Object
        self._detached = True

        # The nzbfile
        self.filepath = nzbfile

    def save(self, nzbfile=None):
        """
        Write an nzbfile to the file and path specified. If no path is
        specified, then the one used to open the class is used.

        If that wasn't specified, then this function will return False.
        The function returns True if the save was successful
        """
        if not nzbfile:
            # Lets try another
            nzbfile = self.filepath

        if not nzbfile:
            # Not much more we can do here
            return False

        # STUB: TODO: Write save() function which should take all segmented
        #             files and write a new NZB file from them.
        return False

    def gid(self):
        """
        Returns the Global Identifier associated with an NZB File.
        This is just a unique way of associating this file with another
        posted to usenet.

        None is returned if the GID can not be acquired

        """
        if self._lazy_gid is None:

            if self.is_valid() is False:
                # Save ourselves the time and cpu of parsing further
                return None

            # get ourselves the gid which is just the md5sum of the first
            # Article-ID
            iter(self)

            if self.xml_iter is None:
                return None

            # get the root element
            try:
                _, self.xml_root = self.xml_iter.next()

                segment = self.xml_root.xpath(
                    '/ns:nzb/ns:file[1]/ns:segments/ns:segment[1]',
                    namespaces=NZB_LXML_NAMESPACES,
                )[0]

                self.xml_root.clear()
                self.xml_root = None
                self.xml_iter = None

            except IndexError:
                logger.warning(
                    'NZB-File is missing initial </segment> element: %s' % \
                    self.filepath,
                )
                # Thrown if no segment elements were found in the first file
                # entry; this NZBFile is not good.

                # We intentionally do not mark the invalidity of the situation
                # because we allow for nzbfiles that are still being
                # constructed.
                return None

            except StopIteration:
                logger.warning(
                    'NZB-File is missing </file> elements: %s' % self.filepath)
                # Thrown if no <file/> elements were found at all.
                # This NZBFile is not good.

                # We intentionally do not mark the invalidity of the situation
                # because we allow for nzbfiles that are still being
                # constructed.
                return None

            except IOError:
                logger.warning('NZB-File is missing: %s' % self.filepath)
                self.xml_root = None
                # Mark situation
                self._lazy_is_valid = False

            except XMLSyntaxError as e:
                logger.error("NZB-File '%s' is corrupt" % self.filepath)
                logger.debug('NZB-File XMLSyntaxError Exception %s' % str(e))
                # Mark situation
                self._lazy_is_valid = False

            except Exception:
                logger.error("NZB-File '%s' is corrupt" % self.filepath)
                logger.debug('NZB-File Exception %s' % str(e))
                # Mark situation
                self._lazy_is_valid = False
                return None

            try:
                # We simply
                _md5sum = hashlib.md5()
                _md5sum.update(segment.text.strip().decode())
                # Store our data
                self._lazy_gid = _md5sum.hexdigest()

            except (TypeError, AttributeError):
                # Can't be done
                logger.warning(
                    'Cannot calculate GID from NZB-File: %s' % self.filepath)

        return self._lazy_gid

    def is_valid(self):
        """
        Validate if the NZB File is okay; this will generate some overhead
        but at the same time it caches a lot of the results it returns so
        future calls will be speedy

        The function returns True if the nzb file is valid, otherwise it
        returns False
        """

        if self._lazy_is_valid is None:
            if self.open():
                # Open DTD file and create dtd object
                dtdfd = open(NZB_XML_DTD_FILE)
                dtd = etree.DTD(dtdfd)
                # Verify our dtd file against our current stream
                try:
                    nzb = etree.parse(self.filepath)

                except XMLSyntaxError as e:
                    if e[0] is not None:
                        # We have corruption
                        logger.error("NZB-File '%s' is corrupt" % self.filepath)
                        logger.debug('NZB-File XMLSyntaxError Exception %s' % str(e))
                        # Mark situation
                        self._lazy_is_valid = False

                self._lazy_is_valid = dtd.validate(nzb)

        return (super(NNTPnzb, self).is_valid() and \
                self._lazy_is_valid is True)

    def next(self):
        """
        Python 2 support
        Support stream type functions and iterations
        """
        if self.xml_root is not None:
            # clear our unused memory
            self.xml_root.clear()

        # get the root element
        try:
            _, self.xml_root = self.xml_iter.next()

            # Increment our iterator
            self.xml_itr_count += 1

        except StopIteration:
            # let this pass through
            self.xml_root = None

        except IOError:
            logger.warning('NZB-File is missing: %s' % self.filepath)
            self.xml_root = None
            # Mark situation
            self._lazy_is_valid = False

        except XMLSyntaxError as e:
            if e[0] is not None:
                # We have corruption
                logger.error("NZB-File '%s' is corrupt" % self.filepath)
                logger.debug('NZB-File XMLSyntaxError Exception %s' % str(e))
                # Mark situation
                self._lazy_is_valid = False
            # else:
            # this is a bug with lxml in earlier versions
            # https://bugs.launchpad.net/lxml/+bug/1185701
            # It occurs when the end of the file is reached and lxml
            # simply just doesn't handle the closure properly
            # it was fixed here:
            # https://github.com/lxml/lxml/commit\
            #       /19f0a477c935b402c93395f8c0cb561646f4bdc3
            # So we can relax and return ok results here
            self.xml_root = None

        except Exception as e:
            logger.error("NZB-File '%s' is corrupt" % self.filepath)
            logger.debug('NZB-File Exception %s' % str(e))
            # Mark situation
            self._lazy_is_valid = False

        if self.xml_root is None or len(self.xml_root) == 0:
            self.xml_iter = None
            self.xml_root = None
            raise StopIteration()

        if self.meta is None:
            # Attempt to populate meta information
            self.meta = {}

            for meta in self.xml_root.xpath('/ns:nzb/ns:head[1]/ns:meta',
                                            namespaces=NZB_LXML_NAMESPACES):
                # Store the Meta Information Detected
                self.meta[meta.attrib['type'].decode()] = \
                    self._htmlparser.unescape(meta.text.strip()).decode()

        # Acquire the Segments Groups
        groups = [
            group.text.strip().decode() for group in self.xml_root.xpath(
            'ns:groups/ns:group',
            namespaces=NZB_LXML_NAMESPACES,
        )]

        # Initialize a NNTPSegmented File Object using the data we read
        _file = NNTPSegmentedPost(
            u'%s.%.3d' % (
                self.meta.get('name', u'unknown'), self.xml_itr_count,
            ),
            poster=self._htmlparser.unescape(
                self.xml_root.attrib.get('poster', '')).decode(),
            epoch=self.xml_root.attrib.get('date', '0'),
            subject=self._htmlparser.unescape(
                self.xml_root.attrib.get('subject', '')).decode(),
            groups = groups,
        )

        # index tracker
        _last_index = 0

        # Now append our segments
        for segment in self.xml_root.xpath('ns:segments/ns:segment',
                                            namespaces=NZB_LXML_NAMESPACES):

            _cur_index = int(segment.attrib.get('number', _last_index+1))
            try:
                _size = int(segment.attrib.get('bytes'))

            except (TypeError, ValueError):
                _size = 0

            article = NNTPArticle(
                subject=_file.subject,
                poster=_file.poster,
                id=segment.text.strip().decode(),
                no=_cur_index,
                size=_size,
            )

            # Add article
            _file.add(article)

            # Track our index
            _last_index = _cur_index

        # Return our object
        return _file

    def __next__(self):
        """
        Python 3 support
        Support stream type functions and iterations
        """
        return self.next()

    def __iter__(self):
        """
        Grants usage of the next()
        """

        # First get a ptr to the head of our data
        super(NNTPnzb, self).__iter__()

        if self.xml_root is not None:
            # clear our unused memory
            self.xml_root.clear()
            # reset our variable
            self.xml_root = None
            self.xml_itr_count = 0

        try:
            self.xml_iter = iter(etree.iterparse(
                self.filepath,
                tag="{%s}file" % NZB_XML_NAMESPACE,
            ))

        except IOError:
            logger.warning('NZB-File is missing: %s' % self.filepath)
            # Mark situation
            self._lazy_is_valid = False

        except XMLSyntaxError as e:
            if e[0] is not None:
                # We have corruption
                logger.error("NZB-File '%s' is corrupt" % self.filepath)
                logger.debug('NZB-File Exception %s' % str(e))
                # Mark situation
                self._lazy_is_valid = False
            # else:
            # this is a bug with lxml in earlier versions
            # https://bugs.launchpad.net/lxml/+bug/1185701
            # It occurs when the end of the file is reached and lxml
            # simply just doesn't handle the closure properly
            # it was fixed here:
            # https://github.com/lxml/lxml/commit\
            #       /19f0a477c935b402c93395f8c0cb561646f4bdc3
            # So we can relax and return ok results here

        except Exception as e:
            logger.error("NZB-File '%s' is corrupt" % self.filepath)
            logger.debug('NZB-File Exception %s' % str(e))
            # Mark situation
            self._lazy_is_valid = False

        return self

    def __len__(self):
        """
        Returns the number of files in the NZB File
        """
        self._lazy_file_count = sum(1 for c in self)
        return self._lazy_file_count

    def __repr__(self):
        """
        Return a printable version of the file being read
        """
        return '<NNTPnzb filename="%s" />' % (
            self.filename,
        )
