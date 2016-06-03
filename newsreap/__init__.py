# -*- coding: utf-8 -*-
#
# The Base Library used to make imports easier for users
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
__title__ = 'NewsReap'
__version__ = '0.0.1'
__author__ = 'Chris Caron <lead2gold@gmail.com>'
__license__ = 'GPLv3'
__copyright__ = 'Copyright 2015 Chris Caron'

# Ensure Content is patched
import gevent.monkey
gevent.monkey.patch_all()

try:
    from lib.NNTPConnection import NNTPConnection

except ImportError:
    import sys
    from os.path import dirname
    from os.path import abspath
    sys.path.insert(0, dirname(abspath(__file__)))

    # Try importing again now
    from lib.NNTPConnection import NNTPConnection

from lib.NNTPManager import NNTPManager
from lib.NNTPSettings import NNTPSettings
