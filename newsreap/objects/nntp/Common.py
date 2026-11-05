# A common set of database queries
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

from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import ProgrammingError

from .Group import Group
from .GroupAlias import GroupAlias
from .GroupOption import GroupOption
from newsreap.NNTPGroup import NNTPGroup

# Logging
import logging
from newsreap.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)


def get_groups(session, lookup=None, watched=False):
    """
    See _get_groups(); None (with an error saying why) when the database
    isn't set up yet
    """
    try:
        return _get_groups(session, lookup=lookup, watched=watched)

    except (OperationalError, ProgrammingError):
        session.rollback()
        logger.error("The database isn't set up yet; run 'nr db init' first.")
        return None


def missing_groups(session, lookups):
    """
    Returns the names given (groups or aliases) that match no group, in
    the order given (each is warned about by get_groups())
    """
    return [name for name in lookups
            if str(name).strip() and not get_groups(session, name)]


def _get_groups(session, lookup=None, watched=False):
    """
    Returns a dictionary of groups indexed by their names and their value
    set to the database key in the Group table.

     - If watched is set to True, then the watched groups are automatically
       considered in the returned results.

     - If the lookup is an alias, it's looked up and the matched groups are
        returned.
     - If the lookup just matches a group; then that group object is returned.
     - If the lookup is an integer; then the group is looked up and fetched
     - If the lookup is a tuple or list, then all of the entries in the list
       are processed using the above criteria and results merged in the list
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
        # PEP8 E712 does not allow us to make a comparison to a boolean value
        # using the == instead of the keyword 'in'.  However SQLAlchemy
        # requires us to do just because that's how the amazing tool works.
        # so to get around the pep8 error, we'll just define a variable equal
        # to True and then we can compare to it
        pep8_e712 = True

        # Fetch our watch list
        results = dict(session.query(Group.name, Group.id)
                       .filter(Group.watch == pep8_e712).all())

    if isinstance(lookup, (str, int)):
        lookup = [lookup, ]

    elif lookup is None:
        # Return whatever results we have
        return results

    elif not isinstance(lookup, (dict, tuple, list)):
        # Not supported; return what we have
        logger.warning(
            "An unsupported group/alias lookup type (%s) was specified." %
            type(lookup),
        )
        return results

    for group_id in lookup:
        if isinstance(group_id, NNTPGroup):
            # We already know we're dealing with an NNTPGroup at this point
            groups = dict(session.query(Group.name, Group.id)
                          .filter(Group.name == str(group_id)).all())
            if not groups:
                logger.warning(
                    "The group '%s' does not exist." % str(group_id),
                )

        elif isinstance(group_id, str):
            _id = group_id.lower().strip()
            if not _id:
                continue

            groups = dict(session.query(Group.name, Group.id)
                          .filter(Group.name == _id).all())

            if not groups:
                # No problem; let us use the alias too
                groups = dict(session.query(Group.name, Group.id)
                              .join(GroupAlias)
                              .filter(GroupAlias.name == _id).all())

                if not groups:
                    # Try one last time using normalization
                    _group = NNTPGroup.normalize(group_id)

                    if _group == _id:
                        # We normalized to the same thing the above check did
                        logger.warning(
                            "The group/alias '%s' does not exist." % group_id,
                        )
                        continue

                    # if we reach here, we normalized to something different
                    groups = dict(session.query(Group.name, Group.id)
                                  .filter(Group.name == _group).all())
                    if not groups:
                        # We're definitely out of options at htis point
                        logger.warning(
                            "The group/alias '%s' does not exist." % group_id,
                        )
                        continue

        elif isinstance(group_id, int) and group_id > 0:
            # A id was specified; fetch it
            groups = dict(session.query(Group.name, Group.id)
                          .filter(Group.id == group_id).all())

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
                results.update(groups)

    return results


def get_group_options(session, group_ids):
    """
    Returns {group id: {'content': ..., 'nzb': ...}} for the groups given
    that have options set ('nr group watch --content'); a value of None
    means the group follows the configuration file
    """
    ids = list(group_ids)
    if not session or not ids:
        return {}

    try:
        return {
            option.group_id: {'content': option.content, 'nzb': option.nzb}
            for option in session.query(GroupOption)
            .filter(GroupOption.group_id.in_(ids))}

    except (OperationalError, ProgrammingError):
        # A database made before group options existed (they're added the
        # first time one is set)
        session.rollback()
        return {}


def set_group_options(session, group_ids, **options):
    """
    Sets options (content=True, nzb=False, ...) on the groups given; an
    option given as None goes back to following the configuration file.
    Returns how many groups changed (not committed).
    """
    # A database made before group options existed gets the table now (on
    # our own connection; another would wait on what we haven't committed)
    GroupOption.__table__.create(bind=session.connection(), checkfirst=True)

    changed = 0
    for group_id in group_ids:
        option = session.get(GroupOption, group_id)
        if option is None:
            option = GroupOption(group_id=group_id)
            session.add(option)

        before = (option.content, option.nzb)
        for name, value in options.items():
            setattr(option, name, value)

        if (option.content, option.nzb) != before:
            changed += 1

    return changed
