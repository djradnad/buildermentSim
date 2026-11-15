"""Untangle (john 2026-11-15): a rectangle whose belts enter only from the east and leave only to the west, and
whose exact transfer is the identity (each entering belt leaves on its own row, whole and unmixed), is only
transport, however it weaves, splits and merges back inside. Replace it with straight belt stretches (hops
where an outside splitter could spill in), then relax.compact deletes the slack columns. One exact check.

    python tools/data/untangle.py --n 2 --grid ~/data/ex/s6yhr6.txt
"""
import argparse
import os
import sys
from fractions import Fraction as F

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
import relax as R  # noqa: E402
from bmsolver import spec as S, bench  # noqa: E402
from bmsolver.design import Design, flows  # noqa: E402


def transparent(g, links, x0, x1, y0, y1, ports=((), ())):
    """None, or the rows that carry a belt through the rectangle when it is pure identity transport"""
    inside = lambda p: x0 <= p[0] <= x1 and y0 <= p[1] <= y1
    ent, ext = {}, {}
    for p, q, k in links:
        a, b = inside(p), inside(q)
        if a == b:
            continue
        if k != 'push' or p[1] != q[1]:
            return None                         # tunnels or vertical moves across the border
        if b and p[0] == x1 + 1:
            ent[q[1]] = q
        elif a and q[0] == x0 - 1:
            ext[p[1]] = p
        else:
            return None
    W_in, W_out = ports                         # window edge ports: (tiles fed from outside east, tiles draining west)
    for q in W_in:
        if inside(q):
            ent[q[1]] = q
    for p in W_out:
        if inside(p):
            ext[p[1]] = p
    if len(ent) != len(ext):
        return None
    if not ent:
        return [] if any(inside(p) for p in g) else None
    sub = {p: c for p, c in g.items() if inside(p)}
    ins, outs = sorted(ent), sorted(ext)
    k = len(ins)
    src = {ent[y]: [F(int(i == j)) for j in range(k)] for i, y in enumerate(ins)}
    try:
        inflow, dr = flows(Design(sub), src, {(ext[y], 1) for y in outs})
    except (ValueError, ZeroDivisionError):
        return None
    if any(sum(v) > 1 for v in inflow.values()):
        return None
    for i, y in enumerate(outs):                # pure and in the same order: entry i leaves on the i-th exit
        if dr.get((ext[y], 1)) != [F(int(i == j)) for j in range(k)]:
            return None
    return list(zip(ins, outs))


def route_rect(w, x0, mapping):
    """belts on (entry row -> exit row), order kept: straight rows, plus one jog column per lane change.
    Upward jogs: topmost entry gets the most eastern column; downward: bottom-most first. Returns tiles or None"""
    ups = sorted([m for m in mapping if m[1] < m[0]], key=lambda m: m[0])
    downs = sorted([m for m in mapping if m[1] > m[0]], key=lambda m: -m[0])
    if len(ups) + len(downs) > w:
        return None
    col = {}
    c = x0 + w - 1
    for m in ups:
        col[m] = c; c -= 1
    c = x0 + w - 1 - len(ups)
    for m in downs:
        col[m] = c; c -= 1
    t = {}
    x1 = x0 + w - 1
    for e, o in mapping:
        if e == o:
            for x in range(x0, x1 + 1):
                t[(x, e)] = 'B1'
            continue
        cx = col[(e, o)]
        d = 'B0' if o < e else 'B2'
        for x in range(cx + 1, x1 + 1):
            t[(x, e)] = 'B1'
        for y in range(min(e, o), max(e, o) + 1):
            t[(cx, y)] = d
        t[(cx, o)] = 'B1'
        for x in range(x0, cx):
            t[(x, o)] = 'B1'
    return t


def straighten_rect(g, W, H, x0, x1, y0, y1, mapping):
    g2 = {p: c for p, c in g.items() if not (x0 <= p[0] <= x1 and y0 <= p[1] <= y1)}
    if all(e == o for e, o in mapping):
        return R.relay(g2, W, H, [(y, x0, x1) for y, _ in mapping])
    t = route_rect(x1 - x0 + 1, x0, mapping)
    if t is None:
        return None
    g2.update(t)
    return g2


def pushes_west(c):
    return bool(c) and ((c[0] in 'BO' and c[1] == '1') or (c[0] == 'S' and c[1] != '3'))


def score(g):
    return (len(g), sum(c[0] != 'B' for c in g.values()))


def untangle(g, W, H, check, maxw=None, ports=((), ())):
    """largest rectangles first; each hit is checked exactly. Returns (g, hits)"""
    hits = 0
    maxw = maxw or W
    changed = True
    while changed:
        changed = False
        links = Design(g).links()
        sizes = sorted(((w, h) for w in range(2, min(maxw, W) + 1) for h in range(1, H + 1)),
                       key=lambda s: -s[0] * s[1])
        for w, h in sizes:
            for y0 in range(0, H - h + 1):
                for x0 in range(0, W - w + 1):
                    x1, y1 = x0 + w - 1, y0 + h - 1
                    rect = [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]
                    mapping = transparent(g, links, x0, x1, y0, y1, ports)
                    if mapping is None:
                        continue
                    rows = [e for e, o in mapping] if all(e == o for e, o in mapping) else None
                    # skip if it is already plain stretches
                    want = R.lay(w, set())
                    if rows is not None and all(all(g.get((x0 + i, y)) == c for i, c in enumerate(want)) for y in rows) and \
                            all(g.get(p) is None for p in rect if p[1] not in rows):
                        continue
                    g2 = straighten_rect(g, W, H, x0, x1, y0, y1, mapping)
                    if g2 is None or score(g2) >= score(g) or not check(g2, W):
                        continue
                    g, hits, changed = g2, hits + 1, True
                    break
                if changed:
                    break
            if changed:
                break
    return g, hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, required=True)
    ap.add_argument('--grid', required=True)
    ap.add_argument('--maxw', type=int, default=16)
    ap.add_argument('--out', default=None)
    a = ap.parse_args()
    import time
    from shrink import auto_unit
    g, W, H = R.parse([l.rstrip('\n') for l in open(os.path.expanduser(a.grid)) if l.strip()])
    um = auto_unit(a.n, g, W, H) if a.n else 1

    def check(g, W):
        if a.n == 0:                            # pass-through test: every entering row leaves on its own row
            fed = {q for _, q, _ in Design(g).links()}
            ins = [y for y in range(H) if g.get((W - 1, y)) and (W - 1, y) not in fed]
            outs = [y for y in range(H) if pushes_west(g.get((0, y)))]
            src = {(W - 1, y): [F(int(i == j)) for j in range(len(ins))] for i, y in enumerate(ins)}
            try:
                _, dr = flows(Design(g), src, {((0, y), 1) for y in outs})
            except ValueError:
                return False
            return len(ins) == len(outs) and all(dr.get(((0, y), 1)) == [F(int(i == j)) for j in range(len(ins))]
                                                 for i, y in enumerate(outs))
        win = S.Spec([S.Group('E', a.n)], [S.Group('W', a.n)], unit_mult=um).window(W, H)
        return win is not None and bench.verify(g, win, None)
    t = time.time()
    W0 = W
    hits = 0
    while True:                                 # straighten, slide together, again until nothing changes
        if a.n:
            ports = ([(W - 1, y) for y in range(a.n)], [(0, y) for y in range(a.n)])
        else:
            fed = {q for _, q, _ in Design(g).links()}
            ports = ([(W - 1, y) for y in range(H) if g.get((W - 1, y)) and (W - 1, y) not in fed],
                     [(0, y) for y in range(H) if pushes_west(g.get((0, y)))])
        g, h = untangle(g, W, H, check, a.maxw, ports)
        hits += h
        g2, W2, _ = R.compact(g, W, H)
        if g2 is not None and W2 < W and check(g2, W2):
            g, W = g2, W2
        elif not h:
            break
    print('%d rectangles straightened, %d -> %d wide, %.1f s, exact %s' % (hits, W0, W, time.time() - t, check(g, W)))
    print('\n'.join(R.rows_of(g, W, H)))
    if a.out:
        open(os.path.expanduser(a.out), 'w').write('\n'.join(R.rows_of(g, W, H)) + '\n')


if __name__ == '__main__':
    main()
