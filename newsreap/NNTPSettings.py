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
#       # Optional; see the config.yaml written by 'nr --init'
#       data_dir: ~/.local/share/newsreap/
#       cache_dir: ~/.cache/newsreap/
#
#   servers:
#     - username: lead2gold
#       password: abc123
#       host: awesome.nntp.server.com
#       port: 563
#       secure: True
#       verify_cert: True
#       compress: True
#       join_group: False
#       use_body: False
#       use_head: True
#       enabled: True
#       encoding: ISO-8859-1
#
#       # Optional: how many times a failed download is retried and when
#       # a connection is treated as dead (no data for stall_timeout
#       # seconds, or averaging under min_rate bytes per second for
#       # slow_window seconds)
#       retries: 3
#       stall_timeout: 60
#       min_rate: 1024
#       slow_window: 120
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
#       poster: 'omg <putin@it.in.russia.ru>'
#       subject: '"{{filename}}" yEnc ({{index}}/{{count}})'
#       max_article_size: 5M
#       max_archive_size: 25M
#
#   processing:
#     - connections: 4
#     - header_batch_size: 5000
#     - ramdisk: /media/ramdisk
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

from os import chmod
from os import environ
from shutil import copyfile
from os import listdir
from os import makedirs
from os import name as os_name
from os.path import isabs
from os.path import isdir
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

from newsreap.NNTPFilter import FILTERS_FILE
from newsreap.NNTPFilter import FILTERS_EXTENSIONS
from newsreap.NNTPFilter import FILTERS_FOLDER
from newsreap.NNTPFilter import NNTPFilter
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

# The Downloads directory line of ~/.config/user-dirs.dirs
USER_DOWNLOAD_RE = re.compile(
    r'^\s*XDG_DOWNLOAD_DIR\s*=\s*"(?P<path>[^"]+)"\s*$')

# Where each kind of file goes (see user_dir()): the XDG variable that
# names it, the folder used when it isn't set, and the Windows variable
USER_DIRS = {
    # Settings you write yourself (config.yaml, filters, plugins)
    'config': ('XDG_CONFIG_HOME', ('.config', ), 'APPDATA'),
    # What can't be fetched again (databases, kept posts, PGP keys)
    'data': ('XDG_DATA_HOME', ('.local', 'share'), 'APPDATA'),
    # What can be fetched again (header caches, downloads in progress)
    'cache': ('XDG_CACHE_HOME', ('.cache', ), 'LOCALAPPDATA'),
}


def user_dir(kind):
    """
    Returns where NewsReap keeps one kind of file ('config', 'data' or
    'cache') the way the system expects (the XDG Base Directory
    specification; see https://specifications.freedesktop.org/basedir-spec/):

    - Linux (and others): ~/.config/newsreap, ~/.local/share/newsreap and
      ~/.cache/newsreap, unless XDG_CONFIG_HOME, XDG_DATA_HOME or
      XDG_CACHE_HOME say otherwise
    - Windows: %APPDATA%\\newsreap (and %LOCALAPPDATA%\\newsreap\\cache)
    """
    variable, fallback, windows = USER_DIRS[kind]

    # A relative path is ignored (as the specification says)
    path = environ.get(variable, '')
    if isabs(path):
        return join(path, 'newsreap')

    if os_name == 'nt' and environ.get(windows):
        return join(environ[windows], 'newsreap',
                    *(('cache', ) if kind == 'cache' else ()))

    return join(expanduser('~'), *fallback, 'newsreap')


def user_download_dir():
    """
    Returns your Downloads directory: the one XDG_DOWNLOAD_DIR names in
    ~/.config/user-dirs.dirs (how Linux desktops keep it), otherwise
    ~/Downloads
    """
    home = expanduser('~')
    config_home = environ.get('XDG_CONFIG_HOME', '')
    path = join(config_home if isabs(config_home) else join(home, '.config'),
                'user-dirs.dirs')
    try:
        with open(path, encoding='utf-8') as fp:
            for line in fp:
                found = USER_DOWNLOAD_RE.match(line)
                if found:
                    value = found.group('path').replace('$HOME', home)
                    if isabs(value):
                        return value

    except OSError:
        # Not a desktop that keeps one
        pass

    return join(home, 'Downloads')


# The Configuration Directory
DEFAULT_BASE_DIR = user_dir('config')

# Where what can't be fetched again is kept (see user_dir())
DEFAULT_DATA_DIR = user_dir('data')

# Where what can be fetched again is kept (see user_dir())
DEFAULT_CACHE_DIR = user_dir('cache')

# Default temporary directory to use if none are specified
DEFAULT_TMP_DIR = join(DEFAULT_CACHE_DIR, 'tmp')

# Possible Configuration Paths
# (each place in turn, .yaml before .yml; the first is where 'nr --init'
# writes one)
DEFAULT_CONFIG_FILE_PATHS = tuple(
    path + extension for path in (
        join(DEFAULT_BASE_DIR, 'config'),
        join(expanduser('~'), 'newsreap', 'config'),
        join(expanduser('~'), '.newsreap', 'config'),
        join(ROOT, 'etc', 'newsreap', 'config'),
        join(ROOT, 'etc', 'newsreap'),
    ) for extension in ('.yaml', '.yml'))

# The starting configuration written by create_config()
CONFIG_TEMPLATE = join(NEWSREAP_ROOT, 'var', 'config.yaml')

# The lines of the template 'nr --init' fills in (the template itself stays
# a configuration that works as it is)
TEMPLATE_BASE_DIR = "   # base_dir: '~/newsreap'\n"
TEMPLATE_DOWNLOAD_DIR = "   # download_dir: '~/Downloads'\n"

# The example filters file create_config() puts in the filters directory
# (named so it isn't read)
FILTERS_TEMPLATE = join(NEWSREAP_ROOT, 'var', 'filters.yaml.template')
FILTERS_EXAMPLE = 'example.yaml.template'

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

# Characters not allowed in the filename of a group's cached database
GROUP_FILENAME_RE = re.compile(r'[^a-z0-9.+_-]', re.ASCII)

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
    # Keep everything in this one directory (%{base_dir}) instead of the
    # usual places (see user_dir()); not set by default
    'base_dir': None,

    # What can't be fetched again (%{data_dir}): the base_dir if one is
    # set, otherwise ~/.local/share/newsreap (see user_dir())
    'data_dir': None,

    # What can be fetched again (%{cache_dir}): <base_dir>/cache if a
    # base_dir is set, otherwise ~/.cache/newsreap (see user_dir())
    'cache_dir': None,

    # work_dir is the location variable and temporary data will be
    # written and removed from. This is also the location all downloaded
    # data will be written too until it's processed.
    'work_dir': join('%{cache_dir}', 'tmp'),
}

# Keyword used in configuration to host all of the defined NNTP Servers
GLOBAL_KEY = 'global'

# How many more times a command is tried (reconnecting first if needed) when
# the connection drops, stalls or slows to a crawl.  We give up after this.
NNTP_RETRIES = 3

# Seconds to wait before retrying; this doubles after each failed attempt
NNTP_RETRY_WAIT = 2.0

# A connection that sends us nothing at all for this many seconds while we
# wait on a response is treated as dead.  Set to 0 to wait forever.
NNTP_STALL_TIMEOUT = 60.0

# A response arriving slower than NNTP_MIN_TRANSFER_RATE (bytes per second)
# on average over NNTP_SLOW_RATE_WINDOW seconds is treated as dead too.
# - The window is long on purpose so a short slow down (someone streaming
#   video, a router throttling usenet traffic) doesn't trip it.
# - Set the rate to 0 to turn this check off.
NNTP_MIN_TRANSFER_RATE = 1024
NNTP_SLOW_RATE_WINDOW = 120.0

# Server Variables mapped to their defaults if not found.
# if None is specified, then the field is mandatory or we'll abort
DEFAULT_SERVER_VARIABLES = {
    'username': None,
    'password': None,
    'host': None,
    'port': 119,
    'secure': None,
    'verify_cert': True,
    # A file of the certificates to trust instead of the system's (ie: a
    # server's own self signed certificate)
    'ca_certs': None,
    'iostream': NNTPIOStream.RFC3977_GZIP,
    'join_group': True,
    'use_head': True,
    'use_body': False,
    'priority': None,
    'enabled': True,

    # How many connections this server allows us (the connections setting
    # under processing: when not set)
    'connections': None,

    # Defines The encoding thing such as the subject are encoded as
    'encoding': NNTP_DEFAULT_ENCODING,

    # How often we retry and when a connection is considered dead
    'retries': NNTP_RETRIES,
    'retry_wait': NNTP_RETRY_WAIT,
    'stall_timeout': NNTP_STALL_TIMEOUT,
    'min_rate': NNTP_MIN_TRANSFER_RATE,
    'slow_window': NNTP_SLOW_RATE_WINDOW,
}

# Keyword used in configuration to host all of the defined NNTP Servers
SERVER_LIST_KEY = 'servers'

# How many connections are made to a server at once when nothing says
# otherwise (safe with every provider we know of; most allow far more)
DEFAULT_CONNECTIONS = 4

# Default Database Variables
DEFAULT_DATABASE_VARIABLES = {
    'engine': None,
    # Where collections are indexed (and searched); an SQLite file in our
    # cache directory unless a database URL is given
    'index_engine': None,
}

# Keyword used in configuration to host the defined Database
DATABASE_KEY = 'database'

# Processing Variables mapped to their defaults if not found.
# if None is specified, then the field is mandatory or we'll abort
DEFAULT_PROCESSING_VARIABLES = {
    # How many connections are made to a server at once (each one is a
    # worker); a server can set its own (see connections()).  threads is
    # the name it used to have (still read).
    'connections': None,
    'threads': None,
    # default header batchfile proccessing
    'header_batch_size': 25000,
    # ramdisk path (optional); leave blank if not set
    # A ramdisk greatly increases processing of certain content
    'ramdisk': None,
    # A collection missing parts is settled (as complete as it will get)
    # once its groups have been scanned this many seconds past it
    'collection_settle_time': 1800,
    # An article posted this many days away from a collection's posting
    # time starts a new collection (a repost); 0 never splits them
    'repost_days': 3,
    # Article headers are removed from the group caches once indexed and
    # posted more than this many days ago; 0 keeps them all
    'raw_retention': 0,
    # The biggest article 'nr update archive' fetches and keeps (text posts
    # are almost always much smaller)
    'archive_max_size': '64KB',
    # The file our filters (scores, tags and what is dropped) are kept in;
    # filters.yaml next to the configuration file when not set
    'filters': None,
    # What 'nr update' does for every group that doesn't say otherwise
    # ('nr group watch --content'): keep what the small articles say, and
    # read the NZB files posted; both off unless turned on
    'content': False,
    'nzb': False,
    # Keep the PGP keys found in archived posts, and which posts each key
    # signed or was encrypted for (see NNTPKeyring); off unless turned on
    'pgp': False,
    # Where gpg keeps the secret keys posts are decrypted with (see
    # NNTPGnuPG); <data_dir>/pgp when not set
    'pgp_home': None,
    # How many times 'nr update' tries a post that couldn't be decrypted
    # again on its own (ie: its passphrase wasn't known yet); 0 leaves it to
    # 'nr pgp decrypt'
    'pgp_retries': 1,
}

# Keyword used in configuration to host all of the defined NNTP Servers
PROCESSING_KEY = 'processing'

# Processing Variables mapped to their defaults if not found.
# if None is specified, then the field is mandatory or we'll abort
DEFAULT_POSTING_VARIABLES = {
    # The maximum article size before splitting the content; most providers do
    # now allow you to exceed 760KB which is why it is the default.
    'max_article_size': '760KB',

    # The maximum archive size before splitting the content
    # Some archivers (such as rar, zip, 7z) can break up a large archive
    # into several smaller archived files

    # If this value is set to auto, then the size is automatically calculated
    # based on the total size of the content being prepared
    'max_archive_size': 'auto',

    # The default poster
    'poster': 'Newsreap <reaper@newsreap.io>',

    # The default subject
    'subject': '{{description}} (%Y-%m-%d)" - "{{filename}}" yEnc '\
               '({{index}}/{{count}})',

    # The password archives are protected with: None (or empty) for none,
    # 'auto' to make one up for each post, or the password itself
    'password': None,

    # How long the passwords we make up are; 0 makes none
    'password_length': 24,
}

POSTING_KEY = 'posting'

# What 'nr get' does with what it downloads (see NNTPDownloader)
DEFAULT_DOWNLOAD_VARIABLES = {
    # Where finished downloads go (each in a directory of its own); your
    # system's Downloads directory when not set (see download_dir())
    'download_dir': None,
    # Repair damaged files with their par2 files
    'repair': True,
    # Extract the archives (rar, 7z, zip) that were downloaded
    'extract': True,
    # Remove what was downloaded once it is all put together and placed
    'cleanup': True,
    # 'nr prune downloads' removes downloads that didn't finish once
    # nothing has touched them for this many days; 0 keeps them until you
    # remove them yourself
    'prune_downloads': 0,
    # 'nr prune work' removes files left in the work directory this many
    # days or more; 0 keeps them
    'prune_work': 1,
}

# Keyword used in configuration for downloading
DOWNLOAD_KEY = 'downloads'

# A Parsed Configuration Shell
VALID_SETTINGS_ENTRY = {
    GLOBAL_KEY: DEFAULT_GLOBAL_VARIABLES,
    DATABASE_KEY: DEFAULT_DATABASE_VARIABLES,
    SERVER_LIST_KEY: [],
    PROCESSING_KEY: DEFAULT_PROCESSING_VARIABLES,
    POSTING_KEY: DEFAULT_POSTING_VARIABLES,
    DOWNLOAD_KEY: DEFAULT_DOWNLOAD_VARIABLES,
}


def create_config(cfg_file=None):
    """
    Writes a starting configuration file and creates the directories it
    refers to.

    - cfg_file: where to write it (defaults to the first standard location)
    - In the standard location, what NewsReap keeps goes where the system
      expects it (see user_dir()).  Anywhere else, everything is kept in
      the same directory as the configuration file (its base_dir).
    - Nothing is ever overwritten or removed; FileExistsError is raised if
      the file (or, when no path was given, any standard configuration
      file) already exists.

    Returns the path written to.
    """
    if cfg_file:
        cfg_file = abspath(expanduser(cfg_file))
        if isfile(cfg_file):
            raise FileExistsError(cfg_file)

    else:
        # A new file here would quietly take over from one found further
        # down our search list, so we leave things alone if any exist
        existing = next(
            (path for path in DEFAULT_CONFIG_FILE_PATHS if isfile(path)), None)
        if existing:
            raise FileExistsError(existing)

        cfg_file = DEFAULT_CONFIG_FILE_PATHS[0]

    config_dir = dirname(cfg_file)

    # Write paths in our home directory as ~/ so they stay readable
    home = expanduser('~')

    def shown(path):
        if path == home or path.startswith(join(home, '')):
            return '~' + path[len(home):]
        return path

    if cfg_file == DEFAULT_CONFIG_FILE_PATHS[0]:
        # The usual place; everything else goes where the system expects
        # it too
        base_dir = None
        line = TEMPLATE_BASE_DIR

    else:
        # Somewhere of its own; keep everything together with it
        base_dir = config_dir
        line = "   base_dir: '%s'\n" % shown(base_dir)

    with open(CONFIG_TEMPLATE) as fp:
        content = fp.read().replace(TEMPLATE_BASE_DIR, line, 1).replace(
            TEMPLATE_DOWNLOAD_DIR,
            "   download_dir: '%s'\n" % shown(user_download_dir()), 1)

    makedirs(config_dir, exist_ok=True)

    # 'x' never replaces a file that showed up since we checked
    with open(cfg_file, 'x') as fp:
        fp.write(content)

    # The file holds passwords; only its owner may read it
    chmod(cfg_file, 0o600)

    # Prepare our directories (our data and the work area)
    makedirs(base_dir or DEFAULT_DATA_DIR, exist_ok=True)
    makedirs(join(join(base_dir, 'cache') if base_dir else
                  DEFAULT_CACHE_DIR, 'tmp'), exist_ok=True)

    # The filters directory, with an example of every setting a rule can
    # have (not read; only .yaml files are).  Not being able to is fine:
    # it is made when a rule is first saved.
    try:
        folder = join(config_dir, FILTERS_FOLDER)
        example = join(folder, FILTERS_EXAMPLE)
        makedirs(folder, exist_ok=True)
        if not isfile(example):
            copyfile(FILTERS_TEMPLATE, example)

    except OSError as e:
        logger.warning(
            'Could not prepare the filters directory %s (%s); carrying on '
            'without it.' % (folder, e))

    return cfg_file


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
        super().__init__()

        # Where our configuration file is (config.yaml, filters and plugins
        # live next to it)
        self.config_dir = DEFAULT_BASE_DIR

        # What can't be fetched again, and what can (see user_dir())
        self.data_dir = DEFAULT_DATA_DIR
        self.cache_dir = DEFAULT_CACHE_DIR

        # The one directory everything is kept in (see base_dir)
        self._base_dir = None

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
        self._resolve_connections()

        # Initializing Database Config
        self.nntp_database = DEFAULT_DATABASE_VARIABLES.copy()

        # Initializing Posting
        self.nntp_posting = DEFAULT_POSTING_VARIABLES.copy()

        # Initializing Downloading
        self.nntp_downloads = DEFAULT_DOWNLOAD_VARIABLES.copy()

        # Is valid flag
        self._is_valid = False

        # Store first matched configuration file found
        if not cfg_file:
            # Load the first configuration file found in default path list
            cfg_file = next((path for path in DEFAULT_CONFIG_FILE_PATHS
                             if isfile(path)), None)

        if isinstance(cfg_file, str) and isfile(cfg_file):
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

        if hasattr(self, '_mask_re'):
            # Forget our cached masks (the directories may change)
            del self._mask_re

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
            with open(cfg_file) as fp:
                cfg_data = yaml.safe_load(fp)
            logger.debug('Successfully parsed YAML configuration from %s' % (
                cfg_file,
            ))

        except ParserError as e:
            logger.debug('%s' % (str(e)))
            logger.error('Failed to parse YAML configuration from %s' % (
                cfg_file,
            ))
            return _cfg_data

        except OSError as e:
            logger.debug('%s' % (str(e)))
            logger.error('Failed to access YAML configuration from %s' % (
                cfg_file,
            ))
            return _cfg_data

        except (ScannerError, ParserError) as e:
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

        if POSTING_KEY in cfg_data:
            _cfg_data[POSTING_KEY].update(cfg_data[POSTING_KEY])

        if isinstance(cfg_data.get(DOWNLOAD_KEY), dict):
            _cfg_data[DOWNLOAD_KEY].update(cfg_data[DOWNLOAD_KEY])

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

        # The one directory everything is kept in (see base_dir)
        self._base_dir = None

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

        # reset posting dictionary
        self.nntp_posting = {}

        # reset downloading dictionary
        self.nntp_downloads = DEFAULT_DOWNLOAD_VARIABLES.copy()

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

        elif isinstance(cfg_file, str) and isfile(cfg_file):
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

        self._set_directories(cfg_file)

        self.work_dir = self.cfg_data[GLOBAL_KEY].get(
            'work_dir',
            self.work_dir,
        )
        if self.work_dir is None:
            self.work_dir = join(self.cache_dir, 'tmp')

        # Prepare our work_dir
        self.work_dir = self.apply_mask(self.work_dir, is_dir=True)

        # Save configuration file path
        self.cfg_file = abspath(expanduser(cfg_file))
        logger.info('Loaded configuration file %s' % (self.cfg_file))

        # Strip out only the information we're not interested in
        self.nntp_processing.update({
            k: self.cfg_data[PROCESSING_KEY][k]
            for k in DEFAULT_PROCESSING_VARIABLES
            if k in self.cfg_data[PROCESSING_KEY]})
        self._resolve_connections()

        self.nntp_database.update({
            k: self.cfg_data[DATABASE_KEY][k]
            for k in DEFAULT_DATABASE_VARIABLES
            if k in self.cfg_data[DATABASE_KEY]})

        self.nntp_posting.update({
            k: self.cfg_data[POSTING_KEY][k]
            for k in DEFAULT_POSTING_VARIABLES
            if k in self.cfg_data[POSTING_KEY]})

        self.nntp_downloads.update({
            k: self.cfg_data[DOWNLOAD_KEY][k]
            for k in DEFAULT_DOWNLOAD_VARIABLES
            if k in self.cfg_data.get(DOWNLOAD_KEY, {})})

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
            results.update({
                k: s[k] for k in DEFAULT_SERVER_VARIABLES if k in s})

            # Purge any entries from our list that are set to 'None'
            results = {k: v for k, v in results.items() if v is not None}

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
            [v for v in _nntp_servers.values()
             if v.get('enabled', True)],
            key=itemgetter("priority"),
        )

        logger.info("Loaded %d NNTP enabled server(s)" %
                    len(self.nntp_servers))

        # Is valid flag
        self._is_valid = len(self.nntp_servers) > 0

        if not self._is_valid:
            return False

        if isinstance(self.nntp_database['engine'], str):
            # Allow %{base_dir} and friends in our database location
            self.nntp_database['engine'] = \
                self.apply_mask(self.nntp_database['engine'])

        # Open up our database connection if one exists
        if self.open(engine=self.nntp_database['engine'], reset=None):
            self.session()

        return self._is_valid

    def _set_directories(self, cfg_file):
        """
        Works out where each kind of file goes from the global settings:

        - base_dir: everything in one directory (data in it, caches in its
          cache/ directory)
        - data_dir / cache_dir: each kind somewhere of its own
        - neither: the usual places for the system (see user_dir())
        """
        settings = self.cfg_data[GLOBAL_KEY]
        self.config_dir = dirname(abspath(expanduser(cfg_file)))

        # Worked out first (the others can refer to it)
        base_dir = None

        def folder(key, default):
            value = settings.get(key)
            if not isinstance(value, str) or not value.strip():
                return default

            # %{base_dir} and %{config_dir} can be used in the others
            value = value.strip()\
                .replace('%{config_dir}', self.config_dir)\
                .replace('%{base_dir}', base_dir or self.config_dir)
            return abspath(expanduser(value))

        base_dir = folder('base_dir', None)
        self._base_dir = base_dir
        self.data_dir = folder('data_dir', base_dir or DEFAULT_DATA_DIR)
        self.cache_dir = folder(
            'cache_dir', join(base_dir, 'cache') if base_dir
            else DEFAULT_CACHE_DIR)

    @property
    def base_dir(self):
        """
        The one directory everything is kept in when the base_dir setting
        is used; otherwise our data_dir
        """
        return self._base_dir or self.data_dir

    @base_dir.setter
    def base_dir(self, path):
        """
        Keeps everything in one directory: our data in it, our caches in
        its cache/ directory
        """
        self._base_dir = abspath(expanduser(path)) if path else None
        if self._base_dir:
            self.data_dir = self._base_dir
            self.cache_dir = join(self._base_dir, 'cache')

        if hasattr(self, '_mask_re'):
            # Our masks name the directories
            del self._mask_re

    def _resolve_connections(self):
        """
        Works out how many connections are made at once (the connections
        setting, or threads as it used to be called) and keeps both names
        set to it
        """
        value = self.nntp_processing.get('connections')
        if value is None:
            value = self.nntp_processing.get('threads')

        self.nntp_processing['connections'] = self.nntp_processing[
            'threads'] = self._count(value, DEFAULT_CONNECTIONS, 'connections')

    @staticmethod
    def _count(value, default, name):
        """
        Returns a setting that must be a whole number of 1 or more (the
        default when it isn't set, or isn't usable)
        """
        if value is None:
            return default

        try:
            value = int(value)
            if value >= 1:
                return value

        except (TypeError, ValueError):
            pass

        logger.warning("%s: '%s' isn't a number of 1 or more; using %d." % (
            name, value, default))
        return default

    def connections(self, server=None):
        """
        Returns how many connections may be made to a server at once: its
        own connections setting, otherwise the one under processing:
        (our first server when none is given)
        """
        if server is None:
            server = self.nntp_servers[0] if self.nntp_servers else {}

        default = self.nntp_processing['connections']
        return self._count(
            server.get('connections'), default, 'connections (%s)' % (
                server.get('host') or 'server'))

    def download_dir(self):
        """
        Returns where finished downloads go: the download_dir setting,
        otherwise your system's Downloads directory (XDG_DOWNLOAD_DIR in
        ~/.config/user-dirs.dirs on Linux), otherwise ~/Downloads
        """
        path = self.nntp_downloads.get('download_dir')
        if isinstance(path, str) and path.strip():
            return abspath(expanduser(self.apply_mask(path.strip())))

        return user_download_dir()

    def staging_dir(self):
        """
        Returns where downloads are put together before they are placed
        (each in a directory of its own); see NNTPDownloader
        """
        return join(self.cache_dir, 'downloads')

    def group_database(self, name):
        """
        Returns the path of the database a group's articles are cached in

        Group names come from the server, so anything that isn't safe in
        a filename (ie: a '/' or a leading dot) is replaced with '_'.
        """
        # Keep the characters group names normally use
        safe = GROUP_FILENAME_RE.sub('_', name.strip().lower()).lstrip('.')

        return join(
            self.cache_dir, 'search',
            '%s%s' % (safe or '_', SQLITE_DATABASE_EXTENSION))

    def index_database(self):
        """
        Returns where every collection found (across all groups) is kept:
        the index_engine database URL if one is set, otherwise the path of
        an SQLite file in our data directory (what it holds, such as the
        posts kept, may no longer be on any server)
        """
        engine = self.nntp_database.get('index_engine')
        if isinstance(engine, str) and engine.strip():
            return self.apply_mask(engine.strip())

        return join(self.data_dir, 'index%s' % SQLITE_DATABASE_EXTENSION)

    def pgp(self):
        """
        Returns True if the PGP keys found in archived posts are kept (the
        pgp setting; see NNTPKeyring)
        """
        return parse_bool(self.nntp_processing.get('pgp'), False)

    def content(self):
        """
        Returns True if 'nr update' keeps what the small articles of a group
        say without being asked (the content setting); a group can say
        otherwise (see 'nr group watch')
        """
        return parse_bool(self.nntp_processing.get('content'), False)

    def nzb(self):
        """
        Returns True if 'nr update' reads the NZB files posted to a group
        without being asked (the nzb setting); a group can say otherwise
        (see 'nr group watch')
        """
        return parse_bool(self.nntp_processing.get('nzb'), False)

    def pgp_home(self):
        """
        Returns where gpg keeps the secret keys posts are decrypted with
        (the pgp_home setting, ie: ~/.gnupg to share your own); <data_dir>/pgp
        when not set
        """
        path = self.nntp_processing.get('pgp_home')
        if isinstance(path, str) and path.strip():
            return abspath(expanduser(self.apply_mask(path.strip())))

        return join(self.data_dir, 'pgp')

    def pgp_retries(self):
        """
        Returns how many times 'nr update' tries a post that couldn't be
        decrypted again on its own (the pgp_retries setting)
        """
        value = self.nntp_processing.get('pgp_retries')
        try:
            return max(0, int(value))

        except (TypeError, ValueError):
            if value is not None:
                logger.warning(
                    "pgp_retries: '%s' isn't a number; using %d." % (
                        value, DEFAULT_PROCESSING_VARIABLES['pgp_retries']))

            return DEFAULT_PROCESSING_VARIABLES['pgp_retries']

    def gpg(self):
        """
        Returns the NNTPGnuPG posts are decrypted with; None if the pgp
        setting is off
        """
        if not self.pgp():
            return None

        from newsreap.NNTPGnuPG import NNTPGnuPG
        return NNTPGnuPG(self.pgp_home(), passphrases=join(
            self.data_dir, 'pgp', 'passphrases'))

    def filters_file(self):
        """
        Returns where our filters are kept (see NNTPFilter): the filters
        setting if one is set (a directory of .yaml files, or one file),
        otherwise the filters/ directory next to our configuration file.

        A filters.yaml (or filters.yml) file next to the configuration file
        is still used when the filters/ directory holds no filters files.
        """
        path = self.nntp_processing.get('filters')
        if isinstance(path, str) and path.strip():
            return abspath(expanduser(self.apply_mask(path.strip())))

        folder = join(self.config_dir, FILTERS_FOLDER)
        single = next((
            path for path in (
                join(self.config_dir, 'filters%s' % extension)
                for extension in FILTERS_EXTENSIONS)
            if isfile(path)), join(self.config_dir, FILTERS_FILE))
        try:
            files = isdir(folder) and any(
                name.lower().endswith(FILTERS_EXTENSIONS)
                for name in listdir(folder))

        except OSError:
            # Unreadable; NNTPFilter says so
            files = True

        if isfile(single) and not files:
            # The one file used before there was a directory of them
            return single

        return folder

    def filters(self, reload=False):
        """
        Returns our filters (an NNTPFilter); they are read once and kept

        Raises ValueError (saying which rule, and why) if the filters file
        has a rule that doesn't make sense.
        """
        if reload or getattr(self, '_filters', None) is None:
            path = self.filters_file()
            setting = self.nntp_processing.get('filters')
            if isinstance(setting, str) and setting.strip() and \
                    not isdir(path) and not isfile(path):
                # Asked for, but not there; said loudly, and we carry on
                logger.error(
                    "The filters setting names %s, which doesn't exist; no "
                    "filters are used until it does." % path)

            self._filters = NNTPFilter(path)

        return self._filters

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

        except OSError as e:
            logger.debug('%s' % (str(e)))
            logger.error('Failed to write configuration file %s' % (
                cfg_file,
            ))
            return False

        # Update central configuration
        self.cfg_file = cfg_file

        if hasattr(self, '_mask_re'):
            # Presumably something has changed if the user called save so we
            # destroy our cached mask to be safe
            del self._mask_re

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
                '%{config_dir}': self.config_dir,
                '%{data_dir}': self.data_dir,
                '%{cache_dir}': self.cache_dir,
                '%{work_dir}': self.work_dir,
                '%{ramdisk}': self.nntp_processing.get('ramdisk', ''),
            }

            # A checking script for handling entries not supported
            self._mask_check_re = re.compile('(%{([^}]r+)?}?)+', re.ASCII)

            # we build our mask once for speed
            self._mask_re = re.compile(
                r'(' + '|'.join(self._mask_map) + r')',
                re.IGNORECASE | re.ASCII,
            )

        # extra mask is created if an additional mask was provided
        extra_mask_re = None

        if mask_map:
            # a mask map was provided as input too
            extra_mask_re = re.compile(
                r'(' + '|'.join(mask_map) + r')',
                re.IGNORECASE | re.ASCII,
            )

        # Apply our lookups
        content = self._mask_re.sub(
            lambda x: self._mask_map[x.group().lower()], content)

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
                lambda x: self._mask_map[x.group().lower()], content)

            # If we get here, we still have left over keys that have
            # not been looked up
            if mask_map:
                # The extra masks are found whatever their case too
                extras = {key.lower(): value for key, value in mask_map.items()}
                content = extra_mask_re.sub(
                    lambda x: extras[x.group().lower()], content)

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
