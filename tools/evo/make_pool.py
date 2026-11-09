"""Write the training pool of layout CNFs (one per port-position cube) to ~/cnfs/pool, as used by sets/*.txt.

    python tools/evo/make_pool.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from bmsolver import spec as S, cnf, guide  # noqa: E402

out = os.path.expanduser('~/cnfs/pool')
os.makedirs(out, exist_ok=True)
jobs = []
for n in (14, 16, 18, 20, 22):
    jobs.append(('split%d_3x%d' % (n, n), S.splitter(n), 3, n))
for n in (13, 16, 19):
    jobs.append(('split%d_2x%d' % (n, n), S.splitter(n), 2, n))
for W, H in ((9, 4), (6, 6), (7, 6), (6, 5), (10, 4)):
    jobs.append(('bal3_%dx%d' % (W, H), S.balancer(3), W, H))
for W, H in ((8, 6), (9, 5), (7, 7)):
    jobs.append(('bal4_%dx%d' % (W, H), S.balancer(4), W, H))
for W, H in ((3, 8), (4, 6)):
    jobs.append(('bal2_%dx%d' % (W, H), S.balancer(2), W, H))
for name, sp, W, H in jobs:
    for k, w in enumerate(cnf.cubes(sp.window(W, H), mirror=True)[:8]):
        mod, tr, lay = guide.build(w, 'local')
        cnf.write_dimacs(tr.f, os.path.join(out, '%s_c%d.cnf' % (name, k)))
# the bal3 7x5 / 8x5 / split24 3x24 test files in sets/test.txt live one folder up
C = {c[0]: c for c in __import__('bmsolver.bench', fromlist=['CASES']).CASES}
for name in ('bal3_7x5', 'bal3_8x5'):
    _, mk, W, H, exp, t = C[name]
    for k, w in enumerate(cnf.cubes(mk().window(W, H), mirror=True)):
        mod, tr, lay = guide.build(w, 'local')
        cnf.write_dimacs(tr.f, os.path.expanduser('~/cnfs/%s_c%d_L.cnf' % (name, k)))
_, mk, W, H, exp, t = C['split24_3x24']
mod, tr, lay = guide.build(mk().window(W, H), 'local')
cnf.write_dimacs(tr.f, os.path.expanduser('~/cnfs/split24_3x24_L.cnf'))
