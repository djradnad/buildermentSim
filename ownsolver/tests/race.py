"""Race: own solver vs CP-SAT (1 worker) vs kissat (CNF of the same model via bmsolver/cnf.py) on spec files.

Usage: python3 race.py [--timeout S] [--jobs 2] [--solvers own,cpsat,kissat] [--out race.jsonl] SPEC.json ...
Each (spec, solver) run is a separate process using 1 core; at most --jobs at a time. Every FOUND grid from any
solver is re-checked exactly (common.exact_check, the check.py flow engine with exact fractions; check.py itself
too for equal-share balancers). Results go to --out (jsonl) and a table is printed (markdown).
Statuses: FOUND / INFEASIBLE / UNKNOWN (timeout) / ERROR. A FOUND that fails the exact check is shown as BADFIND.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from common import ROOT, from_json, exact_check, check_py, parse_grid  # noqa: E402

BMOWN = os.environ.get('BMOWN', os.path.join(ROOT, 'ownsolver', 'bmown'))
KISSAT = os.environ.get('KISSAT', '/root/evo/v2_ts/build/kissat')


def run_own(path, timeout):
    t = time.time()
    try:
        extra = os.environ.get('OWN_ARGS', '').split()
        p = subprocess.run([BMOWN, path, '--timeout', str(timeout)] + extra, capture_output=True, text=True,
                           timeout=timeout + 30)
    except subprocess.TimeoutExpired:
        return dict(status='UNKNOWN', time=time.time() - t, note='killed')
    wall = time.time() - t
    lines = p.stdout.strip().split('\n')
    if not lines or not lines[0].startswith('STATUS'):
        return dict(status='ERROR', time=wall, note=(p.stdout[-300:] + p.stderr[-300:]))
    st = lines[0].split()[1]
    res = dict(status=st, time=wall)
    for l in lines:
        if l.startswith('TIME'):
            res['stats'] = l
    if st == 'FOUND':
        js = json.load(open(path))
        res['grid'] = lines[1:1 + js['H']]
    return res


def run_cpsat(path, timeout):
    from bmsolver import model
    win = from_json(json.load(open(path)))
    r = model.solve(win, timeout=timeout, workers=1)
    st = {'OPTIMAL': 'FOUND', 'FEASIBLE': 'FOUND'}.get(r['status'], r['status'])
    res = dict(status=st, time=r['time'])
    if 'grid' in r:
        res['grid'] = [''.join(r['grid'].get((x, y)) or '..' for x in range(win.W)) for y in range(win.H)]
    return res


def run_kissat(path, timeout):
    from bmsolver import model, cnf
    win = from_json(json.load(open(path)))
    t = time.time()
    mod = model.Model(win)
    r = cnf.solve(mod, engine=KISSAT, timeout=timeout)
    res = dict(status={'FEASIBLE': 'FOUND'}.get(r['status'], r['status']), time=time.time() - t,
               solve_time=r['time'], build=r['build'])
    if 'grid' in r:
        res['grid'] = [''.join(r['grid'].get((x, y)) or '..' for x in range(win.W)) for y in range(win.H)]
    return res


def one(solver, path, timeout):
    """Run in a child process: print one JSON line."""
    fn = dict(own=run_own, cpsat=run_cpsat, kissat=run_kissat)[solver]
    try:
        res = fn(path, timeout)
    except Exception as e:
        res = dict(status='ERROR', time=0, note=repr(e)[:300])
    print(json.dumps(res))


def verify(path, res):
    if res.get('status') != 'FOUND':
        return res
    win = from_json(json.load(open(path)))
    grid = parse_grid('\n'.join(res['grid']))
    ok, why = exact_check(win, grid)
    res['exact'], res['exact_why'] = ok, why
    if ok and all(sum(v) > 0 for (p, d), (k, v) in win.ports.items() if k == 'out'):
        vecs = [tuple(v) for (p, d), (k, v) in win.ports.items() if k == 'out']
        if len(set(vecs)) == 1:                       # equal shares: also run check.py itself
            try:
                ok2, _ = check_py(win, grid)
                res['check_py'] = ok2
            except Exception as e:
                res['check_py'] = 'err %r' % e
    if not ok or res.get('check_py') is False:
        res['status'] = 'BADFIND'
    return res


def launch(solver, path, timeout):
    env = dict(os.environ, PYTHONPATH=ROOT)
    t = time.time()
    try:
        p = subprocess.run([sys.executable, __file__, '--one', solver, path, '--timeout', str(timeout)],
                           capture_output=True, text=True, timeout=timeout + 120, env=env)
        res = json.loads(p.stdout.strip().split('\n')[-1])
    except subprocess.TimeoutExpired:
        res = dict(status='UNKNOWN', time=time.time() - t, note='killed')
    except Exception as e:
        res = dict(status='ERROR', time=time.time() - t, note=repr(e)[:300])
    res = verify(path, res)
    res.update(solver=solver, spec=os.path.basename(path), name=json.load(open(path)).get('name', ''),
               timeout=timeout)
    return res


def table(rows, solvers):
    by = {}
    for r in rows:
        by.setdefault(r['spec'], {})[r['solver']] = r
    out = ['| case | ' + ' | '.join(solvers) + ' |', '|---|' + '---|' * len(solvers)]
    for spec, d in by.items():
        name = next(iter(d.values()))['name'] or spec
        cells = []
        for s in solvers:
            r = d.get(s)
            cells.append('-' if r is None else '%s %.1fs' % (r['status'], r['time']))
        out.append('| %s | %s |' % (name, ' | '.join(cells)))
    return '\n'.join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('specs', nargs='*')
    ap.add_argument('--timeout', type=float, default=60)
    ap.add_argument('--jobs', type=int, default=2)
    ap.add_argument('--solvers', default='own,cpsat,kissat')
    ap.add_argument('--out', default=os.path.join(HERE, 'race.jsonl'))
    ap.add_argument('--one', default=None)
    a = ap.parse_args()
    if a.one:
        return one(a.one, a.specs[0], a.timeout)
    solvers = a.solvers.split(',')
    jobs = [(s, p) for p in a.specs for s in solvers]
    rows = []
    with ThreadPoolExecutor(a.jobs) as ex, open(a.out, 'a') as fo:
        for r in ex.map(lambda j: launch(j[0], j[1], a.timeout), jobs):
            rows.append(r)
            fo.write(json.dumps(r) + '\n')
            fo.flush()
            print('%-8s %-10s %7.2fs %s %s' % (r['solver'], r['status'], r['time'], r['name'],
                                               r.get('exact_why', '') + ' ' + r.get('note', '')), flush=True)
    print(table(rows, solvers))


if __name__ == '__main__':
    main()
