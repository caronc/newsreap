# -*- coding: utf-8 -*-
#
# A wrapper to greatly simplify manipulation of NZB Files
#
# Copyright (C) 2015-2017 Chris Caron <lead2gold@gmail.com>
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
from os.path import basename
from os.path import splitext

from newsreap.codecs.CodecBase import CodecBase
from newsreap.codecs.CodecYenc import CodecYenc
from newsreap.NNTPContent import NNTPContent
from newsreap.NNTPContent import NNTPFileMode
from newsreap.NNTPArticle import NNTPArticle
from newsreap.NNTPEmptyContent import NNTPEmptyContent
from newsreap.NNTPSegmentedPost import NNTPSegmentedPost
from newsreap.Mime import Mime
from newsreap.Mime import DEFAULT_MIME_TYPE
from HTMLParser import HTMLParser
from xml.sax.saxutils import escape as sax_escape

# Logging
import logging
from newsreap.Logging import NEWSREAP_LOGGER
logger = logging.getLogger(NEWSREAP_LOGGER)

# XML Parsing
from lxml import etree
from lxml.etree import XMLSyntaxError
import hashlib
import re

# Some Common Information for the NZB Construction
XML_VERSION = "1.0"
XML_ENCODING = "UTF-8"
XML_DOCTYPE = 'nzb'
NZB_DTD_VERSION = '1.1'
NZB_XML_DTD = 'http://www.newzbin.com/DTD/nzb/nzb-%s.dtd' % NZB_DTD_VERSION
NZB_XML_NAMESPACE = 'http://www.newzbin.com/DTD/2003/nzb'
from newsreap import __version__
from newsreap import __title__


class NZBParseMode(object):
    """
    Parsing modes for our NZB-File
    """
    # Do nothing special, just act as a normal class when iterating it
    Simple = 0

    # Ignore NZBFile Entries that already point to downloaded content.
    # Setting this flag allows you to iterate over the same NZB-File (while
    # processing it) a second time and only get results that have not been
    # handled.
    IgnoreAttached = 1

    # Ignore any entries in the NZB-File detected as being PAR files.
    IgnorePars = 2

# A list of the modes defined above
NZB_PARSE_MODES = (
    NZBParseMode.Simple,
    NZBParseMode.IgnoreAttached,
    NZBParseMode.IgnorePars,
)


class XMLDTDType(object):
    Public = u'PUBLIC'
    System = u'SYSTEM'


NZB_XML_DTD_MAP = {
    XMLDTDType.Public: '"-//newzBin//DTD NZB %s//EN" "%s"' % (
            NZB_DTD_VERSION, NZB_XML_DTD,
    ),
    XMLDTDType.System: '"-//newzBin//DTD NZB %s//EN" "%s"' % (
            NZB_DTD_VERSION, basename(NZB_XML_DTD),
    ),
}

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

# NZB-Filename
NZB_EXTENSION_RE = re.compile(r'(?P<fname>).nzb$', re.IGNORECASE)


class NNTPnzb(NNTPContent):
    """
    This class reads and writes to nzb files.

    It can also be passed into a NNTPConnection() get()
    call for the retrieval of articles identified.

    """

    def __init__(self, nzbfile=None, encoding=XML_ENCODING, work_dir=None,
                 mode=NZBParseMode.Simple, codecs=None, *args, **kwargs):
        """
        Initialize NNTP NZB object

        A nzbfile can optionally be specified identifying the location of the
        nzb file to open/read or even eventually write to if you're generating
        one.

        codecs: define the codecs you want to use to parse the articles
                contents
        """

        self._lazy_is_valid = None
        self._lazy_gid = None

        # XML Stream/Iter Pointer
        self.xml_iter = None
        self.xml_root = None
        self.xml_itr_count = 0

        # Meta information placed into (or read from) the <head/> tag
        self.meta = None

        # Encoding to use when handling the NZB File
        self.encoding = encoding

        # Track segmented files when added
        self.segments = sortedset(key=lambda x: x.key())

        # Segments are loaded on our first iteration through the NZB-File
        # Once these are loaded, most of the functions references come from the
        # self.segments instead of re-parsing the XML file again and again
        # We can use self.segments to rebuild a brand new XML file too
        self._segments_loaded = None
        self._segment_iter = None

        # An NZB-File Mode which can control out it responds
        self._nzb_mode = mode

        # Initialize our parent
        super(NNTPnzb, self).__init__(work_dir=work_dir, *args, **kwargs)

        # Used for it's ability to convert to and
        self._htmlparser = None

        # NNTPContent Object
        self._detached = True

        # The nzbfile
        self.filepath = nzbfile

        # Some pretty nzb printing meta information the padding character to
        # use. You probably wouldn't want to set this to anything other then a
        # space (' ') or a tab ('\t').
        self.padding = ' '

        # The multiplier is how many of these padding characters you want to
        # increase by per indent. Hence if you set this to 2 and the padding
        # to a space (' '), then each time a new sub-element is created, it
        # is indented 2 more spaces.
        #
        # Example of padding_multiplier set to 2 (and padding to space):
        #     1|<root>
        #     2|  <level1>
        #     3|    <level2>
        #     4|      <level3>
        #     5|      </level3>
        #     6|    </level2>
        #     7|  </level1>
        #     8|</root>
        #
        self.padding_multiplier = 2

        # Create our Mime object since we'll be using it a lot
        self._mime = Mime()

        # Load our Codecs
        self._codecs = codecs

        # Provide default codecs if required
        if not self._codecs:
            self._codecs = [CodecYenc(), ]

        elif isinstance(self._codecs, CodecBase):
            self._codecs = [self._codecs, ]

    def mode(self, mode):
        """
        Sets the parse mode of our NNTnzb object

        """
        if mode not in NZB_PARSE_MODES:
            return False

        self._nzb_mode = mode
        return True

    def save(self, nzbfile=None, pretty=True, dtd_type=XMLDTDType.Public):
        """
        Write an nzbfile to the file and path specified. If no path is
        specified, then the one used to open the class is used.

        If that wasn't specified, then this function will return False.
        The function returns True if the save was successful

        If pretty is set to True then the output is formatted gently
        on the eyes; otherwise it is packed for disk size
        """
        if self.open(filepath=nzbfile, mode=NNTPFileMode.BINARY_RW_TRUNCATE):
            eol = '\n'
            indent = ''

            self.write('<?xml version="%s" encoding="%s"?>%s' % (
                XML_VERSION,
                self.encoding,
                eol,
            ))
            self.write('<!DOCTYPE %s %s %s>%s' % (
                XML_DOCTYPE,
                dtd_type,
                NZB_XML_DTD_MAP[dtd_type],
                eol,
            ))

            if not pretty:
                # No more end-of-lines are nessisary from this point on if
                # we're not out to format the content in any human-readable
                # fashion
                eol = ''

            self.write('<nzb xmlns="%s">%s%s' % (
                NZB_XML_NAMESPACE,
                eol, eol,
            ))

            if self.meta:
                if pretty:
                    indent = ''.ljust(self.padding_multiplier, self.padding)

                # Handle the meta information if there is anything at all to
                # print
                self.write('%s<head>%s' % (
                    indent,
                    eol,
                ))

                if pretty:
                    indent = ''.ljust(self.padding_multiplier*2, self.padding)

                for k, v in self.meta.items():
                    self.write('%s<meta type="%s">%s</meta>%s' % (
                        indent,
                        self.escape_xml(k.encode(self.encoding)),
                        self.escape_xml(v.encode(self.encoding)),
                        eol,
                    ))

                if pretty:
                    indent = ''.ljust(self.padding_multiplier, self.padding)

                self.write('%s</head>%s%s' % (
                    indent,
                    eol,
                    eol,
                ))

            for segno, segment in enumerate(self.segments):
                if not len(segment):
                    logger.error(
                        "NZB-File '%s' has no defined articles." % self.path())
                    self.remove()
                    return False

                if not len(segment.groups):
                    logger.warning(
                        "NZB-File '%s' (segno %d) has no defined groups." % (
                            self.path(),
                            segno,
                        ),
                    )

                if pretty:
                    indent = ''.ljust(self.padding_multiplier, self.padding)

                self.write('%s<file poster="%s" date="%s" subject="%s">%s' % (
                    indent,
                    self.escape_xml(segment.poster),
                    segment.utc.strftime('%s'),
                    self.escape_xml(segment.subject),
                    eol,
                ))

                if pretty:
                    indent = ''.ljust(
                        self.padding_multiplier*2, self.padding)

                self.write('%s<groups>%s' % (
                    indent,
                    eol,
                ))

                if pretty:
                    indent = ''.ljust(
                        self.padding_multiplier*3, self.padding)

                for group in segment.groups:
                    self.write('%s<group>%s</group>%s' % (
                        indent,
                        group,
                        eol,
                    ))

                if pretty:
                    indent = ''.ljust(
                        self.padding_multiplier*2, self.padding)
                self.write('%s</groups>%s' % (
                    indent,
                    eol,
                ))

                self.write('%s<segments>%s' % (
                    indent,
                    eol,
                ))

                for part, article in enumerate(segment):
                    if pretty:
                        indent = ''.ljust(
                            self.padding_multiplier*3, self.padding)

                    for attachment in article:
                        # use enumerated content and not the part assigned.
                        # this is by design because it gives developers the
                        # ability to add/remove items from the attachment and
                        # only use the part numbers for their own personal
                        # ordering.

                        _bytes = len(attachment)
                        self.write(
                            '%s<segment %snumber="%d">%s</segment>%s' % (
                                indent,
                                (('bytes="%d" ' % _bytes) if _bytes else ''),
                                part+1,
                                self.escape_xml(article.msgid()),
                                eol,
                            )
                        )

                if pretty:
                    indent = ''.ljust(self.padding_multiplier*2, self.padding)

                self.write('%s</segments>%s' % (
                    indent,
                    eol,
                ))

                if pretty:
                    indent = ''.ljust(self.padding_multiplier, self.padding)

                self.write('%s</file>%s%s' % (
                    indent,
                    eol, eol,
                ))

            if pretty:
                indent = ''

            self.write('%s<!-- Generated by %s v%s -->%s' % (
                indent, __title__, __version__, eol,
            ))

            self.write('%s</nzb>%s' % (
                indent,
                eol,
            ))
            # close our file
            self.close()
            return True

        logger.error("NZB-File '%s' could not be accessed." % self.path())
        return False

    def gid(self):
        """
        Returns the Global Identifier associated with an NZB File.
        This is just a unique way of associating this file with another
        posted to usenet.

        None is returned if the GID can not be acquired

        """
        if self._lazy_gid is None:

            if self._segments_loaded is True:
                # Use our segments already loaded in memory
                if len(self.segments) > 0:
                    try:
                        # Use the md5 hash of the first message-id of the
                        # first segment
                        _md5sum = hashlib.md5()
                        _md5sum.update(self.segments[0][0].msgid())

                        # Store our data
                        self._lazy_gid = _md5sum.hexdigest()

                    except (TypeError, AttributeError, IndexError):
                        # Can't be done
                        logger.warning(
                            'Cannot calculate GID from NZB-Segments: %s' %
                            self.filepath)

                return self._lazy_gid

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
                self.xml_itr_count = 0
                self.xml_iter = None

            except IndexError:
                logger.warning(
                    'NZB-File is missing initial </segment> element: %s' %
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
                self.xml_itr_count = 0
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
                # Use the md5 hash of the first message-id of the
                # first segment
                _md5sum = hashlib.md5()
                _md5sum.update(segment.text.strip())

                # Store our data
                self._lazy_gid = _md5sum.hexdigest()

            except (TypeError, AttributeError):
                # Can't be done
                logger.warning(
                    'Cannot calculate GID from NZB-File: %s' % self.filepath)

        return self._lazy_gid

    def load(self, filepath=None, mode=NZBParseMode.Simple, detached=True):
        """
        Loads segments into memory (this object)
        """

        # Reset all entries
        self.segments.clear()
        self._segments_loaded = None
        self._segment_iter = None
        self._nzb_mode = mode

        if filepath is not None:
            # Reset our variables
            self._lazy_is_valid = None
            self._lazy_gid = None
            self.close()

            if not super(NNTPnzb, self).load(filepath=filepath):
                self._segments_loaded = False
                return False

        for sp in self:
            # Load our segmented post entries into memory
            self.segments.add(sp)

        # Toggle our segments loaded flag
        self._segments_loaded = True

        return True

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
                        logger.error(
                            "NZB-File '%s' is corrupt" % self.filepath)
                        logger.debug(
                            'NZB-File XMLSyntaxError Exception %s' % str(e))
                        # Mark situation
                        self._lazy_is_valid = False
                        # We failed
                        return False

                self._lazy_is_valid = dtd.validate(nzb)

        return self._lazy_is_valid is True

    def escape_xml(self, unescaped_xml, encoding=None):
        """
        A Simple wrapper to Escape XML charaters from a string
        """

        if isinstance(unescaped_xml, unicode):
            # Encode content as per defined in our XML
            if encoding is None:
                encoding = self.encoding
            unescaped_xml = unescaped_xml.encode(encoding)

        return sax_escape(unescaped_xml, {"'": "&apos;", "\"": "&quot;"})

    def add(self, obj):
        """
        Adds an NNTPSegmentedPost to an nzb object
        """

        if not isinstance(obj, NNTPSegmentedPost):
            return False

        # duplicates are ignored in a blist and therefore
        # we just capture the length of our list before
        # and after so that we can properly return a True/False
        # value
        _bcnt = len(self.segments)

        # Add our segment
        self.segments.add(obj)

        # A reference value we can use to compare against
        file_count = len(self.segments)

        # We're manually loading the segments so we can trust wherever
        # we leave off
        self._segments_loaded = True

        return file_count > _bcnt

    def unescape_xml(self, escaped_xml, encoding=None):
        """
        A Simple wrapper to UnEscape XML characters from a string
        """

        if encoding is None:
            encoding = self.encoding

        if self._htmlparser is None:
            # Define on first use
            self._htmlparser = HTMLParser()

        return self._htmlparser.unescape(escaped_xml).decode(encoding)

    def segcount(self):
        """
        Returns the total number of segments in the NZB File
        """
        seg_count = 0
        seg_count += sum(len(c) for c in self)
        return seg_count

    def deobsfucate(self, filebase=None):
        """
        Scans through the segments, articles and content associated with an
        NZB-File and sets up the filenames defined in the segments to it's
        best effort

        filebase: provide a fallback filename base (the part of the file
                  before the extension) to build on if we can't detect
                  the file on our own.

        """
        # The name from the meta tag
        _name = self.meta.get('name', '').decode(self.encoding).strip()
        if not _name:
            if filebase:
                # Use base provided as a base
                _name = filebase

            else:
                # If we can't get a name from the meta tag to start with, then
                # we use the NZB-Filename itself as a backup
                _name = splitext(basename(self.path()))[0]

        for segment in self.segments:
            filename = segment.deobsfucate(_name)
            if filename:
                # Update
                segment.filename = filename

    def next(self):
        """
        Python 2 support
        Support stream type functions and iterations
        """

        # We track our iterator since we move along if our mode tells us to do
        # so.
        _iter = None

        if self.xml_root is not None:
            # clear our unused memory
            self.xml_root.clear()

        if self._segment_iter:
            while 1:
                _iter = self._segment_iter.next()
                if self._valid_by_mode(_iter):
                    return _iter

        # get the root element
        try:
            _, self.xml_root = self.xml_iter.next()

            # Increment our iterator
            self.xml_itr_count += 1

        except StopIteration:
            # let this pass through
            self.xml_root = None
            self.xml_itr_count = 0

        except IOError:
            logger.warning('NZB-File is missing: %s' % self.filepath)
            self.xml_root = None
            self.xml_itr_count = 0
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
            self.xml_itr_count = 0

        except Exception as e:
            logger.error("NZB-File '%s' is corrupt" % self.filepath)
            logger.debug('NZB-File Exception %s' % str(e))
            # Mark situation
            self._lazy_is_valid = False

        if self.xml_root is None or len(self.xml_root) == 0:
            self.xml_iter = None
            self.xml_root = None
            self.xml_itr_count = 0

            raise StopIteration()

        if self.meta is None:
            # Attempt to populate meta information
            self.meta = {}

            for meta in self.xml_root.xpath('/ns:nzb/ns:head[1]/ns:meta',
                                            namespaces=NZB_LXML_NAMESPACES):
                # Store the Meta Information Detected
                self.meta[meta.attrib['type'].decode(self.encoding)] = \
                    self.unescape_xml(meta.text.strip())

        # Acquire the Segments Groups
        groups = [
            group.text.strip().decode(self.encoding)
            for group in self.xml_root.xpath(
                'ns:groups/ns:group',
                namespaces=NZB_LXML_NAMESPACES,
            )
        ]

        # The detected filename
        _filename = ''

        # The name from the meta tag
        _name = self.meta.get('name', '').decode(self.encoding).strip()

        if not _name and self.filepath is not None:
            # Lets try to generate a name frome our NZB-File
            tmpfname = basename(self.filepath)

            # Strip our extension off the end (if present)
            result = NZB_EXTENSION_RE(tmpfname)
            if result and result.group('fname'):
                # Store our new filename as our name
                _name = result.group('fname')

        # Subject
        _subject = self.unescape_xml(
                self.xml_root.attrib.get('subject', '')).decode(self.encoding)

        # Poster
        _poster = self.unescape_xml(
                self.xml_root.attrib.get('poster', '')).decode(self.encoding)

        # Use our Codec(s) to extract our Yenc Subject
        matched = None
        for c in self._codecs:
            # for each entry, parse our article
            matched = c.parse_article(
                subject=_subject,
                poster=_poster,
            )
            if matched:
                # We matched
                break

        if matched:
            # We succesfully got a filename from our subject line
            _filename = matched.get('fname', '').strip()
            if _filename and _name:
                # always allow the name to over-ride the detected filename if
                # we actually have a real name we can assciated with it by
                _ext = self._mime.extension_from_filename(_filename)
                if _ext:
                    _filename = '{0}{1}'.format(_name, _ext)

        # Initialize a NNTPSegmented File Object using the data we read
        _file = NNTPSegmentedPost(
            _filename,
            poster=_poster,
            epoch=self.xml_root.attrib.get('date', '0'),
            subject=_subject,
            groups=groups,
            work_dir=self.work_dir,
            sort_no=self.xml_itr_count,
        )

        # index tracker
        _last_index = 0

        # Now append our segments
        for segment in self.xml_root.xpath(
                'ns:segments/ns:segment', namespaces=NZB_LXML_NAMESPACES):

            _cur_index = int(segment.attrib.get('number', _last_index+1))
            try:
                _size = int(segment.attrib.get('bytes'))
                if _size < 0:
                    _size = 0

            except (TypeError, ValueError):
                _size = 0

            article = NNTPArticle(
                subject=_file.subject,
                poster=_file.poster,
                id=self.unescape_xml(segment.text),
                no=_cur_index,
                work_dir=self.work_dir,
                codecs=self._codecs,
            )

            # Store our empty content Placeholder
            article.add(
                NNTPEmptyContent(
                    filepath=_filename,
                    part=self.xml_itr_count,
                    total_size=_size,
                    work_dir=self.work_dir,
                )
            )

            # Add article
            _file.add(article)

            # Track our index
            _last_index = _cur_index

        if not self._valid_by_mode(_file):
            # Not used; recursively move along
            return self.next()

        # Return our object
        return _file

    def _valid_by_mode(self, segmented):
        """
        Internally called function that takes a segmented object and matches
        it against the mode defined by this class. We return True if the mode
        would otherwise accept this entry, otherwise we return False.
        """

        if self._nzb_mode == NZBParseMode.Simple:
            # We accept everything
            return True

        # Our Mime Response
        mr = None

        # Check our segmented content to see if it's a par-file
        if segmented.filename:
            # use filename
            mr = self._mime.from_filename(segmented.filename)

        if mr is None or mr == DEFAULT_MIME_TYPE:
            try:
                for article in segmented:
                    for content in article:
                        if content.filename:
                            mr = self._mime.from_filename(content.filename)

                        if mr is None or mr == DEFAULT_MIME_TYPE:
                            mr = self._mime.from_file(content.path())

                        if not (mr is None or mr == DEFAULT_MIME_TYPE):
                            raise StopIteration()

            except StopIteration:
                # we're done
                pass

        if mr is None or mr == DEFAULT_MIME_TYPE:
            # We don't know the type, so we can't do much here
            return True

        if (self._nzb_mode & NZBParseMode.IgnorePars):
            # our mime type is going to be one of the following
            #  - application/par2
            #  - application/x-par2
            #
            # it wil always be in lower case, so the following should
            # get our match for us:
            if mr.type().endswith('par2'):
                return False

        if (self._nzb_mode & NZBParseMode.IgnoreAttached):
            if segmented[0][0].is_attached():
                return False

        # If we get here, we can go ahead and process our file
        return True

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

        if self._segments_loaded is True:
            self._segment_iter = iter(self.segments)
            return self

        elif len(self.segments) > 0:
            # make sure we don't have any segments lingering
            self.segments.clear()

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

    def size(self):
        """
        Returns the calculated size of all of the contents residing in the
        NZB-File.  If it has not been downloaded yet, the size is calculated
        based on the contents of the NZB-File

        """
        if self._segments_loaded:
            return sum(s.size() for s in self.segments)

        else:
            return sum(c.size() for c in self)

    def __len__(self):
        """
        Returns the number of files in the NZB File
        """
        return len(self.segments) if self._segments_loaded is True\
            else sum(1 for c in self)

    def __getitem__(self, index):
        """
        Support accessing NNTPSegmentedPost objects by index
        """
        if self._segments_loaded is None:
            # Attempt to preload our content
            self.load()

        return self.segments[index]

    def __repr__(self):
        """
        Return a printable version of the file being read
        """
        return '<NNTPnzb filename="%s" />' % (
            self.filepath,
        )
