
## Linux Distribution
If you're using a mainstream Linux distribution then there
isn't a whole lot needed to get you all set up.

### Red Hat / CentOS 6 & 7
This is my personal favorite choice; the OS is usually always a bit outdated, but with it comes so much stability.
```bash
# Install the nessisary dependencies; feel free to
# connect to the repository on nuxref for them if they
# aren't otherwise available; see http://nuxref.com/repo/
sudo yum --enablerepo=nuxref* install \
           python-blist python-yenc \
           PyYAML python-sqlalchemy \
           python-gevent python-click \
           python-dateutil \
           python-cryptography pytz

7zip support (add /usr/bin/7za)
sudo yum --enablerepo=nuxref* install p7zip

rar support (add /usr/bin/rar)
sudo yum --enablerepo=nuxref* install rar

# Testers might want to also install the following:
sudo yum --enablerepo=nuxref* --best install \
           python-nose

# You're done; go ahead and use the newsreap tools
```
### Fedora 22
```bash
# Install the nessisary dependencies; feel free to
# connect to the repository on nuxref for them if they
# aren't otherwise available; see http://nuxref.com/repo/
sudo dnf --enablerepo=nuxref* --best install \
           python-blist python-yenc \
           PyYAML python-sqlalchemy \
           python-gevent python-click \
           python-cryptography

# Testers might want to also install the following:
sudo dnf --enablerepo=nuxref* --best install \
           python-nose


# You're done; go ahead and use the newsreap tools
```

## Other Distributions
### PIP
For other distributions, you may need to go the PIP route.
```
# Browse to the directory you installed newsreap into
# Then Install the nessisary dependencies like so:
pip install -r requirements.txt
```

__Note:__ _Windows users_ will need to have access to a compiler (as pip will need to compile things such as [gevent](https://pypi.python.org/pypi/gevent/) and [cryptography](https://pypi.python.org/pypi/cryptography/). As long as the [Microsoft Visual C++ Compiler for Python 2.7](https://www.microsoft.com/en-ca/download/details.aspx?id=44266) is installed, you shouldn't have a problem.
