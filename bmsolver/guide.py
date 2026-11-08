"""Guided SAT solving: experiments with heuristics that know what the variables mean.

The CNF from cnf.BinTranslation has ~100k-800k variables, but only a few hundred are layout choices (one piece per
tile: E = empty, B/S/I/O/A + rotation, M = machine part, Y = machine placement, opt = port option). The rest are adder
circuits for the flows, fully decided by the layout. A generic solver does not know that. This module tries:

  phases  - initial polarity of the layout variables (e.g. 'empty' prior, or a learned per-feature prior)
  decide  - branch on layout variables first (IPASIR-UP decide() callback, CaDiCaL 1.9.5 via pysat)
  config  - CaDiCaL option sets, to be chosen per instance family by a bandit (see mab_portfolio)

    python -m bmsolver.guide --case bal3_8x5 --mode base,phase_empty,decide --timeout 600
"""
import argparse
import re
import threading
import time

from . import bench, model, cnf

_NAME = re.compile(r'^([EBSIOAMY])(\d?)\((-?\d+), (-?\d+)\)$|^(Y)(\d+)$|^(opt)$')


def build(win):
    """CNF + map of layout variables: list of (sat var, kind, rot, x, y) for named piece variables."""
    mod = model.Model(win)
    tr = cnf.BinTranslation(mod.m)
    layout = []
    for i, v in enumerate(tr.proto.variables):
        if i not in tr.bool or not v.name:
            continue
        m = _NAME.match(v.name)
        if not m:
            continue
        if m.group(1):
            k, o, x, y = m.group(1), m.group(2), int(m.group(3)), int(m.group(4))
            layout.append((tr.bool[i], k, int(o) if o else -1, x, y))
        else:
            layout.append((tr.bool[i], m.group(5) or m.group(7), -1, -1, -1))
    return mod, tr, layout


# ---------------------------------------------------------------- phase priors
def phase_empty(layout, win):
    """every tile empty unless forced: SAT layouts are sparse, so most piece literals are false"""
    return [v if k == 'E' else -v for v, k, o, x, y in layout if k in 'EBSIOAM']


def phase_belt_flow(layout, win):
    """empty prior, but belts pointing west (the usual input-east, output-west flow) start true"""
    out = []
    for v, k, o, x, y in layout:
        if k == 'E':
            out.append(-v)
        elif k == 'B' and o == 3:
            out.append(v)
        elif k in 'BSIOAM':
            out.append(-v)
    return out


PHASES = {'phase_empty': phase_empty, 'phase_flow': phase_belt_flow}


# ---------------------------------------------------------------- decide-first propagator
from pysat.engines import Propagator as _Prop


class LayoutFirst(_Prop):
    """IPASIR-UP propagator: decide on unassigned layout variables first, in a fixed order (pysat Propagator API).
    order: list of signed literals (sign = preferred phase)."""

    def __init__(self, order):
        super().__init__()
        self.order = order
        self.vars = [abs(l) for l in order]
        self.val = {}
        self.trail = [[]]
        self.ptr = 0

    def setup_observe(self, solver):
        for v in self.vars:
            solver.observe(v)

    def on_assignment(self, l, fixed=False):
        self.val[abs(l)] = l > 0
        if not fixed:
            self.trail[-1].append(abs(l))

    def on_new_level(self):
        self.trail.append([])

    def on_backtrack(self, to):
        while len(self.trail) > to + 1:
            for v in self.trail.pop():
                self.val.pop(v, None)
        self.ptr = 0

    def decide(self):
        o = self.order
        while self.ptr < len(o):
            l = o[self.ptr]
            if abs(l) not in self.val:
                return l
            self.ptr += 1
        return 0

    def propagate(self):
        return []

    def provide_reason(self, lit):
        return []

    def check_model(self, model):
        return True

    def add_clause(self):
        return []


def order_rowmajor(layout, win, phase=phase_empty):
    """layout literals ordered by tile (west to east, then north to south), E first, then pieces"""
    ph = {abs(l): l for l in phase(layout, win)}
    tiles = sorted({(x, y) for v, k, o, x, y in layout if x >= 0}, key=lambda p: (-p[0], p[1]))
    by = {}
    for v, k, o, x, y in layout:
        by.setdefault((x, y), []).append(v)
    out = [ph.get(v, -v) for v, k, o, x, y in layout if x < 0]      # port options, machine placements first
    for t in tiles:
        out += [ph.get(v, -v) for v in by[t]]
    return out


# ---------------------------------------------------------------- run
def run(win, mode='base', timeout=600, config=None, phases=None):
    from pysat.solvers import Cadical195
    t0 = time.time()
    mod, tr, layout = build(win)
    tb = time.time() - t0
    s = Cadical195(bootstrap_with=tr.f.clauses)
    if config:
        s.configure(config)
    prop = None
    if mode in PHASES:
        s.set_phases(PHASES[mode](layout, win))
    elif mode == 'decide':
        prop = LayoutFirst(order_rowmajor(layout, win))
        s.connect_propagator(prop)
        prop.setup_observe(s)
    if phases:
        s.set_phases(phases)
    timer = threading.Timer(timeout, s.interrupt)
    timer.start()
    t1 = time.time()
    sat = s.solve_limited(expect_interrupt=True)
    t = time.time() - t1
    timer.cancel()
    res = dict(status='SAT' if sat else 'UNSAT' if sat is False else 'UNKNOWN', time=t, build=tb, nlayout=len(layout),
               vars=tr.f.nv)
    if sat:
        mdl = s.get_model()
        res['grid'] = mod.grid(cnf._Values(tr, mdl))
        res['options'] = mod.chosen_options(cnf._Values(tr, mdl))
    s.delete()
    return res


CONFIGS = {
    'default': None,
    'sat': {'elimreleff': 10, 'stabilizeonly': 1, 'subsumereleff': 60},
    'unsat': {'stabilize': 0, 'walk': 0},
    'nochrono': {'chrono': 0},
    'noelim': {'elim': 0},
    'phase0': {'phase': 0},
}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--case', required=True)
    ap.add_argument('--mode', default='base')
    ap.add_argument('--config', default='default')
    ap.add_argument('--timeout', type=float, default=600)
    a = ap.parse_args(argv)
    cases = {c[0]: c for c in bench.CASES}
    name, mk, W, H, exp, tier = cases[a.case]
    for mode in a.mode.split(','):
        for cfg in a.config.split(','):
            win = mk().window(W, H)
            r = run(win, mode, a.timeout, CONFIGS[cfg])
            ok = ''
            if r['status'] == 'SAT':
                ok = bench.verify(r['grid'], win, r.get('options')) if not getattr(win, 'factory', None) else '?'
            print('%-14s %-12s %-9s %-8s %8.2fs build %.1fs verified=%s layoutvars=%d vars=%d' % (
                name, mode, cfg, r['status'], r['time'], r['build'], ok, r['nlayout'], r['vars']), flush=True)


if __name__ == '__main__':
    main()
