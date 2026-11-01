NewsReap requires **Python 3.13** or newer.

## Linux Distribution
If you're using a mainstream Linux distribution then there
isn't a whole lot needed to get you all set up.

### Fedora
```bash
# Install the nessisary dependencies
sudo dnf install \
           python3-sortedcontainers python3-pyyaml \
           python3-sqlalchemy python3-gevent python3-click \
           python3-dateutil python3-cryptography python3-pytz \
           python3-tqdm python3-lxml

# 7-Zip support (adds /usr/bin/7z)
sudo dnf install 7zip

# par2 support (adds /usr/bin/par2)
sudo dnf install par2cmdline

# unrar (to extract rar files) isn't free software, so it comes from the
# RPM Fusion "nonfree" repository; see https://rpmfusion.org/Configuration
# to add it
sudo dnf install unrar

# You're done; go ahead and use the newsreap tools
```

rar itself (needed to make archives when posting) isn't packaged for
Fedora at all.  Download "RAR for Linux" from
[RARLAB](https://www.rarlab.com/download.htm), unpack it and copy `rar`
(and `unrar`, if you skipped RPM Fusion) into `/usr/local/bin`; newsreap
looks there too.

### Ubuntu / Debian
```bash
# Install the archive tools (optional)
sudo apt-get install 7zip par2 rar unrar

# Then install NewsReap using PIP (see below)
```

## Other Distributions
### PIP
For other distributions, you may need to go the PIP route.
```bash
# Browse to the directory you installed newsreap into
# Then install it (and the `nr` command) like so:
pip install .

# Developers can install the testing tools too:
pip install -e ".[dev]"

# Keeping the index on a database server (see index_engine in the
# configuration file) needs its driver:
pip install ".[postgresql]"
pip install ".[mysql]"
```

__Note:__ Most dependencies ship pre-built wheels for common platforms. If one
isn't available for your system, pip will need a C compiler to build it
(for example [gevent](https://pypi.org/project/gevent/) and
[cryptography](https://pypi.org/project/cryptography/)).
