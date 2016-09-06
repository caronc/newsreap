#!/usr/bin/env python
# Testing if it's faster to slice a string or use startswith() or endswith()
# when detecting if a string contains matches a string.
#
# The shows that slicing is faster then using startswith() or endswith()

from timeit import Timer
setup = '''

from random import choice
import string
chars = string.ascii_uppercase + string.digits

# create a large random string
fname = 'abc.%s.123' % (''.join(choice(chars) for _ in range(10000)))
'''
#
# array slice vs startswith for checking
#

print('startswith : %s' % (Timer(
    'for _ in range(0, 1000000): fname.startswith("abc")',
    setup,
).timeit(1)))

print('slice (wlen) : %s' % (Timer(
    'for _ in range(0, 1000000): fname[0:len("abc")] == "abc"',
    setup,
).timeit(1)))

print('slice (wolen) : %s' % (Timer(
    'for _ in range(0, 1000000): fname[0:3] == "abc"',
    setup,
).timeit(1)))

print

# Test Endswith
print('endswith : %s' % (Timer(
    'for _ in range(0, 1000000): fname.endswith(".123")',
    setup,
).timeit(1)))

print('slice (wlen) : %s' % (Timer(
    'for _ in range(0, 1000000): fname[-len(".123"):] == ".123"',
    setup,
).timeit(1)))

print('slice (wolen) : %s' % (Timer(
    'for _ in range(0, 1000000): fname[-4:] == ".123"',
    setup,
).timeit(1)))

