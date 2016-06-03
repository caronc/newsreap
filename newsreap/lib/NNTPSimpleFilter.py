# -*- coding: utf-8 -*-
#
# Defines a simple (and generic) filter that can be used when
# retrieving results
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

"""
Filters work by catagorizing the group (by regular expression) you want
to match against before applying the filter. followed by a list of the
filter you want to apply to this match. each individual filter is defined
by another dictionary allowing filters to be defined by their fields.

Each filter entries defined fields represent 'AND' which means the more
filters you specify, the more that have to match for the filter to take
action.

If you need to do an OR style, just create more filters that handle all
possible constraints

"""

import re
from NNTPFilterBase import FilterDirectives
from NNTPFilterBase import NNTPFilterBase

class NNTPSimpleFilter(NNTPFilterBase):
    """
    A simple filter that extends the FilterBase. This filter applies
    some basic filters we can search against.
    """

    def __init__(self):
        """
        Initialize Simple Filter
        """
        super(NNTPSimpleFilter, self).__init__()

        self._blacklist = {
            '.*': [{
                FilterDirectives.DESCRIPTION: "Dangerous file types.",
                FilterDirectives.SUBJECT_MATCHES_REGEX: re.compile(
                    '.*\.(exe|sc(f|r)|vb(e|s)?|pif|application|gadget|ms(c|h|i|p)|hta' +\
                    '|jar|bat|c(md|om|pl)|jse?|ws(f|c|h)?|lnk|inf|reg|' +\
                    'psc?(1|2)(xml)?|ms(h(1|2)(xml)?|1|2|))[\s"\'].*',
                re.IGNORECASE),
            }],
        }

        self._scorelist = {
            'alt.bin.*' : [{
                FilterDirectives.DESCRIPTION: "Video Content",
                FilterDirectives.SCORE: 25,
                FilterDirectives.SUBJECT_MATCHES_REGEX: re.compile(
                    '.*\.(avi|m(p(4|eg|g)|kv|ov)|asf|ogg|iso|rm)(\.[0-9]{3})?[\s"\'].*',
                re.IGNORECASE),
            },{
                FilterDirectives.DESCRIPTION: "Image Content",
                FilterDirectives.SCORE: 15,
                FilterDirectives.SUBJECT_MATCHES_REGEX: re.compile(
                    '.*\.(jpe?g|png|bmp|gif)[\s"\'].*',
                re.IGNORECASE),
            },{
                FilterDirectives.DESCRIPTION: "Compressed Content",
                FilterDirectives.SCORE: 25,
                FilterDirectives.SUBJECT_MATCHES_REGEX: re.compile(
                    '.*\.(r(ar|[0-9]{2})|7z|z(ip|[0-9]{2})|tgz|tar\.gz)(\.[0-9]{3})?[\s"\'].*',
                re.IGNORECASE),
            },{
                FilterDirectives.DESCRIPTION: "Recovery Content",
                FilterDirectives.SCORE: 10,
                FilterDirectives.SUBJECT_MATCHES_REGEX: re.compile(
                    '.*\.(par2?)[\s"\'].*',
                re.IGNORECASE),
            }],
        }
