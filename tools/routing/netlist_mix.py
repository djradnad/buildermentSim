# Enumerate splitter netlists (k splitters, 2 full input belts x,y, 2 outputs) whose steady flows give
# outputs {x/5+4y/5, 4x/5+y/5} with every belt <= 1 full belt.
import itertools, sys
from fractions import Fraction as F
import numpy as np
k = int(sys.argv[1])
srcs = ['x', 'y'] + ['s%d.%d' % (i, j) for i in range(k) for j in range(2)]
sinks = ['A', 'B'] + ['t%d.%d' % (i, j) for i in range(k) for j in range(2)]
n = len(srcs)
found = 0
seen = set()
for perm in itertools.permutations(range(n)):
    # srcs[i] -> sinks[perm[i]]
    if perm[0] in (0, 1) or perm[1] in (0, 1):  # inputs straight to outputs: useless
        continue
    # no splitter feeding itself on both inputs from its own outputs
    m = {srcs[i]: sinks[perm[i]] for i in range(n)}
    inv = {v: u for u, v in m.items()}
    # splitter i input value = sum of srcs mapped to t_i.*; outputs s_i.j = in_i/2. unknowns: in_i (k)
    for com in (0, 1):
        pass
    M = np.zeros((k, k)); rhs = np.zeros((k, 2))
    for i in range(k):
        M[i, i] = 1
        for j in range(2):
            s = inv['t%d.%d' % (i, j)]
            if s == 'x': rhs[i, 0] += 1
            elif s == 'y': rhs[i, 1] += 1
            else:
                a = int(s[1:].split('.')[0]); M[i, a] -= 0.5
    if abs(np.linalg.det(M)) < 1e-9:
        continue
    sol = np.linalg.solve(M, rhs)
    if (sol.sum(1) > 2 + 1e-9).any():
        continue
    outs = []
    for o in ('A', 'B'):
        s = inv[o]
        a = int(s[1:].split('.')[0]); outs.append(tuple(np.round(sol[a] / 2 * 5, 6)))
    if sorted(outs) == [(1.0, 4.0), (4.0, 1.0)]:
        key = tuple(sorted(m.items()))
        found += 1
        if found <= 10:
            print(m, 'splitter inputs', [tuple(np.round(r, 4)) for r in sol], flush=True)
print('found', found)
