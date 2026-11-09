"""Finer cube and conquer: port-position cubes, each split further on layout variables chosen by lookahead.

cnf.prove splits a window only by port positions (5 cubes for a 3:3), so on 4 cores the wall time is the slowest
cube. Here every port cube is split again on the layout variable (a piece literal) whose two branches imply the
most other literals (march-style lookahead: score = (implied if true + 1) * (implied if false + 1), via CaDiCaL's
propagate). Failed literals found on the way are fixed. Sub-cubes are written as DIMACS with the cube literals as
unit clauses and solved by an external binary, procs at a time, slowest-looking first.

    python -m bmsolver.cube2 --case bal3_7x5 --depth 3 --binary AE_kissat2025_bump
"""
import argparse
import concurrent.futures as cf
import os
import subprocess
import tempfile
import time

from . import bench, cnf, guide


def lookahead_split(s, cand, base, limit=None):
    """best variable to split on under assumptions base; returns (var, score, fixed literals) or (None, ...)"""
    best, bscore, fixed = None, -1, []
    for v in cand[:limit] if limit else cand:
        ok1, imp1 = s.propagate(assumptions=base + [v])
        ok0, imp0 = s.propagate(assumptions=base + [-v])
        if not ok1 and not ok0:
            return None, -2, None           # the cube itself is refuted by propagation
        if not ok1:
            fixed.append(-v)
            continue
        if not ok0:
            fixed.append(v)
            continue
        sc = (len(imp1) - len(base) + 1) * (len(imp0) - len(base) + 1)
        if sc > bscore:
            best, bscore = v, sc
    return best, bscore, fixed


def make_cubes(f, layout, depth, log=None, kinds='E'):
    """split one CNF into up to 2**depth sub-cubes (lists of literals); refuted branches are dropped"""
    from pysat.solvers import Cadical195
    s = Cadical195(bootstrap_with=f.clauses)
    cand = [v for v, k, o, x, y in layout if k in kinds]
    out = []

    def rec(base, d):
        ok, imp = s.propagate(assumptions=base)
        if not ok:
            return
        if d == 0:
            out.append(base)
            return
        free = [v for v in cand if v not in set(abs(l) for l in imp)]
        v, sc, fixed = lookahead_split(s, free, base)
        if v is None and sc == -2:
            return
        base = base + (fixed or [])
        if v is None:
            out.append(base)
            return
        rec(base + [v], d - 1)
        rec(base + [-v], d - 1)
    rec([], depth)
    s.delete()
    return out


def write_cube(f, lits, path):
    with open(path, 'w') as fh:
        fh.write('p cnf %d %d\n' % (f.nv, len(f.clauses) + len(lits)))
        for c in f.clauses:
            fh.write(' '.join(map(str, c)) + ' 0\n')
        for l in lits:
            fh.write('%d 0\n' % l)


def prove(win, binary, depth=3, procs=4, timeout=None, lemmas='local', log=print):
    t0 = time.time()
    d = tempfile.mkdtemp()
    jobs = []
    wins = cnf.cubes(win, mirror=True)
    for k, w in enumerate(wins):
        mod, tr, layout = guide.build(w, lemmas)
        subs = make_cubes(tr.f, layout, depth)
        for j, lits in enumerate(subs):
            path = os.path.join(d, 'c%d_%d.cnf' % (k, j))
            write_cube(tr.f, lits, path)
            jobs.append((k, j, path, (mod, tr)))
        if log:
            log('  port cube %d: %d sub-cubes (%.1fs)' % (k, len(subs), time.time() - t0))
    tsplit = time.time() - t0
    res = dict(status='UNSAT', cubes=len(jobs), split_time=tsplit)
    running = []

    def one(job):
        k, j, path, _ = job
        p = subprocess.Popen([binary, path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        running.append(p)
        try:
            out = p.communicate(timeout=timeout)[0]
        except subprocess.TimeoutExpired:
            p.kill()
            return job, None, None
        return job, *cnf._parse(out or '')
    times = []
    with cf.ThreadPoolExecutor(procs) as ex:
        futs = [ex.submit(one, j) for j in jobs]
        for fu in cf.as_completed(futs):
            job, sat, vals = fu.result()
            times.append(time.time() - t0)
            if sat:
                mod, tr = job[3]
                res.update(status='SAT', grid=mod.grid(cnf._Values(tr, vals)), window=wins[job[0]])
                for p in running:
                    if p.poll() is None:
                        p.kill()
                for x in futs:
                    x.cancel()
                break
            if sat is None:
                res['status'] = 'UNKNOWN'
    for p in running:
        if p.poll() is None:
            p.kill()
    res['time'] = time.time() - t0
    return res


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--case', required=True)
    ap.add_argument('--depth', type=int, default=3)
    ap.add_argument('--binary', default='AE_kissat2025_bump')
    ap.add_argument('--procs', type=int, default=4)
    ap.add_argument('--timeout', type=float, default=None)
    a = ap.parse_args(argv)
    C = {c[0]: c for c in bench.CASES}
    for name in a.case.split(','):
        _, mk, W, H, exp, tier = C[name]
        win = mk().window(W, H)
        r = prove(win, bench._bin(a.binary), a.depth, a.procs, a.timeout)
        ok = bench.verify(r['grid'], r['window']) if r['status'] == 'SAT' else ''
        print('%-14s cube2 d=%d %-8s (expect %s) %8.1fs split %.1fs cubes=%d verified=%s' % (
            name, a.depth, r['status'], exp, r['time'], r['split_time'], r['cubes'], ok), flush=True)


if __name__ == '__main__':
    main()
