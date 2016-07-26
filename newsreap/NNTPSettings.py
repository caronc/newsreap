# -*- encoding: utf-8 -*-
#
# Centralized Settings and Configuration
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
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# Settings are only valid if at least one server configuration was found,
# whether it be from the database or a configuration file.
#
# A Sample configuration (config.yaml) might look like this:
#   servers:
#     - username: lead2gold
#       password: abc123
#       host: awesome.nntp.server.com
#       port: 563
#       secure: True
#       compress: True
#       join_group: False
#       use_body: False
#       use_head: True
#       encoding: ISO-8859-1
#
#   # have you got another server you want to add as a backup?
#   # you can add as many more as you want here, just follow
#   # the proper yaml formating and indentation; keep adding
#   # as many as you want!
#
#     - username: l2g
#       password: 123abc
#       host: awesome.backup.nntp.server.com
#       port: 563
#       secure: True
#       compress: True
#       join_group: False
#       use_body: False
#       use_head: True
#       enabled: True
#       encoding: ISO-8859-1
#
#   posting:
#     - poster: 'omg <putin@it.in.russia.ru>'
#       max_article_size: 25M
#
#   processing:
#     - threads: 5
#     - header_batch_size: 5000
#     - ramdisk: /media/ramdisk
#
#   groups:
#     - name: 'alt.binaries.test'
#     - name: 'alt.binaries.mp3'
#     - name: 'alt.binaries.series.tv.divx.french'
#     - name: 'alt.binaries.multimedia'
#
#   database:
#     engine: sqlite:////absolute/path/to/foo.db

# Possible database engines taken from:
#      - http://docs.sqlalchemy.org/en/rel_1_0/core/engines.html
#
#
#   ** PostgreSQL:
#       Default
#           postgresql://scott:tiger@localhost/mydatabase
#
#       Psycopg2
#           postgresql+psycopg2://scott:tiger@localhost/mydatabase
#
#       pg8000
#           postgresql+pg8000://scott:tiger@localhost/mydatabase
#
#   ** MySQL:
#       Default
#           mysql://scott:tiger@localhost/foo
#
#       MySQL-Python
#           mysql+mysqldb://scott:tiger@localhost/foo
#
#       MySQL-connector-python
#           mysql+mysqlconnector://scott:tiger@localhost/foo
#
#       OurSQL
#           mysql+oursql://scott:tiger@localhost/foo
#
#   ** Oracle:
#           oracle://scott:tiger@127.0.0.1:1521/sidname
#      (or):
#           oracle+cx_oracle://scott:tiger@tnsname
#
#   ** Microsoft SQL Server:
#        PyODBC
#           mssql+pyodbc://scott:tiger@mydsn
#
#        PymsSQL
#           mssql+pymssql://scott:tiger@hostname:port/dbname
#
#   ** SQLite:
#        Unix/Mac - 4 initial slashes in total
#           sqlite:////absolute/path/to/foo.db
#
#        Windows
#           sqlite:///C:\\path\\to\\foo.db
#
#        Windows alternative using raw string
#           sqlite:///C:\path\to\foo.db
#
#
#   A ramdisk/tmpfs can greatly speed up newsprocessing; You'll want to set it
#   up using at least 2GB;  The following is a good example of how you can
#   create one:
#       sudo mkdir -p /media/ramdisk
#       sudo mount -t tmpfs -o rw,nodev,nosuid,noexec,nodiratime,size=2048m tmpfs /media/ramdisk
#
#   Don't forget to give write permissions to the account (or group) used by
#   the newsreap tool
#
#       sudo chown newsreap.newsreap /media/ramdisk
#
#   Now make sure to include this path in the ramdisk path.  If you don't have
#   a ramdisk; just leave this value empty

import gevent.monkey
gevent.monkey.patch_all()

import sys
import yaml

from os import name as os_name
from os.path import join
from os.path import isfile
from os.path import dirname
from os.path import abspath
from os.path import expanduser
from yaml.scanner import ScannerError
from operator import itemgetter
from collections import defaultdict

# Library path for global usage
NEWSREAP_ROOT = join(dirname(abspath(__file__)))

try:
    from newsreap.NNTPDatabase import NNTPDatabase

except ImportError:
    sys.path.insert(0, dirname(NEWSREAP_ROOT))
    from newsreap.NNTPDatabase import NNTPDatabase

from newsreap.objects.nntp.Server import Server
from newsreap.objects.nntp.Vsp import Vsp
from newsreap.NNTPIOStream import NNTPIOStream
from newsreap.NNTPIOStream import NNTP_DEFAULT_ENCODING

# Logging
import logging
from newsreap.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)

# Root path
if os_name == 'nt':
    ROOT = 'C:\\'
else:
    ROOT = '/'

# Configuration File Name
DEFAULT_CONFIG_FILE = 'config.yaml'

# The Configuration Directory
DEFAULT_CONFIG_PATH = join(expanduser('~'), '.config', 'newsreap')

# Possible Configuration Paths (containing the DEFAULT_CONFIG_FILE)
DEFAULT_CONFIG_FILE_PATHS = (
    join(DEFAULT_CONFIG_PATH, DEFAULT_CONFIG_FILE),
    join(ROOT, 'etc', DEFAULT_CONFIG_FILE),
    join(ROOT, 'etc', 'newsreap', DEFAULT_CONFIG_FILE),
    join(expanduser('~'), '.newsreap', DEFAULT_CONFIG_FILE),
    join(expanduser('~'), 'newsreap', DEFAULT_CONFIG_FILE),
)

# Plugin Keyword mapping:
CLI_PLUGINS_MAPPING = 'NEWSREAP_CLI_PLUGINS'

# Plugin Directory
DEFAULT_CLI_PLUGIN_DIRECTORIES = (
    # Main System
    join(NEWSREAP_ROOT, 'plugins', 'cli'),
    # Other directories we check
    join(ROOT, 'etc', 'plugins', 'cli'),
    join(ROOT, 'etc', 'newsreap', 'plugins', 'cli'),
    join(DEFAULT_CONFIG_PATH, 'plugins', 'cli'),
    join(expanduser('~'), 'newsreap', 'plugins', 'cli'),
    join(expanduser('~'), '.newsreap', 'plugins', 'cli'),
)

# SQLite Database Details
SQLITE_DATABASE_ENGINE = 'sqlite:///%s' % join(DEFAULT_CONFIG_PATH, 'core.db')

# Default block size to read and write to and from memory for
# disk i/o
DEFAULT_BLOCK_SIZE = 8192

# A hidden entry stored which lets the configuration know if content read from
# the database can be safely saved over top of a matching/similar entry read
# from a configuration file.
#
# It's primary usage is basicallly if the token `priority=` is found while
# reading a file, then that entry can not be over-ridden with different data
# fetched from the database. This is the flag that makes this possible.
#
# If set to True, then the data contained can be over-ridden by a matching
# entry found in the database.  If set to False, then the entry can not be
# over-ridden and the data is only updated by cfg file base changes only.
DB_ALLOW_OVERRIDE_KEY = '__db'

# Server Variables mapped to their defaults if not found.
# if None is specified, then the field is mandatory or we'll abort
SERVER_VARIABLES = {
    'username': None,
    'password': None,
    'host': None,
    'port': 119,
    'secure': None,
    'compress': True,
    'join_group': True,
    'use_head': True,
    'use_body': False,
    'priority': None,
    'enabled': True,
    # Defines The encoding thing such as the subject are encoded as
    'encoding': NNTP_DEFAULT_ENCODING,

    # Internal
    DB_ALLOW_OVERRIDE_KEY: False,
}

# Keyword used in configuration to host all of the defined NNTP Servers
SERVER_LIST_KEY = 'servers'

# Database Variables
DATABASE_VARIABLES = (
    'engine',
)

# Keyword used in configuration to host the defined Database
DATABASE_LIST_KEY = 'database'

# Processing Variables mapped to their defaults if not found.
# if None is specified, then the field is mandatory or we'll abort
PROCESSING_VARIABLES = {
    # Default number of threads to spawn
    'threads': 5,
    # default header batchfile proccessing
    'header_batch_size': 25000,
    # Default ramdisk to being just an empty string
    'ramdisk': '',

    # Internal
    DB_ALLOW_OVERRIDE_KEY: False,
}

# Keyword used in configuration to host all of the defined NNTP Servers
PROCESSING_LIST_KEY = 'processing'


class NNTPSettings(NNTPDatabase):
    """
    An object that ties NNTP settings and statistics retrieved a
    database.

    This is also the class that should be used to interact with the
    database, handle database schema upgrades as well as basic
    things such as setting values.
    """

    def __init__(self, cfg_file=None, cfg_path=None,
                 var_path=None, engine=None, reset=None):
        """
        Initializes the configuration based the configuration file specified.
        If no configuration file is specified, then the default paths are
        checked instead.

        cfg_file can be a list of potential config files to which are
        all loaded. The first config file found in the list provided is used.
        If you just pass in a string, that is presumed to be the configuration
        file that is read and loaded.

        cfg_path is where things like the databases are stored if you're
        using sqlite. It's also a spot where some meta information may be
        kept.

        var_path is the location variable and temporary data will be
        written and removed from. This is also the location all downloaded
        data will be written too until it's processed.

        engine is directly passed into SQLAlchemy's engine call during
        it's initialization. By default an sqlite database is created and
        stored in the cfg_path
        """
        # Configuration Path
        self.cfg_files = []

        # Configuration Path
        self.cfg_path = ''

        # The data read from the configuration file
        self.cfg_data = {}

        # A tuple list of the NNTP Server configuration found in
        # the configuration file or can be found in the database if
        # it's detected
        self.nntp_servers = []

        # Initializing Processing
        self.nntp_processing = dict(PROCESSING_VARIABLES)

        # If a database connection can be established, then this is
        # our connection to it.
        self.db_config = {}

        # Database SQLAlchemy Engine
        self.db = None

        # Store Configuration Path
        if cfg_path is None:
            cfg_path = DEFAULT_CONFIG_PATH
        self.cfg_path = cfg_path

        # Store Configuration File
        if cfg_file is None:
            cfg_file = DEFAULT_CONFIG_FILE_PATHS

        elif isinstance(cfg_file, basestring):
            cfg_file = [ cfg_file ]

        elif not isinstance(cfg_file, (list, tuple)):
            raise ValueError(
                "cfg_file must be a list/tuple of strings or just a string.",
            )

        # Load super class
        super(NNTPSettings, self).__init__(engine=engine, reset=reset)

        # Load the configuration file(s)
        self.read(cfg_file)

    def is_valid(self):
        """
        Returns True if the information loaded is valid and false if it isn't
        """
        return len(self.nntp_servers) > 0

    def _read_yaml(self, cfg_file=None):
        """
        Loads the configuration file(s) passed in; if no configuration is passed in
        then the saved configuration is reloaded.

        The function returns True if the information loaded successfully
        and returns False if it doesn't (or is invalid).

        this same information can be acquired from calling the
        is_valid() function.
        """

        if cfg_file is None:
            if len(self.cfg_files) == 0:
                return False
            cfg_file = self.cfg_files

        if isinstance(cfg_file, (list, tuple)):
            # load all entries individually and return is based
            # on at least one successful loaded file
            #return len([ cf for cf in cfg_file \
            #             if isfile(cf) == True and self._read_yaml(cf) ]) > 0
            for cf in cfg_file:
                if isfile(cf) == True:
                    self._read_yaml(cf)
            return len(self.cfg_data) > 0

        if not isfile(cfg_file):
            logger.debug('Failed to locate %s' % (
                cfg_file,
            ))
            return False

        # Append our new data
        cfg_file = abspath(expanduser(cfg_file))
        try:
            cfg_data = yaml.load(file(cfg_file, 'r'))
            logger.debug('Successfully parsed configuration from %s' % (
                cfg_file,
            ))

        except IOError, e:
            logger.debug('%s' % (str(e)))
            logger.error('Failed to access configuration from %s' % (
                cfg_file,
            ))
            return False

        except ScannerError, e:
            self.cfg_data = {}
            logger.debug('%s' % (str(e)))
            logger.error('Failed to parse configuration from %s' % (
                cfg_file,
            ))
            return False

        # track files that were successfully scanned (even if they contain
        # no configuration yet; this allows us to update these files later
        # and send a read() again to reload our config
        if cfg_file not in self.cfg_files:
            self.cfg_files.append(cfg_file)

        if cfg_data:
            # update our server configuration
            # This part is a little complicated because we need merge a list
            # of dictionaries together and the catch is our dictionaries
            # contain sub-dictionaries of their own.  Due to the nature of
            # python, an update() would only perform a shallow copy and we'd
            # over-write our sub dictionaries if we don't handle them first.
            if SERVER_LIST_KEY not in cfg_data:
                cfg_data[SERVER_LIST_KEY] = []

            elif not isinstance(cfg_data[SERVER_LIST_KEY], list):
                logger.error(
                    'Failed to interpret server configuration from %s' % (
                    cfg_file,
                ))
                return False

            if SERVER_LIST_KEY not in self.cfg_data:
                self.cfg_data[SERVER_LIST_KEY] = []

            # The following code merges the new server content against the
            # existing by it's host (hostname) identifier
            merged = defaultdict(dict)
            for l in (self.cfg_data[SERVER_LIST_KEY], cfg_data[SERVER_LIST_KEY]):
                for elem in l:
                    merged[elem['host']].update(elem)

            # Save our new merged changes over the old
            self.cfg_data[SERVER_LIST_KEY] = merged.values()

            # Now eliminate our new server list since we've already handled
            # it's merging
            del cfg_data[SERVER_LIST_KEY]

            # Now we can apply the remaining updates
            self.cfg_data.update(cfg_data)

        else:
            # No data was loaded from a file that was present
            logger.warning('No configuration found in: %s' % (
                cfg_file,
            ))

        # We finished processing content; return our results
        return len(self.cfg_data) > 0

    def _db_get_servers(self, engine=None):
        """
        Loads configuration based on the engine specified

        This function always returns a map of servers (they're key is the
        server hostname) that it successfully extracts from the database.
        If nothing can be found, then an empty dictionary is returned.

        See the SERVER_VARIABLES defined at the head of this file for details
        as to what will be included in this response.

        The dictionary might looks like this:
            'foo.bar.net': {
                'host': 'foo.bar.net',
                'port': 119,
                'secure': True,
                'username': 'foo',
                'password': 'bar',
                'compress': True,
                'join_group': True,
                'use_body': False,
                'use_head': True,
                'priority': 1,
                'enabled': True,
                'encoding': 'ISO-8859-1',
            },
            'host.no.two': {
               # .... and so forth
            }

        """
        # NNTP Servers (first one is Primary, then backups follow)
        _nntp_servers = {}

        # Open our connection to the database if possible
        if not self.open(engine=engine):
            logger.warning("Database not initialized or accessible.")
            return {}

        try:
            # Fetch our server listings ordered by priority
            # intentionally don't filter on the enabled column because
            # we want servers marked as disabled but matched up in the
            # flat file later to be ignored
            results = self._session\
                    .query(Server)\
                    .order_by(Server.priority.asc())\
                    .all()

            for entry in results:
                _key = entry.host.strip().lower()
                if _key not in _nntp_servers:
                    _nntp_servers[_key] = entry.dict()
                    # Database Entry; so it can be later over-ridden
                    _nntp_servers[_key][DB_ALLOW_OVERRIDE_KEY] = True

        except Exception, e:
            # We intentionally do not initialize the database yet
            # because things can change before the user gets around
            # to calling open() again.
            logger.debug("Database Connection exception: %s" % str(e))
            logger.warning("Database not initialized.")

        # We finished processing content; return our results
        return _nntp_servers

    def _db_get_processing(self, engine=None):
        """
        Loads configuration based on the engine specified

        This function always returns a map of server processing details
        If nothing can be found, then an empty dictionary is returned.

        See the PROCESS_VARIABLES defined at the head of this file for details
        as to what will be included in this response.

        The dictionary might looks like this:
            {
                'threads': 5,
                'header_batch_size': 5000,
            }

        """
        # NNTP Servers (first one is Primary, then backups follow)
        _nntp_processing = {}

        # Open our connection to the database if possible
        if not self.open(engine=engine):
            logger.warning("Database not initialized or accessible.")
            return {}

        try:
            # Fetch our processing details from the VSP
            results = self._session\
                    .query(Vsp)\
                    .filter(Vsp.group == PROCESSING_LIST_KEY)\
                    .order_by(Vsp.order.asc())\
                    .all()

            for entry in results:
                if entry.key in ('threads', 'header_batch_size'):
                    _nntp_processing[entry.key] = int(entry.value)

        except Exception, e:
            # We intentionally do not initialize the database yet
            # because things can change before the user gets around
            # to calling open() again.
            logger.debug("Database Connection exception: %s" % str(e))
            logger.warning("Database not initialized.")

        # We finished processing content; return our results
        return _nntp_processing

    def read(self, cfg_file=None, reset=None):
        """
        Load our configuration from the files specified and then
        opens a database connection to see if there is any additional
        configuration worth applying from there.

        Each consecuative read() call stacks with the previous one unless
        reset is set to True.

        """
        if reset is True:
            # Close any database connection already established
            self.close()

            # reset config data read
            self.cfg_data = {}
            # empty server listing
            self.nntp_servers = []
            # reset processing dictionary
            self.nntp_processing = {}
            # reset default database config
            self.db_config = {}

        # a list of processed nntp_servers
        _nntp_servers = {}

        # our processing details (such as thread count, etc)
        _nntp_processing = dict(PROCESSING_VARIABLES)

        # A Simple list of hostnames (keys) so we can handle duplicates
        _hosts = []

        # Track our default priority (it's incremented prior to assignment)
        _priority = 0

        # Track our entry counts (used mostly for human readable logging)
        _entry_no = 0

        self._read_yaml(cfg_file=cfg_file)

        if SERVER_LIST_KEY not in self.cfg_data:
            # Create entry just so no other calling threads have to
            # check for it's existance
            self.cfg_data[SERVER_LIST_KEY] = []

        if PROCESSING_LIST_KEY not in self.cfg_data:
            # Create entry just so no other calling threads have to
            # check for it's existance
            self.cfg_data[PROCESSING_LIST_KEY] = {}

        if DATABASE_LIST_KEY in self.cfg_data:
            # Load our Database configuration
            try:
                #   dict comprehension (v2.7+)
                #    self.db_config = k: self.cfg_data[DATABASE_LIST_KEY][k] \
                #       for k in DATABASE_VARIABLES \
                #           if k in self.cfg_data[DATABASE_LIST_KEY]}
                # v2.6 support
                self.db_config = dict((k, self.cfg_data[DATABASE_LIST_KEY][k]) \
                     for k in DATABASE_VARIABLES \
                        if k in self.cfg_data[DATABASE_LIST_KEY])

            except TypeError:
                # we're dealing with properly formated yaml config however
                # the user did not follow the instructions as to what we
                # are expecting here.
                logger.error('Server configuration is invalid in: %s' % (
                    self.cfg_file,
                ))
                return False
        else:
            # Create entry just so no other calling threads have to
            # check for it's existance
            self.cfg_data[DATABASE_LIST_KEY] = {}

        # Now attempt to fetch any more information from the database if
        # present; we keep this data separate from our other servers
        # intentionally since we can only add them 'if' there is no over-ride
        # specified otherwise that tells us we can't
        _db_nntp_servers = self._db_get_servers(
            engine=self.db_config.get('engine'),
        )

        # Now we need to fetch our server side processing details
        _db_processing = self._db_get_processing(
            engine=self.db_config.get('engine'),
        )

        # TODO: Create a better way of using the database and files.
        #       Perhaps always use the database variables if present and
        #       fill the blanks with the flat file.  Create a
        #       read()/write()/save()/load() to which:
        #           read() fetches the configuration from the flat file.
        #           load() fetches the configuration from the database.
        #           write() writes content back to the configuration file.
        #           save() writes content back to the database.
        #
        #       We make need a new cli option called 'config' to handle
        #       this too.
        #
        #       As it is now; this Settings.py is way to complicated.
        #

        # First we strip out only the inforation we're interested in
        _nntp_processing.update(
            # dict comprehension (v2.7+)
            #   results.update(k: self.cfg_data[PROCESSING_LIST_KEY][k] \
            #                   for k in PROCESSING_VARIABLES.keys()\
            #                     if k in self.cfg_data[PROCESSING_LIST_KEY]})
            # v2.6 support
            dict((k, self.cfg_data[PROCESSING_LIST_KEY][k]) \
                 for k in PROCESSING_VARIABLES.keys() \
                 if k in  self.cfg_data[PROCESSING_LIST_KEY]),
        )

        # Store Processing Information
        self.nntp_processing = _nntp_processing

        # TODO: Store Password in blowfish type setting based on a key
        #       defined in the configuration file.  The same key is used to
        #       decrypt the content.

        # The idea behind this is you intentionally define your config
        # file without priorities defined so that they can later be loaded
        # into the database and used that way (they get loaded reguardless)
        if len(self.cfg_data[SERVER_LIST_KEY]) + len(_db_nntp_servers) == 0:
            # We don't have enough configuration to do much more
            return False

        # Load our Server configuration
        for s in self.cfg_data[SERVER_LIST_KEY]:
            # Each iteration bumps the count to avoid conflicts
            _entry_no += 1

            # Defaults
            results = dict(SERVER_VARIABLES)

            # First we strip out only the inforation we're interested in
            results.update(
                # dict comprehension (v2.7+)
                #   results.update(k: s[k] for k in SERVER_VARIABLES.keys()\
                #                     if k in s})
                # v2.6 support
                dict((k, s[k]) for k in SERVER_VARIABLES.keys() \
                     if k in s),
            )

            # Purge any entries from our list that are set to 'None'
            results = dict(
                # dict comprehension (v2.7+)
                # {k: v for k, v in results.iteritems() if v is None}
                # v2.6 support
                (k, v) for k, v in results.iteritems() if v is not None,
            )

            # our key is always the host(name)
            try:
                _key = results['host'].strip().lower()
                if _key in _hosts:
                    # Duplicate
                    logger.warning(
                        'Duplicate server entry #%d (%s)' % (
                            _entry_no, _key) +
                        ' was ignored specified.',
                    )
                    continue

            except (ValueError, TypeError):
                # Bad entry
                logger.error(
                    'An invalid server "host" entry #%d ' % (
                        _entry_no ) + '(bad `host=` identifier) ' +\
                    'was specified.',
                )
                return False

            except KeyError:
                # Bad entry
                logger.error(
                    'An invalid server entry #%d ' % _entry_no +\
                    '(missing `host=` keyword) was specified.',
                )
                return False

            # Files trump database entry only if the priority keyword
            # was defined. First we initialize our priority to it's
            # highest value. If no priority is defined in the file
            # then the database one trumps
            if 'priority' in results:
                # the priority was defined, we use this no mater what!
                try:
                    _priority = int(results['priority'])

                except (ValueError, TypeError):
                    # Invalid Priority
                    logger.error(
                        'An invalid priority (%s)' % results['priority'] +\
                        ' was specified for host %s in: %s' % (
                        _key,
                        self.cfg_file,
                    ))
                    return False

                # The 'priority' keyword makes this entry locked from
                # being updated by similar matches found in the database
                results[DB_ALLOW_OVERRIDE_KEY] = False

            elif _key in _nntp_servers:
                # already exists in the database
                logger.debug(
                    'Skipping server entry %d (%s)' % (
                        _entry_no, _key,
                    ) + ' as it was already found in the database.',
                )
                continue

            else:
                # We use our last priority detected + 1
                _priority += 1

                # We additionally create a tag that lets database
                # matching database entries that they can safely
                # over-write the contents defined here
                results[DB_ALLOW_OVERRIDE_KEY] = True

            # Ensure our priority is set
            results['priority'] = _priority
            _nntp_servers[_key] = results

            # For Duplicate Tracking we want to store our key we
            # just saved
            _hosts.append(_key)

        # Now we need to merge our Database results with our files
        # but 'ONLY' if the DB_ALLOW_OVERRIDE_KEY is set to True for
        # the entry.
        for k in _db_nntp_servers.keys():
            if k in _nntp_servers:
                if _nntp_servers[k][DB_ALLOW_OVERRIDE_KEY] == True:
                    # Store our database Value
                    _nntp_servers[k] = _db_nntp_servers[k]
            else:
                # Store our database value
                _nntp_servers[k] = _db_nntp_servers[k]

        # Eliminate entries marked as being disabled and treat
        # entries missing the enabled flag as being enabled.
        # We also convert our _nntp_servers dictionary back into
        # a simple list
        self.nntp_servers = sorted(
            [ v for v in _nntp_servers.itervalues() \
             if v.get('enabled', True) ],
            key=itemgetter("priority"),
        )

        logger.info("Loaded %d NNTP enabled server(s)" % \
                    len(self.nntp_servers))
        return True

    def open(self, engine=None, reset=None):
        """
        Opens the database configuration and then proceeds to apply other
        settings read from the configuration file
        """
        result = super(NNTPSettings, self).open(engine=engine, reset=reset)
        if result is None:
            return result

        # If we reach here, we can continue to set things up
        if reset is not None and len(self.nntp_servers):
            logger.debug('Applying %d servers to database.' % (
                len(self.nntp_servers)
            ))
            for server in self.nntp_servers:
                if server['compress']:
                    iostream = NNTPIOStream.RFC3977_GZIP
                else:
                    iostream = NNTPIOStream.RFC3977

                if not self._session.query(Server)\
                    .filter(Server.host == server['host'])\
                    .filter(Server.port == server['port'])\
                    .update({
                        Server.username: server['username'],
                        Server.password: server['password'],
                        Server.secure: server['secure'],
                        Server.iostream: iostream,
                        Server.join_group: server['join_group'],
                        Server.use_head: server['use_head'],
                        Server.use_body: server['use_body'],
                        Server.priority: server['priority'],
                        Server.encoding: server['encoding'],
                    }):

                    self._session.add(
                        Server(
                            host=server['host'],
                            port=server['port'],
                            username=server['username'],
                            password=server['password'],
                            secure=server['secure'],
                            iostream=iostream,
                            join_group=server['join_group'],
                            use_head=server['use_head'],
                            use_body=server['use_body'],
                            priority=server['priority'],
                            encoding=server['encoding'],
                            enabled=server['enabled'],
                        ),
                    )

                self._session.commit()

        # Return Okay
        return True

    def save(self, cfg_file):
        """
        Save's configuration back to disk.
        """

        # TODO
