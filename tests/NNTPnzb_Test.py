# Test the NNTPnzb Object
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

import sys
from os.path import join
from os.path import isdir
from os.path import dirname
from os.path import basename
from os.path import isfile
from os.path import abspath

import pytest

try:
    from tests.TestBase import TestBase

except ImportError:
    sys.path.insert(0, dirname(dirname(abspath(__file__))))
    from tests.TestBase import TestBase

from newsreap.NNTPnzb import NNTPnzb
from newsreap.NNTPnzb import NZBParseMode

from newsreap.NNTPBinaryContent import NNTPBinaryContent
from newsreap.NNTPArticle import NNTPArticle
from newsreap.NNTPSegmentedPost import NNTPSegmentedPost
from os.path import exists


class NNTPnzb_Test(TestBase):
    """
    A Class for testing NNTPnzb which handles all the XML
    parsing and simple iterations over our XML files.

    """

    def test_general_features(self):
        """
        Open a valid nzb file and make sure we can parse it
        """
        # No parameters should create a file
        nzbfile = join(self.var_dir, 'Ubuntu-16.04.1-Server-i386.nzb')
        assert isfile(nzbfile)
        # create an object containing our nzbfile
        nzbobj = NNTPnzb(nzbfile=nzbfile)

        # Until content is loaded into memory, we use the GID detected from
        # the NZB-File (xml)
        assert nzbobj.gid() == '8c6b3a3bc8d925cd63125f7bea31a5c9'

        assert(nzbobj._segments_loaded is None)
        # Attempting to access the object by it's index forces it to load
        # the contents into memory
        _ = nzbobj[0]

        assert(nzbobj._segments_loaded is True)

        # Test iterations
        for article in nzbobj:
            assert isinstance(article, NNTPSegmentedPost)

        # Load our NZB-File into memory
        assert(nzbobj.load() is True)

        # Test iterations (with enumeration)
        for _, article in enumerate(nzbobj):
            assert isinstance(article, NNTPSegmentedPost)

            for seg in article:
                # Iterate over objects
                assert isinstance(seg, NNTPArticle)

        # Test Length (this particular file we know has 55 entries
        # If we don't hardcode this check, we could get it wrong below
        assert len(nzbobj) == 55

        # Processing the length pre-loads our segments
        assert(nzbobj._segments_loaded is True)

        # We should be able to iterate over each entry and get
        # the same count
        assert len(nzbobj) == sum(1 for c in nzbobj)

        assert nzbobj.is_valid() is True

        # use load() to load all of the NZB entries into memory
        # This is never nessisary to do unless you plan on modifying the NZB
        # file. Those reading the tests to learn how the code works, if you're
        # only going to parse the nzbfile for it's entries, just run a
        # for loop over the object (without ever calling load() to use the
        # least amount of memory and only parse line by line on demand
        nzbobj.load()

        # Count should still 55
        assert len(nzbobj) == 55

        # Test enumeration works too
        for no, article in enumerate(nzbobj):
            assert isinstance(article, NNTPSegmentedPost)
            assert str(nzbobj[no]) == str(article)

        for no, segment in enumerate(nzbobj.segments):
            # Test equality
            assert(nzbobj[no] == segment)

    def test_gid_retrievals(self):
        """
        Test different variations of gid retrievals based on what
        defines a valid GID entry in an NZB File.
        """
        # No parameters should create a file
        nzbfile = join(
            self.var_dir,
            'Ubuntu-16.04.1-Server-i386-nofirstsegment.nzb',
        )
        assert(isfile(nzbfile) is True)

        nzbobj = NNTPnzb(nzbfile=nzbfile)
        assert(nzbobj.is_valid() is True)

        # GID Is not retrievable
        assert(nzbobj.gid() is None)

        # No parameters should create a file
        nzbfile = join(
            self.var_dir,
            'Ubuntu-16.04.1-Server-i386-nofile.nzb',
        )
        assert(isfile(nzbfile) is True)

        nzbobj = NNTPnzb(nzbfile=nzbfile)
        assert(nzbobj.is_valid() is True)

        # GID Is not retrievable
        assert(nzbobj.gid() is None)

        # A gid() call does not cause segments to be loaded into memory
        assert(nzbobj._segments_loaded is None)

        # No parameters should create a file
        nzbfile = join(
            self.var_dir,
            'Ubuntu-16.04.1-Server-i386-noindex.nzb',
        )
        assert(isfile(nzbfile) is True)

        nzbobj = NNTPnzb(nzbfile=nzbfile)
        assert(nzbobj.is_valid() is True)

        # GID should still be the correct first entry
        assert(nzbobj.gid() == '8c6b3a3bc8d925cd63125f7bea31a5c9')

        # No parameters should create a file
        nzbfile = join(
            self.var_dir,
            'Ubuntu-16.04.1-Server-i386-badsize.nzb',
        )
        assert isfile(nzbfile)

        nzbobj = NNTPnzb(nzbfile=nzbfile)
        assert(nzbobj.is_valid() is True)

        # is_valid() only checks the NZB-Files validity; it doesn't
        # load our segments
        assert(nzbobj._segments_loaded is None)

        # Confirm nothing is loaded
        assert(len(nzbobj) == 55)

        # Test that we correctly store all Size 0
        # Test iterations
        for article in nzbobj:
            assert isinstance(article, NNTPSegmentedPost) is True
            assert article.size() == 0

        # Load our NZB-File into memory
        assert(nzbobj.load() is True)

        # Our GID remains the same even after the NZB-File is loaded
        # This is important because the GID is based on the file content
        # within the NZB-File.  The first entry should always be alphabetically
        # placed first.  It's possible the XML-File version of the NZB-File
        # does not sort content this way and therefore gid() parses the first
        # entry However, loading the file forces the proper sorting and will
        # always return the correct gid.
        assert(nzbobj.gid() == '8c6b3a3bc8d925cd63125f7bea31a5c9')

        # Test Length (this particular file we know has 55 entries
        # If we don't hardcode this check, we could get it wrong below
        assert(len(nzbobj) == 55)

        # If we save our NZB-File back:
        new_nzbfile = join(self.tmp_dir, 'test.nzbfile.copy.nzb')

        # File should not exist
        assert(isfile(new_nzbfile) is False)

        # Save our new NZB-File
        assert(nzbobj.save(nzbfile=new_nzbfile) is True)

        # NZB-File exists now
        assert(isfile(new_nzbfile) is True)

        # Verify we actually did load our segments
        assert(nzbobj._segments_loaded is True)

        # Now we'll open up our newly Saved nzbfile
        new_nzbobj = NNTPnzb(nzbfile=new_nzbfile)

        # We'll open up our other nzb-File again (just to fully reset it's
        # object)
        nzbobj = NNTPnzb(nzbfile=nzbfile)

        # We're a valid XML
        assert(nzbobj.is_valid() is True)
        assert(new_nzbobj.is_valid() is True)

        # Our NZB-File File Count is the same
        assert(len(nzbobj) == len(new_nzbobj))

        # Load our NZB-File into memory
        assert(new_nzbobj.load() is True)
        assert(nzbobj.load() is True)

        # Our NZB-File File Count is the same
        assert(len(nzbobj) == len(new_nzbobj))
        assert(nzbobj.gid() == new_nzbobj.gid())

    def test_nzbfile_generation(self):
        """
        Tests the creation of NZB Files
        """
        nzbfile = join(self.tmp_dir, 'test.nzbfile.nzb')
        payload = join(self.var_dir, 'uudecoded.tax.jpg')
        assert isfile(nzbfile) is False
        # Create our NZB Object
        nzbobj = NNTPnzb()

        # create a fake article
        segpost = NNTPSegmentedPost(basename(payload))
        content = NNTPBinaryContent(payload)

        article = NNTPArticle('testfile', groups='newsreap.is.awesome')

        # Note that our nzb object segment tracker is not marked as being
        # complete. This flag gets toggled when we add segments manually to
        # our nzb object or if we parse an NZB-File
        assert(nzbobj._segments_loaded is None)

        # Add our Content to the article
        article.add(content)
        # now add our article to the NZBFile
        segpost.add(article)
        # now add our Segmented Post to the NZBFile
        nzbobj.add(segpost)

        # Since .add() was called, this will be set to True now
        assert(nzbobj._segments_loaded is True)

        # Store our file
        assert nzbobj.save(nzbfile) is True
        assert isfile(nzbfile) is True

    def test_bad_files(self):
        """
        Test different variations of bad file inputs
        """
        # No parameters should create a file
        nzbfile = join(self.var_dir, 'missing.file.nzb')
        assert not isfile(nzbfile)

        nzbobj = NNTPnzb(nzbfile=nzbfile)
        assert nzbobj.is_valid() is False
        assert nzbobj.gid() is None

        # Test Length
        assert len(nzbobj) == 0

    def test_iter_skip_pars(self):
        """
        Test scanning NZB-Files and ignoring part entries

        """

        # No parameters should create a file
        nzbfile = join(self.var_dir, 'Ubuntu-16.04.1-Server-i386.nzb')
        assert(isfile(nzbfile) is True)

        # create an object containing our nzbfile but no mode set
        nzbobj = NNTPnzb(nzbfile=nzbfile, mode=NZBParseMode.Simple)

        # A list of all of our expected (parsed) SegmentedFile entries:
        expected_results = (
            "Ubuntu-16.04.1-Server-i386.10",
            "Ubuntu-16.04.1-Server-i386.11",
            "Ubuntu-16.04.1-Server-i386.12",
            "Ubuntu-16.04.1-Server-i386.13",
            "Ubuntu-16.04.1-Server-i386.14",
            "Ubuntu-16.04.1-Server-i386.15",
            "Ubuntu-16.04.1-Server-i386.16",
            "Ubuntu-16.04.1-Server-i386.17",
            "Ubuntu-16.04.1-Server-i386.18",
            "Ubuntu-16.04.1-Server-i386.19",
            "Ubuntu-16.04.1-Server-i386.20",
            "Ubuntu-16.04.1-Server-i386.21",
            "Ubuntu-16.04.1-Server-i386.22",
            "Ubuntu-16.04.1-Server-i386.23",
            "Ubuntu-16.04.1-Server-i386.24",
            "Ubuntu-16.04.1-Server-i386.25",
            "Ubuntu-16.04.1-Server-i386.26",
            "Ubuntu-16.04.1-Server-i386.27",
            "Ubuntu-16.04.1-Server-i386.28",
            "Ubuntu-16.04.1-Server-i386.29",
            "Ubuntu-16.04.1-Server-i386.30",
            "Ubuntu-16.04.1-Server-i386.31",
            "Ubuntu-16.04.1-Server-i386.32",
            "Ubuntu-16.04.1-Server-i386.33",
            "Ubuntu-16.04.1-Server-i386.34",
            "Ubuntu-16.04.1-Server-i386.35",
            "Ubuntu-16.04.1-Server-i386.36",
            "Ubuntu-16.04.1-Server-i386.37",
            "Ubuntu-16.04.1-Server-i386.38",
            "Ubuntu-16.04.1-Server-i386.39",
            "Ubuntu-16.04.1-Server-i386.40",
            "Ubuntu-16.04.1-Server-i386.41",
            "Ubuntu-16.04.1-Server-i386.42",
            "Ubuntu-16.04.1-Server-i386.43",
            "Ubuntu-16.04.1-Server-i386.44",
            "Ubuntu-16.04.1-Server-i386.45",
            "Ubuntu-16.04.1-Server-i386.46",
            "Ubuntu-16.04.1-Server-i386.47",
            "Ubuntu-16.04.1-Server-i386.48",
            "Ubuntu-16.04.1-Server-i386.49",
            "Ubuntu-16.04.1-Server-i386.50",
            "Ubuntu-16.04.1-Server-i386.51",
            "Ubuntu-16.04.1-Server-i386.52",
            "Ubuntu-16.04.1-Server-i386.53",
            "Ubuntu-16.04.1-Server-i386.54",
            "Ubuntu-16.04.1-Server-i386.55",

            # Our Par Files (9 in total)
            "Ubuntu-16.04.1-Server-i386.par2",
            "Ubuntu-16.04.1-Server-i386.vol000+01.par2",
            "Ubuntu-16.04.1-Server-i386.vol001+02.par2",
            "Ubuntu-16.04.1-Server-i386.vol003+04.par2",
            "Ubuntu-16.04.1-Server-i386.vol007+08.par2",
            "Ubuntu-16.04.1-Server-i386.vol015+16.par2",
            "Ubuntu-16.04.1-Server-i386.vol031+32.par2",
            "Ubuntu-16.04.1-Server-i386.vol063+64.par2",
            "Ubuntu-16.04.1-Server-i386.vol127+74.par2",
        )

        expected_iter = iter(expected_results)
        for segment in nzbobj:
            assert(segment.filename == next(expected_iter))

        try:
            # We should have gracefully passed through our entire list
            next(expected_iter)
            # We should never make it here
            assert(False)

        except StopIteration:
            # We really did process our whole list - Excellent!
            assert(True)

        # create an object containing our nzbfile but set to skip pars
        nzbobj_sp = NNTPnzb(nzbfile=nzbfile, mode=NZBParseMode.IgnorePars)

        # Now our skip par list works a bit differently.  Because we know we
        # can successfully detect our par files from the others, this
        # iterator should intentionally skip over .par files
        expected_iter = iter(expected_results)
        results = []
        for segment in nzbobj_sp:
            assert(segment.filename == next(expected_iter))
            results.append(segment.filename)

        # We should still have 9 files left in our list we didn't process
        assert(len(expected_results) - len(results) == 9)

        # One of the big differences we'll notice is that we will have entries
        # left over from our list since we'll be avoiding ParFiles this time
        # around

        # Our list count is different because we're ignoring pars in one list
        assert(len(nzbobj_sp) < len(nzbobj))

    def test_name_comes_from_filename_without_meta(self):
        """
        Without a name in its meta data, the NZB-File's filename is used
        """
        nzbfile = join(self.var_dir, 'Ubuntu-16.04.1-Server-i386.nzb')
        nzbcopy = join(self.tmp_dir, 'NNTPnzb_Test.name', 'Ubuntu-Copy.nzb')
        assert self.touch(nzbcopy) is True

        with open(nzbfile) as src:
            content = src.read()

        # Strip the name from the meta data
        meta = '<meta type="name">Ubuntu-16.04.1-Server-i386</meta>'
        assert meta in content
        with open(nzbcopy, 'w') as dst:
            dst.write(content.replace(meta, ''))

        nzbobj = NNTPnzb(nzbfile=nzbcopy)
        filenames = [segment.filename for segment in nzbobj]
        assert len(filenames) == len(NNTPnzb(nzbfile=nzbfile))
        assert all(f.startswith('Ubuntu-Copy.') for f in filenames)

    def test_saved_files_are_nzb_1_2(self):
        """
        Saved NZB-Files declare NZB 1.2 (hosted on GitHub) and still use the
        1.1 namespace, so older readers can read them
        """
        nzbfile = join(self.var_dir, 'Ubuntu-16.04.1-Server-i386.nzb')
        nzbcopy = join(self.tmp_dir, 'NNTPnzb_Test.dtd', 'Ubuntu.nzb')
        assert self.touch(nzbcopy) is True

        nzbobj = NNTPnzb(nzbfile=nzbfile)
        assert nzbobj.load() is True
        assert nzbobj.save(nzbcopy) is True

        with open(nzbcopy) as f:
            content = f.read()

        assert '<!DOCTYPE nzb PUBLIC "-//nuxref//DTD NZB 1.2//EN" ' \
            '"https://raw.githubusercontent.com/caronc/newsreap/master/' \
            'newsreap/var/nzb-1.2.dtd">' in content
        assert '<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">' in content
        assert NNTPnzb(nzbfile=nzbcopy).is_valid() is True

    def test_nzb_1_2_attributes_are_valid(self):
        """
        The filename and crc32 attributes NZB 1.2 adds pass validation
        """
        nzbfile = join(self.tmp_dir, 'NNTPnzb_Test.dtd', 'v1.2.nzb')
        assert self.touch(nzbfile) is True
        with open(nzbfile, 'w') as f:
            f.write(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">'
                '<file subject="test" filename="test.rar" crc32="deadbeef">'
                '<groups><group>alt.binaries.test</group></groups>'
                '<segments><segment bytes="10" number="1" crc32="00ff00ff">'
                'part1@example.com</segment></segments></file></nzb>\n')

        assert NNTPnzb(nzbfile=nzbfile).is_valid() is True

    def test_repeated_meta_is_kept(self):
        """
        A meta type given more than once (ie: several passwords) keeps
        every value, in order
        """
        nzbfile = join(self.var_dir, 'Ubuntu-16.04.1-Server-i386.nzb')
        nzbcopy = join(self.tmp_dir, 'NNTPnzb_Test.meta', 'Ubuntu.nzb')
        assert self.touch(nzbcopy) is True

        nzbobj = NNTPnzb(nzbfile=nzbfile)
        assert nzbobj.load() is True
        nzbobj.meta = {
            'title': 'Ubuntu', 'password': ['first', 'second'],
            'category': 'Software'}
        assert nzbobj.save(nzbcopy) is True

        nzbobj = NNTPnzb(nzbfile=nzbcopy)
        assert len(list(nzbobj)) == 55
        assert nzbobj.meta == {
            'title': 'Ubuntu', 'password': ['first', 'second'],
            'category': 'Software'}
        assert nzbobj.is_valid() is True

    def test_title_names_the_post(self):
        """
        The title meta names the post (before the older name meta)
        """
        nzbobj = NNTPnzb()
        assert nzbobj.title() == ''

        nzbobj.meta = {'name': 'Older'}
        assert nzbobj.title() == 'Older'

        nzbobj.meta = {'name': 'Older', 'title': ' Newer '}
        assert nzbobj.title() == 'Newer'

        nzbobj.meta = {'title': ['First', 'Second']}
        assert nzbobj.title() == 'First'

    def test_nzb_is_available_from_the_package(self):
        """
        NNTPnzb can be imported straight from the newsreap package
        """
        from newsreap import NNTPnzb as PackageNNTPnzb
        assert PackageNNTPnzb is NNTPnzb


def test_list_reads_every_file():
    """
    list() and len() of an NZB agree (len() doesn't disturb an iteration
    already in progress)
    """
    path = join(dirname(abspath(__file__)), 'var',
                'Ubuntu-16.04.1-Server-i386.nzb')

    nzb = NNTPnzb(path)
    assert len(list(nzb)) == len(nzb) == 55

    # Counting part way through an iteration leaves it alone
    files = iter(nzb)
    next(files)
    assert len(nzb) == 55

    remaining = 0
    for _ in range(100):
        try:
            next(files)

        except StopIteration:
            break

        remaining += 1

    assert remaining == 54


def test_an_nzb_file_cannot_pull_in_other_content(tmp_path):
    """
    Entities an NZB file defines are never expanded, so it can't read a
    file on this machine (or a web address) into what we keep
    """
    secret = tmp_path / 'secret.txt'
    secret.write_text('TOP-SECRET')
    path = tmp_path / 'sneaky.nzb'
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE nzb [\n'
        '  <!ENTITY secret SYSTEM "file://%s">\n'
        '  <!ENTITY word "EXPANDED">\n'
        ']>\n'
        '<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">\n'
        ' <head><meta type="name">&word;</meta></head>\n'
        ' <file poster="p@x" date="1469191680" subject="a (1/1)">\n'
        '  <groups><group>&secret;</group><group>&word;</group>'
        '<group>alt.test</group></groups>\n'
        '  <segments><segment bytes="10" number="1">id@x</segment>'
        '</segments>\n'
        ' </file>\n'
        '</nzb>\n' % secret)

    nzb = NNTPnzb(str(path))
    posts = list(nzb)
    assert len(posts) == 1
    groups = {str(group) for group in posts[0].groups}
    assert groups == {'alt.test'}
    assert 'TOP-SECRET' not in str(nzb.meta)
    assert 'EXPANDED' not in str(nzb.meta)


def test_a_failed_save_leaves_the_file_as_it_was(tmp_path):
    """
    A save that raises part way, or fails, leaves the NZB-File that was
    there as it was (and nothing half written beside it or left open)
    """
    import pytest
    from newsreap.NNTPArticle import NNTPArticle
    from newsreap.NNTPEmptyContent import NNTPEmptyContent
    from newsreap.NNTPnzb import NNTPnzb
    from newsreap.NNTPSegmentedPost import NNTPSegmentedPost

    def post(name):
        found = NNTPSegmentedPost(name, groups=['alt.binaries.test'])
        article = NNTPArticle(id='%s@example.com' % name, no=1)
        article.add(NNTPEmptyContent(filepath=name, part=1, total_size=10))
        found.add(article)
        return found

    path = tmp_path / 'a.nzb'
    nzb = NNTPnzb()
    assert nzb.save(str(path), posts=[post('a.bin')]) is True
    before = path.read_bytes()

    def broken():
        yield post('b.bin')
        raise RuntimeError('the database went away')

    nzb = NNTPnzb()
    with pytest.raises(RuntimeError):
        nzb.save(str(path), posts=broken())
    assert nzb.stream is None

    # A file with no articles can't be written
    assert NNTPnzb().save(str(path), posts=[
        post('c.bin'), NNTPSegmentedPost('empty.bin')]) is False

    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ['a.nzb']


def test_meta_is_read_from_the_file_when_asked_for(tmp_path):
    """
    An NZB-File's meta can be read straight away (without going through
    its files first); one without any has none, a new one has none until
    it is set, and loading another file reads that file's
    """
    from newsreap.NNTPnzb import NNTPnzb

    def nzb_file(name, head):
        path = tmp_path / name
        path.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">\n'
            '%s'
            ' <file poster="me" date="1" subject="a.bin (1/1)">\n'
            '  <groups><group>alt.binaries.test</group></groups>\n'
            '  <segments>\n'
            '   <segment bytes="10" number="1">a@example.com</segment>\n'
            '  </segments>\n'
            ' </file>\n'
            '</nzb>\n' % head)
        return str(path)

    first = nzb_file('a.nzb', ' <head>\n'
                     '  <meta type="title">Holiday</meta>\n'
                     '  <meta type="password">one</meta>\n'
                     '  <meta type="password">two</meta>\n'
                     ' </head>\n')
    nzb = NNTPnzb(first)
    assert nzb.meta == {'title': 'Holiday', 'password': ['one', 'two']}
    assert len(list(nzb)) == 1
    assert nzb.meta == {'title': 'Holiday', 'password': ['one', 'two']}

    assert NNTPnzb(nzb_file('b.nzb', '')).meta == {}
    assert NNTPnzb().meta is None

    nzb.load(nzb_file('c.nzb', ' <head><meta type="title">Other</meta>'
                               '</head>\n'))
    assert nzb.meta == {'title': 'Other'}

    # Saved somewhere else, it keeps its meta
    nzb = NNTPnzb(first)
    assert nzb.save(str(tmp_path / 'copy.nzb')) is True
    assert NNTPnzb(str(tmp_path / 'copy.nzb')).meta == {
        'title': 'Holiday', 'password': ['one', 'two']}


def test_nzb_text_is_read_back_exactly_as_it_was_written(tmp_path):
    """
    What an NZB-File holds reads back as it was (entities are only decoded
    once, so '&amp;not' stays '&not'), and a meta entry without a type is
    passed over
    """
    from newsreap.NNTPnzb import NNTPnzb

    path = tmp_path / 'a.nzb'
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">\n'
        ' <head>\n'
        '  <meta type="password">a&amp;not&amp;amp;b</meta>\n'
        '  <meta>no type</meta>\n'
        ' </head>\n'
        ' <file poster="me &amp;lt;x&amp;gt;" date="1" '
        'subject="Tom &amp;amp; Jerry &quot;a.bin&quot; yEnc (1/1)">\n'
        '  <groups><group>alt.binaries.test</group></groups>\n'
        '  <segments>\n'
        '   <segment bytes="10" number="1">a&amp;not@example.com</segment>\n'
        '  </segments>\n'
        ' </file>\n'
        '</nzb>\n')

    nzb = NNTPnzb(str(path))
    posts = list(nzb)
    assert nzb.meta == {'password': 'a&not&amp;b'}
    assert posts[0].poster == 'me &lt;x&gt;'
    assert posts[0].subject.startswith('Tom &amp; Jerry')
    assert [a.id for a in posts[0]] == ['a&not@example.com']


def open_on(path):
    """
    How many descriptors this process has open on the file given (other
    tests open and close their own meanwhile, so only these count)
    """
    import os

    found = 0
    for fd in os.listdir('/proc/self/fd'):
        try:
            found += os.readlink('/proc/self/fd/%s' % fd) == path

        except OSError:
            # (it was closed while we looked)
            pass

    return found


def test_a_saved_copy_holds_every_file_of_the_nzb_it_was_read_from(tmp_path):
    """
    An NZB-File opened from disk saves all of its files and its meta,
    whether it was loaded first or not (files not loaded are read from the
    disk as they're written, never all held in memory)
    """
    import os
    from shutil import copyfile
    from newsreap.NNTPnzb import NNTPnzb

    source = join(dirname(abspath(__file__)), 'var',
                  'Ubuntu-16.04.1-Server-i386.nzb')

    def ids(path):
        return [(post.subject, sorted(str(g) for g in post.groups),
                 [(a.id, sum(len(c) for c in a)) for a in post])
                for post in NNTPnzb(path)]

    expected = ids(source)
    assert len(expected) == 55

    nzb = NNTPnzb(source)
    nzb.meta = {'title': 'Ubuntu'}
    copy = str(tmp_path / 'copy.nzb')
    assert nzb.save(copy) is True
    assert ids(copy) == expected
    assert NNTPnzb(copy).meta == {'title': 'Ubuntu'}
    assert len(nzb.segments) == 0
    assert nzb.filepath == copy

    if os.path.isdir('/proc/self/fd'):
        # The file read from was closed again
        assert open_on(os.path.realpath(source)) == 0

    # Loaded first, it saves the same
    nzb = NNTPnzb(source)
    assert nzb.load() is True
    loaded = str(tmp_path / 'loaded.nzb')
    assert nzb.save(loaded) is True
    assert ids(loaded) == expected

    # Saved over itself, it still holds every file
    inplace = str(tmp_path / 'inplace.nzb')
    copyfile(source, inplace)
    assert NNTPnzb(inplace).save() is True
    assert ids(inplace) == expected
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        'copy.nzb', 'inplace.nzb', 'loaded.nzb']


def test_a_save_that_cant_take_the_files_place_changes_nothing(tmp_path):
    """
    A save whose file can't be put in place (a folder is there) or can't
    be written at all (its folder is missing) fails, and leaves nothing
    behind or changed
    """
    from newsreap.NNTPArticle import NNTPArticle
    from newsreap.NNTPEmptyContent import NNTPEmptyContent
    from newsreap.NNTPnzb import NNTPnzb
    from newsreap.NNTPSegmentedPost import NNTPSegmentedPost

    post = NNTPSegmentedPost('a.bin', groups=['alt.binaries.test'])
    article = NNTPArticle(id='a@example.com', no=1)
    article.add(NNTPEmptyContent(filepath='a.bin', part=1, total_size=10))
    post.add(article)

    folder = tmp_path / 'a.nzb'
    folder.mkdir()
    (folder / 'keep.txt').write_text('mine')

    nzb = NNTPnzb()
    nzb.add(post)
    assert nzb.save(str(folder)) is False
    assert nzb.filepath != str(folder)
    assert nzb.stream is None
    assert (folder / 'keep.txt').read_text() == 'mine'
    assert sorted(p.name for p in tmp_path.iterdir()) == ['a.nzb']

    assert nzb.save(str(tmp_path / 'missing' / 'a.nzb')) is False
    assert sorted(p.name for p in tmp_path.iterdir()) == ['a.nzb']

    # It can still be saved somewhere it fits
    assert nzb.save(str(tmp_path / 'b.nzb')) is True
    assert [p.id for p in NNTPnzb(str(tmp_path / 'b.nzb'))[0]] == [
        'a@example.com']


def test_meta_of_a_broken_or_file_free_nzb(tmp_path):
    """
    The meta of an NZB-File that is broken is empty (it doesn't raise);
    one with a <head/> but no files still has its meta read
    """
    from newsreap.NNTPnzb import NNTPnzb

    broken = tmp_path / 'broken.nzb'
    broken.write_text('<nzb><head><meta type="title">x</meta')
    assert NNTPnzb(str(broken)).meta == {}

    headonly = tmp_path / 'head.nzb'
    headonly.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">\n'
        ' <head><meta type="title">Holiday</meta></head>\n'
        '</nzb>\n')
    assert NNTPnzb(str(headonly)).meta == {'title': 'Holiday'}


def test_adding_to_an_nzb_read_from_disk_keeps_its_files(tmp_path):
    """
    A file added to an NZB-File opened from disk joins the files it
    already lists (they're loaded first), so saving it keeps them all
    """
    from newsreap.NNTPArticle import NNTPArticle
    from newsreap.NNTPEmptyContent import NNTPEmptyContent
    from newsreap.NNTPnzb import NNTPnzb
    from newsreap.NNTPSegmentedPost import NNTPSegmentedPost

    source = join(dirname(abspath(__file__)), 'var',
                  'Ubuntu-16.04.1-Server-i386.nzb')

    post = NNTPSegmentedPost('extra.bin', groups=['alt.binaries.test'])
    article = NNTPArticle(id='extra@example.com', no=1)
    article.add(NNTPEmptyContent(filepath='extra.bin', part=1,
                                 total_size=10))
    post.add(article)

    nzb = NNTPnzb(source)
    assert nzb.add(post) is True
    assert len(nzb) == 56

    copy = str(tmp_path / 'copy.nzb')
    assert nzb.save(copy) is True
    ids = [a.id for found in NNTPnzb(copy) for a in found]
    assert len(ids) == 1013 + 1
    assert 'extra@example.com' in ids

    # Adding it again changes nothing
    assert nzb.add(post) is False


def test_reading_one_file_keeps_the_mode_set(tmp_path):
    """
    Asking for one file of an NZB-File (nzb[0]) loads it the way its mode
    reads it (ie: par files are still left out)
    """
    from newsreap.NNTPnzb import NNTPnzb
    from newsreap.NNTPnzb import NZBParseMode

    source = join(dirname(abspath(__file__)), 'var',
                  'Ubuntu-16.04.1-Server-i386.nzb')

    every = len(NNTPnzb(source))
    nzb = NNTPnzb(source, mode=NZBParseMode.IgnorePars)
    assert nzb[0] is not None
    assert len(nzb) == len(list(NNTPnzb(
        source, mode=NZBParseMode.IgnorePars))) < every


NZB_HEAD = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">\n')

NZB_FILE = (
    ' <file poster="me" date="1" subject="%s (1/1)">\n'
    '  <groups><group>alt.binaries.test</group></groups>\n'
    '  <segments>\n'
    '   <segment bytes="10" number="1">%s@example.com</segment>\n'
    '  </segments>\n'
    ' </file>\n')


def test_nzb_files_are_checked_against_the_dtd(tmp_path):
    """
    is_valid() checks an NZB-File a part at a time and finds what doesn't
    follow the DTD wherever it is; a file that is read-only is checked
    too, and nothing is left open
    """
    import os
    import stat
    from newsreap.NNTPnzb import NNTPnzb

    good = NZB_FILE % ('a.bin', 'a') + NZB_FILE % ('b.bin', 'b')
    cases = {
        'good': (True, good),
        'empty': (True, ''),
        'head first': (True, ' <head><meta type="title">x</meta></head>\n'
                       + good),
        'unknown attribute': (False, good.replace(
            '<segment ', '<segment junk="1" ', 1)),
        'unknown element in the last file': (False, NZB_FILE % (
            'a.bin', 'a') + (NZB_FILE % ('b.bin', 'b')).replace(
            '<groups>', '<other/><groups>')),
        'head after a file': (False, good + ' <head></head>\n'),
        'text in the nzb': (False, good + 'hello\n'),
        'text before the first file': (False, 'hello\n' + good),
        'broken': (False, good[:-20]),
    }

    for name, (expected, body) in cases.items():
        path = tmp_path / ('%s.nzb' % name.replace(' ', '_'))
        path.write_text(NZB_HEAD + body + ('</nzb>\n' if name != 'broken'
                                           else ''))
        nzb = NNTPnzb(str(path))
        assert nzb.is_valid() is expected, name
        assert nzb.stream is None, name

    assert NNTPnzb(str(tmp_path / 'missing.nzb')).is_valid() is False

    locked = tmp_path / 'good.nzb'
    os.chmod(locked, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    try:
        nzb = NNTPnzb(str(locked))
        assert nzb.is_valid() is True
        assert nzb.stream is None

    finally:
        os.chmod(locked, stat.S_IRUSR | stat.S_IWUSR)


def test_message_ids_lists_every_article_in_order(tmp_path):
    """
    message_ids() gives every article's Message-ID as the NZB-File lists
    them (one without any is passed over); a broken file gives what could
    be read
    """
    from newsreap.NNTPnzb import NNTPnzb

    path = tmp_path / 'a.nzb'
    path.write_text(
        NZB_HEAD + NZB_FILE % ('a.bin', 'a') + NZB_FILE % ('b.bin', ' ')
        + NZB_FILE % ('c.bin', 'c') + '</nzb>\n')
    assert list(NNTPnzb(str(path)).message_ids()) == [
        'a@example.com', '@example.com', 'c@example.com']

    path.write_text(NZB_HEAD + NZB_FILE.replace('%s@example.com', '') % (
        'a.bin', ) + NZB_FILE % ('b.bin', 'b') + ' <file')
    assert list(NNTPnzb(str(path)).message_ids()) == ['b@example.com']

    assert list(NNTPnzb(str(tmp_path / 'missing.nzb')).message_ids()) == []


def files_held(monkeypatch):
    """
    Watches what an NZB-File's reader holds; returns a list whose only
    entry becomes the most files read before the one being read that were
    still held (lxml reads ahead, so the files after it are there too)
    """
    import sys
    from types import SimpleNamespace

    # (the package's NNTPnzb is the class; its module is wanted here)
    module = sys.modules['newsreap.NNTPnzb']

    most = [0]
    iterparse = module.etree.iterparse
    tag = '{%s}file' % module.NZB_XML_NAMESPACE

    def watched(*args, **kwargs):
        for event, element in iterparse(*args, **kwargs):
            current = element if element.tag == tag else next(
                element.iterancestors(tag), None)
            if current is not None:
                most[0] = max(most[0], sum(
                    1 for _ in current.itersiblings(tag, preceding=True)))
            yield event, element

    monkeypatch.setattr(module, 'etree', SimpleNamespace(iterparse=watched))
    return most


def test_reading_an_nzb_file_lets_go_of_each_file_read(tmp_path,
                                                       monkeypatch):
    """
    Reading the files (or the Message-IDs) of an NZB-File only holds the
    file being read, so an NZB-File of many files is never all held
    """
    from newsreap.NNTPnzb import NNTPnzb

    path = tmp_path / 'many.nzb'
    path.write_text(NZB_HEAD + ''.join(
        NZB_FILE % ('%d.bin' % i, str(i)) for i in range(20)) + '</nzb>\n')

    most = files_held(monkeypatch)
    assert len(list(NNTPnzb(str(path)).message_ids())) == 20
    assert most[0] <= 1

    most[0] = 0
    assert len([post.subject for post in NNTPnzb(str(path))]) == 20
    assert most[0] <= 1


@pytest.mark.skipif(
    not isdir('/proc/self/fd'), reason='needs /proc to see open files')
def test_an_nzb_file_read_keeps_nothing_open(tmp_path):
    """
    An NZB-File that was read (its details, files and Message-IDs) keeps
    nothing open on its file while it is still in use
    """
    from newsreap.NNTPnzb import NNTPnzb

    path = str(tmp_path / 'two.nzb')
    with open(path, 'w') as fp:
        fp.write(NZB_HEAD + NZB_FILE % ('a.bin', 'a') + NZB_FILE % (
            'b.bin', 'b') + '</nzb>\n')

    nzb = NNTPnzb(path)
    assert nzb.is_valid() is True
    assert nzb.gid() is not None
    assert open_on(path) == 0

    nzb.title()
    assert len(list(nzb.message_ids())) == 2
    assert len(list(nzb)) == 2
    assert open_on(path) == 0


def test_a_copy_of_a_broken_nzb_file_is_not_saved(tmp_path):
    """
    Saving an NZB-File that breaks off part way fails (its copy would be
    missing files); nothing is written
    """
    from newsreap.NNTPnzb import NNTPnzb

    path = tmp_path / 'a.nzb'
    path.write_text(NZB_HEAD + NZB_FILE % ('a.bin', 'a') + NZB_FILE % (
        'b.bin', 'b') + ' <file poster="me"><groups></nzb>\n')

    assert NNTPnzb(str(path)).save(str(tmp_path / 'copy.nzb')) is False
    assert sorted(p.name for p in tmp_path.iterdir()) == ['a.nzb']


def test_an_nzb_file_needs_a_path_to_be_saved(tmp_path):
    """
    An NZB-File with nowhere to be saved isn't (and nothing is left
    behind, even when what it is given fails part way)
    """
    import pytest
    from newsreap.NNTPArticle import NNTPArticle
    from newsreap.NNTPEmptyContent import NNTPEmptyContent
    from newsreap.NNTPnzb import NNTPnzb
    from newsreap.NNTPSegmentedPost import NNTPSegmentedPost

    def posts():
        post = NNTPSegmentedPost('a.bin', groups=['alt.binaries.test'])
        article = NNTPArticle(id='a@example.com', no=1)
        article.add(NNTPEmptyContent(filepath='a.bin', part=1, total_size=1))
        post.add(article)
        yield post
        raise RuntimeError('the database went away')

    work = tmp_path / 'work'
    work.mkdir()
    nzb = NNTPnzb(work_dir=str(work))
    try:
        assert nzb.save(posts=posts()) is False

    except RuntimeError:
        pytest.fail('the posts were read')

    assert nzb.stream is None
    assert list(work.iterdir()) == []
