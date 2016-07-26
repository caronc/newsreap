# -*- coding: utf-8 -*-
#
# A common set of database queries
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

from newsreap.objects.nntp.Group import Group
from newsreap.objects.nntp.GroupAlias import GroupAlias

# Logging
import logging
from newsreap.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)


def get_groups(session, lookup=None, watched=False):
    """
    Returns a dictionary of groups indexed by their names and their value
    set to the database key in the Group table.

     - If watched is set to True, then the watched groups are automatically
       considered in the returned results.

     - If the lookup is an alias, it's looked up and the matched groups are
        returned.
     - If the lookup just matches a group; then that group object is returned.
     - If the lookup is an integer; then the group is looked up and fetched
     - If the lookup is a tuple or list, then all of the entries in the list are
       processed using the above criteria and results merged in the list
       returned.

       None is returned if there was a problem fetching the information.

       A sample response might look like this:
           {
                u'alt.binaries.test': 34,
                u'alt.binaries.another.group': 3234,
            }
    """

    if not session:
        return None

    # Return list
    results = None

    if watched:
        # Fetch our watch list
        results = dict(session.query(Group.name, Group.id)\
                    .filter(Group.watch==True).all())

    if isinstance(lookup, (basestring, int)):
        lookup = [lookup, ]

    elif lookup is None:
        # Return whatever results we have
        return results

    elif not isinstance(lookup, (dict, tuple, list)):
        # Not supported; return what we have
        logger.warning(
            "An unsupported group/alias lookup type (%s) was specified." % \
            type(lookup),
        )
        return results

    for group_id in lookup:
        if isinstance(group_id, basestring):
            _id = group_id.lower().strip()
            if not _id:
                continue

            groups = dict(session.query(Group.name, Group.id)\
                .filter(Group.name==_id).all())

            if not groups:
                # No problem; let us use the alias too
                groups = dict(session.query(Group.name, Group.id)\
                               .join(GroupAlias)\
                               .filter(GroupAlias.name==_id).all())

                if not groups:
                    logger.warning(
                        "The group/alias '%s' does not exist." % group_id,
                    )
                    continue

        elif isinstance(group_id, int) and group_id > 0:
            # A id was specified; fetch it
            groups = dict(session.query(Group.name, Group.id)\
                       .filter(Group.id==group_id).all())

            if not groups:
                logger.warning("The group id '%d' does not exist." % group_id)
                continue

        else:
            # Not supported - Ignored
            continue

        if groups:
            if results is None:
                # Quick and easy initialization on first match
                results = groups
            else:
                # append to our existing results
                results = dict(results.items() + groups.items())

    return results
