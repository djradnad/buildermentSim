"""Placement loop on a finished layout: everything fixed that touches forms a rigid cluster (a module with its
arms and lanes); clusters slide toward the middle one tile at a time, and after every sweep ALL connecting belts
are routed again from scratch. A sweep is kept only when everything routes and traces.

    python tools/mega/compact_core.py core.json out.json [seconds]
"""
import json
import random
import sys
import time

sys.path.insert(0, '.')
sys.path.insert(0, 'tools/mega')
import reroute_core as rc
from bmsolver import negotiate, place
from bmsolver.pieces import KIND_OF_ID, BUILDING_ID, UG_REACH, step


def clusters(cells):
    """8-connected groups of fixed tiles -> {tile: group number}"""
    group, n = {}, 0
    for c in cells:
        if c in group:
            continue
        group[c] = n
        todo = [c]
        while todo:
            x, y = todo.pop()
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    q = (x + dx, y + dy)
                    if q in cells and q not in group:
                        group[q] = n
                        todo.append(q)
        n += 1
    return group, n


class State:
    def __init__(self, path):
        self.rows, self.belts, self.solid, arms = rc.load(path)
        nets, self.anch = rc.runs(self.belts, self.solid, arms)
        fixed = set(self.solid) | set(self.anch)
        self.group, self.n = clusters(fixed)
        self.cells = [[] for _ in range(self.n)]
        for c, g in self.group.items():
            self.cells[g].append(c)
        self.off = [(0, 0)] * self.n
        self.nets = []
        for tiles, (s, d), (e, de) in nets:
            gs = self.group[step(s, (d + 2) % 4)]            # the fixed tile that feeds the run
            ge = self.group[step(e, de)]                      # the fixed tile the run ends in
            self.nets.append((s, d, gs, e, de, ge))
        self.old = sum(len(t) for t, s, e in nets)
        self.ug = [(p, self.belts[p]) for p in self.anch if self.belts[p][0] in 'IO']

    def pos(self, c, g, off=None):
        o = (off or self.off)[g]
        return (c[0] + o[0], c[1] + o[1])

    def occupied(self, off=None):
        occ = {}
        for g in range(self.n):
            for c in self.cells[g]:
                occ[self.pos(c, g, off)] = g
        return occ

    def box(self, off=None):
        occ = self.occupied(off)
        xs = [p[0] for p in occ]
        ys = [p[1] for p in occ]
        return min(xs), min(ys), max(xs), max(ys)

    def route(self, off, timeout=60):
        occ = self.occupied(off)
        x0, y0, x1, y1 = self.box(off)
        x0, y0, x1, y1 = x0 - 1, y0 - 1, x1 + 1, y1 + 1       # one free ring around for routes
        W, H = x1 - x0 + 1, y1 - y0 + 1
        sh = lambda p: (p[0] - x0, p[1] - y0)
        blocked = {sh(p) for p in occ}
        fixed_ug, no_ug = set(), set()
        pos_ug = {}
        for p, c in self.ug:
            pos_ug[self.pos(p, self.group[p], off)] = c
        for p, c in pos_ug.items():
            fixed_ug.add((sh(p), int(c[1])))
            if c[0] == 'I':
                d = int(c[1])
                for k in range(1, UG_REACH + 1):
                    q = step(p, d, k)
                    if pos_ug.get(q) == 'O%d' % d:
                        break
                    no_ug.add((sh(q), d))
        sources, sinks = [], []
        for i, (s, d, gs, e, de, ge) in enumerate(self.nets):
            a, b = sh(self.pos(s, gs, off)), sh(self.pos(e, ge, off))
            if a in blocked or b in blocked:
                return None
            sources.append((a, d, i))
            sinks.append((b, de, i))
        if len({a for a, d, i in sources} | {b for b, d, i in sinks}) != 2 * len(self.nets):
            return None
        ok, res, secs = negotiate.route(W, H, len(self.nets), blocked, sources, sinks, fixed_ug=fixed_ug, no_ug=no_ug,
                                        open_cost=0.2, timeout=timeout, rounds=40)
        if not ok:
            return None
        try:
            place.trace(dict(W=W, H=H, blocked=blocked, sources=sources, sinks=sinks, nets=len(self.nets), reach=UG_REACH), res)
        except AssertionError:
            return None
        res['origin'] = (x0, y0)
        return res

    def sweep(self, axis, toward, frac, rnd):
        """every cluster (a random share of them) moves one tile toward `toward` along axis where nothing is in
        the way; far clusters first so a whole row can close up"""
        off = list(self.off)
        occ = self.occupied(off)
        centre = []
        for g in range(self.n):
            v = [self.pos(c, g, off)[axis] for c in self.cells[g]]
            centre.append(sum(v) / len(v))
        order = sorted(range(self.n), key=lambda g: abs(centre[g] - toward))    # near ones first make room
        moved = 0
        for g in order:
            if abs(centre[g] - toward) < 1 or rnd.random() > frac:
                continue
            sgn = 1 if centre[g] < toward else -1
            dv = (sgn, 0) if axis == 0 else (0, sgn)
            new = [(p[0] + dv[0], p[1] + dv[1]) for p in (self.pos(c, g, off) for c in self.cells[g])]
            if any(occ.get(p, g) != g for p in new):
                continue
            for c in self.cells[g]:
                del occ[self.pos(c, g, off)]
            off[g] = (off[g][0] + dv[0], off[g][1] + dv[1])
            for p in new:
                occ[p] = g
            moved += 1
        return off, moved

    def save(self, res, out):
        x0, y0 = res['origin']
        rows = []
        for r in self.rows:
            p = (r[1], r[2])
            if r[0] in KIND_OF_ID and p not in self.anch:
                continue
            g = self.group[p] if p in self.group else self.group[rc.footprint(r)[0]]
            o = self.off[g]
            rows.append([r[0], r[1] + o[0], r[2] + o[1]] + list(r[3:]))
        rows += [[BUILDING_ID[c[0]], x + x0, y + y0, int(c[1]), 0, -1, 0] for (x, y), c in sorted(res['tiles'].items())]
        json.dump({'meta': {'name': 'compacted', 'count': len(rows)}, 'rows': rows}, open(out, 'w'))


def main(path, out, seconds=1200):
    rnd = random.Random(1)
    st = State(path)
    t0 = time.time()
    x0, y0, x1, y1 = st.box()
    print('%d clusters, %d runs, box %dx%d' % (st.n, len(st.nets), x1 - x0 + 1, y1 - y0 + 1), flush=True)
    best = st.route(st.off)
    assert best is not None, 'the starting layout does not route'
    frac, fails = 1.0, 0
    while time.time() - t0 < float(seconds) and fails < 12:
        x0, y0, x1, y1 = st.box()
        W, H = x1 - x0 + 1, y1 - y0 + 1
        axis = 0 if W >= H else 1                              # shrink the longer side first (most square)
        if rnd.random() < 0.3:
            axis = 1 - axis
        toward = (x0 + x1) / 2 if axis == 0 else (y0 + y1) / 2
        off, moved = st.sweep(axis, toward, frac, rnd)
        if not moved:
            fails += 1
            continue
        res = st.route(off)
        if res is None:
            frac = max(0.05, frac / 2)
            fails += 1
            continue
        st.off, best, fails = off, res, 0
        frac = min(1.0, frac * 1.5)
        x0, y0, x1, y1 = st.box()
        print('%.0f s: box %dx%d, %d clusters moved, %d route pieces' % (time.time() - t0, x1 - x0 + 1, y1 - y0 + 1, moved, best['pieces']), flush=True)
        st.save(best, out)
    x0, y0, x1, y1 = st.box()
    print('done: box %dx%d, route pieces %d (start %d)' % (x1 - x0 + 1, y1 - y0 + 1, best['pieces'], st.old))


if __name__ == '__main__':
    main(*sys.argv[1:4])
