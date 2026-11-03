"""Tick-by-tick belt simulation that follows the game's own rules (belts, splitters, undergrounds).

The rules were traced from the game (PC/Steam build): how pieces link up, how belts are cut into segments, the
exact segment lengths (curves are quarter arcs), item spacing, splitter round robin and merge priority, all in
float32 like the game. It answers what the exact flow check can't: does the layout really reach full throughput
once items start backing up, and how even are the outputs over time.

    from bmsolver.sim import simulate
    res = simulate(design, inputs, outputs, minutes=5)
    res['out_per_min']       # items per minute at each output
    res['share']             # per output, items per minute from each input

Units: a tile is 800 length units, items sit at least 240 apart, a belt moves 32 units per tick, 60 ticks a
second, so a full belt carries 60 * 32 / 240 * 60 = 480 items per minute.
"""
import math

import numpy as np

from .design import Design
from .pieces import DIRS, UG_REACH, step

f32 = np.float32
SPACING, SNAP, SPEED = 240, 28, 32
TICKS_PER_MIN = 3600
NB_ORDER = ((0, -1), (-1, 0), (0, 1), (1, 0))   # neighbour order the game uses when linking
SUB = {'B': 0, 'S': 1, 'I': 2, 'O': 3}           # belt sub-types


class Node:
    __slots__ = ('sub', 'dir', 'pos', 'inputs', 'outputs')

    def __init__(self, sub, d, pos):
        self.sub, self.dir, self.pos = sub, d, pos
        self.inputs, self.outputs = [], []

    def __repr__(self):
        return 'N%d%s' % (self.sub, self.pos)


def _code_in(p, nb):
    if nb[0] == p[0]:
        return 0 if p[1] < nb[1] else 2
    return 1 if p[0] <= nb[0] else 3


def _code_out(p, nb):
    if p[0] == nb[0]:
        return 0 if nb[1] < p[1] else 2
    return 1 if nb[0] <= p[0] else 3


def _outputs_to(sub, d, c):
    if sub == 1:
        return ((d + 2) & 3) != c
    if sub == 2:
        return False
    return d == c


def _accepts_from(sub, d, c):
    if sub == 0:
        return d != ((c + 2) & 3)
    if sub in (1, 2):
        return d == c
    return False


def _arc(c, start, end):
    cx, cy = c
    sx, sy = start
    ex, ey = end
    kx, ky = (ex - cx) + sx, (ey - cy) + sy
    neg = (cx < ex and sy < cy) or (cx < sx and cy < ey) or (ex < cx and cy < sy) or (sx < cx and ey < cy)
    a0 = f32(math.atan2(float(f32(sy - ky)), float(f32(sx - kx))))
    stp = f32(-0.01745329424738884) if neg else f32(0.01745329424738884)
    pts = []
    for i in range(0, 91, 15):
        a = f32(f32(i) * stp + a0)
        pts.append((f32(f32(math.cos(a)) * f32(0.5) + kx), f32(f32(math.sin(a)) * f32(0.5) + ky)))
    return pts


def _seglen(a, b):
    dx, dy = f32(b[0] - a[0]), f32(b[1] - a[1])
    return f32(math.sqrt(float(f32(dy * dy + dx * dx))))


class Seg:
    """A run of belt nodes items move along. items[i] = gap in front of item i (item 0 is at the front)."""

    def __init__(self, idx, pts, nodes):
        self.idx, self.nodes = idx, nodes
        tot = f32(0)
        for i in range(len(pts) - 1):
            tot = f32(tot + _seglen(pts[i], pts[i + 1]))
        self.L = int(np.round(tot * f32(800)))
        self.items, self.kinds = [], []
        self.free = self.L
        self.first, self.fmin = -1, 0
        self.target = None
        self.inputs = []
        self.chosen = -1
        self.moved = 0
        self.passed = {}

    def recompute(self):
        for i, g in enumerate(self.items):
            if (self.fmin if i == 0 else SPACING) < g:
                self.first = i
                return
        self.first = -1

    def insert(self, m, kind):
        g = max(SPACING, self.free - m)
        if g != SPACING and abs(g - SPACING) <= SNAP:
            g = SPACING
        self.items.append(g)
        self.kinds.append(kind)
        self.free -= g
        self.recompute()

    def remove(self, m):
        self.items.pop(0)
        k = self.kinds.pop(0)
        if self.items:
            self.items[0] = max(0, self.items[0] - m)
            self.free += m
        self.recompute()
        self.moved += 1
        self.passed[k] = self.passed.get(k, 0) + 1
        return k


class Split:
    def __init__(self, outs):
        self.outs, self.idx = outs, 0


def _merge_next(t):
    n = len(t.inputs)
    if n < 2:
        return n - 1
    cur = t.chosen
    i, stop = ((cur + 1) % n, cur) if cur != -1 else (0, n - 1)
    while i != stop:
        src = t.inputs[i]
        if src.items and src.items[0] <= SPACING:
            return i
        i = (i + 1) % n
    return -1


def _merge_gate(s):
    if s.items[0] > 480:
        return
    t = s.target
    if t.chosen == -1:
        best, bg = -1, 100000
        for i, src in enumerate(t.inputs):
            if src.items and src.items[0] < bg:
                best, bg = i, src.items[0]
        t.chosen = best
    s.fmin = SPACING if t.chosen < 0 or t.inputs[t.chosen] is not s else max(0, SPACING - t.free)
    s.recompute()


def _seg_tick(s):
    if not s.items:
        return
    t = s.target
    if isinstance(t, Seg) and len(t.inputs) > 1:
        _merge_gate(s)
    left = SPEED
    k = s.first
    if k != -1:
        mn = s.fmin if k == 0 else SPACING
        g = s.items[k]
        ng = max(mn, g - SPEED)
        s.items[k] = ng
        used = g - ng
        s.free += used
        left = SPEED - used
        if used != SPEED:
            s.recompute()
    if s.items[0] != 0 or t is None:
        return
    kind = s.kinds[0]
    if isinstance(t, Split):
        if not t.outs:
            return
        o = t.outs[t.idx]
        if o.free > SPACING - 1:
            o.insert(left, kind)
            s.remove(left)
            o.chosen = _merge_next(o)
        t.idx = (t.idx + 1) % len(t.outs)
    elif isinstance(t, Seg):
        if t.free > SPACING - 1:
            t.insert(left, kind)
            s.remove(left)
            t.chosen = _merge_next(t)
    elif t == 'sink':
        s.remove(left)


class Belts:
    """The belt network of a design: nodes, links and segments built the way the game builds them."""

    def __init__(self, design):
        self.node = {p: Node(SUB[c[0]], int(c[1]), p) for p, c in design.tiles.items()}
        for n in self.node.values():
            self._link(n)
        self._segments()

    def _find_input(self, n, nb):
        c = _code_in(n.pos, nb)
        ok = n.dir != ((c - 2) & 3) if n.sub == 0 else (n.dir == c if n.sub in (1, 2) else False)
        m = self.node.get(nb)
        return m if ok and m is not None and _outputs_to(m.sub, m.dir, c) else None

    def _find_output(self, n, nb):
        c = _code_out(n.pos, nb)
        if n.sub == 2:
            return None
        ok = ((n.dir + 2) & 3) != c if n.sub == 1 else n.dir == c
        m = self.node.get(nb)
        return m if ok and m is not None and _accepts_from(m.sub, m.dir, c) else None

    def _link(self, n):
        x, y = n.pos
        for dx, dy in NB_ORDER:
            i = self._find_input(n, (x + dx, y + dy))
            o = self._find_output(n, (x + dx, y + dy))
            if i is not None:
                n.inputs.append(i)
            if o is not None:
                n.outputs.append(o)

    @staticmethod
    def _is_split(n):
        return n.sub == 1

    def _walk_fwd(self, n):
        seen = set()
        while len(n.outputs) == 1:
            u = n.outputs[0]
            if u in self.visited or u in seen or self._is_split(u):
                return n
            seen.add(n)
            n = u
        return n

    def _walk_back(self, n):
        seen, steps = set(), 1
        while len(n.inputs) == 1:
            u = n.inputs[0]
            if u in self.visited or u in seen or steps == 60 or self._is_split(u):
                return n
            seen.add(n)
            steps += 1
            n = u
        return n

    def _collect(self, cur):
        nodes, i = [], 0
        multi = len(cur.inputs) > 1
        while cur not in self.visited:
            if self._is_split(cur) or i == 60:
                break
            nodes.append(cur)
            if multi and i > 0:
                return nodes
            self.visited.add(cur)
            if len(cur.outputs) != 1:
                return nodes
            cur = cur.outputs[0]
            i += 1
            multi = len(cur.inputs) > 1
        nodes.append(cur)
        if not multi and i != 60:
            self.visited.add(cur)
        return nodes

    def _segments(self):
        self.visited, self.segs, start_of, split_outs = set(), [], {}, {}

        def make(nodes):
            g = Seg(len(self.segs), self._points(nodes), nodes)
            self.segs.append(g)
            start_of.setdefault(nodes[0], g)
            return g
        for n in self.node.values():
            while n not in self.visited:
                seen = len(self.visited)
                nodes = self._collect(self._walk_back(self._walk_fwd(n)))
                if len(nodes) > 1:
                    make(nodes)
                elif len(self.visited) == seen:
                    break                           # nothing new (guard against looping forever)
        for n in self.node.values():
            if n.sub == 1:
                split_outs[n] = [make([n, o]) for o in n.outputs]
        for n in self.node.values():
            if n.sub == 2:
                for k in range(1, UG_REACH + 1):
                    e = self.node.get(step(n.pos, n.dir, k))
                    if e is not None and e.sub == 3 and e.dir == n.dir:
                        make([n, e])
                        break
        split_of = {sp: Split(outs) for sp, outs in split_outs.items()}
        for g in self.segs:
            last = g.nodes[-1]
            if self._is_split(last):
                g.target = split_of[last]
            else:
                t = start_of.get(last)
                if t is not None and t is not g:
                    g.target = t
                    t.inputs.append(g)
        self.start_of = start_of

    # geometry: the polyline items follow through a run of nodes
    def _node_points(self, n):
        cx, cy = f32(n.pos[0]), f32(n.pos[1])
        half = f32(0.5)
        if len(n.inputs) < 2 and len(n.outputs) < 2 and not self._is_split(n):
            arc = self._arc_points(n)
            if arc is not None:
                return arc
            if n.sub in (2, 3):
                return [(cx, cy)]
            dx, dy = DIRS[n.dir]
            return [self._entry(n), (cx + f32(dx) * half, cy + f32(dy) * half)]
        return [(cx, cy)]

    def _entry(self, n):
        cx, cy = f32(n.pos[0]), f32(n.pos[1])
        half = f32(0.5)
        if len(n.inputs) > 1:
            return (cx, cy)
        if n.sub == 0 and len(n.inputs) == 1:
            ix, iy = n.inputs[0].pos
            return (cx * half + f32(ix) * half, cy * half + f32(iy) * half)
        if n.sub == 3:
            return (cx, cy)
        dx, dy = DIRS[n.dir]
        return (cx - f32(dx) * half, cy - f32(dy) * half)

    def _arc_points(self, n):
        cx, cy = f32(n.pos[0]), f32(n.pos[1])
        half = f32(0.5)
        if len(n.outputs) == 1:
            ox, oy = n.outputs[0].pos
            c = (0 if oy < n.pos[1] else 2) if n.pos[0] == ox else (1 if ox <= n.pos[0] else 3)
            if n.dir != c:
                dx, dy = DIRS[n.dir]
                return _arc((cx, cy), (cx - f32(dx) * half, cy - f32(dy) * half),
                            (f32(ox) * half + cx * half, f32(oy) * half + cy * half))
        if len(n.inputs) == 1:
            ix, iy = n.inputs[0].pos
            c = (0 if n.pos[1] < iy else 2) if ix == n.pos[0] else (1 if n.pos[0] <= ix else 3)
            if n.dir != c:
                dx, dy = DIRS[n.dir]
                return _arc((cx, cy), (cx * half + f32(ix) * half, cy * half + f32(iy) * half),
                            (cx + f32(dx) * half, cy + f32(dy) * half))
        return None

    def _points(self, nodes):
        out = []
        last = len(nodes) - 1
        for i, n in enumerate(nodes):
            pts = self._node_points(n)
            if i == 0:
                out.extend(pts)
            elif i != last or not n.outputs:
                if pts[0] == out[-1]:
                    pts = pts[1:]
                out.extend(pts)
            elif pts[0] != out[-1]:
                out.append(pts[0])
        res = [out[0]]
        for k in range(1, len(out) - 1):
            a, b, c = out[k - 1], out[k], out[k + 1]
            if (a[0] != b[0] or b[0] != c[0]) and (a[1] != b[1] or b[1] != c[1]):
                res.append(b)
        if len(out) > 1:
            res.append(out[-1])
        return res


def simulate(design, inputs, outputs, minutes=5, warmup=2, feed=None):
    """Feed every input (or those listed in feed) with a full belt of its own item, drain every output.
    inputs/outputs as in check.check. Returns per-minute rates measured after `warmup` minutes."""
    d = Design(design.tiles if isinstance(design, Design) else design)
    feeders, drains = [], []
    for i, (p, dirn) in enumerate(inputs):          # a feeder belt behind each input, a drain after each output
        q = step(p, (dirn + 2) % 4)
        d.tiles[q] = 'B%d' % dirn
        feeders.append((i + 1, q))
    for p, dirn in outputs:
        q = step(p, dirn)
        d.tiles[q] = 'B%d' % dirn
        drains.append(q)
    net = Belts(d)
    feed_segs = []
    for kind, q in feeders:
        if feed is not None and kind - 1 not in feed:
            continue
        g = next((s for s in net.segs if s.nodes[0].pos == q), None)
        if g is None:
            raise ValueError('input at %s does not start a belt segment' % (q,))
        feed_segs.append((g, kind))
    out_segs = []
    for q in drains:
        g = next((s for s in net.segs if s.nodes[-1].pos == q), None)
        if g is None:
            raise ValueError('output at %s does not end a belt segment' % (q,))
        g.target = 'sink'
        out_segs.append(g)
    fed = [0] * len(feed_segs)

    def tick():
        for g in net.segs:
            _seg_tick(g)
        for j, (g, kind) in enumerate(feed_segs):
            if g.free > SPACING - 1:
                g.insert(SPEED, kind)
                fed[j] += 1
    for _ in range(int(warmup * TICKS_PER_MIN)):
        tick()
    before = [dict(g.passed) for g in out_segs]
    fed0 = list(fed)
    for _ in range(int(minutes * TICKS_PER_MIN)):
        tick()
    n = len(inputs)
    share = []
    for g, b in zip(out_segs, before):
        share.append([(g.passed.get(k + 1, 0) - b.get(k + 1, 0)) / minutes for k in range(n)])
    return dict(out_per_min=[sum(r) for r in share], share=share,
                in_per_min=[(f - f0) / minutes for f, f0 in zip(fed, fed0)])
