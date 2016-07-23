#!/usr/bin/env python
# Testing the fastest way to sort a list of dictionaries by a key
#

from timeit import Timer
setup = '''
from operator import itemgetter
from random import randint as R
from random import choice as C
import string

mydictlist = []
chars = string.ascii_uppercase + string.digits
size = 3
for x in range(0, Q):
    mydictlist.append({
        'key': R(0, D),
        'val': ''.join(C(chars) for _ in range(size)),
    })

'''
# Queue Size (Q) = 5000000

# Dictionary Key Character Size (D) = 300000

#
# lambda vs itemgetter
#
print('sorted(5000000) using itergettter() : %s' % (
    Timer(
        'sorted(mydictlist, key=itemgetter("key"))',
        'Q=300000;D=5000000'+setup).timeit(1)
))

print('sorted(5000000) using lamda         : %s' % (
    Timer(
        'sorted(mydictlist, key=lambda k: k["key"])',
        'Q=300000;D=5000000'+setup).timeit(1)
))
