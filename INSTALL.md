CentOS 6 & 7
=============
```bash
# Install the nessisary dependencies; feel free to
# connect to the repository on nuxref for them if they
# aren't otherwise available; see http://nuxref.com/repo/
sudo yum --enablerepo=nuxref* install \
           python-blist python-yenc \
           PyYAML python-sqlalchemy \
           python-gevent python-click

```
Fedora 22+
==========
```bash
sudo dnf --enablerepo=nuxref* --best install \
           python-blist python-yenc \
           PyYAML python-sqlalchemy \
           python-gevent python-click
```
