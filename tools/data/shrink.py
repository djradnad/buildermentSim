"""Shrink a found N:N layout one column or row at a time (band LNS): delete column c, keep every tile outside a
band of b columns around it fixed, and let the complete flow model re-solve the band. A hit is checked exactly and
shrunk again. Good for big ratios (5:5, 6:6, ...) where a whole-box solve runs out of time.

    python tools/data/shrink.py --n 5 --grid file.txt --timeout 60 --out ~/data/shrink_5.jsonl
The grid file holds one row per line in the catalogue format ('..' empty, 2 chars per tile), inputs east, outputs
west.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from bmsolver import spec as S, model, bench  # noqa: E402


def parse(rows):
    g = {}
    for y, row in enumerate(rows):
        for x in range(0, len(row), 2):
            c = row[x:x + 2]
            if c != '..':
                g[(x // 2, y)] = c
    return g, len(rows[0]) // 2, len(rows)


def rows_of(g, W, H):
    return [''.join(g.get((x, y)) or '..' for x in range(W)) for y in range(H)]


UNIT_MULT = 1


def auto_unit(n, g, W, H):
    """smallest unit multiplier so every exact flow of the layout is a whole number of units (loops bring in
    1/15, 1/21 ... and later halvings divide those further)"""
    from fractions import Fraction as F
    from math import lcm
    from bmsolver.design import Design, flows
    src = {(W - 1, y): [F(int(i == y)) for i in range(n)] for y in range(n)}
    inflow, _ = flows(Design(g), src, set(((0, y), 1) for y in range(n)))
    L = 1
    for v in inflow.values():
        for x in v:
            L = lcm(L, F(x).denominator)
    D = S.Spec([S.Group('E', n)], [S.Group('W', n)]).D
    return lcm(D, L) // D


def drop(g, axis, c):
    out = {}
    for (x, y), v in g.items():
        k = (x, y)[axis]
        if k == c:
            continue
        if k > c:
            x, y = (x - 1, y) if axis == 0 else (x, y - 1)
        out[x, y] = v
    return out


def attempt(n, g, W, H, axis, c, b, timeout, workers):
    g2 = drop(g, axis, c)
    W2, H2 = (W - 1, H) if axis == 0 else (W, H - 1)
    win = S.Spec([S.Group('E', n)], [S.Group('W', n)], unit_mult=UNIT_MULT).window(W2, H2)
    if win is None:
        return None
    lo, hi = c - b // 2, c - b // 2 + b
    win.fixed = {p: g2.get(p) for p in [(x, y) for x in range(W2) for y in range(H2)]
                 if not (lo <= p[axis] < hi)}
    r = model.solve(win, timeout=timeout, workers=workers)
    if 'grid' in r and bench.verify(r['grid'], win, r.get('options')):
        return r['grid'], W2, H2
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, required=True)
    ap.add_argument('--grid', required=True)
    ap.add_argument('--timeout', type=float, default=60)
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--bands', default='4,6,8')
    ap.add_argument('--out', required=True)
    ap.add_argument('--unit-mult', type=int, default=0, help='finer flow units (loops with 1/15 belts inside chains)')
    a = ap.parse_args()
    global UNIT_MULT
    g, W, H = parse([l.rstrip('\n') for l in open(a.grid) if l.strip()])
    sys.path.insert(0, os.path.dirname(__file__))
    import relax as _r
    g = _r.prune(g, W, H)
    UNIT_MULT = a.unit_mult or auto_unit(a.n, g, W, H)
    print('flow unit multiplier', UNIT_MULT, flush=True)
    failed = set()

    def key(g, W, H, axis, c, b):
        """a band's problem is fixed by the tiles within underground reach of it and the exact flows on them"""
        from fractions import Fraction as F
        from bmsolver.design import Design, flows
        lo, hi = c - b // 2 - 8, c - b // 2 + b + 8
        tiles = sorted(p for p in g if lo <= p[axis] < hi)
        if not hasattr(key, 'cache') or key.cache[0] is not g:
            src = {(W - 1, y): [F(int(i == y)) for i in range(a.n)] for y in range(a.n)} if axis == 0 or True else {}
            try:
                inflow, _ = flows(Design(g), src, set(((0, y), 1) for y in range(a.n)))
            except ValueError:
                inflow = {}
            key.cache = (g, inflow)
        inflow = key.cache[1]
        rel = tuple(((p[0] - (c if axis == 0 else 0), p[1] - (c if axis == 1 else 0)), g[p],
                     tuple(inflow.get(p, ()))) for p in tiles)
        return (axis, b, rel)

    # stage 0 (john's easy-first pipeline): free drops, no solver: delete a line and keep it if the exact check passes
    def free_drops(g, W, H):
        changed = True
        while changed:
            changed = False
            for axis in ((0, 1) if W >= H else (1, 0)):
                c = 1
                while c < (W if axis == 0 else H) - 1:
                    g2 = drop(g, axis, c)
                    W2, H2 = (W - 1, H) if axis == 0 else (W, H - 1)
                    win = S.Spec([S.Group('E', a.n)], [S.Group('W', a.n)], unit_mult=UNIT_MULT).window(W2, H2)
                    if win is not None and bench.verify(g2, win, None):
                        g, W, H = g2, W2, H2
                        print('%dx%d free drop %s %d' % (W, H, 'col' if axis == 0 else 'row', c), flush=True)
                        with open(os.path.expanduser(a.out), 'a') as fh:
                            fh.write(json.dumps(dict(n=a.n, W=W, H=H, grid=rows_of(g, W, H))) + '\n')
                        changed = True
                        continue
                    c += 1
        return g, W, H
    import relax
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'routing'))
    import tidy

    def straighten(g, W, H):
        """stage 0 (john's hand method): slide parts together over plain belt stretches, no solver, one check"""
        g1 = tidy.false_pass(g, a.n, W, H) or g          # disguised belts first (any shape, curved too)
        g2, W2, _ = relax.compact(g1, W, H)
        if g2 is None:
            g2, W2 = g1, W
        if g2 is g:
            return g, W, H
        win = S.Spec([S.Group('E', a.n)], [S.Group('W', a.n)], unit_mult=UNIT_MULT).window(W2, H)
        if win is None or not bench.verify(g2, win, None):
            if g1 is g:
                return g, W, H
            g2, W2 = g1, W                              # the swaps alone (tidy checked them)
        print('%dx%d straightened (was %d wide, %d splitters left)' % (W2, H, W, sum(c[0] == 'S' for c in g2.values())), flush=True)
        with open(os.path.expanduser(a.out), 'a') as fh:
            fh.write(json.dumps(dict(n=a.n, W=W2, H=H, grid=rows_of(g2, W2, H))) + '\n')
        return g2, W2, H
    if os.environ.get('FREE_DROPS') == '1':
        g, W, H = free_drops(g, W, H)
    g, W, H = straighten(g, W, H)
    import seam

    def seam_stage(g, W, H):
        """john's staircase cuts: one tile per row, pieces kept whole, exact check"""
        win_of = lambda W2: S.Spec([S.Group('E', a.n)], [S.Group('W', a.n)], unit_mult=UNIT_MULT).window(W2, H)
        chk = lambda g2, W2: (lambda w: w is not None and bench.verify(g2, w, None))(win_of(W2))

        def log(g2, W2, path):
            print('%dx%d seam cut %s' % (W2, H, path), flush=True)
            with open(os.path.expanduser(a.out), 'a') as fh:
                fh.write(json.dumps(dict(n=a.n, W=W2, H=H, grid=rows_of(g2, W2, H))) + '\n')
        g2, W2 = seam.carve(g, W, H, chk, log)
        return g2, W2, H
    g, W, H = seam_stage(g, W, H)

    resume = {}
    improved = True
    while improved:
        improved = False
        # shrink the longer side first (most square first)
        axes = tuple(int(v) for v in os.environ['SHRINK_AXES'].split(',')) if os.environ.get('SHRINK_AXES') else ((0, 1) if W >= H else (1, 0))
        for axis in axes:
            n_lines = W if axis == 0 else H
            for b in map(int, a.bands.split(',')):
                cs = list(range(1, n_lines - 1))
                k0 = resume.get((axis, b), 0) % max(1, len(cs))
                for c in cs[k0:] + cs[:k0]:         # carry on from the last hit instead of restarting west
                    t = time.time()
                    k = key(g, W, H, axis, c, b)
                    if k in failed:
                        continue
                    r = attempt(a.n, g, W, H, axis, c, b, a.timeout, a.workers)
                    if not r:
                        failed.add(k)
                    print('%dx%d drop %s %d band %d: %s %.0fs' % (W, H, 'col' if axis == 0 else 'row', c, b,
                                                                  'ok' if r else '-', time.time() - t), flush=True)
                    if r:
                        resume[(axis, b)] = c
                        g, W, H = straighten(*r)
                        with open(os.path.expanduser(a.out), 'a') as fh:
                            fh.write(json.dumps(dict(n=a.n, W=W, H=H, grid=rows_of(g, W, H))) + '\n')
                        print('\n'.join(rows_of(g, W, H)), flush=True)
                        improved = True
                        break
                if improved:
                    break
            if improved:
                break
        if not improved:                        # windows stuck: try seam cuts once more
            W0 = W
            g, W, H = seam_stage(g, W, H)
            improved = W < W0


if __name__ == '__main__':
    main()
