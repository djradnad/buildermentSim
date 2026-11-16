"""Open-ended channel: h rows, L columns, walls above and below, both ends completely free.

Anything may happen outside the two ends: belts may be fed in or taken away on any row, tunnels may start or end
outside (even pass under the whole strip). So if k belts cannot cross a strip of length L here, they cannot cross
ANY channel of h rows that has a straight stretch of L columns, whatever is built before and after it.
INFEASIBLE from the solver is therefore a proof for every longer channel too.

    python tools/routing/strip.py h k L [seconds] [reach]     # can k belts (net, eastward) cross?
    python tools/routing/strip.py scan h k [seconds] [reach]  # longest L that still works (stops at Lmax)
"""
import sys
import time
from ortools.sat.python import cp_model

DIRS = {0: (0, -1), 1: (-1, 0), 2: (0, 1), 3: (1, 0)}


def build(h, L, reach=7, west=0, splitters=False):
    """west = number of belts that must also cross westward (two-way traffic): classes 0 (east) and 1 (west)"""
    ncls = 2 if west else 1
    m = cp_model.CpModel()
    cells = [(x, y) for x in range(L) for y in range(h)]
    cs = set(cells)

    def add(p, d, n=1):
        q = (p[0] + n * DIRS[d][0], p[1] + n * DIRS[d][1])
        if not 0 <= q[1] < h:
            return None
        if q[0] < 0:
            return 'W'
        if q[0] >= L:
            return 'E'
        return q

    X = {}
    for p in cells:
        for c in range(ncls):
            for k in 'BIO':
                for d in range(4):
                    X[p, c, k, d] = m.NewBoolVar('%s%d_%d_%d_%d' % (k, d, c, p[0], p[1]))
        m.AddAtMostOne(X[p, c, k, d] for c in range(ncls) for k in 'BIO' for d in range(4))
    T = {}          # (p, c, d, n) inside -> inside
    TOUT = {}       # (p, c, d): entrance whose exit is outside
    TIN = {}        # (q, c, d): exit whose entrance is outside
    OVER = {}       # (y, c, d): tunnel passing under the whole strip
    FEED = {}       # (p, c, d): outside pushes into p moving in direction d
    for p in cells:
        for c in range(ncls):
            for d in range(4):
                q = add(p, d)
                push = [X[p, c, 'B', d], X[p, c, 'O', d]]
                if q is None:
                    for v in push:
                        m.Add(v == 0)
                elif q in ('W', 'E'):
                    pass                    # outside accepts anything
                else:
                    acc = [X[q, c, 'B', e] for e in range(4) if e != (d + 2) % 4] + [X[q, c, 'I', d]]
                    for v in push:
                        m.AddBoolOr(acc).OnlyEnforceIf(v)
            for kk in 'BI':
                for d in range(4):
                    v = X[p, c, kk, d]
                    srcs = []
                    for e in range(4):
                        if kk == 'B' and e == (d + 2) % 4:
                            continue
                        if kk == 'I' and e != d:
                            continue
                        r = add(p, (e + 2) % 4)
                        if r is None:
                            continue
                        if r in ('W', 'E'):
                            if (p, c, e) not in FEED:
                                FEED[p, c, e] = m.NewBoolVar('')
                            srcs.append(FEED[p, c, e])
                        else:
                            srcs += [X[r, c, 'B', e], X[r, c, 'O', e]]
                    m.Add(sum(srcs) == 1).OnlyEnforceIf(v)
            for d in range(4):
                opts = []
                for n in range(1, reach + 1):
                    q = add(p, d, n)
                    if q is None:
                        break
                    between = []
                    for j in range(1, n):
                        r = add(p, d, j)
                        if r in ('W', 'E'):
                            break
                        between += [X[r, c2, kk, d] for c2 in range(ncls) for kk in 'IO']
                    if q in ('W', 'E'):
                        t = m.NewBoolVar('')
                        TOUT[p, c, d] = t
                        for b in between:
                            m.AddImplication(t, b.Not())
                        opts.append(t)
                        break
                    t = m.NewBoolVar('')
                    T[p, c, d, n] = t
                    m.AddImplication(t, X[q, c, 'O', d])
                    for b in between:
                        m.AddImplication(t, b.Not())
                    opts.append(t)
                m.Add(sum(opts) == X[p, c, 'I', d])
    for (p, c, e), f in FEED.items():       # the outside only feeds a piece that accepts from that side
        m.AddBoolOr([X[p, c, 'B', d] for d in range(4) if d != (e + 2) % 4] + [X[p, c, 'I', e]]).OnlyEnforceIf(f)
    for p in cells:
        for c in range(ncls):
            for d in range(4):
                into = []
                for n in range(1, reach + 1):
                    r = add(p, (d + 2) % 4, n)
                    if r is None:
                        break
                    if r in ('W', 'E'):
                        t = m.NewBoolVar('')
                        TIN[p, c, d] = t
                        for j in range(1, n):
                            rr = add(p, (d + 2) % 4, j)
                            for c2 in range(ncls):
                                for kk in 'IO':
                                    m.AddImplication(t, X[rr, c2, kk, d].Not())
                        into.append(t)
                        break
                    if (r, c, d, n) in T:
                        into.append(T[r, c, d, n])
                m.Add(sum(into) == X[p, c, 'O', d])
    if L + 1 <= reach:
        for y in range(h):
            for c in range(ncls):
                for d in (1, 3):
                    t = m.NewBoolVar('')
                    OVER[y, c, d] = t
                    for x in range(L):
                        for c2 in range(ncls):
                            for kk in 'IO':
                                m.AddImplication(t, X[(x, y), c2, kk, d].Not())
            for d in (1, 3):
                m.AddAtMostOne(OVER[y, c, d] for c in range(ncls))

    def flux(cut, c):
        """net eastward crossings of the line between column cut and cut+1 (cut = -1 .. L-1), class c"""
        e = []
        for y in range(h):
            p, q = (cut, y), (cut + 1, y)
            if p in cs:
                e += [X[p, c, 'B', 3], X[p, c, 'O', 3]]
            elif (q, c, 3) in FEED:
                e.append(FEED[q, c, 3])
            if q in cs:
                e += [-X[q, c, 'B', 1], -X[q, c, 'O', 1]]
            elif (p, c, 1) in FEED:
                e.append(-FEED[p, c, 1])
        for (p, cc, d, n), t in T.items():
            if cc == c and d == 3 and p[0] <= cut < p[0] + n:
                e.append(t)
            if cc == c and d == 1 and p[0] - n <= cut < p[0]:
                e.append(-t)
        for (p, cc, d), t in TOUT.items():
            if cc == c and d == 3 and p[0] <= cut:
                e.append(t)
            if cc == c and d == 1 and cut < p[0]:
                e.append(-t)
        for (p, cc, d), t in TIN.items():
            if cc == c and d == 3 and cut < p[0]:
                e.append(t)
            if cc == c and d == 1 and p[0] <= cut:
                e.append(-t)
        for (y, cc, d), t in OVER.items():
            if cc == c:
                e.append(t if d == 3 else -t)
        return sum(e)
    return m, X, flux


CH = {('B', 0): '^', ('B', 1): '<', ('B', 2): 'v', ('B', 3): '>', ('I', 0): 'n', ('I', 1): 'w', ('I', 2): 's',
      ('I', 3): 'e', ('O', 0): 'N', ('O', 1): 'W', ('O', 2): 'S', ('O', 3): 'E'}


def solve(h, k, L, secs=600, reach=7, west=0, workers=4):
    m, X, flux = build(h, L, reach, west)
    for cut in range(-1, L):
        m.Add(flux(cut, 0) == k)
        if west:
            m.Add(flux(cut, 1) == -west)
    s = cp_model.CpSolver()
    s.parameters.max_time_in_seconds = secs
    s.parameters.num_workers = workers
    r = s.Solve(m)
    rows = None
    if r in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        rows = []
        for y in range(h):
            line = ''
            for x in range(L):
                ch = '.'
                for (p, c, kk, d), v in X.items():
                    if p == (x, y) and s.Value(v):
                        ch = CH[kk, d]
                line += ch
            rows.append(line)
    return {cp_model.OPTIMAL: 'FOUND', cp_model.FEASIBLE: 'FOUND', cp_model.INFEASIBLE: 'INFEASIBLE'}.get(r, 'UNKNOWN'), rows


if __name__ == '__main__':
    a = sys.argv[1:]
    if a[0] == 'scan':
        h, k = int(a[1]), int(a[2])
        secs = float(a[3]) if len(a) > 3 else 600
        reach = int(a[4]) if len(a) > 4 else 7
        west = int(a[5]) if len(a) > 5 else 0
        Lmax = int(a[6]) if len(a) > 6 else 40
        last = None
        for L in range(1, Lmax + 1):
            t0 = time.time()
            st, rows = solve(h, k, L, secs, reach, west)
            if st != 'FOUND':
                print('scan h %d k %d west %d reach %d: L=%d %s (%.0fs); longest found L=%s' % (h, k, west, reach, L, st, time.time() - t0, last), flush=True)
                break
            last = L
        else:
            print('scan h %d k %d west %d reach %d: still FOUND at L=%d' % (h, k, west, reach, Lmax), flush=True)
    else:
        h, k, L = int(a[0]), int(a[1]), int(a[2])
        secs = float(a[3]) if len(a) > 3 else 600
        reach = int(a[4]) if len(a) > 4 else 7
        west = int(a[5]) if len(a) > 5 else 0
        t0 = time.time()
        st, rows = solve(h, k, L, secs, reach, west)
        print('strip h %d k %d L %d west %d reach %d: %s %.0fs' % (h, k, L, west, reach, st, time.time() - t0), flush=True)
        for r in rows or []:
            print('   ', r)
