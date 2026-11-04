"""Tileable factory modules: solve one copy, repeat it n times side by side.

Lanes run west to east through every copy. An input lane enters copy 0 on the west carrying what all n copies
take, each copy's arms take their share, and the rest goes on east into the next copy (a line port, so the lane
stays an arm line: everything on it ends in arms). A collector lane gathers the product and leaves the east end.
A lane sits on the same row on both edges, so copies join up. The copy is solved with every lane at its fullest
(input lanes as in copy 0, collector lanes as in the last copy), which is the worst case for belt capacity.

    u = Unit(machines, items, per_unit, recipes, D, lanes, arm_cap, n)
    r = u.solve(W, H)            # one copy
    ok, rep = u.verify(r, W, H)  # n copies side by side in the game-exact sim
"""
import os

from . import model, gsim
from .model import Window


class Unit:
    def __init__(self, machines, items, per_unit, recipes, D, lanes, arm_cap, n):
        """lanes: [(kind 'in' | 'collect', item index, units one copy takes or adds)]."""
        self.machines, self.items, self.per_unit, self.recipes = machines, items, per_unit, recipes
        self.D, self.lanes, self.arm_cap, self.n = D, lanes, arm_cap, n

    def vec(self, c, v):
        x = [0] * len(self.items)
        x[c] = v
        return x

    def window(self, W, H, rows=None):
        """rows: fixed row per lane, or None to let the solver pick (one option group per lane)."""
        w = Window(W, H, len(self.items), self.D)
        w.machines, w.arm_cap = self.machines, self.arm_cap
        w.closed_edges = True             # nothing may spill into the neighbouring copy
        n = self.n
        for k, (kind, c, v) in enumerate(self.lanes):
            west, east = (v * n, v * (n - 1)) if kind == 'in' else (v * (n - 1), v * n)
            opts = []
            for r in (range(H) if rows is None else [rows[k]]):
                opt = {}
                if west:
                    opt[(0, r), 1] = ('in', self.vec(c, west))
                if east:
                    opt[(W - 1, r), 3] = ('out', self.vec(c, east))
                if kind == 'in' and east:
                    w.line_ports.add(((W - 1, r), 3))
                opts.append(opt)
            w.port_options.append(opts)
        return w

    def solve(self, W, H, rows=None, timeout=600, workers=4):
        w = self.window(W, H, rows)
        r = model.solve(w, timeout=timeout, workers=workers)
        r['window'] = w
        return r

    def compose(self, r, W, copies=None):
        """grid, machines of `copies` (default n) copies side by side."""
        copies = copies or self.n
        grid, ms = {}, []
        for k in range(copies):
            for (x, y), code in r['grid'].items():
                grid[x + k * W, y] = code
            ms += [(ti, (a[0] + k * W, a[1]), o) for ti, a, o in r['machines']]
        # end caps: an input lane that ends at the last copy's east edge gets one more belt, else a lone exit there
        # has no belt segment and the arms at it see nothing (no next copy to hand on to)
        # start caps likewise: a collector lane starts empty in copy 0, so its west entrance gets one belt in front
        for (t, d), spec in self.chosen_ports(r).items():
            if (t, d) in r['window'].line_ports:
                grid[W * copies, t[1]] = 'B3'
            elif spec[0] == 'in' and t[0] == 0 and self.lane_of(spec)[0] == 'collect':
                grid[-1, t[1]] = 'B3'
        return grid, ms

    def lane_of(self, spec):
        c = next(i for i, v in enumerate(spec[1]) if v)
        return next(l for l in self.lanes if l[1] == c)

    def chosen_ports(self, r):
        out = {}
        for g, j in zip(r['window'].port_options, r['options']):
            out.update(g[j])
        return out

    def verify(self, r, W, H, copies=None):
        """Sim of the copies joined: lanes fed on the west of copy 0 at their full rate, collectors drained on the
        east of the last copy. ok when every machine runs at its planned rate and the collectors get it all."""
        copies = copies or self.n
        grid, ms = self.compose(r, W, copies)
        w = Window(W * copies + 1, H, len(self.items), self.D)
        w.machines, w.arm_cap = self.machines, self.arm_cap
        rows = [next(t[1] for (t, d), spec in opt.items()) for g, j in zip(r['window'].port_options, r['options'])
                for opt in [g[j]]]
        for (kind, c, v), row in zip(self.lanes, rows):
            if kind == 'in':
                w.ports[(0, row), 1] = ('in', self.vec(c, v * copies))
            else:
                w.ports[(W * copies - 1, row), 3] = ('out', self.vec(c, v * copies))
        # (window one column wider for the end caps; collectors still leave the last copy's east edge)
        rep = gsim.run(w, grid, ms, self.items, self.per_unit, self.recipes, warmup=30, minutes=120,
                       filters=None)
        want = {tuple(a): sum(self.machines[ti]['make']) * self.per_unit for ti, a, o in ms}
        got = {(m['x'], m['y']): m['perMin'] for m in rep['machines']}
        planned = sum(v * copies for kind, c, v in self.lanes if kind == 'collect') * self.per_unit
        outs = sum(sum(x.values()) for x in rep['outputs'].values())
        ok = set(got) == set(want) and all(abs(got[k] - x) < 1e-9 for k, x in want.items()) and abs(outs - planned) < 1e-9
        return ok, rep


def steel_unit(n=3, arm_cap=12):
    """One copy = 2 steel forges + 1 graphite forge (T4): ore in 2 lanes (one forge each), coal, logs, and a steel
    collector. 1 unit = 7.5/min, belt = 64 units; an ore lane feeds 3 copies at most (3 x 135 = 405 per min)."""
    machines = [dict(bid=3009, count=1, need=[0, 18, 18, 0, 0], make=[0, 0, 0, 6, 0], tier=4),
                dict(bid=3009, count=2, need=[18, 0, 0, 3, 0], make=[0, 0, 0, 0, 3], tier=4)]
    lanes = [('in', 0, 18), ('in', 0, 18), ('in', 1, 18), ('in', 2, 18), ('collect', 4, 6)]
    return Unit(machines, ['Iron Ore', 'Coal', 'Wood Log', 'Graphite', 'Steel'], 7.5, [2022, 2017], 64, lanes,
                arm_cap, n)
