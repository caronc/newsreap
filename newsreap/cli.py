# NewsReap Command Line Interface (CLI)
#
# Copyright (C) 2015-2016 Chris Caron <lead2gold@gmail.com>
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE

# This is the easiest way to interact with NewsReap from the command line
# It handles most basic functionality and offers users the ability to
# create their own plugins to enhance it's functionality further.
#
# Type nr --help for command help.
#
# Successfully executed commands always return a zero (0) to the command
# line, otherwise a non-zero value is returned which may or may not
# identify details on the problem that occurred.
#
# Create a starting configuration file (and the directories it uses):
#    nr --init
#
# Now update the configuration (~/.config/newsreap/config.yaml) to reflect
# your NNTP Setup
#

# To initialize your workable database safely you might do the following:
#
#   # Initialize our database (only needs to be ran the first time)
#   nr db init
#
#   # Poll the configured NNTP Servers in your config.yaml file for
#   # all of the usenet groups supported.
#   nr update groups
#
#   # You can list them all now by typing:
#   nr group list --all
#
#   # Aliases allow you to simplify group reference so you don't have to type
#   # the whole name out each time. The below creates an alias called
#   # 'a.b.test' and assciates it with the group alt.binaries.test.
#   nr alias add test alt.binaries.test
#
#   # You can list the aliases and what they're associated with by typing
#   nr alias list
#
#   # You can also associate more then one group with the same alias
#   nr alias add test alt.binaries.testing alt.binaries.test.files
#
#   # Index all the groups defined by our alias.
#   # the below only looks back untl Jan 1st, 2014
#   # Depending on how many articles are in the group, this can take a very
#   # long time. The -vv at least lets you know something is going on.
#
#   # consider setting a database like PostgreSQL or MySQL for speed
#   # alternatively, if you insist on using the default SQLite database, then
#   # consider setting up a RAM drive for faster processing of the table
#   # writes.
#   nr -vvv update search --date-from=2014 test
#
#   # Index a from/to range if you want.
#   nr -vvv update search --date-from=2013 --date-to=2014.02 test
#
#   # Newsreap also maintains a watch list which is groups you just always
#   # intend to index. It can be a conbination of aliases you set up, or
#   # just a few individual groups. The below adds our 'test' group to
#   # a watch list.
#   nr group watch test
#
#   # We can now update our search by typing this following:
#   nr -vvv update search --watched

#   # Index the entire group (the whole retention period you have to work with)
#   nr group index test
#
#   # Now that you've got content indexed, you can browse for things
#   # You must specify an alias/group as your first argument
#   # From there, you can specify your search arguments
#   nr search test "keyword or string 1" "keyword or string 2" "etc..."
#
#   # Getting to many hits?  Filter them; the below only shows entries
#   # that scored higher then 30
#   nr search test --score=30 "keyword or string 1" \
#                                   "keyword or string 2" "etc..."
#
#   # You can do ranges too
#   # The below only shows entries that have scored between -90 and 30
#   nr search test --score=-90-30 "keyword or string 1" \
#                                       "keyword or string 2" "etc..."
#
#   # Want to elminate crap from your database that you know is just
#   # taking up useless junk (and space, and thus speed because it's indexed):
#   # use the delete option; it takes all the same parameters and options the
#   # search function does (--score, etc).
#   nr delete "keyword or string 1" "etc"
#
#  If you setup you filters right, you can download content by group or
#  single files.  Just pay attention to the id on the left (in your search)

# This monkey patching must be done before anything else (including the
# threading module) is imported
import gevent.monkey
gevent.monkey.patch_all()

import click
from os.path import basename
from os.path import isdir
from os.path import isfile

# Path
from newsreap.NNTPSettings import CLI_PLUGINS_MAPPING

# Import our file based paths
from newsreap.NNTPSettings import DEFAULT_CLI_PLUGIN_DIRECTORIES
from newsreap.NNTPSettings import NNTPSettings
from newsreap.NNTPSettings import create_config
from newsreap.NNTPSettings import DEFAULT_CONFIG_FILE_PATHS
from newsreap.NNTPManager import NNTPManager
from newsreap.Utils import scan_pylib
from newsreap.Utils import load_pylib

# Logging
from newsreap.Logging import *
import logging
logger = logging.getLogger(NEWSREAP_CLI)


# General Options
@click.group(invoke_without_command=True, no_args_is_help=True)
@click.option('--config', '-c',
              help='Specify configuration file.')
@click.option('--verbose', '-v', count=True,
              help='Verbose mode.')
@click.option('--noprompt', '-y', is_flag=True,
              help='Assume `yes` to all prompts.')
@click.option('--statistics', is_flag=True,
              help='Show a summary of transfer statistics (speeds, '
                   'articles, retries) once the command finishes.')
@click.option('--init', is_flag=True,
              help='Create a starting configuration file (at --config if '
                   'given) along with the directories it uses.')
@click.pass_context
def cli(ctx, config, verbose, noprompt, statistics, init):
    ctx.obj['verbose'] = verbose

    # Add our handlers at the parent level
    add_handler(logging.getLogger(SQLALCHEMY_LOGGER))
    add_handler(logging.getLogger(NEWSREAP_LOGGER))

    # Handle Verbosity
    set_verbosity(verbose)

    ctx.obj['noprompt'] = noprompt

    if init:
        try:
            path = create_config(config)

        except FileExistsError as e:
            # Tell the user where the configuration we found lives
            logger.error(
                "A configuration file already exists at '%s'; nothing was "
                "changed." % e.args[0])

            if config is None:
                # It wasn't the file they asked for, so make it clear this
                # is the one NewsReap is reading today
                logger.error(
                    'NewsReap already uses that file; edit it, or use '
                    '--config to create one somewhere else.')
            ctx.exit(1)

        except OSError as e:
            logger.error('Could not create the configuration: %s' % e)
            ctx.exit(1)

        # Files outside of our usual locations have to be pointed to
        nr = 'nr' if path in DEFAULT_CONFIG_FILE_PATHS \
            else "nr --config '%s'" % path

        click.echo("Created '%s'." % path)
        click.echo('Next steps:')
        click.echo('  1. Edit it and fill in your server details')
        click.echo('  2. Prepare the database:     %s db init' % nr)
        click.echo('  3. Fetch the group listing:  %s update groups' % nr)
        ctx.exit(0)

    if ctx.invoked_subcommand is None:
        # Nothing to do
        click.echo(ctx.get_help())
        ctx.exit(0)

    if config is not None and not isfile(config):
        logger.error(
            "The YAML config file '%s' was not found." % config,
        )
        exit(1)

    # NNTPSettings() for storing and retrieving settings
    ctx.obj['NNTPSettings'] = NNTPSettings(cfg_file=config)

    if not ctx.obj['NNTPSettings'].is_valid():
        # our configuration was invalid
        logger.error("No valid config.yaml file was found.")
        exit(1)

    # NNTPManager() for interacting with all configured NNTP Servers
    ctx.obj['NNTPManager'] = NNTPManager(
        settings=ctx.obj['NNTPSettings'],
    )

    if statistics:
        # Show what our connections did once the command is done (even
        # if it ends early)
        ctx.call_on_close(lambda: show_statistics(ctx.obj['NNTPManager']))


def show_statistics(manager):
    """
    Prints a summary of the transfer statistics of all of our connections

    It goes to stderr so the command's own output can still be piped.
    """
    click.echo('', err=True)
    click.secho('Statistics', bold=True, err=True)
    click.echo('-' * 40, err=True)
    click.echo(manager.stats.summary(), err=True)

# Dynamically Build CLI List; This is done by iterating through
# plugin directories and looking for CLI_PLUGINS_MAPPING
# which is expected to be a dictionary containing the mapping of
# the cli group (the key) to the function prefixes defined.
#
# If we can load it we'll save it here
plugins = scan_pylib(paths=[d for d in DEFAULT_CLI_PLUGIN_DIRECTORIES \
                    if isdir(d) is True])

# Now we iterate over the keys
for k, v in plugins.items():
    for _pyfile in v:
        # Apply entry
        obj = load_pylib('_nrcli_%s' % k, _pyfile)
        if not hasattr(obj, CLI_PLUGINS_MAPPING):
            continue

        if isinstance(obj.NEWSREAP_CLI_PLUGINS, str):
            # 1-1 mapping of a function
            _click_func = getattr(obj, obj.NEWSREAP_CLI_PLUGINS, None)
            if callable(_click_func):
                cli.add_command(_click_func)
                break

        elif isinstance(obj.NEWSREAP_CLI_PLUGINS, dict):
            # parse format:
            # shorthand:function
            for sf, _meta in obj.NEWSREAP_CLI_PLUGINS.items():
                # A flag used to track whether at least one command was added
                # otherwise why bother store the entry.
                store = False

                # Default Action Description
                group_desc = None

                if isinstance(_meta, str):
                    fn_prefix = _meta

                elif isinstance(_meta, dict):
                    # Support Dictionaries; but a prefix 'MUST'
                    # be specified or we move on
                    # {
                    #    'prefix': 'function_prefix',
                    #    'desc': 'action description',
                    # }
                    fn_prefix = _meta.get('prefix', None)

                    # Get Description (if present)
                    group_desc = _meta.get('desc', None)

                if not fn_prefix:
                    # Ignore entry
                    logger.warning('Ignoring bad plugin %s' % (
                        basename(_pyfile),
                    ))
                    continue

                # If we find a function identical to the module
                # we are accessing; then we are no longer dealing with
                # a group; instead we're dealing with a command
                command = None
                for fn in dir(obj):
                    if command is None and fn == fn_prefix:
                        # No group; this is a command; save it in our
                        # group_func arg so it gets added using the logic
                        # below
                        _click_group_func = getattr(obj, fn)
                        # Toggle flag allowing it to be added
                        store = True
                        break

                    elif not fn.startswith('%s_' % fn_prefix):
                        continue

                    # Anything below here and we're dealing with a fn_prefix
                    # entry
                    elif command is None:
                        # we're dealing with a group
                        def _click_group_func(ctx):
                            pass

                        if group_desc is None:
                            # Save our group description to this group
                            group_desc = ""

                        # Store our doc string
                        _click_group_func.__doc__ = group_desc

                        # Apply our Decorators; the below is equivalent to
                        #       @cli.group(name=sf)
                        #       @click.pass_context
                        #       def _click_group_func(ctx):
                        #           pass
                        #
                        # We intententionally use the decorators this way so
                        # that we an apply our group_desc (if specified) from
                        # the plugin modules we detect and load.
                        _click_group_func = \
                                click.pass_context(_click_group_func)
                        _click_group_func = \
                                cli.group(name=sf)(_click_group_func)

                        # Set the flag and fall through
                        command = False

                    if fn_prefix:
                        # Get our fn_suffix
                        fn_suffix = fn[len(fn_prefix)+1:]

                    # Store our function
                    _click_func = getattr(obj, fn)
                    _click_group_func.add_command(_click_func)

                    # Flip the store flag
                    store = True

            if store:
                cli.add_command(_click_group_func)


def main():
    """
    Runs the NewsReap command line interface
    """
    cli(obj={})


if __name__ == '__main__':
    main()
