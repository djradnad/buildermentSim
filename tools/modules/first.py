"""First module cases: one recipe, whole input belts in on the east, whole output belts out on the west.

    python tools/modules/first.py plating8 8x5,7x5 600
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from bmsolver import bench, model  # noqa: E402
from bmsolver.bench import Factory  # noqa: E402


def plating8(arms=True, free=False):
    """iron plating T4: 8 workshops (4 ingot -> 2 plating per craft, 30 crafts/min each). 2 ingot belts in,
    1 plating belt out. 1 unit = 30/min, a belt = 16 units."""
    return Factory([(0, [16, 0]), (1, [16, 0])], (0, [0, 16]),
                   [dict(bid=3005, count=8, need=[4, 0], make=[0, 2], tier=4)],
                   ['Iron Ingot', 'Iron Plating'], 30, [2014], D=16, arm_cap=3 if arms else None, free=free)


def heatsink16(arms=True, free=False):
    """heat sink T4: 16 workshops (5 copper ingot -> 1 heat sink, 30 crafts/min each). 5 ingot belts, 1 out."""
    return Factory([(r, [16, 0]) for r in range(5)], (0, [0, 16]),
                   [dict(bid=3005, count=16, need=[5, 0], make=[0, 1], tier=4)],
                   ['Copper Ingot', 'Heat Sink'], 30, [2013], D=16, arm_cap=3 if arms else None, free=free)


def ingot16(free='each'):
    """iron ingot T4: 16 furnaces (1 ore -> 1 ingot, 90/min each). 3 ore belts in, 3 ingot belts out. 1 unit = 30/min."""
    return Factory([(r, [16, 0]) for r in range(3)], [(r, [0, 16]) for r in range(3)],
                   [dict(bid=3006, count=16, need=[3, 0], make=[0, 3], tier=4)],
                   ['Iron Ore', 'Iron Ingot'], 30, [2004], D=16, arm_cap=3, free=free)


def ore_plating(half=False, free='each'):
    """iron ore -> ingot -> plating in one module, T4. Whole: 16 furnaces + 12 workshops, 3 ore belts in, 1.5 plating
    belts out (16 + 8 units). Half: 8 furnaces + 6 workshops, 1.5 ore belts in (16 + 8), 12 units of plating out."""
    k = 1 if half else 2
    ins = [(0, [16, 0, 0]), (1, [8, 0, 0])] if half else [(r, [16, 0, 0]) for r in range(3)]
    outs = [(0, [0, 0, 12])] if half else [(0, [0, 0, 16]), (1, [0, 0, 8])]
    return Factory(ins, outs, [dict(bid=3006, count=8 * k, need=[3, 0, 0], make=[0, 3, 0], tier=4),
                               dict(bid=3005, count=6 * k, need=[0, 4, 0], make=[0, 0, 2], tier=4)],
                   ['Iron Ore', 'Iron Ingot', 'Iron Plating'], 30, [2004, 2014], D=16, arm_cap=3, free=free)


def show(grid, machines, W, H):
    """text picture: belt codes, MM for machine tiles"""
    from bmsolver.model import MACHINES
    cells = {}
    for ti, a, o in machines:
        cells[tuple(a)] = 'M%d' % o
    return '\n'.join(''.join(cells.get((x, y)) or grid.get((x, y)) or '..' for x in range(W)) for y in range(H))


def ore_plating2(free='each'):
    """2 ore belts -> 1 plating belt, T4: 960 ore/min needs 10 2/3 furnaces, so 11 furnaces that may run underfilled
    (one at 2/3) and 8 workshops."""
    return Factory([(0, [16, 0, 0]), (1, [16, 0, 0])], [(0, [0, 0, 16])],
                   [dict(bid=3006, count=11, need=[3, 0, 0], make=[0, 3, 0], tier=4, partial=True),
                    dict(bid=3005, count=8, need=[0, 4, 0], make=[0, 0, 2], tier=4)],
                   ['Iron Ore', 'Iron Ingot', 'Iron Plating'], 30, [2004, 2014], D=16, arm_cap=3, free=free)


CASES = dict(ore_plating2=ore_plating2, plating8=plating8, plating8_noarms=lambda: plating8(False), heatsink16=heatsink16,
             plating8_free=lambda: plating8(free=True), heatsink16_free=lambda: heatsink16(free=True),
             plating8_each=lambda: plating8(free='each'), plating8_edges=lambda: plating8(free='edges'),
             heatsink16_each=lambda: heatsink16(free='each'), ingot16=ingot16,
             ore_plating=ore_plating, ore_plating_half=lambda: ore_plating(True))

if __name__ == '__main__':
    name, sizes, timeout = sys.argv[1], sys.argv[2], float(sys.argv[3])
    polish = float(sys.argv[4]) if len(sys.argv) > 4 else 0     # seconds to then minimise pieces from the find
    f = CASES[name]()
    for s in sizes.split(','):
        W, H = map(int, s.split('x'))
        win = f.window(W, H)
        t0 = time.time()
        r = model.solve(win, timeout=timeout, workers=4)
        if polish and 'grid' in r:
            r2 = model.solve(win, timeout=polish, workers=4, objective=True, hint=r)
            if 'grid' in r2 and r2['pieces'] <= r['pieces']:
                r2['status'] = r['status'] + '+' + r2['status']
                r = r2
        ok = ''
        if 'grid' in r:
            ok = bench.verify_factory(r['grid'], win, r.get('machines'), r.get('options'), r.get('filters'), r.get('fill'))
        print('%s %dx%d %s %.1fs verified=%s pieces=%s options=%s' % (name, W, H, r['status'], time.time() - t0, ok,
              r.get('pieces'), r.get('options')), flush=True)
        if 'grid' in r:
            import json
            os.makedirs(os.path.expanduser('~/data/modules'), exist_ok=True)
            rec = dict(case=name, W=W, H=H, verified=bool(ok), time=round(time.time() - t0, 1), options=r.get('options'),
                       fill={'%d,%d' % tuple(k): v for k, v in (r.get('fill') or {}).items()},
                       grid={'%d,%d' % p: c for p, c in r['grid'].items() if c},
                       machines=[[ti, list(a), o] for ti, a, o in r['machines']],
                       filters={'%d,%d' % k if isinstance(k, tuple) else str(k): v for k, v in (r.get('filters') or {}).items()})
            with open(os.path.expanduser('~/data/modules/%s.jsonl' % name), 'a') as fh:
                fh.write(json.dumps(rec, default=str) + '\n')
            print(show(r['grid'], r['machines'], W, H), flush=True)
