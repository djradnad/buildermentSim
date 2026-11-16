"""All-width impossibility proofs by column-line states (john 2026-11-14: N:N at N tall).

A LINE is the border between two columns. What crosses it fully describes how the two sides interact: per row,
a surface belt going west or east (with its flow of every item), and underground hops going west or east, each
with how far back its entrance is (its OFFSET) and its flow. If two lines of a layout have the same state, the
columns between them can be cut out and the two sides glued: every belt and hop still meets the same piece, every
hop keeps its length (offsets match) and the first-exit rule still holds. So a layout of the smallest width has
all its line states different.

STEP: an open strip of width K (inputs on its east edge as in the box, the west edge an open line with free
flows) whose K+1 line states are all different. If no such strip exists, no smallest layout is K or more wide,
so (with every closed box narrower than K already proven impossible) none exists at any width. As with the flow
model this is exact up to its flow unit D.

    python -m bmsolver.strip --n 2 --height 2 --k 1..12 --engine ~/solvers/kissat
"""
import argparse
import os
import time

from .model import Model
from .pieces import UG_REACH


def line_states(mod, win):
    """per line l (between columns l and l+1, l = -1 .. W-1): list of slots (active literal or 0/1, flow vector)"""
    m, W, H, nc = mod.m, win.W, win.H, win.ncom
    F = mod.F
    slots = {l: {} for l in range(-1, W)}          # l -> key -> [(lit, link index or vec)]

    def put(l, key, lit, vec):
        slots[l].setdefault(key, []).append((lit, vec))
    for i, (p, q, lit, kind) in enumerate(mod.links):
        if kind == 'push' and p[1] == q[1] and p[0] != q[0]:
            l = min(p[0], q[0])
            put(l, ('s', p[1], 'W' if q[0] < p[0] else 'E'), lit, [F[i, c] for c in range(nc)])
        elif kind == 'ug' and p[1] == q[1] and p[0] != q[0]:
            if q[0] < p[0]:
                for l in range(q[0], p[0]):
                    put(l, ('t', p[1], 'W', p[0] - l), lit, [F[i, c] for c in range(nc)])
            else:
                for l in range(p[0], q[0]):
                    put(l, ('t', p[1], 'E', l - p[0] + 1), lit, [F[i, c] for c in range(nc)])
    for i, p in mod.open_tout:
        lit = mod.links[i][2]
        for l in range(-1, p[0]):
            put(l, ('t', p[1], 'W', p[0] - l), lit, [F[i, c] for c in range(nc)])
    for i, q, mm in mod.open_tin:
        lit = mod.links[i][2]
        for l in range(-1, q[0]):
            put(l, ('t', q[1], 'E', l + mm + 1), lit, [F[i, c] for c in range(nc)])
    # what each side offers across the line, not only the links: a tile pushing east into one that doesn't accept
    # makes no link, but after a cut it would meet another tile; so the state also holds, per row, whether the west
    # tile pushes east / accepts westbound items and the east tile pushes west / accepts eastbound items
    X = mod.X
    V = lambda p, k, o: X.get((p, k + str(o % 4)))
    nz = lambda lst: [v for v in lst if v is not None]
    pushes = lambda p, d: nz([V(p, 'B', d), V(p, 'S', d), V(p, 'S', d + 1), V(p, 'S', d + 3), V(p, 'O', d)])
    accepts = lambda q, d: nz([V(q, 'B', o) for o in range(4) if o != (d + 2) % 4] + [V(q, 'S', d), V(q, 'I', d)])
    for l in range(-1, W - 1):
        for y in range(H):
            a, b = (l, y), (l + 1, y)
            if l >= 0:
                put(l, ('pE', y), sum(pushes(a, 3)), None)
                put(l, ('aW', y), sum(accepts(a, 1)), None)
            else:
                put(l, ('pE', y), m.NewBoolVar(''), None)       # the tile west of the strip: unknown
                put(l, ('aW', y), m.NewBoolVar(''), None)
            put(l, ('pW', y), sum(pushes(b, 1)), None)
            put(l, ('aE', y), sum(accepts(b, 3)), None)
    # the east edge of the box is a line too: the inputs come in on the surface, nothing else crosses
    inp = {t[1]: list(sp[1]) for (t, d), sp in win.ports.items() if (sp[0] if isinstance(sp, tuple) else sp) == 'in'}
    for y in range(H):
        a = (W - 1, y)
        put(W - 1, ('pE', y), sum(pushes(a, 3)), None)
        put(W - 1, ('aW', y), sum(accepts(a, 1)), None)
        put(W - 1, ('pW', y), 1 if y in inp else 0, None)
        put(W - 1, ('aE', y), 0, None)
        if y in inp:
            put(W - 1, ('s', y, 'W'), 1, inp[y])
    for y, (u_in, u_out, fi, fo) in mod.open_surface.items():
        put(-1, ('s', y, 'W'), u_out, fo)
        put(-1, ('s', y, 'E'), u_in, fi)
    # the east edge of the box: the inputs, nothing else
    return slots


def build(win, K_lines=None):
    mod = Model(win)
    m, W, H, nc, D = mod.m, win.W, win.H, win.ncom, win.D
    slots = line_states(mod, win)
    keys = sorted({k for l in slots for k in slots[l]}, key=str)
    # per line, per key: active (bool) and flow vector (ints); a slot may have several candidate links but at most
    # one is active at a time (one belt between two tiles in one direction; one hop per entrance offset)
    act, vec = {}, {}
    lines = list(range(-1, W))
    for l in lines:
        for k in keys:
            cands = slots[l].get(k, [])
            a = m.NewBoolVar('')
            if cands:
                m.Add(sum(c[0] for c in cands) == a)
            else:
                m.Add(a == 0)
            v = []
            for c in range(nc):
                x = m.NewIntVar(0, D, '')
                if cands and cands[0][1] is not None:
                    m.Add(x == sum(cv[1][c] for cv in cands))
                else:
                    m.Add(x == 0)
                v.append(x)
            act[l, k], vec[l, k] = a, v
    # in a real layout every input goes west across every line, net one full belt of it (the box has no other
    # ports): west-going flow minus east-going flow of each item is exactly one belt on every line
    for l in lines:
        for c in range(nc):
            wv = [vec[l, k][c] for k in keys if k[0] in ('s', 't') and k[2] == 'W']
            ev = [vec[l, k][c] for k in keys if k[0] in ('s', 't') and k[2] == 'E']
            m.Add(sum(wv) - sum(ev) == D)
    for a_, b_ in [(i, j) for i in lines for j in lines if j < i]:
        diffs = []
        for k in keys:
            x, y = act[a_, k], act[b_, k]
            da = m.NewBoolVar('')
            m.AddBoolOr([x, y]).OnlyEnforceIf(da)
            m.AddBoolOr([x.Not(), y.Not()]).OnlyEnforceIf(da)
            m.Add(x == y).OnlyEnforceIf(da.Not())
            diffs.append(da)
            for c in range(nc):
                u, w = vec[a_, k][c], vec[b_, k][c]
                gt, lt = m.NewBoolVar(''), m.NewBoolVar('')
                m.Add(u >= w + 1).OnlyEnforceIf(gt)
                m.Add(u <= w - 1).OnlyEnforceIf(lt)
                diffs += [gt, lt]
        m.AddBoolOr(diffs)
    return mod


def window(n, K, H):
    from . import spec as S
    win = S.Spec([S.Group('E', n, at=0)], [S.Group('W', n, at=0)]).window(K, H)
    # the strip keeps the box's east edge (inputs) and opens its west edge; drop the output ports
    win.ports = {k: v for k, v in win.ports.items() if (v[0] if isinstance(v, tuple) else v) != 'out'}
    win.port_options = [g for g in win.port_options if not any(sp[0] == 'out' for opt in g for sp in opt.values())]
    win.open_west = True
    return win


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, required=True)
    ap.add_argument('--height', type=int, required=True)
    ap.add_argument('--k', default='1..10')
    ap.add_argument('--engine', default='~/solvers/kissat')
    ap.add_argument('--timeout', type=float, default=3600)
    a = ap.parse_args()
    lo, hi = map(int, a.k.split('..'))
    from . import cnf
    for K in range(lo, hi + 1):
        win = window(a.n, K, a.height)
        t = time.time()
        mod = build(win)
        r = cnf.solve(mod, engine=os.path.expanduser(a.engine), timeout=a.timeout)
        print('%d:%d %d tall, strip K=%d: %s %.1fs' % (a.n, a.n, a.height, K, r['status'], time.time() - t), flush=True)
        if r['status'] == 'FEASIBLE':
            g = r['grid']
            for y in range(a.height):
                print(' '.join(g.get((x, y)) or '..' for x in range(K)))
        if r['status'] == 'INFEASIBLE':
            print('-> no smallest layout is %d or more wide; with every box narrower than %d proven impossible, '
                  'N:N at this height is impossible at every width (up to flow unit D=%d)' % (K, K, win.D))
            break


if __name__ == '__main__':
    main()
