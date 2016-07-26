#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# SetupTools Script
#
# Copyright (C) 2015 Chris Caron <lead2gold@gmail.com>
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

from setuptools import setup
from setuptools import find_packages


setup(
    name='newsreap',
    version='0.0.1',
    description='Usenet Indexer',
    long_description=open('README.md').read() + \
                        '\n\n' + open('HISTORY.rst').read(),
    url='https://github.com/caronc/newsreap',
    keywords='usenet nntp index',
    author='Chris Caron',
    author_email='lead2gold@gmail.com',
    packages=find_packages(),
    include_package_data=True,
    test_suite='tests',
    install_requires=open('requirements.txt').readlines(),
    classifiers=(
        'Natural Language :: English',
        'Programming Language :: Python',
        'Programming Language :: Python :: 2.6',
        'Programming Language :: Python :: 2.7',
        'Topic :: Communications :: Usenet News',
        'License :: OSI Approved :: GNU General Public License v3 (GPLv3)',
    ),
)
