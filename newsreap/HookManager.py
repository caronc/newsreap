# -*- coding: utf-8 -*-
#
# HookManager allows the management of one or more hooks
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

from blist import sortedset
from os.path import isfile

from .Hook import Hook
from .Utils import parse_paths
from .Utils import scan_pylib
from .Utils import load_pylib

# Logging
import logging
from newsreap.Logging import NEWSREAP_HOOKS
logger = logging.getLogger(NEWSREAP_HOOKS)


class HookManager(object):
    """
    Hook Managers allow you to easily manage one or more hook.

    Hooks allow external users to define their own logic that they'd like to
    occur at certain times events occur.  It allows you to effectivly define
    a callback.

    """

    def __init__(self):
        """
        Initialize our object

        """

        # We maintain a sorted set of hooks.  This is so we can priortize
        # our calling efforts
        self.hooks = sortedset(key=lambda x: x.key())

    def add(self, names, paths='.', priority=1000):
        """
        Adds first module identified by the name found in the paths defined.

        name can also be an absolute path to a module too.  If more then
        one path is specified, then the first one matched is referenced

        The priority is only used if one isn't detected off of the hook
        decorator

        """
        # duplicates are ignored in a blist and therefore
        # we just capture the length of our list before
        # and after so that we can properly return a True/False
        # value
        _bcnt = len(self.hooks)

        if isinstance(names, Hook):
            # Add our hook
            self.hooks.add(names)
            return len(self.hooks) > _bcnt

        # Get all of the entries from what was specified
        names = parse_paths(names)
        paths = parse_paths(paths)
        modules = []
        for path in paths:
            result = scan_pylib(path)
            if result is not None:
                modules.append(result)

        # our default response if we can't get one loaded
        result = None

        for name in names:

            if isfile(name):
                _module = scan_pylib(name)
                if len(_module) != 1:
                    continue

                name = _module.keys()[0]
                if name in self:
                    # Duplicates are not allowed
                    continue

                # Attempt to load our found module
                result = load_pylib(name, next(iter(_module[name])))

            elif name in self:
                # Duplicates are not allowed
                continue

            else:
                # Find our module based on path(s) specified
                for module in modules:
                    if name in module:
                        result = load_pylib(name, next(iter(module[name])))

            if result is not None:
                # Add our hook
                self.hooks.add(
                    Hook(name=name, module=result, priority=priority),
                )
        return len(self.hooks) > _bcnt

    def reset(self):
        """
        Resets our object

        """
        self.hooks.clear()

    def call(self, function_name, *args, **kwargs):
        """
        Executes the specified function while passing in the same parameters
        you feed it here.

        This will execute all loaded hooks that have a matching function
        name. The called function will be provided the shared args and kwargs

        a list is returned containing all of the return values in the
        order to which they were returned.

        """
        # first we generate a list of all of our functions
        ordered_funcs = None
        for hook in self.hooks:
            if function_name in hook:
                if ordered_funcs is None:
                    ordered_funcs = hook[function_name]
                else:
                    ordered_funcs |= hook[function_name]

        # Our response
        # We sort on index zero (0) which will be our priority
        responses = sortedset(key=lambda x: x['key'])

        # We now have an ordered set of hooks to call; itereate over each and
        # execute it
        for func in ordered_funcs:
            priority = int(getattr(func, Hook.priority_id, Hook.priority))
            module = getattr(func, Hook.module_id, func.__name__)

            try:
                # Execute our function and return it into our
                # tuple which provides us the priority (used to sort
                # our response), our function name (which may or may
                # not be the same as the function call type) and
                # our result
                responses.add({
                    # Store our priority and module path for unambiguity
                    # This becomes our key
                    'key': '%.6d/%s' % (priority, module),

                    # Store our priority
                    'priority': priority,

                    # Store our module path:
                    'module': module,

                    # Store our result
                    'result': func(args, **kwargs),
                })

            except:
                logger.warning(
                    "Hook Exception calling {}.".format(module))

        return responses

    def __iter__(self):
        """
        Grants usage of the next()
        """
        # returns an iterator to our modules
        return iter(self.hooks)

    def __len__(self):
        """
        Returns the number of modules loaded
        """
        return len(self.hooks)

    def keys(self):
        """
        Returns the keys we've defined as hooks

        """
        return [x.name for x in self.hooks]

    def iterkeys(self):
        """
        Support iterators for our keys

        """
        return iter(self.keys())

    def __contains__(self, function_name):
        """
        Support 'in' keyword

        """
        return function_name in self.keys()

    def __getitem__(self, index):
        """
        Support accessing the hook directly if it exists

        """
        if isinstance(index, basestring):
            # Support indexing by string
            result = next((x for x in self.hooks if x.name == index), None)
            if result is None:
                raise IndexError('Hook index %s was not found' % index)
            return result

        return self.hooks[index]

    def __repr__(self):
        """
        Return an unambigious version of the object

        """

        return '<HookManager hooks=%d />' % len(self.hooks)
