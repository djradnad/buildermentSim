"""Mass-produce solved balancer layouts with the staged solver (training data for a learned guide).

Each line of the output (JSON) is one solve: n, W, H, input/output first lanes, blocked tiles, status, time,
and the grid (rows of 2-char codes) when a layout was found. INFEASIBLE answers are kept too (they are labels).

    python tools/data/gen.py --out ~/data/bal.jsonl --count 1000 --timeout 120 --seed 1
"""
import argparse
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from bmsolver import radix, spec as S, bench  # noqa: E402

SIZES = {2: (3, 7), 3: (5, 10), 4: (6, 12), 6: (9, 15)}


def one(rng, engine, timeout):
    n = rng.choice([2, 2, 3, 3, 3, 4, 4, 6])
    lo, hi = SIZES[n]
    H = rng.randint(max(n, 3), max(n, 3) + 3)
    W = rng.randint(lo, hi)
    ia, oa = rng.randint(0, H - n), rng.randint(0, H - n)
    win = S.Spec([S.Group('E', n, at=ia)], [S.Group('W', n, at=oa)]).window(W, H)
    nb = rng.choice([0, 0, 1, 2, 3])
    blocked = []
    for _ in range(nb):
        p = (rng.randint(1, W - 2), rng.randint(0, H - 1))
        blocked.append(p)
    win.blocked = set(blocked)
    ar = rng.choice(radix.factorisations(n))
    r = radix.solve(win, ar, engine, timeout)
    rec = dict(n=n, W=W, H=H, at=[ia, oa], blocked=blocked, arity=ar, status=r['status'], time=round(r['time'], 2))
    if 'grid' in r:
        g = r['grid']
        rec['ok'] = bool(bench.verify(g, win, r.get('options')))
        rec['grid'] = [''.join((g.get((x, y)) or '..') for x in range(W)) for y in range(H)]
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--count', type=int, default=100)
    ap.add_argument('--timeout', type=float, default=120)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--engine', default='~/solvers/cadical')
    a = ap.parse_args()
    rng = random.Random(a.seed)
    for _ in range(a.count):
        rec = one(rng, os.path.expanduser(a.engine), a.timeout)
        with open(os.path.expanduser(a.out), 'a') as fh:
            fh.write(json.dumps(rec) + '\n')
        print(rec['n'], rec['W'], rec['H'], rec['status'], rec['time'], rec.get('ok', ''), flush=True)


if __name__ == '__main__':
    main()
