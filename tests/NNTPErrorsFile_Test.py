# Test the .ERRORS file beside a file that couldn't all be put together
#
# Copyright (C) 2026 Chris Caron <lead2gold@gmail.com>
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

import os
from datetime import datetime

from newsreap.NNTPErrorsFile import ERRORS_EXTENSION
from newsreap.NNTPErrorsFile import NNTPErrorsFile
from newsreap.NNTPErrorsFile import PART_MISSING
from newsreap.NNTPRetry import NNTPRetry

PARTS = {
    'part2@example.com': {
        'part': 2, 'state': PART_MISSING,
        'tried_at': datetime(2026, 1, 1, 12, 0),
        'tried_by': ['0123456789ab', 'ba9876543210'],
        'posted': datetime(2026, 1, 1, 11, 30)},
}


def test_what_is_written_is_read_back(tmp_path):
    """
    The lines and the parts written are read back as they were; it says
    on top what it is about
    """
    path = str(tmp_path / 'notes.txt')
    assert NNTPErrorsFile(path).save(['Part 2: no server has it'], PARTS)

    text = open(path + ERRORS_EXTENSION, encoding='utf-8').read()
    assert text.startswith('# notes.txt could not all be put together')
    assert not (tmp_path / ('notes.txt' + ERRORS_EXTENSION + '.tmp')).exists()

    found = NNTPErrorsFile(path).load()
    assert (found.found, found.corrupt) == (True, False)
    assert found.problems == ['Part 2: no server has it']
    assert found.parts == PARTS
    assert list(found) == ['part2@example.com'] and len(found) == 1


def test_none_there_is_neither_found_nor_corrupt(tmp_path):
    """
    A file without one is whole
    """
    found = NNTPErrorsFile(str(tmp_path / 'notes.txt')).load()
    assert (found.found, found.corrupt, found.parts) == (False, False, {})


def test_one_that_cant_be_read_back_is_corrupt(tmp_path):
    """
    Anything that isn't what we write (cut short, changed by hand, about
    another file, a version we don't know, too big) counts as corrupt and
    nothing is remembered of its parts
    """
    path = str(tmp_path / 'notes.txt')
    good = NNTPErrorsFile(path)
    good.save(['x'], PARTS)
    text = open(path + ERRORS_EXTENSION, encoding='utf-8').read()

    for broken in (
            text[:len(text) // 2] + '\n  - [',
            'just some words',
            text.replace('file: notes.txt', 'file: other.txt'),
            text.replace('version: 1', 'version: 99'),
            text.replace('state: missing', 'state: gone'),
            text.replace("tried_at: 2026-01-01 12:00:00",
                         "tried_at: 'not a date'"),
            text.replace('part: 2', 'part: two'),
            'version: 1\nfile: notes.txt\nparts: [1, 2]\n'):
        with open(path + ERRORS_EXTENSION, 'w', encoding='utf-8') as fp:
            fp.write(broken)

        found = NNTPErrorsFile(path).load()
        assert (found.found, found.corrupt, found.parts) == (True, True, {}), \
            broken

    # Too big to be one of ours
    with open(path + ERRORS_EXTENSION, 'w', encoding='utf-8') as fp:
        fp.write(text)
    import newsreap.NNTPErrorsFile as module
    before = module.MAX_ERRORS_SIZE
    module.MAX_ERRORS_SIZE = 10
    try:
        assert NNTPErrorsFile(path).load().corrupt

    finally:
        module.MAX_ERRORS_SIZE = before


def test_removing_it_leaves_nothing_behind(tmp_path):
    """
    Removed once its file is whole; removing one that isn't there is fine
    """
    path = str(tmp_path / 'notes.txt')
    errors = NNTPErrorsFile(path)
    errors.save(['x'], PARTS)
    assert errors.remove()
    assert not (tmp_path / ('notes.txt' + ERRORS_EXTENSION)).exists()
    assert errors.remove()


def test_dates_written_with_a_timezone_are_read_as_utc(tmp_path):
    """
    A date given a timezone by hand is moved to UTC (as ours are), so it
    can be compared with the others
    """
    path = str(tmp_path / 'notes.txt')
    NNTPErrorsFile(path).save(['x'], PARTS)
    target = path + ERRORS_EXTENSION
    text = open(target, encoding='utf-8').read()
    with open(target, 'w', encoding='utf-8') as fp:
        fp.write(text.replace('tried_at: 2026-01-01 12:00:00',
                              'tried_at: 2026-01-01 14:00:00+02:00'))

    found = NNTPErrorsFile(path).load()
    assert not found.corrupt
    entry = found.parts['part2@example.com']
    assert entry['tried_at'] == datetime(2026, 1, 1, 12, 0)
    assert entry['tried_at'].tzinfo is None
    assert NNTPRetry([]).state(entry['tried_by'], entry['tried_at'],
                               entry['posted']) == 'ask'


def test_a_name_that_isnt_utf8_can_still_be_written(tmp_path):
    """
    A file named in another character set (ie: Latin-1, as old posts
    often are) still gets its .ERRORS file, and it is read back
    """
    path = os.path.join(str(tmp_path), 'caf\udce9.txt')
    assert NNTPErrorsFile(path).save(['Part 1 of caf\udce9.txt'], PARTS)

    found = NNTPErrorsFile(path).load()
    assert (found.found, found.corrupt) == (True, False)
    assert found.parts == PARTS


def test_deeply_nested_junk_is_corrupt(tmp_path):
    """
    A file of brackets nested too deep to read is corrupt, not a crash
    """
    path = str(tmp_path / 'notes.txt')
    with open(path + ERRORS_EXTENSION, 'w', encoding='utf-8') as fp:
        fp.write('parts: ' + '[' * 200000)

    assert NNTPErrorsFile(path).load().corrupt
