"""Benchmark suite: every solving method on the same fixed set of windows, timed the same way.

    python -m bmsolver.bench --tier quick --configs cpsat1,kissat,cadical --out results.csv

Each case is a window with a known answer (a layout exists, or it was proven impossible), so a method that answers
wrongly is caught at once (status 'WRONG'). Found layouts are re-checked with the exact flow check (check.py).
Configs are named methods; new ideas get a new config and run against the same cases.
"""
import argparse
import csv
import os
import sys
import time

from . import spec as S, model, cnf
from .design import Design
from .check import check, find_ports

SOLVERS = os.environ.get('BM_SOLVERS', os.path.expanduser('~/solvers'))


def _bin(name):
    p = os.path.join(SOLVERS, name)
    if not os.path.isfile(p):
        raise FileNotFoundError('%s not found (set BM_SOLVERS to the folder with the SAT binaries)' % p)
    return p


# name, spec, W, H, expected ('SAT' | 'UNSAT'), tier
CASES = [
    ('split5_3x5', lambda: S.splitter(5), 3, 5, 'SAT', 'quick'),
    ('split5_2x5', lambda: S.splitter(5), 2, 5, 'UNSAT', 'quick'),
    ('split8_2x8', lambda: S.splitter(8), 2, 8, 'SAT', 'quick'),
    ('split12_2x12', lambda: S.splitter(12), 2, 12, 'SAT', 'quick'),
    ('split24_2x24', lambda: S.splitter(24), 2, 24, 'UNSAT', 'quick'),
    ('split24_3x24', lambda: S.splitter(24), 3, 24, 'SAT', 'quick'),
    ('merge8_2x8', lambda: S.merger(8), 2, 8, None, 'quick'),
    ('bal2_3x2', lambda: S.balancer(2), 3, 2, None, 'quick'),
    ('bal3_8x5', lambda: S.balancer(3), 8, 5, 'SAT', 'hard'),
    ('bal3_7x5', lambda: S.balancer(3), 7, 5, 'UNSAT', 'hard'),
    ('bal4_11x5', lambda: S.balancer(4), 11, 5, 'SAT', 'huge'),
    ('bal4_10x5', lambda: S.balancer(4), 10, 5, 'UNSAT', 'huge'),
    ('bal4_7x8', lambda: S.balancer(4), 7, 8, 'SAT', 'huge'),
    ('bal4_7x7', lambda: S.balancer(4), 7, 7, 'SAT', 'huge'),
    ('bal4_7x6', lambda: S.balancer(4), 7, 6, None, 'huge'),
    ('bal4_6x7', lambda: S.balancer(4), 6, 7, None, 'huge'),
]


def run_cpsat(workers):
    def f(win, timeout):
        r = model.solve(win, timeout=timeout, workers=workers)
        st = {'OPTIMAL': 'SAT', 'FEASIBLE': 'SAT', 'INFEASIBLE': 'UNSAT'}.get(r['status'], 'UNKNOWN')
        return dict(status=st, time=r['time'], grid=r.get('grid'), options=r.get('options'))
    return f


def run_sat(binary):
    def f(win, timeout):
        t0 = time.time()
        r = cnf.solve(model.Model(win), engine=_bin(binary), timeout=timeout)
        st = {'FEASIBLE': 'SAT', 'INFEASIBLE': 'UNSAT'}.get(r['status'], 'UNKNOWN')
        return dict(status=st, time=time.time() - t0, grid=r.get('grid'), options=r.get('options'), vars=r['vars'], clauses=r['clauses'])
    return f


def run_cube(binary, procs, mirror=True):
    def f(win, timeout):
        r = cnf.prove(win, _bin(binary), procs=procs, timeout=timeout, mirror=mirror, log=None)
        st = {'FEASIBLE': 'SAT', 'INFEASIBLE': 'UNSAT'}.get(r['status'], 'UNKNOWN')
        return dict(status=st, time=r['time'], grid=r.get('grid'), window=r.get('window'), cubes=r['cubes'])
    return f


def run_race(binaries, cpsat_workers=1):
    def f(win, timeout):
        r = cnf.race(win, [_bin(b) for b in binaries], timeout=timeout, cpsat_workers=cpsat_workers, log=None)
        st = {'FEASIBLE': 'SAT', 'INFEASIBLE': 'UNSAT'}.get(r['status'], 'UNKNOWN')
        return dict(status=st, time=r['time'], grid=r.get('grid'), options=r.get('options'), winner=r.get('winner'))
    return f


CONFIGS = {
    'cpsat1': run_cpsat(1),
    'cpsat4': run_cpsat(4),
    'kissat': run_sat('kissat'),
    'cadical': run_sat('cadical'),
    'cube_kissat4': run_cube('kissat', 4),
    'race3': run_race(['kissat', 'cadical']),
}


def verify(grid, win, options=None):
    """Independent exact flow check (check.py) of a found layout, with the ports the window offers: an input edge
    counts when its tile accepts items from outside, an output edge when its tile pushes across it. Input rates come
    from the window's port vectors. Idle pieces (allowed by the model, removed by polish) are dropped first."""
    from fractions import Fraction
    from .pieces import accepts, push_dirs
    keys = dict(win.ports)
    for i, g in enumerate(win.port_options):
        for j, opt in enumerate(g):
            if options is None or options[i] == j:
                for k, v in opt.items():
                    keys.setdefault(k, v)

    def attempt(grid):
        ins, outs, rates = [], [], []
        for (t, d), spec in sorted(keys.items()):
            kind = spec[0] if isinstance(spec, tuple) else spec
            c = grid.get(t)
            if c is None:
                continue
            if kind == 'in' and accepts(c, (d + 2) % 4):
                ins.append((t, (d + 2) % 4))
                rates.append(Fraction(sum(spec[1]), win.D))
            elif kind == 'out' and d in push_dirs(c):
                outs.append((t, d))
        return check(Design(grid), ins, outs, rates=rates, mix=win.ncom > 1)
    r = attempt(grid)
    if not r['ok'] and r['idle']:
        r = attempt({p: c for p, c in grid.items() if p not in r['idle']})
    return r['ok']


def run(case_names, config_names, timeout, out=None, window_hook=None):
    rows = []
    for name, mk, W, H, expect, tier in CASES:
        if name not in case_names:
            continue
        for cfg in config_names:
            win = mk().window(W, H)
            if window_hook:
                window_hook(win)
            r = CONFIGS[cfg](win, timeout)
            ok = ''
            if r['status'] == 'SAT' and r.get('grid'):
                ok = verify(r['grid'], r.get('window', win), r.get('options'))
            status = r['status']
            if expect and status in ('SAT', 'UNSAT') and status != expect:
                status = 'WRONG(%s)' % status
            row = dict(case=name, config=cfg, status=status, time=round(r['time'], 2), verified=ok,
                       extra=';'.join('%s=%s' % (k, v) for k, v in r.items() if k not in ('status', 'time', 'grid', 'options', 'window')))
            rows.append(row)
            print('%-14s %-14s %-12s %8.2fs %s %s' % (name, cfg, status, r['time'], ok, row['extra']), flush=True)
            if out:
                new = not os.path.exists(out)
                with open(out, 'a', newline='') as fh:
                    w = csv.DictWriter(fh, fieldnames=list(row))
                    if new:
                        w.writeheader()
                    w.writerow(row)
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--tier', default='quick', help='quick, hard, huge or all (comma list)')
    ap.add_argument('--cases', default=None, help='comma list of case names (overrides --tier)')
    ap.add_argument('--configs', default='cpsat1,kissat,cadical')
    ap.add_argument('--timeout', type=float, default=600)
    ap.add_argument('--out', default=None)
    a = ap.parse_args(argv)
    tiers = set(a.tier.split(','))
    names = a.cases.split(',') if a.cases else [c[0] for c in CASES if 'all' in tiers or c[5] in tiers]
    run(names, a.configs.split(','), a.timeout, a.out)


if __name__ == '__main__':
    main()
