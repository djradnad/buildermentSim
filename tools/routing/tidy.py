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
    from bmsolver.pieces import accepts, push_dirs
    nin = nout = 0                      # bench.verify skips missing ports: insist on all n ins and n outs
    for (t, d), spec in list(win.ports.items()) + [kv for gr in (win.port_options or []) for kv in gr[0].items()]:
        if True:
            c = g.get(t)
            if c and spec[0] == 'in' and accepts(c, (d + 2) % 4):
                nin += 1
            elif c and spec[0] == 'out' and d in push_dirs(c):
                nout += 1
    if nin != n or nout != n:
        return False
    try:
        return bench.verify(g, win, [0] * len(win.port_options) if win.port_options else None)
    except Exception:   # a port tile was removed or broken
        return False


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
            g2 = false_pass(g, n, W, H) or pair_pass(g, n, W, H) or chain_pass(g, n, W, H)
            if g2 is not None:
                g, changed = g2, True
    return g


def false_pass(g, n, W, H):
    """Splitters that send everything one way (one link out) are belts in disguise, often in chains or L shapes
    where each shields the next. Swap them all at once to belts facing their one way out (a side way out gives a
    curved belt); any new belt that a real splitter would now spill into stays a splitter; repeat to a fixed point,
    then one exact check (bisect if it fails)."""
    from bmsolver.design import Design
    def links_of(t):
        out = {}
        for p, q, k in Design(t).links():
            out.setdefault(p, []).append(q)
        return out
    L = links_of(g)
    cand = {}
    for p, c in g.items():
        if c[0] != 'S':
            continue
        qs = L.get(p, [])
        sinks = [d for d in range(4) if (p[0] == 0 and d == 1)]   # west edge outputs drain
        if len(qs) + len(sinks) == 1:
            q = qs[0] if qs else None
            d = [k for k, v in DIRS.items() if q and (p[0] + v[0], p[1] + v[1]) == q]
            cand[p] = 'B%d' % (d[0] if d else 1)
    if not cand:
        return None
    feeders = {}
    for p, qs in L.items():
        for q in qs:
            feeders.setdefault(q, set()).add(p)
    while True:
        g2 = dict(g); g2.update(cand)
        L2 = links_of(g2)
        bad = set()
        for p2, qs in L2.items():
            for q in qs:
                if q in cand and p2 not in feeders.get(q, set()):
                    bad.add(q)
        if not bad:
            break
        for q in bad:
            del cand[q]
        if not cand:
            return None
    items = sorted(cand.items())
    def tryset(sub):
        g2 = dict(g); g2.update(dict(sub))
        return g2 if ok(g2, n, W, H) else None
    r = tryset(items)
    if r is not None:
        return r
    while len(items) > 1:                       # keep the half that still checks
        h = len(items) // 2
        for part in (items[:h], items[h:]):
            r = tryset(part)
            if r is not None:
                return r
        items = items[:h]
    return None


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


GOLD = {'B': 4, 'S': 10, 'I': 10, 'O': 10}
CODES = [k + str(d) for k in 'BSIO' for d in range(4)]


def score(g):
    """site order inside a box: pieces, then splitters, then gold (belts over hops when pieces tie)"""
    return (len(g), sum(1 for c in g.values() if c[0] == 'S'), sum(GOLD[c[0]] for c in g.values()))


def moves(g, W, H):
    tiles = sorted(g)
    for p in tiles:                                         # dead tile
        yield {p: None}
    for p in tiles:                                         # hop instead of a straight belt run (fewer pieces)
        c = g[p]
        if c[0] != 'B':
            continue
        dx, dy = DIRS[int(c[1])]
        for k in range(2, 8):
            q = (p[0] + k * dx, p[1] + k * dy)
            run = [(p[0] + j * dx, p[1] + j * dy) for j in range(k + 1)]
            if any(g.get(t) != c for t in run):
                break
            m = {t: None for t in run[1:-1]}
            m[p] = 'I' + c[1]; m[q] = 'O' + c[1]
            yield m
    for p in tiles:                                         # two tiles in a line: drop both (merge hop chains),
        for d in range(4):                                  # or move one onto the other (shift a hop end)
            dx, dy = DIRS[d]
            for k in range(1, 8):
                q = (p[0] + k * dx, p[1] + k * dy)
                if q in g:
                    yield {p: None, q: None}
                    if k == 1:
                        yield {p: g[q], q: None}
    for p in tiles:                                         # any other piece in place
        for c in CODES:
            if c != g[p]:
                yield {p: c}


def polish(g, n, W, H):
    """john-style cleanup: take any local change that lowers (pieces, splitters, gold) and keeps the exact check"""
    best = score(g)
    improved = True
    while improved:
        improved = False
        for m in moves(g, W, H):
            g2 = dict(g)
            for t, c in m.items():
                if c is None:
                    g2.pop(t, None)
                else:
                    g2[t] = c
            sc = score(g2)
            if sc < best and ok(g2, n, W, H):
                g, best, improved = g2, sc, True
                break
    return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, required=True)
    ap.add_argument('--grid', required=True)
    a = ap.parse_args()
    g, W, H = parse([l.rstrip('\n') for l in open(os.path.expanduser(a.grid)) if l.strip()])
    assert ok(g, a.n, W, H), 'input layout fails the exact check'
    g = polish(tidy(g, a.n, W, H), a.n, W, H)
    pc = lambda k: sum(1 for c in g.values() if c[0] == k)
    print('pieces %d, splitters %d, hop ends %d, gold %d' % (len(g), pc('S'), pc('I') + pc('O'), score(g)[2]))
    print('\n'.join(''.join(g.get((x, y), '..') for x in range(W)) for y in range(H)))


if __name__ == '__main__':
    main()
