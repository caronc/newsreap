#!/usr/bin/env python
# Testing the fastest way to check a dictionary for more
# than one key
#
# foo = {'foo':1,'zip':2,'zam':3,'bar':4}
#
# if ("foo","bar") in foo:
#   #do stuff
#

from timeit import Timer
setup = '''
from random import randint as R
d=dict((str(R(0,1000000)),R(0,1000000)) for i in range(D))
q=dict((str(R(0,1000000)),R(0,1000000)) for i in range(Q))
print("looking for %s items in %s"%(len(q),len(d)))
'''

print Timer('set(q) <= set(d)', 'D=1000000;Q=100;'+setup).timeit(1)
print Timer('set(q) <= set(d.keys())', 'D=1000000;Q=100;'+setup).timeit(1)
print Timer('all(k in d for k in q)', 'D=1000000;Q=100;'+setup).timeit(1)
