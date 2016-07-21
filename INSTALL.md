
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
           python-gevent python-click

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
           python-gevent python-click

# Testers might want to also install the following:
sudo dnf --enablerepo=nuxref* --best install \
           python-nose


# You're done; go ahead and use the newsreap tools
```

### Virtualenv
Python Virtual Environments are kind of handy to have but are a little trickier to work with.  Explaining these here is probably a bit overkill. They are
maybe only needed for that hardcore developer who's working on newsreap as
well as other apps and doesn't want my python dependencies colliding with
there other ones. This is more advanced and not for everyone.

#### Step 1: The Environment Setup
##### Red Hat / CentOS 6 & 7
```bash
sudo yum install python-virtualenv python-pip curl \
             python-virtualenvwrapper gcc
# either open a new terminal window or run the following
# to make your current terminal window ready to use the
# virtualenvwrapper
. /etc/profile.d/virtualenvwrapper.sh

# Create a new newsreap virtualenv
mkvirtualenv -p python2.7 newsreap
```
##### Fedora 22+
```bash
sudo dnf install python-virtualenv python-pip curl \
             python-virtualenvwrapper gcc

# either open a new terminal window or run the following
# to make your current terminal window ready to use the
# virtualenvwrapper
. /etc/profile.d/virtualenvwrapper.sh

# Create a new newsreap virtualenv
mkvirtualenv -p python2.7 newsreap
```
##### Ubuntu & Debian
```
sudo apt-get install virtualenv \
        python-pip unzip gcc

# enable virtualenvwrapper manually if you don't have
# bash autocomplete set up yet:
echo 'source /etc/bash_completion.d/virtualenvwrapper' >> ~/.bashrc
echo "export WORKON_HOME=~/.virtualenvs" >> ~/.bashrc

# close your terminal window and open a new one, or
# just do the following to make your current window active
export WORKON_HOME=~/.virtualenvs
source /etc/bash_completion.d/virtualenvwrapper

# Create a new newsreap virtualenv
mkvirtualenv -p python2.7 newsreap
```
##### Windows
```bash
# make sure pip is installed (it comes with python when you
# download it and install it:
pip install virtualenv
pip install virtualenvwrapper-win

# Create a new newsreap virtualenv
mkvirtualenv -p python2.7 newsreap
```

#### Step 2: The Environment
Once you've got your virtual environment set up, you
can interact with it at anytime:
```bash
# You can leave this environment and return by typing
deactivate

# In the future, you can just change to this environment
# by typing:
workon newsreap
# You should see a (newsreap) prefix infront of your directory

# Browse to the directory you installed newsreap into
# Then Install the nessisary dependencies;
pip install -r requirements.txt
```
