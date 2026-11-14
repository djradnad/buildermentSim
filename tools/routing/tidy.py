"""Greedy tidy of a found layout: swap splitters for belts and underground pairs for belt runs wherever the exact
flow check still passes (john: splitters only where they split or shield a side; plain belts over hops).

    python tools/routing/tidy.py --n 3 --grid file.txt
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from bmsolver import spec as S, bench  # noqa: E402
from bmsolver.pieces import DIRS  # noqa: E402


def parse(rows):
    g = {}
    for y, row in enumerate(rows):
        for x in range(0, len(row), 2):
            if row[x:x + 2] != '..':
                g[(x // 2, y)] = row[x:x + 2]
    return g, len(rows[0]) // 2, len(rows)


def ok(g, n, W, H):
    win = S.Spec([S.Group('E', n, at=0)], [S.Group('W', n, at=0)]).window(W, H)
    return bench.verify(g, win, [0] * len(win.port_options) if win.port_options else None)


def tidy(g, n, W, H):
    changed = True
    while changed:
        changed = False
        for p, c in sorted(g.items()):
            if c[0] == 'S':
                for o in (c[1],) + tuple(str(d) for d in range(4) if str(d) != c[1]):
                    g2 = dict(g); g2[p] = 'B' + o
                    if ok(g2, n, W, H):
                        g, changed = g2, True
                        break
            elif c[0] == 'I':
                d = int(c[1]); dx, dy = DIRS[d]
                for k in range(1, 8):
                    q = (p[0] + k * dx, p[1] + k * dy)
                    if g.get(q) == 'O' + c[1]:
                        run = [(p[0] + j * dx, p[1] + j * dy) for j in range(k + 1)]
                        if all(t in (p, q) or t not in g for t in run):
                            g2 = dict(g)
                            for t in run:
                                g2[t] = 'B' + c[1]
                            if ok(g2, n, W, H):
                                g, changed = g2, True
                        break
            if changed:
                break
        if not changed:
            g2 = pair_pass(g, n, W, H) or chain_pass(g, n, W, H)
            if g2 is not None:
                g, changed = g2, True
    return g


def opts(c):
    return (c[1],) + tuple(str(d) for d in range(4) if str(d) != c[1])


def pair_pass(g, n, W, H, reach=4):
    """Splitters that only work as a pair (each alone fails the check): swap two nearby ones at once."""
    sp = sorted(p for p, c in g.items() if c[0] == 'S')
    for i, p in enumerate(sp):
        for q in sp[i + 1:]:
            if abs(p[0] - q[0]) + abs(p[1] - q[1]) > reach:
                continue
            for a in opts(g[p]):
                for b in opts(g[q]):
                    g2 = dict(g); g2[p] = 'B' + a; g2[q] = 'B' + b
                    if ok(g2, n, W, H):
                        return g2
    return None


def chain_pass(g, n, W, H, cap=10):
    """Chains of touching splitters where none splits (each only shields its neighbour from spill): any one alone
    fails, so swap whole connected subsets (largest first) to belts in their own direction."""
    from itertools import combinations
    sp = {p for p, c in g.items() if c[0] == 'S'}
    seen = set()
    for p0 in sorted(sp):
        if p0 in seen:
            continue
        comp, st = [], [p0]
        seen.add(p0)
        while st:
            p = st.pop(); comp.append(p)
            for dx, dy in DIRS.values():
                q = (p[0] + dx, p[1] + dy)
                if q in sp and q not in seen:
                    seen.add(q); st.append(q)
        if len(comp) < 3:
            continue
        comp = sorted(comp)[:cap]
        for k in range(len(comp), 2, -1):
            for sub in combinations(comp, k):
                g2 = dict(g)
                for p in sub:
                    g2[p] = 'B' + g[p][1]
                if ok(g2, n, W, H):
                    return g2
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, required=True)
    ap.add_argument('--grid', required=True)
    a = ap.parse_args()
    g, W, H = parse([l.rstrip('\n') for l in open(os.path.expanduser(a.grid)) if l.strip()])
    assert ok(g, a.n, W, H), 'input layout fails the exact check'
    g = tidy(g, a.n, W, H)
    pc = lambda k: sum(1 for c in g.values() if c[0] == k)
    print('pieces %d, splitters %d, hop ends %d' % (len(g), pc('S'), pc('I') + pc('O')))
    print('\n'.join(''.join(g.get((x, y), '..') for x in range(W)) for y in range(H)))


if __name__ == '__main__':
    main()
