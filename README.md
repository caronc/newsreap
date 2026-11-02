[![Tests](https://github.com/caronc/newsreap/actions/workflows/tests.yml/badge.svg?branch=master)](https://github.com/caronc/newsreap/actions/workflows/tests.yml)
[![Paypal](https://img.shields.io/badge/paypal-donate-green.svg)](https://www.paypal.com/cgi-bin/webscr?cmd=_s-xclick&hosted_button_id=MHANV39UZNQ5E)

## Introduction
NewsReap is a library written to wrap around the NNTP protocol. It simplifies
a lot of development. But it also comes with a standalone script that can allow
you interface with your Usenet providers.

NewsReap supports features such as:
* SSL Support
* Group Searching
* Multi-Provider support; basically prioritize your Usenet providers and those
  of higher priority will always be used first.
* Multithreaded (handles multiple connections simultaneously)
* Aliases allow you to not only group multiple Usenet groups into 1; you can use aliases to also simplify the reference to the group itself.
* Filters keep your database trim and ranked: drop spam before it is ever
  stored (or purge it later, with a dry run first), and score and tag what
  you care about (`nr filter --help`).

Some under the hood stuff that developers should appreciate about NewsReap are:
* It suports character sets and does it's best to allow you to work with unicode characters
* It's backend is based on SQLAlchemy making it really easy to adapt to your own projects.
* It's front-end CLI is built using Click which allows anyone to extend it to their likings.

__Note:__ This is a work in progress; a lot of what is documented here is what the final solution will be.  But it is definitely not there yet!  The backend is in good shape, but the CLI tool is what needs to be worked on next.

## Installation
NewsReap needs a __config.yaml__ file; it looks for one in the following places (in order):

1. ~/.config/newsreap/config.yaml
2. ~/newsreap/config.yaml
3. ~/.newsreap/config.yaml
4. /etc/newsreap/config.yaml
5. /etc/newsreap.yaml

The easiest way to get started is to let NewsReap write one for you:
```bash
# Writes ~/.config/newsreap/config.yaml and creates the directories it uses
nr --init

# Or keep everything somewhere else
nr --config ~/usenet/config.yaml --init
```

`--init` never overwrites or removes anything; it stops if a configuration file already exists. Fill in your server details and you can start interfacing with your NNTP Provider.

NewsReap requires Python 3.13 or newer. Install it (and all of its dependencies) like so:
```bash
# Browse to the directory you installed newsreap into
# Then install it; this also gives you the `nr` command
pip install .
```

You can also run the tool straight from the source directory without installing it:
```bash
pip install -r requirements.txt
./bin/nr --help
```

See [INSTALL.md](INSTALL.md) for the optional archive tools (7-Zip, rar and par2).

## Downloading
If you know the article-id/Message-ID of the item in question, the following
will retrieve it for you and save it based on your downloading configuration
defined in your configuration file.

You can use the 'search' mode to get a listing of content and their associated
Message-ID (first column).

```bash
# Fetch Message-ID aajk2jb from alt.binaries.test
nr get --group=alt.binaries.test aajk2jb

# If your usenet server doesn't require the `join_group` option, then
# you don't need to specify the group prior to the Message-ID. Hence this
# would work on these servers.
nr get aajk2jb

# You can specify as many articles as you like too to fetch them all
nr get aajk2jb aajkdb aak2jb
```
If you want to download an nzb file; the command doesn't change:
```bash
# Just specify it on the commandline
nr get /path/to/nzbfile.nzb

# You can specify more then one too (there is no limit)
nr get /path/to/nzbfile.nzb /another/path/to/nzbfile.nzb
```

If you want to view a header of a particular post you can do the following:
```bash
# Fetch the Article header associated with the Message-ID abcd@1234
nr get --headers abcd@1234

```

## Posting
Posting assumes the following:
* You have a directory containing content you want to post to an NNTP Server.
* The content you want to post may (or may not) require some additional preparation (archiving + parchives). The script will support both ways.
* The directory name you post to will play a critical role in it's *naming* (identification).
* The directory you point to will become the key input for all of the phases (should you do them one after another).
* A workspace is prepared based on the directory name you point to. For example, if you attempt to post the contents of a directory called ```mystuff``` then the workspace created is ```mystuff.nrws```.

Posting works in 5 phases that you control:
1. **Preparation** (--prep or -P): This phase is completely optional. Preparation assumes you don't want to pass the content as is and instead want to archive and provide par2 files as well. If the directory you're being prepared is called ```my.backup.dir``` then the preparation directory will be created as ```my.backup.dir.nrws/prep```.  Once content is prepared in the newly created **prep** directory, you can freely add to it if you want.  Add a README.nfo, or whatever you want.  The preparation directory is just the blue-prints of how you want content to be posted as. You can not post directories, therefore any directory you create will be ignored by the staging phase. Had you had directories to post, they should be archived so they can be presented as files.
2. **Staging** (--stage or -S): If a *prep* directory exists in the workspace the it is used. However if one doesn't exist, then the path being referenced is assumed to be in the final format you want to post. Staging creates a new directory in the workspace called **staged**.  It additionally creates a small SQLite (database) called **staged.db** that also resides in the workspace.  This stage is takes everything *to-be-posted* and prepares it in the correct *NNTPArticle* format.  This means building small messages that don't exceed NNTP Limitations and converting all binary data to yEnc encoded. All meta information is written to the SQLite database for further reference.  This database is also used to track what has been posted and what hasn't.  By default on this stage, everything is marked as having *not* been uploaded/posted yet.
3. **Uploading** (--upload or -U): This phase will only work if content was previously staged.  This phase relies heavily on the newly created SQLite database. This stage iterates over the database and posts everything to the NNTPServer.  This stage is multi-threaded and will utilize all of the allowed connections to your NNTP Server you've defined.  Content that is uploaded successfully (as acknowledged by the remote server) is flagged in the database as being uploaded.  If a failure occurs, the content is not retransmitted.  However you an run this stage again and again as uploaded content is never re-uploaded (only missed entries).  The last thing this stage does is write an NZB-File. If you were originally dealing with ```my.backup.dir``` then you will now have a ```my.backup.dir.nzb```.
4. **Verification** (--verify or -V): This phase is completely optional.  It utilizes the same SQLite database the *upload* phase did and attempts to verify that every segment got posted to the NNTP Server. It's useful if you just want that warm fuzzy feeling that the post went okay.
5. **Cleanup** (--clean -or -C): This phase can be ran at anytime you want, it destroys the workspace managed by this script but does not touch the original directory content was referenced.  It will never touch the generated nzb file (if one was generated) either.  It's just a safe clean up option.

Here is an example
```bash

# Let's generate a directory to work with
mkdir content.to.post

# Now lets place some content into it. Please note I'm demonstrating
# using just simple text, but this directory can contain any content
# you wish and as much of it as you like.
cat << _EOF > content.to.post/README.txt
Hello world, I'm going to be posted onto an NNTP Server!
_EOF

#
# Phase 1: Preparation
#

# we prep our newly created directory; this will create a new directory
# called content.to.post.nrws.  From within that new directory
# we will find a directory called 'prep'
nr post --prep content.to.post

# Let's see what got created
find content.to.post.nrws/prep
#  output:
#    content.to.post.nrws/prep
#    content.to.post.nrws/prep/content.to.post.rar.par2
#    content.to.post.nrws/prep/content.to.post.rar.vol0+1.par2
#    content.to.post.nrws/prep/content.to.post.rar

#
# Phase 2: Staging
#

# Same command as above and same source directory, the fact that a
# prep directory and workspace exists, the below command knows to use
# that instead.  If they didn't, it would always use the directory
# identified below (which is what makes the prepping part optional)
nr post --stage content.to.post --groups alt.binaries.test

# we will now find a directory called 'staged' and a database called
# 'staged.db' in our workspace
find content.to.post.nrws/staged content.to.post.nrws/staged.db
#  output:
#    content.to.post.nrws/staged
#    content.to.post.nrws/staged/content.to.post.rar.par2
#    content.to.post.nrws/staged/content.to.post.rar.vol0+1.par2
#    content.to.post.nrws/staged/content.to.post.rar
#    content.to.post.nrws/staged.db

# These files 'look' simiar to what is in the prep directory but they are not,
# they are already in an ascii format... have a look:
head -n2 content.to.post.nrws/staged/content.to.post.rar
#  output:
#    =ybegin part=1 total=1 line=128 size=710 name=content.to.post.rar
#    =ypart begin=1 end=710

#
# Phase 3: Upload
#

# Same command as above and same source directory. This phase will reference
# our new SQLite database (content.to.post.nrws/staged.db) and our staged
# files residing in content.to.post.nrws/staged
nr post --upload content.to.post

# You'll now have an NZB-File you can reference. In this case it will be
# called: content.to.post.nzb
# You're technically done now, but it wouldn't hurt to verify your results:

#
# Phase 4: Verification
#

# Run a verification on our data to see how it made out:
nr post --verify content.to.post

#
# Phase 5: Clean Up
#
# This just gets rid of our workspace since we're done with it now
nr post --clean content.to.post

```

In all cases, the post action will return zero (0) if everything went according to plan.  Otherwise it will
return a non-zero value.  If you want to increase the verbosity of the output, just add a *-v* switch
following the *nr* command:
```bash
# Adding the -v increases the verbosity
nr -v post --verify content.to.post

# Adding a lot of -v makes the verbosity overkill, but you'll want to do this
# if you need to raise a ticket later:
nr -vvv post --verify content.to.post

```

## Indexing
Indexing allows you to scan headers from one or more groups and download them to a local cache.
```bash
# Initialize the database
nr db init

# Cache the group listings
nr update groups

# When that's done;  you can choose to add groups to a watch list
# which will be automatically indexed for you:
nr group watch alt.binaries.sounds
nr group watch alt.binaries.multimedia

# You can create aliases too so you don't have to always type so much.
nr alias add alt.binaries.sounds sounds

# You can handle all interactions with that group by just
# identifying it's alias now!

```

### Search
Fetch the headers of a group, then search them.  Everything is kept on
your machine; the headers are grouped into files and collections
(releases) as they arrive, so they can be searched right away:
```bash
# The first time, say how far back to go (a.b. is short for
# alt.binaries.)
nr update a.b.test2 --newest 200000

# After that it carries on from where it left off (anything missed, and
# everything new)
nr update a.b.test2

# An overview: where everything is (and how big), the servers, every
# group with something kept, the aliases, and what the index holds
nr status

# Everything (newest first, or --oldest-first; a screen at a time on a
# terminal), or what has keywords in it
nr search -g a.b.test2
nr search --oldest-first
nr search holiday photos

# Collections are searched unless told otherwise; --posts (what
# --content kept) and --raw (cached headers) can be added, each shown
# under its own heading
nr search holiday --collections --posts

# A keyword starting with - leaves out what has it (put -- before it)
nr search holiday -- -2019

# Collections whose articles are still arriving are left out unless asked
nr search --forming holiday

# Save one as an NZB file (using the id shown on the left)
nr nzb save 42 --output ~/Downloads
```

See `nr search --help` for the find-style options (`--isubject '*holiday*'`,
`--iposter`, `--ifilename`, ...), scores and tags.

### More Than Headers
Switches pull in more to search with (each one only once, and they can be
combined):
```bash
# Keep what the small articles (64KB or less) say, so their text can be
# searched with 'nr search --posts'
nr update a.b.test2 --content

# Read the NZB files people posted into the index
nr update a.b.test2 --nzb

# Do either with the headers you already have (fetching no new ones)
nr update a.b.test2 --cached --nzb --content

# Work every score and tag out again after changing filters.yaml
nr update a.b.test2 --rescore

# Start a group over (everything kept of it is removed first)
nr update a.b.test2 --reset --days 7
```

Obfuscated posts (a made up subject for every article) are put together
from how they were posted: the poster, the shape of the subject and
Message-ID, and the groups they went to.  How many files one has is
worked out from its article sizes (every file's last article is shorter)
and shown as an estimate (~38 files).  Their real file names come from an
NZB file, or from the articles themselves.

A collection missing parts is treated as done (settled) once its groups
have been scanned `collection_settle_time` seconds (1800 by default) past
its last article.

### Articles
```bash
# Everything known of articles: their headers, score, the collection and
# file they are part of, and what they say.  One not kept yet is fetched
# (whole, so the connection is never broken off) and kept.
nr show '<message-id@example.com>' another@example.com

# Download them and save what they hold (the names saved are shown)
nr fetch '<message-id@example.com>' --output ~/Downloads
```

### Keeping Up
Watch the groups you care about and fetch them as often as you like:

```bash
nr group watch a.b.test2
nr update --watched

# Consider adding this to a crontab:
cat << _EOF > /etc/cron.d/newsreap
# Fetch (and index) every watched group every 20 minutes; you don't need
# root to run this (it's better not to)
*/20 * * * * someuser nr update --watched
_EOF
```

## Developers
Tests, linting and builds are all run through [tox](https://tox.wiki/):
```bash
# Install tox once
pip install tox

# Run the full test suite (with code coverage)
tox -e qa

# A quicker test run without coverage; extra arguments go to pytest
tox -e test -- tests/CodecYenc_Test.py

# Check the code for lint issues (and fix what can be fixed)
tox -e lint
tox -e format

# Build the source and wheel packages
tox -e build
```

Tests that need the 7-Zip, rar or par2 tools are skipped when those tools
aren't installed.

### Testing conversations with an NNTP Server
`tests/FakeNNTPServer.py` is a small scripted NNTP server for testing how
NewsReap talks to a server:
- Out of the box it answers like a typical provider would (login,
  compression, group listings, overviews, articles and posting).
- `on()` lets a test script any reply; use it to act like the odd servers
  found in the wild (slow or stalling replies, dropped connections, garbage,
  missing terminators, etc).
- Every command it receives is recorded, so a test can check the exact
  conversation that took place.

```python
from tests.FakeNNTPServer import FakeNNTPServer, Reply
from tests.conftest import connect_to

def test_slow_server(nntp_server):
    # Send the article 64 bytes at a time
    nntp_server.add_article('5', 'tests/var/00000005.ntx', 'alt.binaries.test')
    nntp_server.on(r'^ARTICLE', Reply(
        '220 0 <5>', body=open('tests/var/00000005.ntx', 'rb').read(),
        chunk_size=64, delay=0.01))

    sock = connect_to(nntp_server)
    assert sock.connect()
    assert sock.get('5', work_dir='/tmp', group='alt.binaries.test')
    assert nntp_server.commands()[-2:] == [
        'GROUP alt.binaries.test', 'ARTICLE <5>']
```

See `tests/NNTPFlow_Test.py` for many more examples.

The core of this tool wraps around it's framework it provides

```python
from newsreap import NNTPConnection
from newsreap import NNTPnzb

# Create an NNTP Connection
sock = NNTPConnection(
    host='remote.usenet.host',
    port=119,
    # Set this to True and use the Secure port
    # if you want.
    secure=False,
    username='validuser',
    password='validpass',
)

# Now you can do things such as:

# - Retreive a group listing from the server
groups = sock.groups()

# - Alternatively you can filter the list of groups returned:
binary_groups = sock.groups(filters="alt.binaries")

# - Iterate over articles in a group (returns a listing)
items = sock.xover()

# - Retrieve an article (if you know it's ID)
#      - The work_dir is where the content is placed into
#      - The group is where the item is found; not all
#        usenet servers require this option to be set; but it's
#        there fore those that do:
#
result = sock.get('message-id', work_dir='/tmp', group='alt.binaries.test')

# - Retrieve a series of articles if you have an nzbfile:
result = sock.get(
    NNTPnzb('/path/to/NZBfile', work_dir='/tmp'),
    # Define the path you want your content to download to. This is not
    # a permanent path; it's just a temporary store that can be used
    # for file storage.
    work_dir='/tmp',
)
```
