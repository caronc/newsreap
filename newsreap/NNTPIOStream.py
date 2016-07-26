# -*- coding: utf-8 -*-
#
# NNTP Server I/O Streams
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

# It might be a bit over-engineered to maintain the Compression as an
# Enumeration instead a True/False category.  But this allows us to
# easily support different formats in the future.
#
# incompatibilities between news servers can also be checked against
# the IO Stream and handled differently by breaking this into it's
# own object type.

# Define the default encoding to use on NNTP I/O Streams
NNTP_DEFAULT_ENCODING = 'ISO-8859-1'

class NNTPIOStream(object):
    """
    Defines the supported NNTP Input/Output Streams

    """
    # RFC-3977 Standard NNTP Stream - No compression at all
    RFC3977 = 'rfc3977'

    # GZip RFC-3977 (COMPRESS Keyword used)
    RFC3977_GZIP = 'gzip.rfc3977'


# For Error Handling we maintain a list of supported I/O Streams
NNTP_SUPPORTED_IO_STREAMS = (
    # The sequence here is important since it also plays a roll
    # in the enumeration stored in referenced databases potentially
    # used outside of this class. Always add new types to the end.
    NNTPIOStream.RFC3977,
    NNTPIOStream.RFC3977_GZIP,
)
