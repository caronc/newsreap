#!/usr/bin/env python
# A test to see the fastest way to check for a negative value
# and make it positive abs(); but abs() proves to be very slow
# compared to a simple if/else check.
from timeit import Timer

test = """
for v in x:
    max(v, -v)
"""
t = Timer(test, setup="""x = (1, 0, -1)""")
print '%-25s: %.12f' % ('max(v, -v); comparrison', t.timeit())

test = """
for v in x:
    abs(v)
"""
t = Timer(test, setup="""x = (1, 0, -1)""")
print '%-25s: %.12f' % ('abs(v); comparrison', t.timeit())

test = """
for v in x:
    if v < 0:
        v *= -1
"""
t = Timer(test, setup="""x = (1, 0, -1)""")
print '%-25s: %.12f' % ('v *= -1; comparrison', t.timeit())

test = """
for v in x:
    if v < 0:
        v = -v
"""
t = Timer(test, setup="""x = (1, 0, -1)""")
print '%-25s: %.12f' % ('v = -v; comparrison', t.timeit())
