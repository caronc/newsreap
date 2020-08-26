[![Build Status](https://travis-ci.org/caronc/newsreap.svg?branch=master)](https://travis-ci.org/caronc/newsreap)[![Coverage Status](https://coveralls.io/repos/caronc/newsreap/badge.svg?branch=master)](https://coveralls.io/r/caronc/newsreap?branch=master)
[![Paypal](http://repo.nuxref.com/pub/img/paypaldonate.svg)](https://www.paypal.com/cgi-bin/webscr?cmd=_s-xclick&hosted_button_id=MHANV39UZNQ5E)

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

Some under the hood stuff that developers should appreciate about NewsReap are:
* It suports character sets and does it's best to allow you to work with unicode characters
* It's backend is based on SQLAlchemy making it really easy to adapt to your own projects.
* It's front-end CLI is built using Click which allows anyone to extend it to their likings.

__Note:__ This is a work in progress; a lot of what is documented here is what the final solution will be.  But it is definitely not there yet!  The backend is in good shape, but the CLI tool is what needs to be worked on next.

## Installation
Make sure you configure your __config.yaml__ file (sample included) and place it in anyone of the following directories:

1. ~/.config/newsreap/config.yaml
2. ~/newsreap/config.yaml
3. ~/.newsreap/config.yaml
4. /etc/newsreap/config.yaml
5. /etc/config.yaml

Assuming you configured the file correctly, you can now start interfacing with you're NNTP Provider.
You'll want to make sure you meet all of the dependencies:
```bash
# Browse to the directory you installed newsreap into
# Then Install the necessary dependencies like so:
pip install -r requirements.txt
```

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
Once you've indexed certain content you can then begin searching it
for content:
```bash
# If you want to just scan/search the groups manually; you can now
# cache these watched groups with the following command:
nr update search --watched

# using the search keyword, all variables specified after it
# are treated as filters:
# You can use -/+ tokens to filter your results further by
# default + is presumed to be infront of anything not otherwise
# having it identified
#
#  + the keyword 'must' exist somewhere in the title (treat it as an 'and')
#  - the keyword can not be present in the title (treat it as an 'and not')
#
#  Entries without either a -/+ are treated as being an 'or'.
#
#  If you need to search for a + or -, you'll need to escape them with a
#  backslash (\).  If you need to identify a backslash in the search; then
#  escape it with another backslash
#
# Searches are not case sensitive
nr search sintel -porn +720p

# Here is how the escaping works; The following looks for anything
# with +++ in it's title:
nr search \+\+\+
```

### Continued Indexing Usage
Indexing can be automated and keep up with what gets posted. You can set up watch lists and then
automate there scanning as often as you want.

```bash
# Index all of the groups being watched
nr update index --watched

# Alternative; just index the groups identified
# You can reference them by their aliases too here (if you set any up)
nr update index alt.binaries.multimedia sounds

# Consider adding this to a crontab:
cat << _EOF > /etc/cron.d/nzbindex
# Index all groups every 20 minutes
# You don't need root to run this script; it would actually be
# in your best interest to run this as a non-root user
*/20 * * * * root nr update index --watched
_EOF
```

## Developers
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
