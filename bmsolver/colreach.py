"""All-width proofs by column reachability (john 2026-11-14: the N:N at N tall trap).

Build a layout one column at a time from the input edge (east) towards the output edge (west). Everything the
columns already placed can still do to the rest is written down in the BORDER STATE of the line west of them, per
row:
    w   what the tile east of the line pushes west: nothing, a belt of flow v (the next tile must take it), or a
        splitter output left unused (the next tile must not take westbound items)
    e   whether the tile east of the line takes eastbound items, and if so how much it was promised from the west
        (0 = no link, so the next tile must not push east)
    hw  underground hops going west whose exit is still to come: distance so far and flow
    he  the nearest underground exit facing east whose entrances are still to come: distance, flow still owed,
        and whether one entrance has been seen
Flows are integer vectors in units of 1/D belt per input item, as in the flow model (model.py), so there are
finitely many states. A column is any choice of pieces whose links, splitter shares, first-exit hop pairing and
flow conservation all hold, with every eastbound inflow from the west guessed and carried as a promise. Every
layout of every width is a path from the start state (the inputs) to a final state (exact outputs, nothing open),
so if the search runs out of new states without reaching a final one, no box of ANY width exists (up to D).
Reaching a final state gives the narrowest width and a layout.

Every line also carries net one belt of each item westward (D per item); states breaking that are dropped.

    python -m bmsolver.colreach --n 2 --height 2 --D 4
"""
import argparse
import itertools
import time

from .pieces import UG_REACH

PIECES = [None] + [k + str(o) for k in 'BSIO' for o in range(4)]
NOACC, ACC0 = 'n', 'z'          # e slot: east tile doesn't take eastbound / takes it but nothing promised


def pushes(c):
    if c is None:
        return ()
    k, o = c[0], int(c[1])
    if k in 'BO':
        return (o,)
    if k == 'S':
        return (o, (o + 1) % 4, (o + 3) % 4)
    return ()


def accepts(c, d):
    if c is None:
        return False
    k, o = c[0], int(c[1])
    if k == 'B':
        return o != (d + 2) % 4
    if k in 'SI':
        return o == d
    return False


def vadd(a, b):
    return tuple(x + y for x, y in zip(a, b))


def sum_v(vs, z):
    for v in vs:
        z = vadd(z, v)
    return z


def vsub(a, b):
    return tuple(x - y for x, y in zip(a, b))


class Problem:
    def __init__(self, H, nc, D, in_rows, out_rows, out_vec):
        self.H, self.nc, self.D = H, nc, D
        self.in_rows, self.out_rows, self.out_vec = in_rows, out_rows, out_vec
        self.zero = (0,) * nc
        self.vecs = [v for v in itertools.product(range(D + 1), repeat=nc) if 1 <= sum(v) <= D]
        self.cols = [col for col in itertools.product(PIECES, repeat=H) if self.col_ok(col)]

    def col_ok(self, col):
        """static checks inside one column: belts/exits hand on vertically, vertical hops pair"""
        H = self.H
        for y, c in enumerate(col):
            if c is None:
                continue
            k, o = c[0], int(c[1])
            if k in 'BO' and o in (0, 2):
                q = y - 1 if o == 0 else y + 1
                if not (0 <= q < H and accepts(col[q], o)):
                    return False
            if k == 'I' and o in (0, 2):
                if self.vpair(col, y) is None:
                    return False
            if k == 'O' and o in (0, 2):
                if not any(self.vpair(col, z) == y for z in range(H) if col[z] == 'I' + str(o)):
                    return False
        return True

    def vpair(self, col, y):
        o = int(col[y][1])
        s = -1 if o == 0 else 1
        for n in range(1, UG_REACH + 1):
            q = y + s * n
            if not 0 <= q < self.H:
                return None
            if col[q] == 'O' + str(o):
                return q
        return None

    def start(self):
        rows = []
        for y in range(self.H):
            w = None
            if y in self.in_rows:
                v = [0] * self.nc
                v[self.in_rows.index(y)] = self.D
                w = ('W', tuple(v))
            rows.append((w, NOACC, (), None))
        return tuple(rows)

    def final(self, st):
        for y, (w, e, hw, he) in enumerate(st):
            if hw or he is not None or (e not in (NOACC, ACC0)):
                return False
            if y in self.out_rows:
                if w != ('W', self.out_vec):
                    return False
            elif w is not None and w[0] == 'W':
                return False
        return True

    def net_ok(self, st):
        tot = self.zero
        for w, e, hw, he in st:
            if w is not None and w[0] == 'W':
                tot = vadd(tot, w[1])
            for n, v in hw:
                tot = vadd(tot, v)
            if e not in (NOACC, ACC0):
                tot = vsub(tot, e)
            if he is not None:
                tot = vsub(tot, he[1])
        return all(t == self.D for t in tot)

    # ---- one column
    def step(self, st):
        """all states west of one more column placed west of the line with state st"""
        out = {}
        H = self.H
        for col in self.cols:
            ok = True
            for y in range(H):
                w, e, hw, he = st[y]
                c = col[y]
                if w is not None:
                    if w[0] == 'W' and not accepts(c, 1):
                        ok = False
                    if w[0] == 'X' and accepts(c, 1):
                        ok = False
                pe = 3 in pushes(c)
                if e == NOACC:
                    if pe and c[0] in 'BO':
                        ok = False
                elif e == ACC0:
                    if pe:
                        ok = False
                else:
                    if not pe:
                        ok = False
                if c is not None and c[0] in 'BO' and int(c[1]) == 1:
                    pass
                if c == 'O3' and he is not None:
                    ok = False          # an exit between an older east exit and its entrances still to come
                if c == 'I3' and (he is None or he[0] > UG_REACH):
                    ok = False
                if not ok:
                    break
            if not ok:
                continue
            for y in range(H):
                c = col[y]
                if c is not None and c[0] == 'O' and int(c[1]) == 1 and not st[y][2]:
                    ok = False          # a west exit with no hop feeding it
                if c is not None and c[0] == 'O' and int(c[1]) == 3 and st[y][1] in (NOACC, ACC0):
                    ok = False
            if not ok:
                continue
            for ns in self.fill(st, col):
                out.setdefault(ns, col)
        return out

    def fill(self, st, col):
        H, nc = self.H, self.nc
        # guesses: eastbound inflow from the west per row (tile accepts eastbound), splitter west output used or not
        gin = []
        for y in range(H):
            c = col[y]
            gin.append([None] + self.vecs if accepts(c, 3) else [None])
        gsp = []
        for y in range(H):
            c = col[y]
            gsp.append([True, False] if (c is not None and c[0] == 'S' and 1 in pushes(c)) else [None])
        res = set()
        for ins in itertools.product(*gin):
            for spw in itertools.product(*gsp):
                ns = self.solve_col(st, col, ins, spw)
                if ns is not None and self.net_ok(ns):
                    res.add(ns)
        return res

    def solve_col(self, st, col, ins, spw):
        H, zero = self.H, self.zero
        inflow = [zero] * H
        known = [True] * H
        # links into each tile: from east (st w), from west (guess), from west hops ending, vertical
        for y in range(H):
            w, e, hw, he = st[y]
            c = col[y]
            if w is not None and w[0] == 'W':
                inflow[y] = vadd(inflow[y], w[1])
            if ins[y] is not None:
                inflow[y] = vadd(inflow[y], ins[y])
            if c == 'O1':
                for n, v in hw:
                    inflow[y] = vadd(inflow[y], v)
            if c == 'O3':
                inflow[y] = e           # what its entrances (still to come, west) must bring
        # vertical links: a -> b. Order tiles so each tile's vertical inputs are done first.
        vin = {y: [] for y in range(H)}        # y -> list of (source row, kind)
        vout = {y: [] for y in range(H)}
        for y in range(H):
            c = col[y]
            for d in pushes(c):
                if d in (0, 2):
                    q = y - 1 if d == 0 else y + 1
                    if 0 <= q < H and accepts(col[q], d):
                        vin[q].append(y)
                        vout[y].append(q)
            if c is not None and c[0] == 'I' and int(c[1]) in (0, 2):
                q = self.vpair(col, y)
                vin[q].append(y)
                vout[y].append(q)
        order, done = [], set()
        while len(order) < H:
            prog = False
            for y in range(H):
                if y not in done and all(s in done for s in vin[y]):
                    order.append(y)
                    done.add(y)
                    prog = True
            if not prog:
                return None             # a cycle inside one column (can't happen with these pieces)
        sent = {}                       # (src, dst) -> flow
        new = [None] * H
        westout = [None] * H
        eastout = [zero] * H
        for y in order:
            c = col[y]
            f = inflow[y]
            for s in vin[y]:
                f = vadd(f, sent[s, y])
            if c is None:
                if sum(f):
                    return None
                continue
            k, o = c[0], int(c[1])
            if k in 'BO':
                if sum(f) == 0 or sum(f) > self.D:
                    return None
                if o in (0, 2):
                    sent[y, vout[y][0]] = f
                elif o == 1:
                    westout[y] = ('W', f)
                else:
                    eastout[y] = f
            elif k == 'I':
                if sum(f) == 0 or sum(f) > self.D:
                    return None
                if o in (0, 2):
                    sent[y, vout[y][0]] = f
                elif o == 1:
                    westout[y] = ('HW', f)
                else:
                    eastout[y] = ('HE', f)
            else:
                used = []
                for d in pushes(c):
                    if d in (0, 2):
                        q = y - 1 if d == 0 else y + 1
                        if 0 <= q < H and accepts(col[q], d):
                            used.append(d)
                    elif d == 3:
                        if st[y][1] not in (NOACC, ACC0):
                            used.append(d)
                    elif d == 1:
                        if spw[y]:
                            used.append(d)
                if not used:
                    if sum(f):
                        return None
                    if 1 in pushes(c):
                        westout[y] = ('X',)
                    continue
                if any(x % len(used) for x in f):
                    return None
                sh = tuple(x // len(used) for x in f)
                if sum(sh) == 0:
                    return None
                for d in used:
                    if d in (0, 2):
                        sent[y, y - 1 if d == 0 else y + 1] = sh
                    elif d == 3:
                        eastout[y] = sh
                    else:
                        westout[y] = ('W', sh)
                if 1 in pushes(c) and 1 not in used:
                    westout[y] = ('X',)
        # check promises to the east, build the new state
        rows = []
        for y in range(H):
            w, e, hw, he = st[y]
            c = col[y]
            eo = eastout[y]
            nhe = he
            if isinstance(eo, tuple) and eo and eo[0] == 'HE':
                if nhe is None:
                    return None
                r = vsub(nhe[1], eo[1])
                if min(r) < 0:
                    return None
                nhe = (nhe[0], r, 'fed')
                eo = zero
            if e not in (NOACC, ACC0):
                if eo != e:
                    return None
            elif sum(eo):
                return None
            if c == 'O3':
                nhe = (0, e, None)
            # west hops
            nhw = []
            if c != 'O1':
                for n, v in hw:
                    if n + 1 > UG_REACH:
                        return None
                    nhw.append((n + 1, v))
            wo = westout[y]
            nw = None
            if wo is not None:
                if wo[0] == 'HW':
                    nhw.append((1, wo[1]))
                else:
                    nw = wo
            if nhe is not None:
                if sum(nhe[1]) == 0:
                    nhe = None
                else:
                    if nhe[0] + 1 > UG_REACH:
                        return None
                    nhe = (nhe[0] + 1, nhe[1])
            if accepts(c, 3):
                ne = ACC0 if ins[y] is None else ins[y]
            else:
                ne = NOACC
            if nhw:                     # all pending west hops of a row end at the same first exit: only the
                nhw = [(max(n for n, v in nhw), sum_v([v for n, v in nhw], self.zero))]   # oldest's deadline and the sum matter
                if sum(nhw[0][1]) > self.D:
                    return None
            if nhe is not None:
                nhe = (nhe[0], nhe[1])  # one entrance seen or not doesn't matter while flow is still owed
            rows.append((nw, ne, tuple(nhw), nhe))
        return tuple(rows)


def split_dist(st):
    """(state without hop distances, distances): a state with every distance <= another's (same rest) has at least
    as much slack, so whatever finishes from the other finishes from it too"""
    key, dist = [], []
    for w, e, hw, he in st:
        key.append((w, e, tuple(v for n, v in hw), None if he is None else he[1]))
        dist += [n for n, v in hw] + ([] if he is None else [he[0]])
    return tuple(key), tuple(dist)


def search(P, max_w=200, log=print):
    s0 = P.start()
    seen = {s0: None}
    best = {}                           # key -> list of minimal distance tuples
    k0, d0 = split_dist(s0)
    best[k0] = [d0]
    front = [s0]
    t0 = time.time()
    for wdt in range(1, max_w + 1):
        nxt = []
        for s in front:
            for ns, col in P.step(s).items():
                if ns in seen:
                    continue
                k, d = split_dist(ns)
                lst = best.setdefault(k, [])
                if any(all(a <= b for a, b in zip(o, d)) for o in lst):
                    continue
                lst[:] = [o for o in lst if not all(b <= a for a, b in zip(o, d))] + [d]
                seen[ns] = (s, col)
                nxt.append(ns)
                if P.final(ns):
                    log('width %d: FOUND (%d states, %.0fs)' % (wdt, len(seen), time.time() - t0))
                    return 'FOUND', wdt, seen, ns
        log('width %d: %d new states, %d total, %.0fs' % (wdt, len(nxt), len(seen), time.time() - t0))
        if not nxt:
            return 'IMPOSSIBLE', wdt, seen, None
        front = nxt
    return 'UNKNOWN', max_w, seen, None


def layout(seen, fin):
    """the columns of a found layout, west to east, as rows of piece codes"""
    cols = []
    s = fin
    while seen[s] is not None:
        s, col = seen[s]
        cols.append(col)
    H = len(cols[0])
    return [[col[y] for col in cols] for y in range(H)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, required=True)
    ap.add_argument('--height', type=int, required=True)
    ap.add_argument('--D', type=int, default=4)
    ap.add_argument('--max-w', type=int, default=200)
    a = ap.parse_args()
    n, H, D = a.n, a.height, a.D
    assert D % n == 0
    P = Problem(H, n, D, list(range(n)), list(range(n)), tuple([D // n] * n))
    print('%d:%d %d tall, D=%d, %d column kinds' % (n, n, H, D, len(P.cols)), flush=True)
    r, wdt, seen, fin = search(P, a.max_w, log=lambda s: print(s, flush=True))
    print('%d:%d %d tall: %s at width %d' % (n, n, H, r, wdt))
    if fin is not None:
        g = layout(seen, fin)
        print('\n'.join(''.join(c or '..' for c in row) for row in g))


if __name__ == '__main__':
    main()
