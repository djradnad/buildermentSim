"""Belt capacity of any corridor shape with open mouths (straight strips, corners, T shapes ...).

A region is a set of tiles. Outside it there are walls (nothing on them, nothing under them) except at the mouths,
where everything is free: belts may be fed in or taken away, tunnels may start or end outside. So INFEASIBLE here
means no layout at all can push that many belts through a corridor containing this shape.

Each traffic class c has a list of (mouth, net belts entering through it); negative = leaving.

    python tools/routing/region.py corner h A B k [secs] [reach]   # k belts in at the west mouth, out at the south mouth
    python tools/routing/region.py cornermax h A B                 # most belts round the corner
"""
import sys
import time
from ortools.sat.python import cp_model

DIRS = {0: (0, -1), 1: (-1, 0), 2: (0, 1), 3: (1, 0)}
CH = {('B', 0): '^', ('B', 1): '<', ('B', 2): 'v', ('B', 3): '>', ('I', 0): 'n', ('I', 1): 'w', ('I', 2): 's',
      ('I', 3): 'e', ('O', 0): 'N', ('O', 1): 'W', ('O', 2): 'S', ('O', 3): 'E'}


class Region:
    def __init__(self, cells, mouths, reach=7, ncls=1):
        """mouths: {name: set of outside positions that are free}; positions outside cells and outside every mouth
        are walls"""
        self.cells, self.cs = sorted(cells), set(cells)
        self.mouths = mouths
        self.free = set().union(*mouths.values()) if mouths else set()
        self.reach, self.ncls = reach, ncls
        self.m = m = cp_model.CpModel()
        self.X, self.T, self.TOUT, self.TIN, self.OVER, self.FEED = {}, {}, {}, {}, {}, {}
        X, T, TOUT, TIN, OVER, FEED = self.X, self.T, self.TOUT, self.TIN, self.OVER, self.FEED
        cs, free = self.cs, self.free

        def add(p, d, n=1):
            return (p[0] + n * DIRS[d][0], p[1] + n * DIRS[d][1])
        self.add = add
        for p in self.cells:
            for c in range(ncls):
                for k in 'BIO':
                    for d in range(4):
                        X[p, c, k, d] = m.NewBoolVar('')
            m.AddAtMostOne(X[p, c, k, d] for c in range(ncls) for k in 'BIO' for d in range(4))
        for p in self.cells:
            for c in range(ncls):
                for d in range(4):
                    q = add(p, d)
                    push = [X[p, c, 'B', d], X[p, c, 'O', d]]
                    if q in cs:
                        acc = [X[q, c, 'B', e] for e in range(4) if e != (d + 2) % 4] + [X[q, c, 'I', d]]
                        for v in push:
                            m.AddBoolOr(acc).OnlyEnforceIf(v)
                    elif q not in free:
                        for v in push:
                            m.Add(v == 0)
                for kk in 'BI':
                    for d in range(4):
                        srcs = []
                        for e in range(4):
                            if (kk == 'B' and e == (d + 2) % 4) or (kk == 'I' and e != d):
                                continue
                            r = add(p, (e + 2) % 4)
                            if r in cs:
                                srcs += [X[r, c, 'B', e], X[r, c, 'O', e]]
                            elif r in free:
                                FEED.setdefault((p, c, e), m.NewBoolVar(''))
                                srcs.append(FEED[p, c, e])
                        m.Add(sum(srcs) == 1).OnlyEnforceIf(X[p, c, kk, d])
                for d in range(4):
                    opts, between = [], []
                    for n in range(1, reach + 1):
                        q = add(p, d, n)
                        if q in cs:
                            t = m.NewBoolVar('')
                            T[p, c, d, n] = t
                            m.AddImplication(t, X[q, c, 'O', d])
                            for b in between:
                                m.AddImplication(t, b.Not())
                            opts.append(t)
                            between = between + [X[q, c2, kk, d] for c2 in range(ncls) for kk in 'IO']
                        elif q in free:
                            t = m.NewBoolVar('')
                            TOUT[p, c, d] = (t, n)
                            for b in between:
                                m.AddImplication(t, b.Not())
                            opts.append(t)
                            break
                        else:
                            break
                    m.Add(sum(opts) == X[p, c, 'I', d])
        for (p, c, e), f in FEED.items():       # the outside only feeds a piece that accepts from that side
            m.AddBoolOr([X[p, c, 'B', d] for d in range(4) if d != (e + 2) % 4] + [X[p, c, 'I', e]]).OnlyEnforceIf(f)
        for p in self.cells:
            for c in range(ncls):
                for d in range(4):
                    into, between = [], []
                    for n in range(1, reach + 1):
                        r = add(p, (d + 2) % 4, n)
                        if r in cs:
                            if (r, c, d, n) in T:
                                into.append(T[r, c, d, n])
                            between = between + [X[r, c2, kk, d] for c2 in range(ncls) for kk in 'IO']
                        elif r in free:
                            t = m.NewBoolVar('')
                            TIN[p, c, d] = (t, n)
                            for b in between:
                                m.AddImplication(t, b.Not())
                            into.append(t)
                            break
                        else:
                            break
                    m.Add(sum(into) == X[p, c, 'O', d])
        # tunnels that pass under the whole region from one free tile to another
        for f in sorted(free):
            for d in range(4):
                run, q = [], add(f, d)
                while q in cs and len(run) < reach - 1:
                    run.append(q)
                    q = add(q, d)
                if run and q in free:
                    for c in range(ncls):
                        t = m.NewBoolVar('')
                        OVER[f, c, d] = (t, run)
                        for r in run:
                            for c2 in range(ncls):
                                for kk in 'IO':
                                    m.AddImplication(t, X[r, c2, kk, d].Not())
                    m.AddAtMostOne(OVER[f, c, d][0] for c in range(ncls))

    def flux_in(self, mouth, c):
        """net belts of class c entering the region through a mouth"""
        X, add, cs = self.X, self.add, self.cs
        fr = self.mouths[mouth]
        e = []
        for p in self.cells:
            for d in range(4):
                q = add(p, d)
                if q in fr:
                    e += [-X[p, c, 'B', d], -X[p, c, 'O', d]]
                    if (p, c, (d + 2) % 4) in self.FEED:
                        e.append(self.FEED[p, c, (d + 2) % 4])
        for (p, cc, d), (t, n) in self.TOUT.items():
            if cc == c and add(p, d, n) in fr:
                e.append(-t)
        for (p, cc, d), (t, n) in self.TIN.items():
            if cc == c and add(p, (d + 2) % 4, n) in fr:
                e.append(t)
        for (f, cc, d), (t, run) in self.OVER.items():
            if cc == c:
                if f in fr:
                    e.append(t)
                if add(run[-1], d) in fr:
                    e.append(-t)
        return sum(e)

    def solve(self, secs=600, workers=4, maximize=None):
        s = cp_model.CpSolver()
        s.parameters.max_time_in_seconds = secs
        s.parameters.num_workers = workers
        if maximize is not None:
            self.m.Maximize(maximize)
        r = s.Solve(self.m)
        st = {cp_model.OPTIMAL: 'FOUND', cp_model.FEASIBLE: 'FOUND', cp_model.INFEASIBLE: 'INFEASIBLE'}.get(r, 'UNKNOWN')
        rows = None
        if st == 'FOUND':
            xs = [p[0] for p in self.cells]
            ys = [p[1] for p in self.cells]
            g = {}
            for (p, c, kk, d), v in self.X.items():
                if s.Value(v):
                    g[p] = CH[kk, d]
            rows = [''.join(g.get((x, y), '.' if (x, y) in self.cs else '#') for x in range(min(xs), max(xs) + 1))
                    for y in range(min(ys), max(ys) + 1)]
        self.last = s
        return st, rows, (s.ObjectiveValue() if maximize is not None and st == 'FOUND' else None), \
            (r == cp_model.OPTIMAL)


def strip(h, L, reach=7, ncls=1):
    cells = {(x, y) for x in range(L) for y in range(h)}
    mouths = {'W': {(-1, y) for y in range(h)}, 'E': {(L, y) for y in range(h)}}
    return Region(cells, mouths, reach, ncls)


def corner(h, A, B, reach=7, ncls=1, hv=None):
    """west arm h rows tall and A long, south arm hv columns wide and B long, joined by an h x hv corner block"""
    hv = hv or h
    cells = {(x, y) for x in range(A + hv) for y in range(h)} | {(x, y) for x in range(A, A + hv) for y in range(h, h + B)}
    mouths = {'W': {(-1, y) for y in range(h)}, 'S': {(x, h + B) for x in range(A, A + hv)}}
    return Region(cells, mouths, reach, ncls)


def surface_only(R):
    """mouths become plain ports: belts come in and leave on the surface only"""
    for (t, n) in list(R.TIN.values()) + list(R.TOUT.values()):
        R.m.Add(t == 0)
    for (t, run) in R.OVER.values():
        R.m.Add(t == 0)


def gap(H, h, F, L, top=0):
    """a real layout: H rows on both sides (F columns each), only rows top..top+h-1 open over the L middle columns"""
    cells = {(x, y) for x in range(F) for y in range(H)} | {(x, y) for x in range(F, F + L) for y in range(top, top + h)} \
        | {(x, y) for x in range(F + L, 2 * F + L) for y in range(H)}
    mouths = {'W': {(-1, y) for y in range(H)}, 'E': {(2 * F + L, y) for y in range(H)}}
    R = Region(cells, mouths)
    surface_only(R)
    return R


def codes(R):
    """solved layout as the 2-character grid rows used everywhere else (B3 belt east, I3/O3 tunnel in/out ...)"""
    s = R.last
    g = {}
    for (p, c, kk, d), v in R.X.items():
        if s.Value(v):
            g[p] = '%s%d' % (kk, d)
    xs = [p[0] for p in R.cells]
    ys = [p[1] for p in R.cells]
    return [''.join(g.get((x, y), '..') for x in range(min(xs), max(xs) + 1)) for y in range(min(ys), max(ys) + 1)]


def trace_grid(rows, reach=7):
    """follow every belt of a finished 2-char grid from the west edge; -> number of belts that reach the east edge,
    checks that every piece is used exactly once and nothing is fed twice"""
    H, W = len(rows), len(rows[0]) // 2
    g = {(x, y): rows[y][2 * x:2 * x + 2] for y in range(H) for x in range(W) if rows[y][2 * x:2 * x + 2] != '..'}

    def nxt(p):
        k, d = g[p][0], int(g[p][1])
        if k == 'I':
            for n in range(1, reach + 1):
                q = (p[0] + n * DIRS[d][0], p[1] + n * DIRS[d][1])
                if q in g and g[q] == 'O%d' % d:
                    return q
                assert not (q in g and g[q] == 'I%d' % d), ('entrance inside tunnel', p)
            raise AssertionError(('no exit', p))
        q = (p[0] + DIRS[d][0], p[1] + DIRS[d][1])
        if q[0] >= W:
            return 'OUT'
        assert q in g, ('pushes into nothing', p)
        kk, dd = g[q][0], int(g[q][1])
        assert (kk == 'B' and dd != (d + 2) % 4) or (kk == 'I' and dd == d), ('not accepted', p, q)
        return q
    used, done = set(), 0
    for y in range(H):
        p = (0, y)
        if p in g and g[p] in ('B3', 'B0', 'B2', 'I3'):
            fed = [q for q in g if q not in used and nxt(q) == p]
            if fed:
                continue                    # fed from inside, not an input
            while p != 'OUT':
                assert p not in used, ('fed twice', p)
                used.add(p)
                p = nxt(p)
            done += 1
    assert used == set(g), ('unused pieces', sorted(set(g) - used)[:5])
    return done


if __name__ == '__main__' and sys.argv[1] == 'gap':
    H, h, F, L, k = [int(v) for v in sys.argv[2:7]]
    R = gap(H, h, F, L, (H - h) // 2)
    R.m.Add(R.flux_in('W', 0) == k)
    R.m.Minimize(sum(R.X.values()))
    st, rows, _, _ = R.solve(float(sys.argv[7]) if len(sys.argv) > 7 else 600)
    print('gap H %d h %d F %d L %d k %d: %s' % (H, h, F, L, k, st), flush=True)
    if st == 'FOUND':
        cr = codes(R)
        print('traced belts:', trace_grid(cr))
        print('\n'.join(cr))


if __name__ == '__main__':
    a = sys.argv[1:]
    t0 = time.time()
    if a[0] == 'strip':
        h, k, L = int(a[1]), int(a[2]), int(a[3])
        R = strip(h, L)
        R.m.Add(R.flux_in('W', 0) == k)
        st, rows, _, _ = R.solve(float(a[4]) if len(a) > 4 else 600)
        print('strip h %d k %d L %d: %s %.0fs' % (h, k, L, st, time.time() - t0), flush=True)
    elif a[0] == 'corner':
        h, A, B, k = int(a[1]), int(a[2]), int(a[3]), int(a[4])
        R = corner(h, A, B, int(a[6]) if len(a) > 6 else 7)
        R.m.Add(R.flux_in('W', 0) == k)
        st, rows, _, _ = R.solve(float(a[5]) if len(a) > 5 else 600)
        print('corner h %d A %d B %d k %d: %s %.0fs' % (h, A, B, k, st, time.time() - t0), flush=True)
    elif a[0] == 'cornermax':
        h, A, B = int(a[1]), int(a[2]), int(a[3])
        R = corner(h, A, B)
        st, rows, obj, opt = R.solve(float(a[4]) if len(a) > 4 else 600, maximize=R.flux_in('W', 0))
        print('cornermax h %d A %d B %d: %s best %s optimal %s %.0fs' % (h, A, B, st, obj, opt, time.time() - t0), flush=True)
    for r in (locals().get('rows') or []) if a[0] != 'gap' else []:
        print('   ', r)
