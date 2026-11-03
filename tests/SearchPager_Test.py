# Test showing search results a screen at a time
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

import fcntl
import os
import pty
import select
import subprocess
import sys
import termios
import time
from shutil import which

import pytest

from newsreap.plugins.cli import search

# Shows 500 lines of results with the search command's pager
SHOW = (
    'from newsreap.plugins.cli.search import page\n'
    'page("result %d" % no for no in range(500))\n'
)


def read(fd, seconds, until=None):
    """
    Returns what a terminal shows within `seconds` (sooner once `until`
    is seen)
    """
    found = b''
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        ready, _, _ = select.select([fd], [], [], 0.1)
        if ready:
            try:
                found += os.read(fd, 65536)

            except OSError:
                break

            if until is not None and until in found:
                break

    return found


@pytest.mark.skipif(not which('less'), reason='less is not installed')
def test_keys_reach_the_pager_when_another_terminal_shows_the_results():
    """
    Run through tox, what we write goes to a terminal of tox's own while
    keys are still typed into the user's; the pager must listen to the
    user's (or it can't be moved through, or closed)
    """
    keyboard, keyboard_end = pty.openpty()
    screen, screen_end = pty.openpty()

    def own_terminal():
        # The user's terminal is the one we were started from
        os.setsid()
        fcntl.ioctl(keyboard_end, termios.TIOCSCTTY, 0)

    env = {k: v for k, v in os.environ.items()
           if k not in ('PAGER', 'LESS')}
    env['TERM'] = 'xterm'
    process = subprocess.Popen(
        [sys.executable, '-c', SHOW], stdin=keyboard_end,
        stdout=screen_end, stderr=screen_end, env=env,
        preexec_fn=own_terminal)
    os.close(keyboard_end)
    os.close(screen_end)

    try:
        assert b'result 0' in read(screen, 10, until=b'\n:')

        # Down a row, then a page
        os.write(keyboard, b'\x1bOB')
        assert b'result 23' in read(screen, 3, until=b'result 23')
        os.write(keyboard, b' ')
        assert b'result 40' in read(screen, 3, until=b'result 40')

        os.write(keyboard, b'q')
        process.wait(timeout=5)

    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(keyboard)
        os.close(screen)


def test_results_are_written_out_without_a_terminal(capsys):
    """
    Piped or saved to a file, every line is written out (no pager)
    """
    assert search.pager_command() is None
    assert search.page(['one', 'two']) is True
    assert capsys.readouterr().out == 'one\ntwo\n'
    assert search.page([]) is False


def test_the_pager_setting_is_used(monkeypatch):
    """
    PAGER picks the pager; a terminal that can't page gets none
    """
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: True, raising=False)
    monkeypatch.setattr(sys.stdout, 'isatty', lambda: True, raising=False)

    monkeypatch.setenv('PAGER', 'cat -n')
    assert search.pager_command() == [which('cat'), '-n']

    monkeypatch.setenv('PAGER', 'no-such-pager')
    assert search.pager_command() is None

    monkeypatch.delenv('PAGER')
    monkeypatch.setenv('TERM', 'dumb')
    assert search.pager_command() is None
