# The hooks 'nr update --content' uses to turn articles into searchable text
#
# Copyright (C) 2026 Chris Caron <lead2gold@gmail.com>
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
# Every module here is loaded by NNTPArchiver (unless told not to).  Each
# one can define:
#  - pre_archive(post): return False to not keep the article at all
#  - archive_convert(post): fill in what is kept (post.kind, post.text,
#    post.files, ...); set post.done once the article is dealt with so the
#    ones after it leave it alone
#  - post_archive(post): called once the article was kept
# They run in priority order (lowest first); see NNTPArchivePost for what
# a post holds.
