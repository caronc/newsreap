# -*- coding: utf-8 -*-
#
# An NNTPRequest Object used by the NNTPManagaer
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

import gevent.monkey
gevent.monkey.patch_all()

from newsreap.NNTPRequest import NNTPRequest

class NNTPConnectionRequest(NNTPRequest):
    """
    This is used as a direct wrapper to the NNTPConnection() class.

    If you know the functions you want to call specifically, you just
    pass them in as a list of actions into here.

    Hence, to get the article id 'ABCD', you'd use the NNTPConnection.get()
    function like : con.get('ABCD', '/path/to/download')

    This request would look like this:
        req = NNTPConnectionRequest([('get', ('ABCD', '/path/to/download'), {}),])

    The syntax is:
        [ ('function', ('arg1', 'arg2,' 'argN'), {'key':'val', 'key2':'val2'}), ]


    We pass in a list of tuples because we can execute more then one command if we
    want:
        # The following would download 3 separate articles
        req = NNTPConnectionRequest([
            ('get', ('ABCD', '/path/to/download'), {}),
            ('get', ('ABCE', '/path/to/download'), {}),
            ('get', ('ABCF', '/path/to/download'), {}),
        ])
    """

    def __init__(self, actions, *args, **kwargs):
        """
        Initializes a request object and the actions specified
        """
        super(NNTPConnectionRequest, self).__init__(*args, **kwargs)

        # Store our actions
        self.actions = actions


    def run(self, connection, *args, **kwargs):
        """
        Executes actions and returns response object

        """

        for action in self.actions:

            if self.is_set():
                # Early exit; we can't process a response that has already been
                # set.  This flag is usually set remotely if aborting
                return False

            _name = action[0]
            _func = getattr(connection, _name)

            # Handle Arguments
            try:
                _args = action[1]
                if not _args:
                    _args = list()

            except IndexError:
                _args = list()

            # Handle Keyword Arguments
            try:
                _kwargs = action[2]
                if not _kwargs:
                    _kwargs = dict()

            except IndexError:
                _kwargs = dict()

            self.append(_func(*_args, **_kwargs))

        # Set our completion flag; this flags any blocking
        # services waiting for us to complete to resume
        self.set()

        # Return that we've set content okay
        return True


    def __repr__(self):
        """
        Return an unambigious version of the object
        """
        return '<NNTPConnectionRequest action="%s" done=%s elapsed=%ss />' % (
            repr(self.actions),
            self.is_set(),
            self.elapsed(),
        )
