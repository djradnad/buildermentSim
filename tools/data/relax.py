"""Fast column removal by straightening (2026-11-15, john's hand method): every row stretch that only carries one
belt west (plain belts, hops and the empty tiles under hops, nothing merging in from the side) is elastic: it can
be laid again at any length. A column where every row is elastic can go; the stretches it cut are laid again
(fewest pieces, empty under any splitter that could spill into them, hops at most 6 tiles apart). No solver: the
column choice is pure bookkeeping, one exact flow check at the end, bisected if it fails.

    python tools/data/relax.py --n 7 --grid ~/data/plan_n7.txt --out ~/data/n77_relaxed.txt
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from bmsolver import spec as S, bench  # noqa: E402
from bmsolver.pieces import UG_REACH  # noqa: E402

GAP = UG_REACH - 1
ADJ = 0


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


def prune(g, W, H):
    """drop tiles nothing can ever feed (closed dead loops, stray pieces): they carry nothing and, being unfed,
    were never an accepting neighbour of anything live"""
    from bmsolver.design import Design
    nxt = {}
    for p, q, k in Design(g).links():
        nxt.setdefault(p, []).append(q)
    st = [(W - 1, y) for y in range(H) if (W - 1, y) in g]
    seen = set(st)
    while st:
        p = st.pop()
        for q in nxt.get(p, []):
            if q not in seen:
                seen.add(q); st.append(q)
    return {p: c for p, c in g.items() if p in seen}


def pushes_into(g, x, y):
    """True if a vertical neighbour could put items into (x, y) were it a belt"""
    for dy, toward in ((-1, '2'), (1, '0')):
        c = g.get((x, y + dy))
        if not c:
            continue
        if c[0] == 'S' and c[1] in '0123':
            behind = {'0': '2', '2': '0'}.get(c[1])
            if behind != toward:            # a splitter pushes every way but back
                return True
        elif c[0] in 'BO' and c[1] == toward:
            return True
    return False


def elastic(g, W, H):
    """E[y] = set of columns of row y that belong to a pure westward conduit"""
    E = []
    for y in range(H):
        row = [g.get((x, y)) for x in range(W)]
        spans = []
        for x in range(W):                  # each entrance links to the first exit west of it
            if row[x] == 'I1':
                for k in range(1, UG_REACH + 1):
                    if x - k >= 0 and row[x - k] == 'O1':
                        spans.append((x - k, x))
                        break
        s = set(x for x in range(W) if row[x] == 'B1' and not pushes_into(g, x, y))
        for xo, xi in spans:                # a hop counts only whole: free ends, nothing on top
            if all(row[k] is None for k in range(xo + 1, xi)) and not pushes_into(g, xi, y):
                s.update(range(xo, xi + 1))
        E.append(s)
    return E


def runs(cols):
    out, cur = [], []
    for x in sorted(cols):
        if cur and x != cur[-1] + 1:
            out.append((cur[0], cur[-1])); cur = []
        cur.append(x)
    if cur:
        out.append((cur[0], cur[-1]))
    return out


def lay(L, exposed):
    """fewest-piece westward conduit of L tiles, index 0 = west end; exposed tiles must stay empty (hop interior).
    Returns list of codes or None"""
    INF = 10 ** 9
    # walk from the east end (index L-1) west. state: None = on surface, k = k tiles under since the entrance
    best = {None: (0, [])}
    for i in range(L - 1, -1, -1):
        nb = {}
        def put(st, cost, seq):
            if st not in nb or cost < nb[st][0]:
                nb[st] = (cost, seq)
        for st, (cost, seq) in best.items():
            if st is None:
                if i not in exposed:
                    put(None, cost + 1, seq + ['B1'])
                    if i > 0:
                        put(0, cost + 1, seq + ['I1'])
            else:
                if i not in exposed:
                    put(None, cost + 1, seq + ['O1'])
                if st < GAP and i > 0:
                    put(st + 1, cost, seq + [None])
        best = nb
    if None not in best:
        return None
    return list(reversed(best[None][1]))


def drop_col(g, c):
    return {((x - 1 if x > c else x), y): v for (x, y), v in g.items() if x != c}


def relay(g, W, H, stretches):
    """lay every (y, a, b) stretch again in place"""
    g = dict(g)
    for y, a, b in stretches:
        exposed = {x - a for x in range(a, b + 1) if pushes_into(g, x, y)}
        seq = lay(b - a + 1, exposed)
        if seq is None:
            return None
        for i, c in enumerate(seq):
            if c:
                g[(a + i, y)] = c
            else:
                g.pop((a + i, y), None)
    return g


def shrink_once(g, W, H):
    """drop every column it can by bookkeeping alone; returns new (g, W) and the count dropped"""
    E = elastic(g, W, H)
    common = set(range(1, W - 1))
    for s in E:
        common &= s
    dropped = 0
    for c in sorted(common, reverse=True):          # east first so smaller column numbers stay valid
        E = elastic(g, W, H)
        if not all(c in s for s in E):
            continue
        st = []
        for y in range(H):
            for a, b in runs(E[y]):
                if a <= c <= b:
                    st.append((y, a, b - 1))        # the stretch, one shorter after the drop
        if any(b < a for _, a, b in st):
            continue
        g2 = relay(drop_col(g, c), W - 1, H, st)
        if g2 is None:
            continue
        g, W = g2, W - 1
        dropped += 1
    return g, W, dropped


def realign(g, W, H):
    """lay every elastic stretch again (fewest pieces, hops as long as they go) so the empty tiles line up"""
    E = elastic(g, W, H)
    st = [(y, a, b) for y in range(H) for a, b in runs(E[y])]
    return relay(g, W, H, st) or g


def components(g, W, H, E):
    """rigid parts: tiles that are not elastic (pieces, their inner gaps), joined by side contact"""
    ne = [(x, y) for y in range(H) for x in range(W) if x not in E[y] and g.get((x, y))]   # empty void is nothing
    par = {p: p for p in ne}
    def f(p):
        while par[p] != p:
            par[p] = par[par[p]]; p = par[p]
        return p
    for x, y in ne:
        for q in ((x + 1, y), (x, y + 1)):
            if q in par:
                par[f(q)] = f((x, y))
    from bmsolver.design import Design
    for p, q, k in Design(g).links():           # a tunnel's two ends move together
        if k == 'ug' and p in par and q in par:
            par[f(q)] = f(p)
    comps = {}
    for p in ne:
        comps.setdefault(f(p), []).append(p)
    return list(comps.values())


def compact(g, W, H, glue=frozenset()):
    """slide every rigid part as far west as the elastic stretches allow (same order in every row, stretches at
    least one tile, parts on neighbouring rows never side by side), then lay the stretches again"""
    E = [e - {x for yy, x in glue if yy == y} for y, e in enumerate(elastic(g, W, H))]
    comps = components(g, W, H, E)
    while True:                                 # a part owns whole row spans; parts that interleave merge
        ext = []                                # per part: {row: (min x, max x)}
        for c in comps:
            d = {}
            for x, y in c:
                lo, hi = d.get(y, (x, x))
                d[y] = (min(lo, x), max(hi, x))
            ext.append(d)
        hit = None
        for a in range(len(comps)):
            for b in range(a + 1, len(comps)):
                if any(y in ext[b] and not (ext[b][y][0] > hi or ext[b][y][1] < lo) for y, (lo, hi) in ext[a].items()):
                    hit = (a, b); break
            if hit:
                break
        if not hit:
            break
        a, b = hit
        comps[a] = comps[a] + comps[b]; del comps[b]
    comps = [[(x, y) for y, (lo, hi) in d.items() for x in range(lo, hi + 1)] for d in ext]
    n = len(comps)
    edges = []                                  # (a, b, w): pos_b >= pos_a + w   (pos = shift)
    for a in range(n):
        for b in range(n):
            if a == b:
                continue
            for y, (lo, hi) in ext[a].items():
                for dy in (-1, 0, 1):
                    if y + dy in ext[b]:
                        blo, bhi = ext[b][y + dy]
                        if blo > hi:            # b east of a there
                            gap = 1 if dy == 0 else ADJ
                            edges.append((a, b, hi + 1 + gap - blo))
    lb = [0] * n
    for i, d in enumerate(ext):
        lb[i] = max(-lo + (1 if lo > 0 else 0) for y, (lo, hi) in d.items())
    # longest path: order parts by their west edge
    order = sorted(range(n), key=lambda i: min(lo for lo, hi in ext[i].values()))
    pos = {i: lb[i] for i in range(n)}
    inc = {}
    for a, b, w in edges:
        inc.setdefault(b, []).append((a, w))
    for _ in range(n):                          # relax until stable (a DAG, so n rounds suffice)
        ch = False
        for b in order:
            for a, w in inc.get(b, []):
                if pos[a] + w > pos[b]:
                    pos[b] = pos[a] + w; ch = True
        if not ch:
            break
    W2 = 1
    for i, d in enumerate(ext):
        for y, (lo, hi) in d.items():
            W2 = max(W2, hi + pos[i] + 1 + (1 if hi < W - 1 else 0))
    g2 = {}
    occ = [set() for _ in range(H)]
    for i, c in enumerate(comps):
        for x, y in c:
            if g.get((x, y)):
                g2[(x + pos[i], y)] = g[(x, y)]
            occ[y].add(x + pos[i])
    st = []
    start = {(y, lo + pos[i]): i for i, d in enumerate(ext) for y, (lo, hi) in d.items()}
    for y in range(H):
        free = [x for x in range(W2) if x not in occ[y]]
        for a, b in runs(free):                 # a run carries a belt only if something pushes west into it
            if b == W2 - 1:
                live = bool(g.get((W - 1, y)))
            else:                               # the part east of it fed a tile there before it moved
                i = start.get((y, b + 1))
                live = i is not None and bool(g.get((ext[i][y][0] - 1, y)))
            if live:
                st.append((y, a, b))
    bad = []
    for y, a, b in st:
        exposed = {x - a for x in range(a, b + 1) if pushes_into(g2, x, y)}
        if lay(b - a + 1, exposed) is None:
            bad.append((y, a, b))
    if bad:
        owner = {}
        for i, d in enumerate(ext):
            for y, (lo, hi) in d.items():
                owner[(y, lo + pos[i])] = i; owner[(y, hi + pos[i])] = i
        add = set()
        for y, a, b in bad:                     # that stretch keeps its old shape: glue it to its parts
            A, B = owner.get((y, a - 1)), owner.get((y, b + 1))
            lo = ext[A][y][1] + 1 if A is not None else 0
            hi = ext[B][y][0] - 1 if B is not None else W - 1
            add |= {(y, x) for x in range(lo, hi + 1)}
        if add <= glue:
            return None, W2, st
        return compact(g, W, H, glue | add)
    return relay(g2, W2, H, st), W2, st


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, required=True)
    ap.add_argument('--grid', required=True)
    ap.add_argument('--out', default=None)
    a = ap.parse_args()
    g, W, H = parse([l.rstrip('\n') for l in open(os.path.expanduser(a.grid)) if l.strip()])
    t0 = time.time()
    W0 = W
    while True:
        g, W, d = shrink_once(g, W, H)
        if not d:
            break
    t1 = time.time()
    from shrink import auto_unit
    win = S.Spec([S.Group('E', a.n)], [S.Group('W', a.n)], unit_mult=auto_unit(a.n, g, W, H)).window(W, H)
    ok = win is not None and bench.verify(g, win, None)
    print('%d -> %d wide in %.3f s, exact check %s (%.2f s)' % (W0, W, t1 - t0, ok, time.time() - t1))
    print('\n'.join(rows_of(g, W, H)))
    if a.out and ok:
        open(os.path.expanduser(a.out), 'w').write('\n'.join(rows_of(g, W, H)) + '\n')


if __name__ == '__main__':
    main()
