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


def cached_prove(win, box, a):
    """cnf.prove, but every cube verdict is saved (~/data/cubes/<box>.json), so a box bigger than one run resumes.
    Mirror images are dropped (port order is free in an N:N balancer). Timed-out cubes are retried next run."""
    import concurrent.futures as cf
    from bmsolver import model, lazy
    path = os.path.expanduser('~/data/cubes/%s.json' % box.replace(':', '_'))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    done = json.load(open(path)) if os.path.exists(path) else {}
    cs = cnf.cubes(win, mirror=True)
    todo = [k for k in range(len(cs)) if done.get(str(k)) != 'INFEASIBLE']
    running, res = [], dict(status='INFEASIBLE', cubes=len(cs))

    def one(k):
        if getattr(a, 'cuts', None):
            cs[k].cuts = a.cuts
        mod = model.Model(cs[k])
        lazy.add_flow_lemmas(mod, cs[k])
        return k, cnf.solve(mod, engine=os.path.expanduser(a.engine), timeout=a.timeout, procs=running)
    with cf.ThreadPoolExecutor(a.procs) as ex:
        futs = [ex.submit(one, k) for k in todo]
        for f in cf.as_completed(futs):
            k, r = f.result()
            done[str(k)] = r['status']
            json.dump(done, open(path, 'w'))
            if r['status'] == 'FEASIBLE':
                res.update(status='FEASIBLE', grid=r['grid'], window=cs[k])
                for g in futs:
                    g.cancel()
                for p in running:
                    if p.poll() is None:
                        p.kill()
                break
            if r['status'] != 'INFEASIBLE':
                res['status'] = 'UNKNOWN'
    return res


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
            r = cached_prove(win, box, a)
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
    ap.add_argument('--cuts', default=None, help="redundant line rules per cube: flow, card or both")
    a = ap.parse_args()
    for n in map(int, a.n.split(',')):
        for mode in a.modes.split(','):
            sym = mode == 'sym'
            skip = set()
            while True:
                pr, stp = candidates(n, sym, a.cap, skip)
                cs = sorted(pr + stp, key=lambda b: (max(b), b[0] * b[1], -b[1]))
                st = status(n, sym, a.cap)
                if not any(v in 'Ff' for v in st.values()):
                    # nothing found yet for this ratio: most square first, k x k upward (sym: k x k or k x k+1)
                    sq = []
                    for k in range(n, a.cap + 1):
                        for b in ((k, k), (k, k + 1)):
                            if parity_ok(n, b[1], sym) and st.get(b, '.') not in 'Pp?' and b not in skip:
                                sq.append(b)
                    cs = sq[:a.procs if sym else 1]
                if a.dry:
                    print(n, mode, 'probes', pr, 'steps', stp)
                    break
                if not cs:
                    print('%d:%d %s done' % (n, n, mode), flush=True)
                    break
                if not sym:
                    batch = [cs[0]]
                    recs = [run_box(n, *cs[0], sym, a)]
                else:
                    # a symmetric box has fixed ports, so one cube: run several boxes side by side instead
                    import concurrent.futures as cf
                    import copy
                    b = copy.copy(a)
                    b.procs = 1
                    batch = cs[:a.procs]
                    with cf.ProcessPoolExecutor(len(batch)) as ex:
                        recs = list(ex.map(run_box, *zip(*[(n, W, H, sym, b) for W, H in batch])))
                for (W, H), rec in zip(batch, recs):
                    if rec['status'] not in ('FEASIBLE', 'INFEASIBLE'):
                        skip.add((W, H))


if __name__ == '__main__':
    main()
