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
  you care about (`nr filter --help`).  Drop rules that only look at the
  header (subject, poster, posting time, size, how many groups it was
  crossposted to) run first, so spam is never scored or stored.

Some under the hood stuff that developers should appreciate about NewsReap are:
* It suports character sets and does it's best to allow you to work with unicode characters
* It's backend is based on SQLAlchemy making it really easy to adapt to your own projects.
* It's front-end CLI is built using Click which allows anyone to extend it to their likings.

__Note:__ This is a work in progress; a lot of what is documented here is what the final solution will be.  But it is definitely not there yet!  The backend is in good shape, but the CLI tool is what needs to be worked on next.

## Installation
NewsReap needs a __config.yaml__ file; it looks for one in the following places (in order; `.yml` works too, ie: `config.yml`):

1. ~/.config/newsreap/config.yaml
2. ~/newsreap/config.yaml
3. ~/.newsreap/config.yaml
4. /etc/newsreap/config.yaml
5. /etc/newsreap.yaml

The easiest way to get started is to let NewsReap write one for you:
```bash
# Writes ~/.config/newsreap/config.yaml and creates the directories it uses
nr --init

# Or keep everything together somewhere else
nr --config ~/usenet/config.yaml --init
```

Each kind of file goes where your system expects it (on Linux):

| What | Where |
|---|---|
| `config.yaml` and your filters (`filters/`) | `~/.config/newsreap/` |
| What can't be fetched again: the databases, the posts kept, your PGP keys | `~/.local/share/newsreap/` |
| What can be fetched again: the cached headers, downloads in progress | `~/.cache/newsreap/` |

The `XDG_CONFIG_HOME`, `XDG_DATA_HOME` and `XDG_CACHE_HOME` variables move
them, as do `data_dir` and `cache_dir` in `config.yaml`.  Set `base_dir`
there to keep everything in one directory instead (`nr --config
<file> --init` outside `~/.config/newsreap` does that for you).

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

## Getting Started
A walk through from nothing to a saved NZB file, using one group.  Every
command has more to it; add `--help` to any of them (ie: `nr search
--help`).

### 1. Point NewsReap at Your Server
```bash
# Writes ~/.config/newsreap/config.yaml (it never overwrites one)
nr --init
```

Open that file and fill in your provider's details under `servers:`:
```yaml
servers:
  - host: news.example.com
    port: 563
    username: me
    password: secret
    secure: True
```

### 2. Set Up the Database and Learn the Groups
```bash
# Creates the database NewsReap keeps its settings in
nr db init

# Lists every group your server carries (do this again now and then)
nr update groups
```

### 3. Fetch a Group
A group's article headers (subject, poster, date and size; not the
content) are fetched and kept on your machine, then put together into
files and collections (one collection is one upload, such as a release).
The first time, say how far back to go:
```bash
# a.b. is short for alt.binaries.
nr update a.b.test2 --newest 200000

# Or by date
nr update a.b.test2 --days 7

# Later runs carry on from where the last one stopped
nr update a.b.test2
```

`nr status` shows what is kept (and how big it is) at any time.

### 4. Search
```bash
# The collections of a group, newest first
nr search -g a.b.test2

# Only the ones whose name has these words
nr search holiday photos

# Leave out what has a word (put -- before it; options go before the --,
# since everything after it is a word to search for)
nr search holiday -- -2019

# Every article header, one line each (as a newsreader lists them)
nr search --raw -g a.b.test2
```

On a terminal the results are shown a screen at a time: the up and down
arrows move a row, space and `b` move a page, `/` finds text and `q`
quits.  Add `--no-pager` to write them all out (ie: into a file).

### 5. Save What You Want
The number on the left of each search result is its id:
```bash
nr nzb save 42 --output ~/Downloads
```
Hand the NZB file to your downloader, or use `nr get` (see Downloading).

### 6. Keep Up to Date
```bash
# Remember a group, and give it a shorter name
nr group watch alt.binaries.test2
nr alias add test2 alt.binaries.test2

# Fetch every watched group (a good one for a crontab)
nr update --watched
```

### Where to Go Next
- **Read discussions:** `nr update <group> --content` keeps the text of
  small articles, so `nr search --posts <words>` finds what people wrote
  (see More Than Headers).
- **Drop spam and rank what you like:** filters remove articles before
  they are ever kept, and score and tag the rest (`nr filter --help`).
  They live in `~/.config/newsreap/filters/`: every `.yaml` (or `.yml`)
  file there is read in the order of their names, so `00-spam.yaml` runs
  before `50-scores.yml`, and you can split them up however you like.
  `example.yaml.template` there shows every setting a rule can have.
  Try `nr filter purge ... --dry-run` first to see what a rule removes.
- **Read one article:** `nr show <message-id>`.

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
nr alias add sounds alt.binaries.sounds

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
# terminal: up/down move a row, space/b a page, / finds, q quits), or what
# has keywords in it
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

# Work every score and tag out again after changing your filters
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

### PGP Keys
Some posts are signed or encrypted with PGP, and some carry a key someone
is sharing.  NewsReap can keep those keys, and which posts each one
signed or was encrypted for, so you can see who posted what.  No key or
passphrase is needed; only public details are kept.  It is off until you
add this under `processing:` in your configuration file:
```yaml
processing:
   pgp: yes
```

Keys are then picked up by `nr update <group> --content`; add `--force`
once to read the posts you kept before turning it on.
```bash
# The keys kept (and how many posts each is tied to)
nr pgp list

# Everything about a key: by its key ID, fingerprint or part of a name
nr pgp show 'someone@example.com'

# The posts tied to a key
nr search --key 11349D4561E2FDA5

# Keep a key someone gave you, or hand one on
nr pgp import friend.asc
nr pgp export 'someone@example.com' --output someone.asc
```

#### Reading Posts Encrypted for You
Import your own secret key and the posts encrypted for it are decrypted as
they are kept, so their text can be searched like any other.  This needs
[gpg](https://gnupg.org) (most Linux systems have it; on Windows install
Gpg4win).
```bash
# gpg keeps it in ~/.local/share/newsreap/pgp (set pgp_home: ~/.gnupg to
# use your own)
nr pgp import my-secret-key.asc

# If the key has a passphrase, give it once; it is checked and kept in a
# file only you can read
nr pgp passphrase 'me@example.com'

# Read the posts kept before (or that couldn't be read yet)
nr pgp decrypt
```

Nothing ever stops to ask for a passphrase.  A post whose key needs one
that isn't known (or a wrong one) is kept as it is, still encrypted, and
tried again later:

- The next `nr update <group> --content` fetches it again, up to
  `pgp_retries` times (1 unless set under `processing:`).
- It is given up on at once when trying again can't help: the server no
  longer has it or won't let you have it, or none of your keys open it.
- Importing a secret key or giving a passphrase gives the posts it may
  open their tries back.
- `nr pgp decrypt` tries them all now (`--all` includes the ones given up
  on), and `nr status` says how many are waiting.

Keep in mind that the
decrypted text is stored in the index like any other post's, so if your
index lives on a shared database server, others with access to it can
read it.

### Keeping Up
Watch the groups you care about and fetch them as often as you like:

```bash
nr group watch a.b.test2
nr update --watched

# A group can always keep its posts' text (--content) or read the NZB
# files posted to it (--nzb), without being asked each time
nr group watch alt.discuss.coffee --content
nr group watch a.b.test2 --nzb

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

# Run nr from your checkout
tox -e nr -- search holiday
```

Tests that need the 7-Zip, rar or par2 tools are skipped when those tools
aren't installed.

### Testing Conversations with an NNTP Server
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
