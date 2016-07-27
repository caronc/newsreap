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

from newsreap.NNTPContent import NNTPContent
from newsreap.NNTPSegmentedFile import NNTPSegmentedFile

# Logging
import logging
from newsreap.Logging import NEWSREAP_LOGGER
logger = logging.getLogger(NEWSREAP_LOGGER)

# XML Parsing
from lxml import etree
from lxml.etree import XMLSyntaxError

# Some Common Information for the NZB Construction
NZB_XML_VERSION = "1.0"
NZB_XML_ENCODING = "UTF-8"
NZB_XML_DOCTYPE = 'nzb'
NZB_XML_DTD = 'http://www.newzbin.com/DTD/nzb/nzb-1.1.dtd'
NZB_XML_NAMESPACE = 'http://www.newzbin.com/DTD/2003/nzb'
# If set to PUBLIC then XML_DTD is expected to be on a remote location
# If set to SYSTEM then the XML_DTD is expected to be local to the system
NZB_XML_DTD_TYPE = 'PUBLIC'

# http://stackoverflow.com/questions/7007427/does-a-valid-xml\
#       -file-require-an-xml-declaration
# If XML_STANDALONE is set to True, then no DTD is provided and the
# standalone="yes" flag is specified instead. Otherwise standalone is set to
# "no"
NZB_XML_STANDALONE = True


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

        # The nzbfile
        self.nzbfile = nzbfile

        # File lazy counter; it is only populated on demand
        self._lazy_file_count = None
        self._lazy_is_valid = None

        # XML Stream/Iter Pointer
        self.xml_iter = None
        self.xml_root = None

        # Meta information placed into (or read from) the <head/> tag

        self.meta = {
            'subject': kwargs.get('subject', ''),
            'category': kwargs.get('category', ''),
        }

        # Track segmented files when added
        # TODO: add method for adding to segmented files list
        self.segmented_files = sortedset(key=lambda x: x.key())

        # Initialize our parent
        super(NNTPnzb, self).__init__(tmp_dir=tmp_dir, *args, **kwargs)

    def save(self, nzbfile=None):
        """
        Write an nzbfile to the file and path specified. If no path is
        specified, then the one used to open the class is used.

        If that wasn't specified, then this function will return False.
        The function returns True if the save was successful
        """
        if not nzbfile:
            # Lets try another
            nzbfile = self.nzbfile

        if not nzbfile:
            # Not much more we can do here
            return False

        # STUB: TODO: Write save() function which should take all segmented
        #             files and write a new NZB file from them.
        return False

    def is_valid(self):
        """
        Validate if the NZB File is okay; this will generate some overhead
        but at the same time it caches a lot of the results it returns so
        future calls will be speedy

        The function returns True if the nzb file is valid, otherwise it
        returns False
        """

        if self._lazy_is_valid is None:
            # TODO: Generate .dtd and properly verify file
            # for now we can just call len() because that will
            # set the _is_valid to False if it fails.

            len(self)

            if self._is_valid is True:
                # We parsed data and had no problem
                self._lazy_is_valid = True

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

        except StopIteration:
            # let this pass through
            self.xml_root = None

        except IOError:
            logger.warning('NZB-File is missing: %s' % self.nzbfile)
            self.xml_root = None
            # Mark situation
            self._is_valid = False

        except XMLSyntaxError as e:
            import pdb
            pdb.set_trace()
            if e[0] is not None:
                # We have corruption
                logger.error("NZB-File '%s' is corrupt" % self.nzbfile)
                logger.debug('NZB-File XMLSyntaxError Exception %s' % str(e))
                # Mark situation
                self._is_valid = False
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
            logger.error("NZB-File '%s' is corrupt" % self.nzbfile)
            logger.debug('NZB-File Exception %s' % str(e))
            # Mark situation
            self._is_valid = False

        if self.xml_root is None or len(self.xml_root) == 0:
            self.xml_iter = None
            self.xml_root = None
            raise StopIteration()

        # TODO: Convert xml_root into a NNTPBinaryContent() Object
        return self.xml_root

    def __next__(self):
        """
        Python 3 support
        Support stream type functions and iterations
        """
        if self.xml_root is not None:
            # clear our unused memory
            self.xml_root.clear()

        # get the root element
        try:
            _, self.xml_root = self.xml_iter.next()

        except StopIteration:
            # let this pass through
            self.xml_root = None

        except IOError:
            logger.warning('NZB-File is missing: %s' % self.nzbfile)
            self.xml_root = None
            # Mark situation
            self._is_valid = False

        except XMLSyntaxError as e:
            if e[0] is not None:
                # We have corruption
                logger.error("NZB-File '%s' is corrupt" % self.nzbfile)
                logger.debug('NZB-File Exception %s' % str(e))
                # Mark situation
                self._is_valid = False
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
            logger.error("NZB-File '%s' is corrupt" % self.nzbfile)
            logger.debug('NZB-File Exception %s' % str(e))
            # Mark situation
            self._is_valid = False

        if self.xml_root is None or len(self.xml_root) == 0:
            self.xml_iter = None
            self.xml_root = None
            raise StopIteration()

        # TODO: Convert xml_root into a NNTPSegmentedFile() Object
        return self.xml_root

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

        try:
            self.xml_iter = iter(etree.iterparse(
                self.nzbfile,
                tag="{%s}file" % NZB_XML_NAMESPACE,
            ))

        except IOError:
            logger.warning('NZB-File is missing: %s' % self.nzbfile)
            self._is_valid = False

        except XMLSyntaxError as e:
            if e[0] is not None:
                # We have corruption
                logger.error("NZB-File '%s' is corrupt" % self.nzbfile)
                logger.debug('NZB-File Exception %s' % str(e))
                # Mark situation
                self._is_valid = False
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
            logger.error("NZB-File '%s' is corrupt" % self.nzbfile)
            logger.debug('NZB-File Exception %s' % str(e))

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
