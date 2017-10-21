# -*- coding: utf-8 -*-
#
# Centralized Settings and Configuration
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
#
# Settings are only valid if at least one server configuration was found,
# whether it be from the database or a configuration file.
#
# A Sample configuration (newsreap.yaml) might look like this:
#   global:
#       base_dir: ~/.config/newsreap/
#       work_dir: <base_dir>/var
#
#   servers:
#     - username: lead2gold
#       password: abc123
#       host: awesome.nntp.server.com
#       port: 563
#       secure: True
#       verify_cert: False
#       compress: True
#       join_group: False
#       use_body: False
#       use_head: True
#       enabled: True
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
#     engine: sqlite:////absolute/path/to/mydatabase.db

# Possible database engines taken from:
#      - http://docs.sqlalchemy.org/en/rel_1_0/core/engines.html
#
#
#   ** PostgreSQL:
#       Default
#           postgresql://user:pass@localhost/mydatabase
#
#       Psycopg2
#           postgresql+psycopg2://user:pass@localhost/mydatabase
#
#       pg8000
#           postgresql+pg8000://user:pass@localhost/mydatabase
#
#   ** MySQL:
#       Default
#           mysql://user:pass@localhost/mydatabase
#
#       MySQL-Python
#           mysql+mysqldb://user:pass@localhost/mydatabase
#
#       MySQL-connector-python
#           mysql+mysqlconnector://user:pass@localhost/mydatabase
#
#       OurSQL
#           mysql+oursql://user:pass@localhost/mydatabase
#
#   ** Oracle:
#           oracle://user:pass@127.0.0.1:1521/sidname
#      (or):
#           oracle+cx_oracle://user:pass@tnsname
#
#   ** Microsoft SQL Server:
#        PyODBC
#           mssql+pyodbc://user:pass@mydsn
#
#        PymsSQL
#           mssql+pymssql://user:pass@hostname:port/dbname
#
#   ** SQLite:
#        Unix/Mac - 4 initial slashes in total
#           sqlite:////absolute/path/to/mydatabase.db
#
#        Windows
#           sqlite:///C:\\path\\to\\mydatabase.db
#
#        Windows alternative using raw string
#           sqlite:///C:\path\to\mydatabase.db
#
#
#   A ramdisk/tmpfs can greatly speed up newsprocessing; You'll want to set it
#   up using at least 2GB;  The following is a good example of how you can
#   create one:
#       sudo mkdir -p /media/ramdisk
#       sudo mount -t tmpfs -o rw,nodev,nosuid,noexec,nodiratime,size=2048m \
#                       tmpfs /media/ramdisk
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

import re
import sys
import yaml

from os import name as os_name
from os.path import join
from os.path import isfile
from os.path import dirname
from os.path import abspath
from os.path import expanduser
from yaml.scanner import ScannerError
from yaml.parser import ParserError
from operator import itemgetter
from copy import deepcopy

# Library path for global usage
NEWSREAP_ROOT = join(dirname(abspath(__file__)))

try:
    from newsreap.NNTPDatabase import NNTPDatabase

except ImportError:
    sys.path.insert(0, dirname(NEWSREAP_ROOT))
    from newsreap.NNTPDatabase import NNTPDatabase

from newsreap.objects.nntp.Server import Server
from newsreap.objects.nntp.Vsp import Vsp
from newsreap.NNTPIOStream import NNTP_DEFAULT_ENCODING
from newsreap.NNTPIOStream import NNTPIOStream
from newsreap.Utils import parse_bool
# Logging
import logging
from newsreap.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)

# NNTP End Of Line
NNTP_EOL = '\r\n'

# NNTP End Of Data
NNTP_EOD = '.\r\n'

# Root path
if os_name == 'nt':
    ROOT = 'C:\\'
else:
    ROOT = '/'

# The Configuration Directory
DEFAULT_BASE_DIR = join(expanduser('~'), '.config', 'newsreap')

# Default temporary directory to use if none are specified
DEFAULT_TMP_DIR = expanduser(join('~', '.config', 'newsreap', 'var', 'tmp'))

# Possible Configuration Paths
DEFAULT_CONFIG_FILE_PATHS = (
    join(DEFAULT_BASE_DIR, 'config.yaml'),
    join(expanduser('~'), 'newsreap', 'config.yaml'),
    join(expanduser('~'), '.newsreap', 'config.yaml'),
    join(ROOT, 'etc', 'newsreap', 'config.yaml'),
    join(ROOT, 'etc', 'newsreap.yaml'),
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
    join(DEFAULT_BASE_DIR, 'plugins', 'cli'),
    join(expanduser('~'), '.newsreap', 'plugins', 'cli'),
)

# SQLite Database File Extension
SQLITE_DATABASE_EXTENSION = '.db'

# SQLite Database Details
SQLITE_DATABASE_ENGINE = 'sqlite:///%s%s' % (
    join(DEFAULT_BASE_DIR, 'newsreap.db'),
    SQLITE_DATABASE_EXTENSION,
)

# Default block size to read and write to and from memory for
# disk i/o
DEFAULT_BLOCK_SIZE = 8192

# A hidden entry stored which lets the configuration know if content read from
# the database can be safely saved over top of a matching/similar entry read
# from a configuration file.

# Processing Variables mapped to their defaults if not found.
# if None is specified, then the field is mandatory or we'll abort
DEFAULT_GLOBAL_VARIABLES = {
    # A base directory we can find everything in. It can be referenced
    # elsewhere in the configuration file by using %{base_dir}
    'base_dir': join('~', '.config', 'newsreap'),

    # work_dir is the location variable and temporary data will be
    # written and removed from. This is also the location all downloaded
    # data will be written too until it's processed.
    'work_dir': join('%{base_dir}', 'var', 'tmp'),
}

# Keyword used in configuration to host all of the defined NNTP Servers
GLOBAL_KEY = 'global'

# Server Variables mapped to their defaults if not found.
# if None is specified, then the field is mandatory or we'll abort
DEFAULT_SERVER_VARIABLES = {
    'username': None,
    'password': None,
    'host': None,
    'port': 119,
    'secure': None,
    'verify_cert': False,
    'iostream': NNTPIOStream.RFC3977_GZIP,
    'join_group': True,
    'use_head': True,
    'use_body': False,
    'priority': None,
    'enabled': True,

    # Defines The encoding thing such as the subject are encoded as
    'encoding': NNTP_DEFAULT_ENCODING,
}

# Keyword used in configuration to host all of the defined NNTP Servers
SERVER_LIST_KEY = 'servers'

# Default Database Variables
DEFAULT_DATABASE_VARIABLES = {
    'engine': None,
}

# Keyword used in configuration to host the defined Database
DATABASE_KEY = 'database'

# Processing Variables mapped to their defaults if not found.
# if None is specified, then the field is mandatory or we'll abort
DEFAULT_PROCESSING_VARIABLES = {
    # Default number of threads to spawn
    'threads': 5,
    # default header batchfile proccessing
    'header_batch_size': 25000,
    # ramdisk path (optional); leave blank if not set
    # A ramdisk greatly increases processing of certain content
    'ramdisk': None,
}

# Keyword used in configuration to host all of the defined NNTP Servers
PROCESSING_KEY = 'processing'

# A Parsed Configuration Shell
VALID_SETTINGS_ENTRY = {
    GLOBAL_KEY: DEFAULT_GLOBAL_VARIABLES,
    DATABASE_KEY: DEFAULT_DATABASE_VARIABLES,
    SERVER_LIST_KEY: [],
    PROCESSING_KEY: DEFAULT_PROCESSING_VARIABLES,
}


class NNTPSettings(NNTPDatabase):
    """
    An object that ties NNTP settings and statistics retrieved a
    database.

    This is also the class that should be used to interact with the
    database, handle database schema upgrades as well as basic
    things such as setting values.
    """

    def __init__(self, cfg_file=None):
        """
        Initializes the configuration based the configuration file specified.
        If no configuration file is specified, then the default paths are
        checked instead.

        cfg_file can be a list of potential config files to which are
        all loaded. The first config file found in the list provided is used.
        If you just pass in a string, that is presumed to be the configuration
        file that is read and loaded.
        """
        # Load super class
        super(NNTPSettings, self).__init__()

        # Base Directory
        self.base_dir = DEFAULT_BASE_DIR

        # Work Directory (for temporary file manipulation)
        self.work_dir = DEFAULT_TMP_DIR

        # The data read from the configuration file
        self.cfg_data = {}

        # A tuple list of the NNTP Server configuration found in
        # the configuration file or can be found in the database if
        # it's detected
        self.nntp_servers = []

        # Initializing Processing
        self.nntp_processing = DEFAULT_PROCESSING_VARIABLES.copy()

        # Initializing Database Config
        self.nntp_database = DEFAULT_DATABASE_VARIABLES.copy()

        # Is valid flag
        self._is_valid = False

        # Store first matched configuration file found
        if not cfg_file:
            # Load the first configuration file found in default path list
            cfg_file = next((path for path in DEFAULT_CONFIG_FILE_PATHS
                             if isfile(path)), None)

        if isinstance(cfg_file, basestring) and isfile(cfg_file):
            # A configuration path was specified
            self.read(cfg_file)

        else:
            # No entry
            self.cfg_file = None

    def is_valid(self):
        """
        Returns True if the information loaded is valid and false if it isn't
        """
        return self._is_valid

    def _read_yaml(self, cfg_file=None):
        """
        Loads the configuration file(s) passed in; if no configuration is
        passed in then the saved configuration is reloaded.

        The function returns True if the information loaded successfully
        and returns False if it doesn't (or is invalid).

        """

        if hasattr(self, '__mask_re'):
            # Destroy our cached mask
            del self.__mask_re

        # Default Configuration Starting Point
        _cfg_data = deepcopy(VALID_SETTINGS_ENTRY)

        if cfg_file is None:
            logger.debug('There was no YAML config file specified')
            return _cfg_data

        elif not isfile(cfg_file):
            logger.debug('Failed to locate YAML config file %s' % (cfg_file))
            return _cfg_data

        # Append our new data
        cfg_file = abspath(expanduser(cfg_file))
        try:
            cfg_data = yaml.load(file(cfg_file, 'r'))
            logger.debug('Successfully parsed YAML configuration from %s' % (
                cfg_file,
            ))

        except ParserError, e:
            logger.debug('%s' % (str(e)))
            logger.error('Failed to parse YAML configuration from %s' % (
                cfg_file,
            ))
            return _cfg_data

        except IOError, e:
            logger.debug('%s' % (str(e)))
            logger.error('Failed to access YAML configuration from %s' % (
                cfg_file,
            ))
            return _cfg_data

        except ScannerError, e:
            logger.debug('%s' % (str(e)))
            logger.error('Failed to interpret YAML configuration from %s' % (
                cfg_file,
            ))
            return _cfg_data

        if not isinstance(cfg_data, dict):
            # We failed
            logger.error('Invalid YAML configuration structure in %s' % (
                cfg_file,
            ))
            return _cfg_data

        # If we get here, we read something from the configuration file
        # apply it into our dictionary and return it.
        if SERVER_LIST_KEY not in cfg_data:
            logger.error('No [%s] entries defined in YAML configuration %s' % (
                SERVER_LIST_KEY,
                cfg_file,
            ))

        elif not isinstance(cfg_data[SERVER_LIST_KEY], (list, tuple)):
            if not isinstance(cfg_data[SERVER_LIST_KEY], dict):
                logger.error(
                    'Failed to interpret YAML server configuration from %s' % (
                        cfg_file,
                    )
                )

            else:
                # Treat as single server and convert to list attempting to be
                # user-friendly:
                cfg_data[SERVER_LIST_KEY] = (
                    cfg_data[SERVER_LIST_KEY],
                )

        if GLOBAL_KEY in cfg_data:
            _cfg_data[GLOBAL_KEY].update(cfg_data[GLOBAL_KEY])

        if DATABASE_KEY in cfg_data:
            _cfg_data[DATABASE_KEY].update(cfg_data[DATABASE_KEY])

        if PROCESSING_KEY in cfg_data:
            _cfg_data[PROCESSING_KEY].update(cfg_data[PROCESSING_KEY])

        if SERVER_LIST_KEY in cfg_data:
            for server in cfg_data[SERVER_LIST_KEY]:
                defaults = DEFAULT_SERVER_VARIABLES.copy()
                defaults.update(server)
                _cfg_data[SERVER_LIST_KEY].append(defaults)

        return _cfg_data

    def read(self, cfg_file=None):
        """
        Load our configuration from the files specified and then
        opens a database connection to see if there is any additional
        configuration worth applying from there.

        Each consecuative read() call stacks with the previous one unless
        reset is set to True.

        """
        # Close any database connection already established
        self.close()

        # Base Directory
        self.base_dir = None

        # Working Directory
        self.work_dir = None

        # reset config data read
        self.cfg_data = {}

        # empty server listing
        self.nntp_servers = []

        # reset processing dictionary
        self.nntp_processing = {}

        # reset database dictionary
        self.nntp_database = {}

        # is_valid flag reset
        self._is_valid = False

        # a list of processed nntp_servers
        _nntp_servers = {}

        # A Simple list of hostnames (keys) so we can handle duplicates
        _hosts = []

        # Track our default priority (it's incremented prior to assignment)
        _priority = 0

        logger.debug('Loading configuration file %s' % (cfg_file))

        if cfg_file is None:
            cfg_file = self.cfg_file

        elif isinstance(cfg_file, basestring) and isfile(cfg_file):
            self.cfg_file = cfg_file

        else:
            logger.warning('No configuration found in: %s' % (
                cfg_file,
            ))
            return False

        # read our data
        self.cfg_data = self._read_yaml(cfg_file=cfg_file)

        if GLOBAL_KEY not in self.cfg_data:
            # Default Global Configuration
            self.cfg_data[GLOBAL_KEY] = DEFAULT_GLOBAL_VARIABLES

        self.base_dir = self.cfg_data[GLOBAL_KEY].get(
            'base_dir',
            self.base_dir,
        )
        if self.base_dir is None:
            self.base_dir = DEFAULT_BASE_DIR

        if self.base_dir:
            self.base_dir = abspath(expanduser(self.base_dir))

        self.work_dir = self.cfg_data[GLOBAL_KEY].get(
            'work_dir',
            self.work_dir,
        )
        if self.work_dir is None:
            self.work_dir = DEFAULT_TMP_DIR

        # Prepare our work_dir
        self.work_dir = self.apply_mask(self.work_dir, is_dir=True)

        # Save configuration file path
        self.cfg_file = abspath(expanduser(cfg_file))
        logger.info('Loaded configuration file %s' % (self.cfg_file))

        # Strip out only the information we're not interested in
        self.nntp_processing.update(
            # dict comprehension (v2.7+)
            #   self.nntp_processing\
            #            .update(k: self.cfg_data[PROCESSING_KEY][k] \
            #                   for k in PROCESSING_VARIABLES.keys()\
            #                     if k in self.cfg_data[PROCESSING_KEY]})
            # v2.6 support
            dict((k, self.cfg_data[PROCESSING_KEY][k]) \
                 for k in DEFAULT_PROCESSING_VARIABLES.keys() \
                 if k in self.cfg_data[PROCESSING_KEY]),
        )

        # Strip out only the information we're not interested in
        self.nntp_database.update(
            # dict comprehension (v2.7+)
            #   self.nntp_database\
            #            .update(k: self.cfg_data[DATABASE_KEY][k] \
            #                   for k in DATABASE_VARIABLES.keys()\
            #                     if k in self.cfg_data[DATABASE_KEY]})
            # v2.6 support
            dict((k, self.cfg_data[DATABASE_KEY][k]) \
                 for k in DEFAULT_DATABASE_VARIABLES.keys() \
                 if k in self.cfg_data[DATABASE_KEY]),
        )

        # Parse our content
        _priority = 0
        # Load our Server configuration
        for s in self.cfg_data[SERVER_LIST_KEY]:
            # Defaults
            results = dict(DEFAULT_SERVER_VARIABLES)

            if 'compress' in s:
                # compress flag to over-ride iostream variable; this simplifies
                # the iostream variable for users
                if parse_bool(s['compress']):
                    s['iostream'] = NNTPIOStream.RFC3977_GZIP

                else:
                    s['iostream'] = NNTPIOStream.RFC3977

            # First we strip out only the inforation we're interested in
            results.update(
                # dict comprehension (v2.7+)
                #   results.update(k: s[k] for k in SERVER_VARIABLES.keys()\
                #                     if k in s})
                # v2.6 support
                dict((k, s[k]) for k in DEFAULT_SERVER_VARIABLES.keys() \
                     if k in s),
            )

            # Purge any entries from our list that are set to 'None'
            results = dict(
                # dict comprehension (v2.7+)
                # {k: v for k, v in results.iteritems() if v is None}
                # v2.6 support
                (k, v) for k, v in results.iteritems() if v is not None,
            )

            # our database (server) key is always the hostname
            try:
                _key = results['host'].strip().lower()
                if _key in _hosts:
                    # Duplicate
                    logger.warning(
                        'Duplicate server entry #%d (%s)' % (
                            _priority, _key) +
                        ' was ignored specified.',
                    )
                    continue

                # Update our host using the key for consistency
                results['host'] = _key

            except (ValueError, TypeError):
                # Bad entry
                logger.error(
                    'An invalid server "host" entry #%d ' % (
                        _priority) + '(bad `host=` identifier) ' +
                    'was specified.',
                )
                return False

            except KeyError:
                # Bad entry
                logger.error(
                    'An invalid server entry #%d ' % _priority +
                    '(missing `host=` keyword) was specified.',
                )
                return False

            try:
                _priority = int(results['priority'])

            except (KeyError):
                # Assign default priority; no warnings nessisary.
                _priority += 1

            except (ValueError, TypeError):
                # Assign default priority
                _priority += 1

                if results['priority']:
                    # Invalid Priority
                    logger.warning(
                        'An invalid priority (%s)' % results['priority'] +
                        ' was specified for host %s (using %d) in: %s' % (
                            _key,
                            _priority,
                            self.cfg_file,
                        )
                    )

            # Store our priority
            results['priority'] = _priority
            _nntp_servers[_key] = results

        # Eliminate entries marked as being disabled and treat entries missing
        # the enabled flag as being enabled. We also convert our _nntp_servers
        # dictionary back into a simple list
        self.nntp_servers = sorted(
            [v for v in _nntp_servers.itervalues()
             if v.get('enabled', True)],
            key=itemgetter("priority"),
        )

        logger.info("Loaded %d NNTP enabled server(s)" %
                    len(self.nntp_servers))

        # Is valid flag
        self._is_valid = len(self.nntp_servers) > 0

        if not self._is_valid:
            return False

        # Open up our database connection if one exists
        if self.open(engine=self.nntp_database['engine'], reset=None):
            self.session()

        return self._is_valid

    def save(self, cfg_file=None):
        """
        Save's configuration back to disk.

        """
        if cfg_file is None:
            cfg_file = self.cfg_file

        else:
            # acquire new configuration path
            cfg_file = abspath(expanduser(cfg_file))

        try:
            with open(cfg_file, 'w') as fp:
                yaml.dump(self.cfg_data, fp, default_flow_style=False)

        except IOError, e:
            logger.debug('%s' % (str(e)))
            logger.error('Failed to write configuration file %s' % (
                cfg_file,
            ))
            return False

        # Update central configuration
        self.cfg_file = cfg_file

        if hasattr(self, '__mask_re'):
            # Presumably something has changed if the user called save so we
            # destroy our cached mask to be safe
            del self.__mask_re

        return True

    def apply_mask(self, content, mask_map=None, is_dir=False, lazy=True):
        """
        when provided content, all predefined masks such as %{base_dir} are
        substituted with their actual represented value.

        If you provide your own mask_map (must be a dictionary of key ->
        value) then it will be added to the results

        if is_dir is specified, then abspath and expanduser is also called
        on the returned results.

        If lazy is set to true, then we use our cached values (if they exist)
        """

        if not lazy or not hasattr(self, '_mask_re'):
            # Define our translation map
            self._mask_map = {
                '%{base_dir}': self.base_dir,
                '%{work_dir}': self.work_dir,
                '%{ramdisk}': self.nntp_processing.get('ramdisk', ''),
            }

            # A checking script for handling entries not supported
            self._mask_check_re = re.compile('(%{([^}]r+)?}?)+')

            # we build our mask once for speed
            self._mask_re = re.compile(
                r'(' + '|'.join(self._mask_map.keys()) + r')',
                re.IGNORECASE,
            )

        # extra mask is created if an additional mask was provided
        extra_mask_re = None

        if mask_map:
            # a mask map was provided as input too
            extra_mask_re = re.compile(
                r'(' + '|'.join(mask_map.keys()) + r')',
                re.IGNORECASE,
            )

        # Apply our lookups
        content = self._mask_re.sub(
            lambda x: self._mask_map[x.group()], content)

        # We intentionally don't proces our extra matches yet until our
        # standard entries have been applied at least twice. The reason is
        # because we don't want someone over-riding the %{base_dir} path in
        # the extras causing the work_dir to reference it instead of the
        # one defined in our configuration

        recursion = 0
        _matches = self._mask_check_re.search(content)
        while _matches:
            # Apply our lookups
            content = self._mask_re.sub(
                lambda x: self._mask_map[x.group()], content)

            # If we get here, we still have left over keys that have
            # not been looked up
            if mask_map:
                content = extra_mask_re.sub(
                    lambda x: mask_map[x.group()], content)

            recursion += 1
            if recursion > 5:
                # Recursion Limit hit
                raise AttributeError(
                    "Configuration contains an infinit recursion loop.",
                )

            # Update our match search
            _matches = self._mask_check_re.search(content)

        if is_dir:
            # convert it into an absolute path
            return abspath(expanduser(content))

        # Don't mangle the content any more
        return content
