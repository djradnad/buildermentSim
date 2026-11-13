"""Fill the N:N coverage table box by box, the way john asked (2026-11-13): for every ratio, asym then sym, walk each
row (height) and each column (width) down from the smallest found box until the next smaller box is proven
impossible. Every box goes to the complete flow model (cnf.prove, cube and conquer over port placements), which
both finds layouts and proves impossibility. When N is 2^a 3^b the fast uniform model gets a short first try,
since a found layout needs no proof.

Boxes are taken most square first (smallest longest side, then area). A row or column with no found box yet gets
one test at the cap size, so lines like 3:3 at width 3 get an answer up to the cap. Proofs from the flow model hold
up to its unit D (recorded per box): 5:5 uses D = 60.

    python tools/data/sweep.py --n 2,3,4 --cap 16 --timeout 900 --procs 4
Results append to ~/data/flowcheck.jsonl (same format as flowcheck.py), so coverage.py shows them.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.dirname(__file__))
from bmsolver import spec as S, cnf, bench, uniform  # noqa: E402
import coverage as C  # noqa: E402


def status(n, sym, cap):
    """marks for every box up to cap, with containment applied over the whole range"""
    allb = C.load()
    st = {k: C.mark(v) for k, v in allb[n, sym].items()}
    # a sym layout is also an asym one; an asym proof covers sym too
    other = {k: C.mark(v) for k, v in allb[n, not sym].items()}
    for k, v in other.items():
        if v == ('F' if not sym else 'P') and st.get(k) not in ('F', 'P'):
            st[k] = v
    F = [k for k, v in st.items() if v == 'F']
    P = [k for k, v in st.items() if v == 'P']
    step = 2 if sym else 1
    for W in range(1, cap + 1):
        for H in range(1, cap + 1):
            if st.get((W, H)) in ('F', 'P'):
                continue
            if any(W >= a and H >= b and (H - b) % step == 0 for a, b in F):
                st[W, H] = 'f'
            elif any(W <= a and H <= b and (H - b) % step == 0 for a, b in P):
                st[W, H] = 'p'
    return st


def parity_ok(n, H, sym):
    return not sym or H % 2 == n % 2


def candidates(n, sym, cap, skip):
    """(probes, steps): probes test a line with nothing found at the cap; steps test one below the smallest find"""
    st = status(n, sym, cap)
    get = lambda W, H: st.get((W, H), '.')
    step = 2 if sym else 1
    hs = [h for h in range(1, cap + 1) if parity_ok(n, h, sym)]
    probes, steps = set(), set()
    for H in hs:                                # rows: smallest width at this height
        fs = [W for W in range(1, cap + 1) if get(W, H) in 'Ff']
        (steps if fs else probes).add(((min(fs) - 1) if fs else cap, H))
    for W in range(1, cap + 1):                 # columns: smallest height at this width
        fs = [H for H in hs if get(W, H) in 'Ff']
        (steps if fs else probes).add((W, (min(fs) - step) if fs else hs[-1]))
    ok = lambda b: b[0] >= 1 and b[1] >= 1 and get(*b) not in 'PpFf?' and b not in skip
    key = lambda b: (max(b), b[0] * b[1], -b[1])
    return sorted(filter(ok, probes), key=key), sorted(filter(ok, steps), key=key)


def run_box(n, W, H, sym, a):
    win = S.Spec([S.Group('E', n)], [S.Group('W', n)]).window(W, H, symmetry='y' if sym else None)
    box = '%d:%dx%d%s' % (n, W, H, ':sym' if sym else '')
    t = time.time()
    rec = dict(box=box)
    if win is None:
        rec.update(status='INFEASIBLE', note='ports do not fit')
    else:
        rec['D'] = win.D
        r = None
        if uniform.levels(n) is not None and a.fast:
            try:
                u = uniform.solve(win, n, engine=os.path.expanduser(a.engine), timeout=a.fast)
                if 'grid' in u and bench.verify(u['grid'], win, u.get('options')):
                    r = dict(status='FEASIBLE', grid=u['grid'], window=win, how='uniform')
            except Exception as e:      # the fast try is only a shortcut
                rec['fast_error'] = str(e)[:200]
        if r is None:
            r = cnf.prove(win, os.path.expanduser(a.engine), procs=a.procs, timeout=a.timeout, log=None, lemmas=True)
            r['how'] = 'flow'
        rec.update(status=r['status'], cubes=r.get('cubes'), how=r['how'])
        if 'grid' in r:
            g = r['grid']
            rec['grid'] = [''.join(g.get((x, y)) or '..' for x in range(W)) for y in range(H)]
            rec['ok'] = bool(bench.verify(g, r['window']))
    rec['time'] = round(time.time() - t, 1)
    with open(os.path.expanduser(a.out), 'a') as fh:
        fh.write(json.dumps(rec) + '\n')
    print(box, rec['status'], rec.get('how', ''), rec['time'], rec.get('ok', ''), flush=True)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', default='2,3,4')
    ap.add_argument('--modes', default='asym,sym')
    ap.add_argument('--cap', type=int, default=16)
    ap.add_argument('--timeout', type=float, default=900, help='per cube')
    ap.add_argument('--fast', type=float, default=60, help='uniform first try, seconds (0 = off)')
    ap.add_argument('--procs', type=int, default=4)
    ap.add_argument('--engine', default='~/solvers/kissat')
    ap.add_argument('--out', default='~/data/flowcheck.jsonl')
    ap.add_argument('--dry', action='store_true')
    a = ap.parse_args()
    for n in map(int, a.n.split(',')):
        for mode in a.modes.split(','):
            sym = mode == 'sym'
            skip = set()
            while True:
                pr, stp = candidates(n, sym, a.cap, skip)
                cs = pr + stp
                if a.dry:
                    print(n, mode, 'probes', pr, 'steps', stp)
                    break
                if not cs:
                    print('%d:%d %s done' % (n, n, mode), flush=True)
                    break
                W, H = cs[0]
                rec = run_box(n, W, H, sym, a)
                if rec['status'] not in ('FEASIBLE', 'INFEASIBLE'):
                    skip.add((W, H))


if __name__ == '__main__':
    main()
