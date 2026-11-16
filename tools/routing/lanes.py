"""Lane capacity of a channel: how many full belts fit through h rows, exactly.

The channel is a cylinder: h rows, P columns that wrap around (column P is column 0 again), closed walls above
and below. Any layout on the cylinder is a pattern that repeats for ever, so it carries its belts over any length;
and a channel of any length that carries k belts has, by pigeonhole on its column borders, a repeating stretch.
Pieces are the game's (bmsolver/pieces.py): belts, underground entrances/exits with reach 1..7, an entrance
links to the first exit of its direction, so two same-direction tunnels never overlap in a row (or column).
No splitters: with full belts a splitter or merge never adds capacity (every link still carries <= 1 belt).

    python tools/routing/lanes.py max h P [seconds]          # most eastward belts
    python tools/routing/lanes.py two h P a [seconds]        # a belts east: most belts west
"""
import sys
import time
from ortools.sat.python import cp_model

DIRS = {0: (0, -1), 1: (-1, 0), 2: (0, 1), 3: (1, 0)}
REACH = 7


def build(h, P, ncls=1, reach=REACH, blocked=()):
    m = cp_model.CpModel()
    cells = [(x, y) for x in range(P) for y in range(h) if (x, y) not in blocked]
    cs = set(cells)

    def add(p, d, n=1):
        q = ((p[0] + n * DIRS[d][0]) % P, p[1] + n * DIRS[d][1])
        return q if 0 <= q[1] < h else None

    X = {}
    for p in cells:
        for c in range(ncls):
            for k in 'BIO':
                for d in range(4):
                    X[p, c, k, d] = m.NewBoolVar('%s%d_%d_%d_%d' % (k, d, c, p[0], p[1]))
        m.AddAtMostOne(X[p, c, k, d] for c in range(ncls) for k in 'BIO' for d in range(4))

    def V(p, c, k, d):
        return X.get((p, c, k, d))

    def nz(l):
        return [v for v in l if v is not None]

    T = {}
    for p in cells:
        for c in range(ncls):
            for d in range(4):
                # surface: p pushes in direction d (belt or exit) -> q accepts (same class)
                q = add(p, d)
                push = [X[p, c, 'B', d], X[p, c, 'O', d]]
                if q is None or q not in cs:
                    for v in push:
                        m.Add(v == 0)
                else:
                    acc = [X[q, c, 'B', e] for e in range(4) if e != (d + 2) % 4] + [X[q, c, 'I', d]]
                    for v in push:
                        m.AddBoolOr(acc).OnlyEnforceIf(v)
            # exactly one pusher into a belt or entrance
            for kk, ds in (('B', range(4)), ('I', range(4))):
                for d in ds:
                    v = X[p, c, kk, d]
                    srcs = []
                    for e in range(4):          # e = direction of travel into p
                        if kk == 'B' and e == (d + 2) % 4:
                            continue
                        if kk == 'I' and e != d:
                            continue
                        r = add(p, (e + 2) % 4)
                        if r is not None and r in cs:
                            srcs += [X[r, c, 'B', e], X[r, c, 'O', e]]
                    m.Add(sum(srcs) == 1).OnlyEnforceIf(v)
            # tunnels
            for d in range(4):
                opts = []
                for n in range(1, min(reach, P - 1 if d in (1, 3) else h) + 1):
                    q = add(p, d, n)
                    if q is None:
                        break
                    if q not in cs:
                        break               # walls: nothing passes under a blocked tile
                    t = m.NewBoolVar('')
                    T[p, c, d, n] = t
                    m.AddImplication(t, X[p, c, 'I', d])
                    m.AddImplication(t, X[q, c, 'O', d])
                    for j in range(1, n):
                        r = add(p, d, j)
                        for c2 in range(ncls):
                            m.AddImplication(t, X[r, c2, 'O', d].Not())
                            m.AddImplication(t, X[r, c2, 'I', d].Not())
                    opts.append(t)
                m.Add(sum(opts) == X[p, c, 'I', d])
    for p in cells:
        for c in range(ncls):
            for d in range(4):
                into = [T[r, c, d, n] for n in range(1, reach + 1) for r in [add(p, (d + 2) % 4, n)]
                        if r is not None and (r, c, d, n) in T]
                m.Add(sum(into) == X[p, c, 'O', d])
    # a tunnel of one class may not pass an end of the other class in the same direction either: done above (c2).
    # but an entrance of class c must not link to an exit of class c2: the first exit ahead must be its own
    if ncls > 1:
        for (p, c, d, n), t in T.items():
            pass  # ends of any class strictly between are forbidden; the exit itself is class c by construction

    def flux(cut, c):
        """net eastward crossings of the line between column cut and cut+1, class c"""
        e = []
        for y in range(h):
            p, q = (cut % P, y), ((cut + 1) % P, y)
            if p in cs and q in cs:
                e += [X[p, c, 'B', 3], X[p, c, 'O', 3]]
                e += [-X[q, c, 'B', 1], -X[q, c, 'O', 1]]
        for (p, cc, d, n), t in T.items():
            if cc != c or d not in (1, 3):
                continue
            if d == 3 and (cut - p[0]) % P < n:
                e.append(t)
            if d == 1 and (p[0] - cut - 1) % P < n:
                e.append(-t)
        return sum(e)
    return m, X, T, flux, cells


def show(sol, X, T, h, P, ncls=1):
    ch = {('B', 0): '^', ('B', 1): '<', ('B', 2): 'v', ('B', 3): '>', ('I', 0): 'n', ('I', 1): 'w', ('I', 2): 's',
          ('I', 3): 'e', ('O', 0): 'N', ('O', 1): 'W', ('O', 2): 'S', ('O', 3): 'E'}
    rows = []
    for y in range(h):
        r = ''
        for x in range(P):
            c = '.'
            for (p, cl, k, d), v in X.items():
                if p == (x, y) and sol.Value(v):
                    c = ch[k, d]
            r += c
        rows.append(r)
    return rows


def solve_max(h, P, secs=600, reach=REACH, lo=None, workers=int(__import__('os').environ.get('WORKERS', 4))):
    m, X, T, flux, cells = build(h, P, 1, reach)
    f0 = flux(0, 0)
    if lo is not None:
        m.Add(f0 >= lo)
    m.Maximize(f0)
    s = cp_model.CpSolver()
    s.parameters.max_time_in_seconds = secs
    s.parameters.num_workers = workers
    r = s.Solve(m)
    name = s.StatusName(r)
    if r in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return name, int(s.ObjectiveValue()), int(s.BestObjectiveBound()), show(s, X, T, h, P)
    return name, None, None, None


def solve_two(h, P, a, secs=600, reach=REACH, workers=4):
    m, X, T, flux, cells = build(h, P, 2, reach)
    m.Add(flux(0, 0) >= a)
    fb = -flux(0, 1)
    m.Maximize(fb)
    s = cp_model.CpSolver()
    s.parameters.max_time_in_seconds = secs
    s.parameters.num_workers = workers
    r = s.Solve(m)
    name = s.StatusName(r)
    if r in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return name, int(s.ObjectiveValue()), int(s.BestObjectiveBound()), show(s, X, T, h, P, 2)
    return name, None, None, None


if __name__ == '__main__':
    mode = sys.argv[1]
    h, P = int(sys.argv[2]), int(sys.argv[3])
    t0 = time.time()
    if mode == 'max':
        secs = float(sys.argv[4]) if len(sys.argv) > 4 else 600
        res = solve_max(h, P, secs)
    else:
        a = int(sys.argv[4])
        secs = float(sys.argv[5]) if len(sys.argv) > 5 else 600
        res = solve_two(h, P, a, secs)
    print(mode, 'h', h, 'P', P, sys.argv[4:], res[0], 'best', res[1], 'bound', res[2], '%.0fs' % (time.time() - t0), flush=True)
    for r in res[3] or []:
        print('   ', r)
