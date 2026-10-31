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

# 7zip support (add /usr/bin/7za)
sudo dnf install p7zip

# par2 support (add /usr/bin/par2)
sudo dnf install par2cmdline

# rar support (add /usr/bin/rar); rar is not free software so it may need
# to come from a third party repository such as http://nuxref.com/repo/
sudo dnf --enablerepo=nuxref* install rar

# You're done; go ahead and use the newsreap tools
```

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
```

__Note:__ Most dependencies ship pre-built wheels for common platforms. If one
isn't available for your system, pip will need a C compiler to build it
(for example [gevent](https://pypi.org/project/gevent/) and
[cryptography](https://pypi.org/project/cryptography/)).
