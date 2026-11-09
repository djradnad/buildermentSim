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


class Factory:
    """A factory module case: inputs enter on the east edge (rows given), the product leaves on the west edge.
    extra: more ports [(edge 'N'|'E'|'S'|'W', index from the start of that edge (negative = from its end), kind,
    vec)]. machines: model.Window machine dicts. Found layouts are checked in the game-exact sim (gsim)."""

    def __init__(self, ins, out, machines, items, per_unit, recipes, D=32, extra=(), arm_cap=None, free=False):
        self.arm_cap = arm_cap
        self.free = free        # True: the input belts enter as one block at any east rows, the output at any west row
        self.ins, self.out, self.machines, self.items = ins, out, machines, items
        self.per_unit, self.recipes, self.D, self.extra = per_unit, recipes, D, extra

    def window(self, W, H):
        from .model import Window
        groups = []
        if self.free:
            ports = {}
            n = len(self.ins)
            groups.append([{((W - 1, s + k), 3): ('in', v) for k, (r, v) in enumerate(self.ins)} for s in range(H - n + 1)])
            if self.out:
                groups.append([{((0, r), 1): ('out', self.out[1])} for r in range(H)])
        else:
            ports = {((W - 1, r), 3): ('in', v) for r, v in self.ins}
            if self.out:
                ports[((0, self.out[0]), 1)] = ('out', self.out[1])
        for edge, i, kind, vec in self.extra:
            n = H if edge in 'EW' else W
            i = i % n
            t, d = {'N': ((i, 0), 0), 'E': ((W - 1, i), 3), 'S': ((i, H - 1), 2), 'W': ((0, i), 1)}[edge]
            ports[t, d] = (kind, vec)
        w = Window(W, H, len(self.items), self.D, ports=ports)
        w.port_options = groups
        w.machines = self.machines
        w.arm_cap = self.arm_cap
        w.factory = self
        return w


def graphite2():   # 2 forges T1: 3 coal + 3 log -> 1 graphite / 4 s = 15/min each; 1 unit = 15/min
    return Factory([(0, [6, 0, 0]), (1, [0, 6, 0])], (0, [0, 0, 2]),
                   [dict(bid=3009, count=2, need=[3, 3, 0], make=[0, 0, 1])], ['Coal', 'Wood Log', 'Graphite'], 15, [2022])


def emag2():       # 2 machine shops T1: 6 wire + 2 iron ingot -> 1 magnet / 8 s = 7.5/min each; 1 unit = 7.5/min
    return Factory([(0, [12, 0, 0]), (1, [0, 4, 0])], (0, [0, 0, 2]),
                   [dict(bid=3007, count=2, need=[6, 2, 0], make=[0, 0, 1])],
                   ['Copper Wire', 'Iron Ingot', 'Electromagnet'], 7.5, [2015], D=64)


def wire_chain():  # 3 furnaces (30 ore -> 30 ingot each) + 2 workshops T1 (45 ingot -> 30 wire each); 1 unit = 15/min
    return Factory([(0, [6, 0, 0])], (0, [0, 0, 4]),
                   [dict(bid=3006, count=3, need=[2, 0, 0], make=[0, 2, 0]),
                    dict(bid=3005, count=2, need=[0, 3, 0], make=[0, 0, 2])],
                   ['Copper Ore', 'Copper Ingot', 'Copper Wire'], 15, [2002, 2003])


def forge_bank(n, tier=4, arms=False):
    """n forges on alt steel (4 iron ore + 4 coal -> 1 steel / 6 s; tier 4 = x3 = 30/min each); 1 unit = 30/min,
    a full belt = 16 units. Ore enters on the east, coal on the west next to the steel leaving west (the
    one-ingredient-on-the-output-side style of john's forge banks)."""
    ore = 4 * n
    belts = lambda u: [u - 16 * k if u - 16 * k < 16 else 16 for k in range(-(-u // 16))]
    ins = [(r, [b, 0, 0]) for r, b in enumerate(belts(ore))]
    extra = [('W', 1 + r, 'in', [0, b, 0]) for r, b in enumerate(belts(ore))]
    return Factory(ins, (0, [0, 0, n]), [dict(bid=3009, count=n, need=[4, 4, 0], make=[0, 0, 1], tier=tier)],
                   ['Iron Ore', 'Coal', 'Steel'], 30, [2048], D=16, extra=extra, arm_cap=3 if arms else None)


def steel_chain(ns=2, ng=1, arms=True):
    """john's steel-build style at small scale: graphite forges (3 coal + 3 log -> 1 graphite / 4 s, T4 = 45/min)
    feed steel forges (6 iron ore + 1 graphite -> 1 steel / 8 s, T4 = 22.5/min). Ore, coal and logs enter on the
    west edge (rows 0, 1, 2), steel leaves north at the east corner. 1 unit = 7.5/min, a belt = 64 units."""
    ore, raw = 18 * ns, 18 * ng
    extra = [('W', 0, 'in', [ore, 0, 0, 0, 0]), ('W', 1, 'in', [0, raw, 0, 0, 0]), ('W', 2, 'in', [0, 0, raw, 0, 0]),
             ('N', -1, 'out', [0, 0, 0, 0, 3 * ns])]
    return Factory([], None, [dict(bid=3009, count=ng, need=[0, 18, 18, 0, 0], make=[0, 0, 0, 6, 0], tier=4),
                              dict(bid=3009, count=ns, need=[18, 0, 0, 3, 0], make=[0, 0, 0, 0, 3], tier=4)],
                   ['Iron Ore', 'Coal', 'Wood Log', 'Graphite', 'Steel'], 7.5, [2022, 2017], D=64, extra=extra,
                   arm_cap=12 if arms else None)


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
    ('graphite2_6x5', graphite2, 6, 5, 'SAT', 'factory'),
    ('graphite2_5x5', graphite2, 5, 5, 'UNSAT', 'factory'),
    ('graphite2_6x4', graphite2, 6, 4, 'UNSAT', 'factory'),
    ('emag2_6x5', emag2, 6, 5, 'SAT', 'factory'),
    ('wire_5x5', wire_chain, 5, 5, 'SAT', 'factory'),
    ('forge2_6x6', lambda: forge_bank(2), 6, 6, 'SAT', 'factory'),
    ('forge2_6x5', lambda: forge_bank(2), 6, 5, 'UNSAT', 'factory'),
    ('forge2a_6x5', lambda: forge_bank(2, arms=True), 6, 5, 'SAT', 'factory'),
    ('forge2a_5x5', lambda: forge_bank(2, arms=True), 5, 5, 'UNSAT', 'factory'),
    ('forge2a_5x6', lambda: forge_bank(2, arms=True), 5, 6, 'UNSAT', 'factory'),
    ('forge4a_8x7', lambda: forge_bank(4, arms=True), 8, 7, 'SAT', 'factory_hard'),
    ('forge4_10x6', lambda: forge_bank(4), 10, 6, 'SAT', 'factory_hard'),
    ('forge4_9x6', lambda: forge_bank(4), 9, 6, 'UNSAT', 'factory_hard'),
    ('forge4_8x7', lambda: forge_bank(4), 8, 7, 'UNSAT', 'factory_hard'),
]


def run_cpsat(workers):
    def f(win, timeout):
        r = model.solve(win, timeout=timeout, workers=workers)
        st = {'OPTIMAL': 'SAT', 'FEASIBLE': 'SAT', 'INFEASIBLE': 'UNSAT'}.get(r['status'], 'UNKNOWN')
        return dict(status=st, time=r['time'], grid=r.get('grid'), options=r.get('options'), machines=r.get('machines'), filters=r.get('filters'))
    return f


def run_sat(binary):
    def f(win, timeout):
        t0 = time.time()
        r = cnf.solve(model.Model(win), engine=_bin(binary), timeout=timeout)
        st = {'FEASIBLE': 'SAT', 'INFEASIBLE': 'UNSAT'}.get(r['status'], 'UNKNOWN')
        return dict(status=st, time=time.time() - t0, grid=r.get('grid'), options=r.get('options'), machines=r.get('machines'), filters=r.get('filters'), vars=r['vars'], clauses=r['clauses'])
    return f


def run_cube(binary, procs, mirror=True, lemmas=False):
    def f(win, timeout):
        r = cnf.prove(win, _bin(binary), procs=procs, timeout=timeout, mirror=mirror, log=None, lemmas=lemmas)
        st = {'FEASIBLE': 'SAT', 'INFEASIBLE': 'UNSAT'}.get(r['status'], 'UNKNOWN')
        return dict(status=st, time=r['time'], grid=r.get('grid'), window=r.get('window'), machines=r.get('machines'), filters=r.get('filters'), cubes=r['cubes'])
    return f


def run_race(binaries, cpsat_workers=1):
    def f(win, timeout):
        r = cnf.race(win, [_bin(b) for b in binaries], timeout=timeout, cpsat_workers=cpsat_workers, log=None)
        st = {'FEASIBLE': 'SAT', 'INFEASIBLE': 'UNSAT'}.get(r['status'], 'UNKNOWN')
        return dict(status=st, time=r['time'], grid=r.get('grid'), options=r.get('options'), machines=r.get('machines'), filters=r.get('filters'), winner=r.get('winner'))
    return f


CONFIGS = {
    'cpsat1': run_cpsat(1),
    'cpsat4': run_cpsat(4),
    'kissat': run_sat('kissat'),
    'cadical': run_sat('cadical'),
    'cube_kissat4': run_cube('kissat', 4),
    'cube_bump4L': run_cube('AE_kissat2025_bump', 4, lemmas=True),   # 2025 bronze (UNSAT) + local flow lemmas
    'cube_vsa4L': run_cube('vsa', 4, lemmas=True),
    'cube_cad4L': run_cube('cadical', 4, lemmas=True),
    'race3': run_race(['kissat', 'cadical']),
}


def verify_factory(grid, win, machines, options=None, filters=None):
    """Game-exact sim of a factory layout: every machine and the output must run at exactly the planned rate."""
    from . import gsim
    f = win.factory
    rep = gsim.run(win, grid, machines, f.items, f.per_unit, f.recipes, options=options, warmup=30, minutes=120, filters=filters)
    want = {tuple(a): sum(win.machines[ti]['make']) * f.per_unit for ti, a, o in machines}
    got = {(m['x'], m['y']): m['perMin'] for m in rep['machines']}
    if set(got) != set(want):
        return False
    outs = sum(sum(v.values()) for v in rep['outputs'].values())
    keys = dict(win.ports)
    for i, g in enumerate(win.port_options):
        for j, opt in enumerate(g):
            if options is None or options[i] == j:
                keys.update(opt)
    planned = sum(sum(spec[1]) for spec in keys.values() if isinstance(spec, tuple) and spec[0] == 'out')
    return all(abs(got[k] - w) < 1e-9 for k, w in want.items()) and abs(outs - planned * f.per_unit) < 1e-9


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
    seen, todo, adj = set(), [], {}           # pieces no input reaches carry nothing (closed belt loops the
    for p, q, k in Design(grid).links():      # flow model may fill with circulating flow): drop them first
        adj.setdefault(p, []).append(q)
    for (t, d), spec in keys.items():
        if (spec[0] if isinstance(spec, tuple) else spec) == 'in' and t in grid:
            todo.append(t)
    while todo:
        p = todo.pop()
        if p not in seen:
            seen.add(p)
            todo += adj.get(p, [])
    grid = {p: c for p, c in grid.items() if p in seen}
    r = attempt(grid)
    if not r['ok'] and r['idle']:
        r = attempt({p: c for p, c in grid.items() if p not in r['idle']})
    return r['ok']


def run(case_names, config_names, timeout, out=None, window_hook=None, tag=''):
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
            if r['status'] == 'SAT' and r.get('grid') is not None:
                if getattr(win, 'factory', None):
                    ok = verify_factory(r['grid'], r.get('window', win), r.get('machines') or [], r.get('options'), r.get('filters'))
                else:
                    ok = verify(r['grid'], r.get('window', win), r.get('options'))
            status = r['status']
            if expect and status in ('SAT', 'UNSAT') and status != expect:
                status = 'WRONG(%s)' % status
            row = dict(case=name, config=cfg + tag, status=status, time=round(r['time'], 2), verified=ok,
                       extra=';'.join('%s=%s' % (k, v) for k, v in r.items() if k not in ('status', 'time', 'grid', 'options', 'window', 'machines', 'filters')))
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
    ap.add_argument('--tier', default='quick', help='quick, hard, huge, factory or all (comma list)')
    ap.add_argument('--cases', default=None, help='comma list of case names (overrides --tier)')
    ap.add_argument('--configs', default='cpsat1,kissat,cadical')
    ap.add_argument('--timeout', type=float, default=600)
    ap.add_argument('--out', default=None)
    ap.add_argument('--set', action='append', default=[], help='window attribute for an experiment, e.g. cuts=card')
    a = ap.parse_args(argv)
    sets = [kv.split('=', 1) for kv in a.set]

    def hook(win):
        for k, v in sets:
            setattr(win, k, None if v == 'None' else int(v) if v.isdigit() else v)
    tiers = set(a.tier.split(','))
    names = a.cases.split(',') if a.cases else [c[0] for c in CASES if 'all' in tiers or c[5] in tiers]
    run(names, a.configs.split(','), a.timeout, a.out, hook if sets else None, ''.join('+%s=%s' % kv for kv in map(tuple, sets)))


if __name__ == '__main__':
    main()
