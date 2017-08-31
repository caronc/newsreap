[![Paypal](http://repo.nuxref.com/pub/img/paypaldonate.svg)](https://www.paypal.com/cgi-bin/webscr?cmd=_s-xclick&hosted_button_id=MHANV39UZNQ5E)
[![Patreon](http://repo.nuxref.com/pub/img/patreondonate.svg)](https://www.patreon.com/lead2gold)

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

## Search Usage
Once the database is set up and being populated, it looks after
generating you're configuration
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

## Index Usage
Indexing will iterate over the watched groups (or the ones specified)
and build NZBFiles from content that can be grouped together.

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

## Downloading Usage
If you know the article-id/Message-ID of the item in question, the following
will retrieve it for you and save it based on your downloading configuration
defined in your cofiguration file.

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
