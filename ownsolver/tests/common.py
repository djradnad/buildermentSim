"""Shared helpers for the own-solver test set: spec windows, JSON export, exact re-check of any grid.

A spec here is a model.Window with pinned ports (no free positions). JSON spec format (what the own solver reads):
  {"name", "W", "H", "D", "ncom",
   "ports": [[x, y, d, "in"|"out", [units per commodity]], ...],   d = direction pointing OUT of the window
   "fixed": [[x, y, "B1"|null], ...]}                               tiles forced (null = forced empty)
D units = one full belt. Every port sits on the window edge; other edges have nothing outside.
"""
import json
import os
import sys
from fractions import Fraction

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, ROOT)

from bmsolver.model import Window          # noqa: E402
from bmsolver.design import Design, flows   # noqa: E402
from bmsolver import spec as S              # noqa: E402
from bmsolver.pieces import opposite        # noqa: E402

SIDE_OF_OUTDIR = {0: 'N', 1: 'W', 2: 'S', 3: 'E'}


def balancer_window(n, m, W, H, in_rows, out_rows, mix=True, rates=None):
    """n inputs on E at rows in_rows, m outputs on W at rows out_rows (pinned)."""
    sp = S.Spec([S.Group('E', n, positions=in_rows, rate=None if rates is None else rates)],
                [S.Group('W', m, positions=out_rows)], mix=mix)
    win = sp.window(W, H)
    win.name = '%d:%d%s %dx%d in%s out%s' % (n, m, '' if mix else 'u', W, H, list(in_rows), list(out_rows))
    return win


def mixer32_window(W, H=4):
    """john's 3:2 pure-leftover mixer: inputs E rows 2,3 (A, B full belts); outputs W rows 1,2,3 =
    3/5A+2/5B, 2/5A, 3/5B."""
    D = 60
    win = Window(W, H, 2, D)
    win.ports[((W - 1, 2), 3)] = ('in', [60, 0])
    win.ports[((W - 1, 3), 3)] = ('in', [0, 60])
    win.ports[((0, 1), 1)] = ('out', [36, 24])
    win.ports[((0, 2), 1)] = ('out', [24, 0])
    win.ports[((0, 3), 1)] = ('out', [0, 36])
    win.name = 'mix3:2 %dx%d' % (W, H)
    return win


def needed_unit_mult(design, ins, D, ncom, rates):
    """Smallest u so that every per-commodity flow on every link of the design is a whole number of D*u units."""
    import math
    src = {}
    for i, (p, d) in enumerate(ins):
        v = [Fraction(0)] * ncom
        v[i if ncom > 1 else 0] = Fraction(rates[i])
        src[p] = [a + b for a, b in zip(src.get(p, [0] * ncom), v)]
    inflow, drained = flows(design, src, set())
    nout = {p: 0 for p in design.tiles}
    for p, q, k in design.links():
        nout[p] += 1
    den = 1
    for p, v in inflow.items():
        k = nout[p] if design.tiles[p][0] == 'S' and nout[p] else 1
        for x in v:
            den = math.lcm(den, (x * D).denominator, (x * D / k).denominator)
    return den


def window_of_design(design, ins, outs, mix):
    """Pinned window for a finished design (normalized to 0,0). ins: [(tile, dir items move)], outs: [(tile, dir)].
    Ports grouped per side in lane order; rates default (as full as outputs allow)."""
    W, H = design.size
    def grp(lst, inp):
        sides = {}
        for p, d in lst:
            side = SIDE_OF_OUTDIR[opposite(d) if inp else d]
            off = p[1] if side in 'EW' else p[0]
            sides.setdefault(side, []).append(off)
        return sides
    gi, go = grp(ins, True), grp(outs, False)
    sp = S.Spec([S.Group(s, len(v), positions=v) for s, v in gi.items()],
                [S.Group(s, len(v), positions=v) for s, v in go.items()], mix=mix)
    win = sp.window(W, H)
    if win is None:
        raise ValueError('ports do not fit')
    # finer flow units when the design's inner flows need them (splitter outputs get 1/k of a tile's flow;
    # outputs into the edge count too: use sinks-free flows only to get each tile's inflow)
    order = sorted(ins, key=lambda t: 0)
    u = needed_unit_mult(design, ins_in_lane_order(win, ins), win.D, win.ncom, [Fraction(sum(v), win.D) for v in lane_vecs(win)])
    if u > 1:
        sp = S.Spec(sp.inputs, sp.outputs, mix=mix, unit_mult=u)
        win = sp.window(W, H)
    return win, sp


def ins_in_lane_order(win, ins):
    """the inputs (tile, dir) ordered by which commodity their port carries"""
    def key(t):
        v = win.ports[(t[0], opposite(t[1]))][1]
        return max(range(len(v)), key=lambda i: v[i])
    return sorted(ins, key=key)


def lane_vecs(win):
    vs = [v for (p, d), (k, v) in win.ports.items() if k == 'in']
    return sorted(vs, key=lambda v: max(range(len(v)), key=lambda i: v[i]))


def pin(win, design):
    win.fixed = {(x, y): design.tiles.get((x, y)) for x in range(win.W) for y in range(win.H)}
    return win


def to_json(win, name=None):
    assert not win.port_options, 'free port positions not exported'
    return dict(name=name or getattr(win, 'name', ''), W=win.W, H=win.H, D=win.D, ncom=win.ncom,
                ports=[[p[0], p[1], d, k, list(v)] for (p, d), (k, v) in sorted(win.ports.items())],
                fixed=[[p[0], p[1], c] for p, c in sorted(win.fixed.items())])


def from_json(js):
    win = Window(js['W'], js['H'], js['ncom'], js['D'])
    for x, y, d, k, v in js['ports']:
        win.ports[((x, y), d)] = (k, list(v))
    win.fixed = {(x, y): c for x, y, c in js.get('fixed', [])}
    win.name = js.get('name', '')
    return win


def save(win, path, name=None):
    with open(path, 'w') as f:
        json.dump(to_json(win, name), f)


def grid_text(grid, W, H):
    return '\n'.join(''.join(grid.get((x, y)) or '..' for x in range(W)) for y in range(H))


def parse_grid(text):
    return Design.from_text(text).tiles


def exact_check(win, grid):
    """Independent exact check of a full grid against a pinned window, using design.flows (fractions), the same
    engine as bmsolver/check.py, generalised to arbitrary per-port target vectors. Returns (ok, reason)."""
    tiles = {p: c for p, c in grid.items() if c}
    for p in tiles:
        if not win.inside(p):
            return False, 'tile outside window %s' % (p,)
    for p, c in win.fixed.items():
        if tiles.get(p) != c:
            return False, 'fixed tile %s changed' % (p,)
    if not tiles:
        return False, 'empty'
    d = Design(tiles)
    D = win.D
    # pieces that no input reaches carry nothing: the game ignores them (a solver without an objective may leave
    # such stray pieces). Drop them before the flow check, so a stray closed loop can't make flows() singular.
    reach, stack, nxt = set(), [p for (p, od), (k, v) in win.ports.items() if k == 'in' and p in tiles], {}
    for p, q, k in d.links():
        nxt.setdefault(p, []).append(q)
    while stack:
        p = stack.pop()
        if p in reach:
            continue
        reach.add(p)
        stack += nxt.get(p, [])
    stray = [p for p in tiles if p not in reach]
    if stray:
        tiles = {p: c for p, c in tiles.items() if p in reach}
        d = Design(tiles)
    srcs, sinks, want = {}, set(), {}
    for (p, od), (k, v) in win.ports.items():
        if k == 'in':
            c = tiles.get(p)
            from bmsolver.pieces import accepts
            if c is None or not accepts(c, opposite(od)):
                return False, 'input port %s not accepted' % (p,)
            srcs[p] = [a + Fraction(b, D) for a, b in zip(srcs.get(p, [0] * win.ncom), v)]
        elif k == 'out':
            sinks.add((p, od))
            want[(p, od)] = [Fraction(b, D) for b in v]
    # pieces pushing out across an edge with no port (belts/exits/splitter fronts would spill, splitter sides
    # facing a portless edge are ignored by the game -> flows() only counts linked outputs + sinks)
    from bmsolver.pieces import push_dirs, step
    for p, c in tiles.items():
        for dd in push_dirs(c):
            q = step(p, dd)
            if not win.inside(q) and (p, dd) not in sinks and c[0] in 'BO':
                return False, 'piece %s %s pushes out of window with no port' % (p, c)
    # an inside piece that could accept from outside where an input port isn't is fine (nothing outside)
    try:
        inflow, drained = flows(d, srcs, sinks)
    except ValueError:
        return False, 'loop with no exit'
    for s, w in want.items():
        got = drained.get(s, [Fraction(0)] * win.ncom)
        if list(got) != w:
            return False, 'port %s gets %s want %s' % (s, [str(g) for g in got], [str(x) for x in w])
    outs = {p: 0 for p in tiles}
    for p, q, k in d.links():
        outs[p] += 1
    sink_t = {p for p, _ in sinks}
    for p, v in inflow.items():
        if sum(v) > 1:
            return False, 'overfull %s' % (p,)
        if sum(v) > 0 and outs[p] == 0 and p not in sink_t:
            return False, 'stuck %s' % (p,)
        if sum(v) == 0:
            return False, 'idle piece %s' % (p,)
    return True, 'ok' if not stray else 'ok (%d stray pieces ignored)' % len(stray)


def reachable(win, tiles):
    """the tiles some input port reaches through links (the rest carry nothing: strays)"""
    d = Design(tiles)
    reach, stack, nxt = set(), [p for (p, od), (k, v) in win.ports.items() if k == 'in' and p in tiles], {}
    for p, q, k in d.links():
        nxt.setdefault(p, []).append(q)
    while stack:
        p = stack.pop()
        if p in reach:
            continue
        reach.add(p)
        stack += nxt.get(p, [])
    return {p: c for p, c in tiles.items() if p in reach}


def check_py(win, grid):
    """bmsolver/check.py itself, for balancer windows (equal shares): mix when ncom > 1. Stray pieces (no input
    reaches them) are dropped first, as in exact_check."""
    from bmsolver.check import check
    tiles = reachable(win, {p: c for p, c in grid.items() if c})
    ins = sorted([(p, opposite(d)) for (p, d), (k, v) in win.ports.items() if k == 'in'],
                 key=lambda t: max(range(win.ncom), key=lambda i: win.ports[(t[0], opposite(t[1]))][1][i]))
    outs = sorted([(p, d) for (p, d), (k, v) in win.ports.items() if k == 'out'], key=lambda t: (t[0][1], t[0][0]))
    rates = [Fraction(sum(win.ports[(p, opposite(d))][1]), win.D) for p, d in ins]
    r = check(Design(tiles), ins, outs, rates=rates, mix=win.ncom > 1)
    return r['ok'], r
