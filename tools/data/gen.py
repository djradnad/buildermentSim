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

SIZES = {2: (3, 7), 3: (8, 11), 4: (8, 12)}   # widths where layouts usually fit (more found examples)


def one(rng, engine, timeout):
    n = rng.choice([2, 3, 3, 3, 4, 4])
    lo, hi = SIZES[n]
    H = rng.randint(max(n, 3) + (n > 2), max(n, 3) + 3)
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


# smallest known asym boxes (W, H) per N: a sweep solves every height in range at widths from just below the
# boundary to a little above it, every port placement (sym too when it fits), which gives both found and
# ruled-out examples right at the edge of what fits
BOUNDS = {2: [(3, 3), (2, 4)], 3: [(8, 5), (5, 9)], 4: [(7, 7), (9, 6), (6, 9), (11, 5)]}


def sweep_jobs(ns, sym_only=False):
    jobs = []
    for n in ns:
        for H in range(max(n, 3), max(n, 3) + 6 + (4 if sym_only else 0)):
            wmin = min([w for w, h in BOUNDS[n] if h <= H] or [12])
            for W in range(max(2, wmin - 1), wmin + (5 if sym_only else 3)):
                for ia in range(H - n + 1):
                    for oa in range(H - n + 1):
                        if not sym_only:
                            jobs.append((n, W, H, ia, oa, False))
                if (H - n) % 2 == 0:
                    jobs.append((n, W, H, (H - n) // 2, (H - n) // 2, True))
                    if n % 2 and not sym_only:
                        jobs.append((n, W, H, (H - n) // 2, (H - n) // 2, 'quasi'))
    return jobs


def run_job(job, engine, timeout, variants=1, closed=False):
    """solve one box; with variants > 1, ban the set of tiles each found layout uses and solve again, so the
    data holds several different layouts of the same box"""
    from bmsolver import cnf
    n, W, H, ia, oa, sym = job
    win = S.Spec([S.Group('E', n, at=ia)], [S.Group('W', n, at=oa)]).window(W, H, symmetry='y' if sym else None, quasi=1 if sym == 'quasi' else 0)
    if win is None:
        return []
    win.closed_edges = closed          # no splitter face out of a non-port edge (john's clean class)
    ar = radix.factorisations(n)[0]
    mod = radix.build(win, ar, pass_splitters=False)
    out = []
    for v in range(variants):
        r = cnf.solve(mod, engine=engine, timeout=timeout)
        rec = dict(n=n, W=W, H=H, at=[ia, oa], sym=sym, closed=closed, blocked=[], arity=ar, variant=v, status=r['status'],
                   time=round(r['time'], 2))
        out.append(rec)
        if 'grid' not in r:
            break
        g = r['grid']
        rec['ok'] = bool(bench.verify(g, win, r.get('options')))
        rec['grid'] = [''.join((g.get((x, y)) or '..') for x in range(W)) for y in range(H)]
        # the next layout must use a different set of tiles (not just swap a belt for another piece)
        mod.m.AddBoolOr([mod.X[p, 'E'] if c else mod.X[p, 'E'].Not() for p, c in g.items() if (p, 'E') in mod.X])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--count', type=int, default=100)
    ap.add_argument('--timeout', type=float, default=120)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--engine', default='~/solvers/cadical')
    ap.add_argument('--sweep', default=None, help='e.g. 2,3,4: systematic sizes instead of random')
    ap.add_argument('--variants', type=int, default=1, help='different layouts per box (sweep mode)')
    ap.add_argument('--closed', action='store_true', help='no splitter facing a non-port edge')
    ap.add_argument('--only-quasi', action='store_true')
    ap.add_argument('--sym-only', action='store_true', help='symmetric jobs only, heights up to N+9')
    ap.add_argument('--part', default='0/1', help='k/m: this process takes every m-th job from k')
    a = ap.parse_args()
    if a.sweep:
        k, mm = map(int, a.part.split('/'))
        jobs = sweep_jobs([int(x) for x in a.sweep.split(',')], a.sym_only)
        if a.only_quasi:
            jobs = [j for j in jobs if j[5] == 'quasi']
        jobs = jobs[k::mm]
        print(len(jobs), 'jobs', flush=True)
        for job in jobs:
            for rec in run_job(job, os.path.expanduser(a.engine), a.timeout, a.variants, a.closed):
                with open(os.path.expanduser(a.out), 'a') as fh:
                    fh.write(json.dumps(rec) + '\n')
                print(job, rec['variant'], rec['status'], rec['time'], rec.get('ok', ''), flush=True)
        return
    rng = random.Random(a.seed)
    for _ in range(a.count):
        rec = one(rng, os.path.expanduser(a.engine), a.timeout)
        with open(os.path.expanduser(a.out), 'a') as fh:
            fh.write(json.dumps(rec) + '\n')
        print(rec['n'], rec['W'], rec['H'], rec['status'], rec['time'], rec.get('ok', ''), flush=True)


if __name__ == '__main__':
    main()
