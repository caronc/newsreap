#!/usr/bin/env python
from timeit import Timer

t = Timer(
    """
    for v in x:
        max(v, -v)
    """, setup="""x = (1, 0, -1)""")
print '%-25s: %.12f' % ('max(v, -v); comparrison', t.timeit())

t = Timer(
    """
    for v in x:
        abs(v)
    """, setup="""x = (1, 0, -1)""")
print '%-25s: %.12f' % ('abs(v); comparrison', t.timeit())

t = Timer(
    """
    for v in x:
        if v < 0:
            v *= -1
    """, setup="""x = (1, 0, -1)""")
print '%-25s: %.12f' % ('v *= -1; comparrison', t.timeit())

t = Timer(
    """
    for v in x:
        if v < 0:
            v = -v
    """, setup="""x = (1, 0, -1)""")
print '%-25s: %.12f' % ('v = -v; comparrison', t.timeit())
