"""Seam cuts (2026-11-15, from john's hand cuts on the 7:7): delete ONE tile per row, at a different column in each
row, where every deleted tile is a plain westward belt, a hop interior or empty. The east part of each row slides
one tile west. Between two neighbouring rows the seam may only move sideways across columns where those rows do not
touch (no link, no splitter, no tunnel between them), so every piece stays whole. A dynamic program picks the
seam nearest a target column; the exact flow check accepts it.

    python tools/data/seam.py --n 7 --grid ~/data/ex/b33w2r.txt --out ~/data/ex/b33w2r_seam.txt
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
import relax as R  # noqa: E402
from bmsolver import spec as S, bench  # noqa: E402
from bmsolver.design import Design  # noqa: E402


def analyse(g, W, H):
    """cuttable[y] = columns whose tile may go; tie[y] = columns where rows y and y+1 interact"""
    links = Design(g).links()
    ug_inside = [set() for _ in range(H)]
    tie = [set() for _ in range(H)]
    for p, q, k in links:
        if p[0] == q[0]:                        # vertical: a push or a tunnel joins every row pair in between
            for y in range(min(p[1], q[1]), max(p[1], q[1])):
                tie[y].add(p[0])
        elif p[1] == q[1] and k == 'ug':
            for x in range(min(p[0], q[0]) + 1, max(p[0], q[0])):
                ug_inside[p[1]].add(x)
    for (x, y), c in g.items():                 # splitters touch both neighbours even where they do not push
        if c[0] == 'S':
            if y > 0:
                tie[y - 1].add(x)
            if y < H - 1:
                tie[y].add(x)
    cut = []
    for y in range(H):
        s = set()
        for x in range(1, W - 1):
            c = g.get((x, y))
            if c == 'B1' or c is None:
                s.add(x)
        cut.append(s)
    return cut, tie


def seams(g, W, H, target):
    """the seam (x per row) with the least total distance from target, or None"""
    cut, tie = analyse(g, W, H)
    INF = 10 ** 9
    best = [{x: (abs(x - target), None) for x in cut[0]}]
    for y in range(1, H):
        cur = {}
        prev = best[-1]
        for x in cut[y]:
            bc, bp = INF, None
            for px, (c, _) in prev.items():
                lo, hi = min(px, x), max(px, x)
                if any(t in tie[y - 1] for t in range(lo + 1, hi + 1)):
                    continue                    # one row shifts there and the other does not
                if c < bc:
                    bc, bp = c, px
            if bp is not None:
                cur[x] = (bc + abs(x - target), bp)
        best.append(cur)
        if not cur:
            return None
    x = min(best[-1], key=lambda k: best[-1][k][0])
    path = [x]
    for y in range(H - 1, 0, -1):
        x = best[y][x][1]
        path.append(x)
    return list(reversed(path))


def apply_seam(g, path):
    out = {}
    for (x, y), c in g.items():
        s = path[y]
        if x == s:
            continue
        out[(x - 1 if x > s else x, y)] = c
    return out


def carve(g, W, H, check, log=None):
    changed = True
    while changed:
        changed = False
        for t in range(W - 2, 0, -1):
            path = seams(g, W, H, t)
            if path is None:
                continue
            g2 = apply_seam(g, path)
            if check(g2, W - 1):
                g, W, changed = g2, W - 1, True
                if log:
                    log(g, W, path)
    return g, W


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, required=True)
    ap.add_argument('--grid', required=True)
    ap.add_argument('--out', default=None)
    a = ap.parse_args()
    from shrink import auto_unit
    g, W, H = R.parse([l.rstrip('\n') for l in open(os.path.expanduser(a.grid)) if l.strip()])
    g = R.prune(g, W, H)
    um = auto_unit(a.n, g, W, H)

    def check(g, W):
        win = S.Spec([S.Group('E', a.n)], [S.Group('W', a.n)], unit_mult=um).window(W, H)
        return win is not None and bench.verify(g, win, None)
    t = time.time()
    W0 = W
    g, W = carve(g, W, H, check, lambda g, W, p: print('%d wide, seam %s' % (W, p), flush=True))
    print('%d -> %d wide in %.0f s, exact %s' % (W0, W, time.time() - t, check(g, W)))
    if a.out:
        open(os.path.expanduser(a.out), 'w').write('\n'.join(R.rows_of(g, W, H)) + '\n')


if __name__ == '__main__':
    main()
