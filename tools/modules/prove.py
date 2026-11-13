"""Smallest whole-belt recipe modules, with proofs.

A whole-belt module takes a whole number of full input belts and gives a whole number of full output belts (the
machine count is fixed by the recipe's ratio, see ratios.py); every belt inside dead-ends in the module. Ports may sit
on any edge tile. For each case the boxes are walked most square first (longest side s = 2, 3, ...; for each s the
heights 1..s, smallest area first) until one is found. Every box gets a verdict: FOUND (layout, checked in the
game-exact sim), INFEASIBLE (proven: no layout exists in that box) or UNKNOWN (time ran out, nothing proven).
Every box with a longest side below s lies inside the (s-1) x (s-1) square, so "s x h found, everything before it
INFEASIBLE" proves the most square and then smallest module. Verdicts are kept in ~/data/modules/prove_<case>.status,
so a stopped run resumes.

    python tools/modules/prove.py sand4,silicon8 1800        # cases, seconds per box
    python tools/modules/prove.py table                      # markdown table of all verdicts so far
"""
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from bmsolver import bench, model  # noqa: E402
from bmsolver.bench import Factory  # noqa: E402

OUT = os.path.expanduser('~/data/modules')
U = 16                                                       # a full belt in units of 30/min


def one(bid, rid, count, need, make, belts_in, belts_out, items):
    """one recipe with one ingredient: count machines, need/make in units of 30/min per machine"""
    return (Factory([(r, [U, 0]) for r in range(belts_in)], [(r, [0, U]) for r in range(belts_out)],
                    [dict(bid=bid, count=count, need=[need, 0], make=[0, make], tier=4)], items, 30, [rid], D=U,
                    arm_cap=3, free='edges'), count, belts_in + belts_out)


CASES = {   # name: (Factory, machine tiles, ports)
    'sand4': lambda: one(3005, 2006, 4, 4, 4, 1, 1, ['Stone', 'Sand']),
    'silicon8': lambda: one(3006, 2007, 8, 4, 2, 2, 1, ['Sand', 'Silicon']),
    'plating8': lambda: one(3005, 2014, 8, 4, 2, 2, 1, ['Iron Ingot', 'Iron Plating']),
    'lens8': lambda: one(3005, 2012, 8, 6, 2, 3, 1, ['Glass', 'Condenser Lens']),
    'glass16': lambda: one(3006, 2008, 16, 4, 1, 4, 1, ['Sand', 'Glass']),
    'ingot16': lambda: one(3006, 2004, 16, 3, 3, 3, 3, ['Iron Ore', 'Iron Ingot']),
    'heatsink16': lambda: one(3005, 2013, 16, 5, 1, 5, 1, ['Copper Ingot', 'Heat Sink']),
}


def status(name):
    p = os.path.join(OUT, 'prove_%s.status' % name)
    return {l.split()[0]: l.split()[1:] for l in open(p)} if os.path.exists(p) else {}


def box(name, f, w, h, timeout, done):
    """verdict for one box (from the status file when settled there) -> True when a layout exists"""
    key = '%dx%d' % (w, h)
    if key in done and (done[key][0] != 'UNKNOWN' or float(done[key][1]) >= timeout):
        return done[key][0] in ('OPTIMAL', 'FEASIBLE')
    win = f.window(w, h)
    t0 = time.time()
    r = model.solve(win, timeout=timeout, workers=4)
    ok = ''
    if 'grid' in r:
        ok = bench.verify_factory(r['grid'], win, r.get('machines'), r.get('options'), r.get('filters'), r.get('fill'))
        rec = dict(case=name, W=w, H=h, verified=bool(ok), time=round(time.time() - t0, 1), options=r.get('options'),
                   fill={'%d,%d' % tuple(k): v for k, v in (r.get('fill') or {}).items()},
                   grid={'%d,%d' % p: c for p, c in r['grid'].items() if c},
                   machines=[[ti, list(a), o] for ti, a, o in r['machines']],
                   filters={'%d,%d' % k if isinstance(k, tuple) else str(k): v for k, v in (r.get('filters') or {}).items()})
        with open(os.path.join(OUT, 'prove_%s.jsonl' % name), 'a') as fh:
            fh.write(json.dumps(rec, default=str) + '\n')
    print('%s %s %s %.0fs verified=%s pieces=%s' % (name, key, r['status'], time.time() - t0, ok, r.get('pieces')), flush=True)
    with open(os.path.join(OUT, 'prove_%s.status' % name), 'a') as fh:
        fh.write('%s %s %.0f %s\n' % (key, r['status'], max(time.time() - t0, timeout if r['status'] == 'UNKNOWN' else 0), bool(ok)))
    done[key] = [r['status'], '%.0f' % timeout, str(bool(ok))]
    return 'grid' in r


def run(name, timeout):
    f, tiles, ports = CASES[name]()
    low = tiles + ports                                      # every machine tile and one belt tile per port
    done = status(name)
    s = max(2, math.ceil(math.sqrt(low)))
    best = None
    while best is None:                                      # most square: longest side s, then the smallest height
        for h in range(1, s + 1):
            if s * h >= low and box(name, f, s, h, timeout, done):
                best = (s, h)
                break
        else:
            s += 1
    for h in range(best[1] - 1, 0, -1):                      # flatter boxes: the narrowest for every smaller height
        for w in range(max(best[0] + 1, math.ceil(low / h)), 4 * best[0] + 1):
            if box(name, f, w, h, timeout, done):
                break


def table():
    print('| Module | Machines | Belts in / out | Smallest found | Proven impossible | Not decided |')
    print('|---|---|---|---|---|---|')
    for name in CASES:
        st = status(name)
        if not st:
            continue
        f, tiles, ports = CASES[name]()
        got = [k for k, v in st.items() if v[0] in ('OPTIMAL', 'FEASIBLE')]
        no = [k for k, v in st.items() if v[0] == 'INFEASIBLE']
        un = [k for k, v in st.items() if v[0] == 'UNKNOWN']
        print('| %s | %d | %d / %d | %s | %s | %s |' % (name, tiles, len(f.ins), len(f.out), ', '.join(got) or '-',
                                                       ', '.join(no) or '-', ', '.join(un) or '-'))


if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    if sys.argv[1] == 'table':
        table()
    else:
        for name in sys.argv[1].split(','):
            run(name, float(sys.argv[2]))
