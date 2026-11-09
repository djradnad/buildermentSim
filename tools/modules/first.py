"""First module cases: one recipe, whole input belts in on the east, whole output belts out on the west.

    python tools/modules/first.py plating8 8x5,7x5 600
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from bmsolver import bench, model  # noqa: E402
from bmsolver.bench import Factory  # noqa: E402


def plating8(arms=True):
    """iron plating T4: 8 workshops (4 ingot -> 2 plating per craft, 30 crafts/min each). 2 ingot belts in,
    1 plating belt out. 1 unit = 30/min, a belt = 16 units."""
    return Factory([(0, [16, 0]), (1, [16, 0])], (0, [0, 16]),
                   [dict(bid=3005, count=8, need=[4, 0], make=[0, 2], tier=4)],
                   ['Iron Ingot', 'Iron Plating'], 30, [2014], D=16, arm_cap=3 if arms else None)


def heatsink16(arms=True):
    """heat sink T4: 16 workshops (5 copper ingot -> 1 heat sink, 30 crafts/min each). 5 ingot belts, 1 out."""
    return Factory([(r, [16, 0]) for r in range(5)], (0, [0, 16]),
                   [dict(bid=3005, count=16, need=[5, 0], make=[0, 1], tier=4)],
                   ['Copper Ingot', 'Heat Sink'], 30, [2013], D=16, arm_cap=3 if arms else None)


def show(grid, machines, W, H):
    """text picture: belt codes, MM for machine tiles"""
    from bmsolver.model import MACHINES
    cells = {}
    for ti, a, o in machines:
        cells[tuple(a)] = 'M%d' % o
    return '\n'.join(''.join(cells.get((x, y)) or grid.get((x, y)) or '..' for x in range(W)) for y in range(H))


CASES = dict(plating8=plating8, plating8_noarms=lambda: plating8(False), heatsink16=heatsink16)

if __name__ == '__main__':
    name, sizes, timeout = sys.argv[1], sys.argv[2], float(sys.argv[3])
    f = CASES[name]()
    for s in sizes.split(','):
        W, H = map(int, s.split('x'))
        win = f.window(W, H)
        t0 = time.time()
        r = model.solve(win, timeout=timeout, workers=4)
        ok = ''
        if 'grid' in r:
            ok = bench.verify_factory(r['grid'], win, r.get('machines'), r.get('options'), r.get('filters'))
        print('%s %dx%d %s %.1fs verified=%s' % (name, W, H, r['status'], time.time() - t0, ok), flush=True)
        if 'grid' in r:
            import json
            os.makedirs(os.path.expanduser('~/data/modules'), exist_ok=True)
            rec = dict(case=name, W=W, H=H, verified=bool(ok), time=round(time.time() - t0, 1),
                       grid={'%d,%d' % p: c for p, c in r['grid'].items() if c},
                       machines=[[ti, list(a), o] for ti, a, o in r['machines']],
                       filters={'%d,%d' % k if isinstance(k, tuple) else str(k): v for k, v in (r.get('filters') or {}).items()})
            with open(os.path.expanduser('~/data/modules/%s.jsonl' % name), 'a') as fh:
                fh.write(json.dumps(rec, default=str) + '\n')
            print(show(r['grid'], r['machines'], W, H), flush=True)
