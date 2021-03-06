# -*- coding: utf-8 -*-
#
# hook decorator used by Hook() and HookManager()
#
# Copyright (C) 2017 Chris Caron <lead2gold@gmail.com>
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

from os.path import splitext
from os.path import basename

from newsreap.Hook import Hook


def hook(name=None, priority=Hook.priority):
    """
    @hook decorator allows you to map functions you've defined to be called
    at specific times throughout the life of an action performed by newsreap.

    if you don't specify a name then the function name you wrap is used.

    The priority field allows you to set your function to be called before
    or after others that might also be configured to run for a specific
    type of hook.
        @hook(name="new_name", priority=100)
        def your_function(*args, **kwargs):
            ...

    The lower the priority, the further to the front of the line of execution
    it moves to. The default value is 1000
    """
    if callable(name):
        def callable_hook(func):
            # Acquire our potentially existing entries as we don't want to
            # replace them
            entries = getattr(func, Hook.hook_id, dict())

            # Store our new entry contents
            entries[func.__name__] = {
                'priority': Hook.priority,
            }

            # Save our entries back to the function
            setattr(func, Hook.hook_id, entries)

            # Storing meta information into our object allows us to reference
            # it later on by external wrappers such as the HookManager
            setattr(func, Hook.module_id, '{module}.{function}'.format(
                module=splitext(basename(__file__))[0],
                function=func.__name__,
            ))

            return func
        return callable_hook(name)

    else:
        def noncallable_hook(func):
            # Acquire our potentially existing entries as we don't want to
            # replace them
            entries = getattr(func, Hook.hook_id, dict())

            # Store our new entry contents
            entries[func.__name__ if name is None else name] = {
                'priority': priority,
            }

            # Save our entries back to the function
            setattr(func, Hook.hook_id, entries)

            # Storing meta information into our object allows us to reference
            # it later on by external wrappers such as the HookManager
            setattr(func, Hook.module_id, '{module}.{function}'.format(
                module=splitext(basename(__file__))[0],
                function=func.__name__,
            ))

            return func

        return noncallable_hook
