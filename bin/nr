#!/usr/bin/env python3
# NewsReap Command Line Interface (CLI) launcher
#
# Copyright (C) 2015-2026 Chris Caron <lead2gold@gmail.com>
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

"""
Runs the NewsReap CLI straight from a source checkout (no install needed).

Once installed, the same tool is available as the `nr` command.
"""
import sys
from os.path import abspath
from os.path import dirname

# Prefer the newsreap package sitting next to this script
sys.path.insert(0, dirname(dirname(abspath(__file__))))

from newsreap.cli import main  # noqa: E402

if __name__ == '__main__':
    main()
