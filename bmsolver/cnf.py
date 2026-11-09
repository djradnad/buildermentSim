"""The same layout model as model.py, as plain CNF for any SAT solver.

model.Model builds a CP-SAT model. This module translates that model (its proto) clause by clause, so every rule
stays exactly the same and only the engine changes:
  - a 0/1 variable becomes one SAT variable;
  - enc='binary' (default, BinTranslation): an integer 0..ub (a flow) becomes its binary digits, sums become adder
    circuits and bounds become comparators (about 60x fewer clauses than the order encoding);
  - enc='order' (Translation): an integer becomes ub order literals [x >= 1], ..., [x >= ub] and a linear constraint
    a count of literals within bounds (two-way totalizer cut at the bound);
  - enforcement literals guard every clause of a constraint.
race() runs CP-SAT and external SAT binaries (kissat, cadical) at once on one window; first definite answer wins.
Solvers: any pysat solver by name (kissat404, cadical195, glucose4, cryptosat, ...) or an external DIMACS binary.
"""
import os
import subprocess
import tempfile
import time

from ortools.sat import cp_model_pb2
from google.protobuf import text_format


class CNF:
    def __init__(self):
        self.nv = 0
        self.clauses = []
        self.TRUE = self.new()
        self.clauses.append([self.TRUE])

    def new(self):
        self.nv += 1
        return self.nv

    def add(self, c):
        self.clauses.append(c)


def _load(model):
    tmp = tempfile.NamedTemporaryFile('w', suffix='.pb.txt', delete=False)
    tmp.close()
    model.ExportToFile(tmp.name)
    p = cp_model_pb2.CpModelProto()
    text_format.Parse(open(tmp.name).read(), p)
    os.unlink(tmp.name)
    return p


class Translation:
    """CNF of a CP-SAT model. value(index) reads a variable back from a SAT model."""

    def __init__(self, model):
        p = self.proto = _load(model)
        f = self.f = CNF()
        self.bool = {}       # var index -> sat var
        self.ord = {}        # var index -> [lit for x>=1, ..., x>=ub]
        self.lo = {}
        for i, v in enumerate(p.variables):
            lo, hi = v.domain[0], v.domain[-1]
            assert len(v.domain) == 2, 'domains with holes are not used by model.py'
            if lo == 0 and hi == 1:
                self.bool[i] = f.new()
            else:
                self.lo[i] = lo
                o = [f.new() for _ in range(hi - lo)]
                for a, b in zip(o[1:], o):
                    f.add([-a, b])
                self.ord[i] = o
        for ct in p.constraints:
            getattr(self, '_' + ct.WhichOneof('constraint'))(ct)

    # ---- literals
    def lit(self, ref):
        """sat literal of a CP-SAT boolean literal reference"""
        if ref >= 0:
            return self.bool[ref]
        return -self.bool[-ref - 1]

    def ge(self, i, k):
        """sat literal for x_i >= k (TRUE / FALSE as the constant literal when outside the range)"""
        o, lo = self.ord[i], self.lo[i]
        k -= lo
        if k <= 0:
            return self.f.TRUE
        if k > len(o):
            return -self.f.TRUE
        return o[k - 1]

    def guard(self, ct):
        return [-self.lit(e) for e in ct.enforcement_literal]

    # ---- constraints
    def _bool_or(self, ct):
        self.f.add(self.guard(ct) + [self.lit(l) for l in ct.bool_or.literals])

    def _bool_and(self, ct):
        g = self.guard(ct)
        for l in ct.bool_and.literals:
            self.f.add(g + [self.lit(l)])

    def _exactly_one(self, ct):
        lits = [self.lit(l) for l in ct.exactly_one.literals]
        g = self.guard(ct)
        self.f.add(g + lits)
        self._amo(lits, g)

    def _at_most_one(self, ct):
        self._amo([self.lit(l) for l in ct.at_most_one.literals], self.guard(ct))

    def _amo(self, lits, g):
        if len(lits) <= 6:
            for a in range(len(lits)):
                for b in range(a + 1, len(lits)):
                    self.f.add(g + [-lits[a], -lits[b]])
            return
        # sequential counter
        s = [self.f.new() for _ in lits[:-1]]
        self.f.add(g + [-lits[0], s[0]])
        for k in range(1, len(lits) - 1):
            self.f.add(g + [-lits[k], s[k]])
            self.f.add(g + [-s[k - 1], s[k]])
            self.f.add(g + [-lits[k], -s[k - 1]])
        self.f.add(g + [-lits[-1], -s[-1]])

    def _linear(self, ct):
        L = ct.linear
        dom = list(L.domain)
        assert len(dom) == 2
        lo, hi = dom
        g = self.guard(ct)
        terms = list(zip(L.vars, L.coeffs))
        ints = [(v, c) for v, c in terms if v in self.ord]
        # x - y in [lo, hi] (both integers, unit coefficients): direct implications
        if len(terms) == 2 and len(ints) == 2 and sorted(c for _, c in ints) == [-1, 1]:
            (x, _), (y, _) = sorted(ints, key=lambda t: -t[1])
            ux, uy = len(self.ord[x]) + self.lo[x], len(self.ord[y]) + self.lo[y]
            for k in range(self.lo[x], ux + 2):          # x >= k  ->  y >= k - hi
                if hi < 2**62:
                    self._imp(g, self.ge(x, k), self.ge(y, k - hi))
            for k in range(self.lo[y], uy + 2):          # y >= k  ->  x >= k + lo
                if lo > -2**62:
                    self._imp(g, self.ge(y, k), self.ge(x, k + lo))
            return
        # general: sum of literals (with repetition) + offset in [lo, hi]
        lits, off = [], 0
        for v, c in terms:
            if v in self.bool:
                b = self.bool[v]
                if c > 0:
                    lits += [b] * c
                else:
                    lits += [-b] * (-c)
                    off += c
            else:
                o, base = self.ord[v], self.lo[v]
                if c > 0:
                    lits += o * c
                    off += c * base
                else:
                    lits += [-l for l in o] * (-c)
                    off += c * (base + len(o))
        n = len(lits)
        clo = max(0, lo - off) if lo > -2**62 else 0
        chi = min(n, hi - off) if hi < 2**62 else n
        if clo > n or chi < 0 or clo > chi:
            self.f.add(g)            # impossible: the guard must be false
            return
        if clo == 0 and chi == n:
            return
        if len(lits) == 1:
            if clo == 1:
                self.f.add(g + [lits[0]])
            if chi == 0:
                self.f.add(g + [-lits[0]])
            return
        if clo == 0 and chi == 0:
            for l in lits:
                self.f.add(g + [-l])
            return
        if clo == n:
            for l in lits:
                self.f.add(g + [l])
            return
        if clo <= 1 and chi == n:
            if clo == 1:
                self.f.add(g + list(dict.fromkeys(lits)))
            return
        out = self._totalizer(lits, chi + 1)
        if chi < n:
            self.f.add(g + [-out[chi]])          # not count >= chi + 1
        if clo > 0:
            self.f.add(g + [out[clo - 1]])       # count >= clo

    def _imp(self, g, a, b):
        T = self.f.TRUE
        if a == -T or b == T:
            return
        c = g[:]
        if a != T:
            c.append(-a)
        if b != -T:
            c.append(b)
        self.f.add(c)

    def _totalizer(self, lits, cap):
        """out[j] <-> count >= j + 1, for j < min(len, cap)"""
        if len(lits) == 1:
            return [lits[0]]
        m = len(lits) // 2
        a, b = self._totalizer(lits[:m], cap), self._totalizer(lits[m:], cap)
        na, nb = len(a), len(b)
        full_a, full_b = na == len(lits[:m]), nb == len(lits[m:])
        n = min(len(lits), cap)
        r = [self.f.new() for _ in range(n)]
        A = lambda i: a[i - 1]
        B = lambda j: b[j - 1]
        for i in range(0, na + 1):
            for j in range(0, nb + 1):
                k = i + j
                if 1 <= k <= n:
                    c = []
                    if i:
                        c.append(-A(i))
                    if j:
                        c.append(-B(j))
                    self.f.add(c + [r[k - 1]])
                # down: count_a <= i and count_b <= j -> count < i + j + 1
                k = i + j + 1
                if k <= n:
                    c = []
                    if i < na:
                        c.append(A(i + 1))
                    elif not full_a:
                        continue
                    if j < nb:
                        c.append(B(j + 1))
                    elif not full_b:
                        continue
                    self.f.add(c + [-r[k - 1]])
        return r

    # ---- reading back
    def value(self, model_set, i):
        if i in self.bool:
            return 1 if self.bool[i] in model_set else 0
        return self.lo[i] + sum(1 for l in self.ord[i] if l in model_set)


class _Values:
    """stands in for a CpSolver when reading a layout back with Model.grid"""

    def __init__(self, tr, sat_model):
        self.tr, self.s = tr, set(l for l in sat_model if l > 0)

    def Value(self, var):
        idx = var.Index() if hasattr(var, 'Index') else var.index
        return self.tr.value(self.s, idx)

    BooleanValue = Value


def solve(mod, engine='cadical195', timeout=None, log=False, enc='binary', procs=None):
    """mod: a model.Model. Returns dict(status, time, grid?, build, clauses, vars)."""
    t0 = time.time()
    tr = (BinTranslation if enc == 'binary' else Translation)(mod.m)
    tb = time.time() - t0
    f = tr.f
    res = dict(build=tb, vars=f.nv, clauses=len(f.clauses), engine=engine)
    t1 = time.time()
    if os.path.sep in engine or os.path.isfile(engine):
        sat, model_ = _external(engine, f, timeout, procs)
    else:
        from pysat.solvers import Solver
        with Solver(name=engine, bootstrap_with=f.clauses) as s:
            if timeout:
                import threading
                timer = threading.Timer(timeout, s.interrupt)
                timer.start()
                sat = s.solve_limited(expect_interrupt=True)
                timer.cancel()
            else:
                sat = s.solve()
            model_ = s.get_model() if sat else None
    res['time'] = time.time() - t1
    res['status'] = 'FEASIBLE' if sat else 'INFEASIBLE' if sat is False else 'UNKNOWN'
    if sat:
        res['grid'] = mod.grid(_Values(tr, model_))
        res['options'] = mod.chosen_options(_Values(tr, model_))
        res['machines'] = mod.machines_chosen(_Values(tr, model_))
        res['filters'] = mod.arm_filters(_Values(tr, model_)) if mod.arm_links else {}
        res['violations'] = len(violations(tr, model_))
    return res


def write_dimacs(f, path):
    with open(path, 'w') as fh:
        fh.write('p cnf %d %d\n' % (f.nv, len(f.clauses)))
        for c in f.clauses:
            fh.write(' '.join(map(str, c)) + ' 0\n')


def _external(binary, f, timeout, procs=None):
    """Run a DIMACS solver binary. procs: an optional list the running process is added to (so another thread
    can stop it)."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'm.cnf')
        write_dimacs(f, path)
        p = subprocess.Popen([binary, path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        if procs is not None:
            procs.append(p)
        try:
            out = p.communicate(timeout=timeout)[0]
        except subprocess.TimeoutExpired:
            p.kill()
            p.communicate()
            return None, None
    return _parse(out or '')


def violations(tr, sat_model, limit=10):
    """CP-SAT constraints the decoded assignment breaks (for testing the translation)."""
    s = set(l for l in sat_model if l > 0)
    val = lambda i: tr.value(s, i)
    lv = lambda r: val(r) if r >= 0 else 1 - val(-r - 1)
    bad = []
    for n, ct in enumerate(tr.proto.constraints):
        if not all(lv(e) for e in ct.enforcement_literal):
            continue
        k = ct.WhichOneof('constraint')
        if k == 'bool_or':
            ok = any(lv(l) for l in ct.bool_or.literals)
        elif k == 'bool_and':
            ok = all(lv(l) for l in ct.bool_and.literals)
        elif k == 'exactly_one':
            ok = sum(lv(l) for l in ct.exactly_one.literals) == 1
        elif k == 'linear':
            t = sum(c * val(v) for v, c in zip(ct.linear.vars, ct.linear.coeffs))
            ok = ct.linear.domain[0] <= t <= ct.linear.domain[1]
        else:
            ok = True
        if not ok:
            bad.append((n, k, str(ct).replace('\n', ' ')[:300]))
            if len(bad) >= limit:
                break
    return bad


class BinTranslation(Translation):
    """Like Translation, but integers are binary numbers and linear constraints are adder circuits with
    comparators: far fewer clauses for large flow units (a 1:5 has D = 60), weaker propagation."""

    def __init__(self, model):
        p = self.proto = _load(model)
        f = self.f = CNF()
        self.bool, self.bits, self.lo = {}, {}, {}
        T = f.TRUE
        for i, v in enumerate(p.variables):
            lo, hi = v.domain[0], v.domain[-1]
            assert len(v.domain) == 2
            if lo == 0 and hi == 1:
                self.bool[i] = f.new()
            else:
                self.lo[i] = lo
                width = max(1, (hi - lo).bit_length())
                self.bits[i] = [f.new() for _ in range(width)]
                if (1 << width) - 1 > hi - lo:
                    self._assert_le_const(self.bits[i], hi - lo, [])
        self.ord = {}
        for ct in p.constraints:
            getattr(self, '_' + ct.WhichOneof('constraint'))(ct)

    # ---- small circuits (literals; the constants are TRUE / -TRUE)
    def _and(self, a, b):
        T = self.f.TRUE
        if a == -T or b == -T:
            return -T
        if a == T:
            return b
        if b == T:
            return a
        r = self.f.new()
        self.f.add([-r, a]); self.f.add([-r, b]); self.f.add([r, -a, -b])
        return r

    def _xor(self, a, b):
        T = self.f.TRUE
        if a == -T:
            return b
        if b == -T:
            return a
        if a == T:
            return -b
        if b == T:
            return -a
        r = self.f.new()
        self.f.add([-r, a, b]); self.f.add([-r, -a, -b]); self.f.add([r, -a, b]); self.f.add([r, a, -b])
        return r

    def _or(self, a, b):
        return -self._and(-a, -b)

    def _fa(self, a, b, c):
        s = self._xor(self._xor(a, b), c)
        carry = self._or(self._and(a, b), self._and(c, self._xor(a, b)))
        return s, carry

    def _add(self, X, Y):
        F = -self.f.TRUE
        n = max(len(X), len(Y))
        X = X + [F] * (n - len(X)); Y = Y + [F] * (n - len(Y))
        out, c = [], F
        for a, b in zip(X, Y):
            s, c = self._fa(a, b, c)
            out.append(s)
        out.append(c)
        while len(out) > 1 and out[-1] == F:
            out.pop()
        return out

    def _sum(self, nums):
        F = -self.f.TRUE
        nums = [n for n in nums if any(l != F for l in n)]
        if not nums:
            return [F]
        while len(nums) > 1:
            nums.sort(key=len)
            a, b = nums.pop(0), nums.pop(0)
            nums.append(self._add(a, b))
        return nums[0]

    def _const(self, c):
        T = self.f.TRUE
        return [T if (c >> k) & 1 else -T for k in range(max(1, c.bit_length()))]

    def _term(self, v, c):
        """the number |c| * v as bits"""
        c = abs(c)
        T = self.f.TRUE
        if v in self.bool:
            b = self.bool[v]
            return [b if (c >> k) & 1 else -T for k in range(c.bit_length())]
        X = self.bits[v]
        parts = [[-T] * k + X for k in range(c.bit_length()) if (c >> k) & 1]
        return self._sum(parts)

    def _ge(self, P, Q):
        """literal r with r -> (P >= Q)"""
        F = -self.f.TRUE
        n = max(len(P), len(Q))
        P = P + [F] * (n - len(P)); Q = Q + [F] * (n - len(Q))
        # from the lowest bit up: g_k = P[0..k] >= Q[0..k]
        g = self.f.TRUE
        for p, q in zip(P, Q):
            # g' = (p & !q) | ((p == q) & g)
            g = self._or(self._and(p, -q), self._and(-self._xor(p, q), g))
        return g

    def _assert_le_const(self, X, c, guard):
        self.f.add(guard + [self._ge(self._const(c), X)])

    def _eq(self, P, Q, guard):
        F = -self.f.TRUE
        n = max(len(P), len(Q))
        P = P + [F] * (n - len(P)); Q = Q + [F] * (n - len(Q))
        for p, q in zip(P, Q):
            self._imp(guard, p, q)
            self._imp(guard, q, p)

    def _linear(self, ct):
        L = ct.linear
        lo, hi = L.domain[0], L.domain[1]
        g = self.guard(ct)
        terms = list(zip(L.vars, L.coeffs))
        if all(v in self.bool for v, _ in terms) and all(abs(c) == 1 for _, c in terms):
            return Translation._linear(self, ct)        # small cardinality: the order path handles it well
        off = 0
        pos, neg = [], []
        for v, c in terms:
            if v in self.lo:
                off += c * self.lo[v]
            (pos if c > 0 else neg).append(self._term(v, c))
        INF = 2**62
        lo = lo - off if lo > -INF else None
        hi = hi - off if hi < INF else None
        P, N = self._sum(pos), self._sum(neg)
        def shifted(X, k):            # X + k for k >= 0
            return X if k == 0 else self._add(X, self._const(k))
        if lo is not None and hi is not None and lo == hi:
            if lo >= 0:
                self._eq(P, shifted(N, lo), g)
            else:
                self._eq(shifted(P, -lo), N, g)
            return
        if lo is not None:            # P - N >= lo
            r = self._ge(P, shifted(N, lo)) if lo >= 0 else self._ge(shifted(P, -lo), N)
            self.f.add(g + [r])
        if hi is not None:            # P - N <= hi
            r = self._ge(shifted(N, hi), P) if hi >= 0 else self._ge(N, shifted(P, -hi))
            self.f.add(g + [r])

    def ge(self, i, k):
        raise NotImplementedError

    def value(self, model_set, i):
        if i in self.bool:
            return 1 if self.bool[i] in model_set else 0
        return self.lo[i] + sum(1 << k for k, l in enumerate(self.bits[i]) if l in model_set)


def _parse(out):
    status = [l for l in out.splitlines() if l.startswith('s ')]
    if not status or 'UNKNOWN' in status[0]:
        return None, None
    if 'UNSATISFIABLE' in status[0]:
        return False, None
    vals = []
    for l in out.splitlines():
        if l.startswith('v '):
            vals += [int(x) for x in l[2:].split() if x != '0']
    return True, vals


def _cpsat_worker(win, timeout, workers, q):
    from . import model
    r = model.solve(win, timeout=timeout, workers=workers)
    q.put(('cpsat', r))


def race(win, binaries, timeout=600, cpsat_workers=1, enc='binary', log=print):
    """Run CP-SAT and several external SAT solvers on the same window at once; the first definite answer wins
    and the rest are stopped. binaries: paths of DIMACS solvers (kissat, cadical, ...). Every solver gets the
    exact same rules (the CNF is a translation of the CP-SAT model), so whichever answers first is as good as any.
    Returns dict(status, winner, time, grid?)."""
    import multiprocessing as mp
    import signal
    from . import model
    t0 = time.time()
    mod = model.Model(win)
    tr = (BinTranslation if enc == 'binary' else Translation)(mod.m)
    d = tempfile.mkdtemp()
    path = os.path.join(d, 'm.cnf')
    write_dimacs(tr.f, path)
    procs = {}
    for b in binaries:
        fo = open(os.path.join(d, os.path.basename(b) + '.out'), 'w+')
        procs[b] = (subprocess.Popen([b, path], stdout=fo, stderr=subprocess.DEVNULL, start_new_session=True), fo)
    q = mp.get_context('fork').Queue()
    cp = None
    if cpsat_workers:
        cp = mp.get_context('fork').Process(target=_cpsat_worker, args=(win, timeout, cpsat_workers, q), daemon=True)
        cp.start()
    res = dict(status='UNKNOWN', winner=None)
    try:
        while time.time() - t0 < timeout:
            for b, (p, fo) in list(procs.items()):
                if p.poll() is not None:
                    fo.seek(0)
                    sat, vals = _parse(fo.read())
                    del procs[b]
                    if sat is not None:
                        res.update(status='FEASIBLE' if sat else 'INFEASIBLE', winner=os.path.basename(b))
                        if sat:
                            res['grid'] = mod.grid(_Values(tr, vals))
                            res['options'] = mod.chosen_options(_Values(tr, vals))
                            res['machines'] = mod.machines_chosen(_Values(tr, vals))
                            res['filters'] = mod.arm_filters(_Values(tr, vals)) if mod.arm_links else {}
                        return res
            if cp is not None and not q.empty():
                _, r = q.get()
                cp = None
                if r['status'] != 'UNKNOWN':
                    res.update(status='FEASIBLE' if r['status'] in ('OPTIMAL', 'FEASIBLE') else r['status'], winner='cpsat')
                    if 'grid' in r:
                        res['grid'] = r['grid']
                        res['options'] = r.get('options')
                        res['machines'] = r.get('machines')
                        res['filters'] = r.get('filters')
                    return res
            if not procs and cp is None:
                break
            time.sleep(0.05)
        return res
    finally:
        res['time'] = time.time() - t0
        for b, (p, fo) in procs.items():
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if cp is not None:
            cp.kill()
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def cubes(win, mirror=False):
    """Split a window into independent sub-windows, one per choice of port positions (each option group fixed).
    The window is feasible iff some cube is. mirror=True drops top/bottom mirror images of a choice: only for specs
    where the order of inputs and of outputs doesn't matter (1:N splitters, N:N balancers), since a mirror
    reverses that order."""
    import copy
    import itertools
    from .pieces import MIRROR_Y
    if not win.port_options:
        return [win]
    H = win.H

    def mir(opt):
        return frozenset((((x, H - 1 - y), MIRROR_Y[d]), kind) for ((x, y), d), (kind, vec) in opt.items())

    keyed = [[frozenset(((t, kind) for t, (kind, vec) in opt.items())) for opt in g] for g in win.port_options]
    seen, out = set(), []
    for combo in itertools.product(*[range(len(g)) for g in win.port_options]):
        key = frozenset(keyed[i][c] for i, c in enumerate(combo))
        mkey = frozenset(mir(win.port_options[i][c]) for i, c in enumerate(combo))
        fixed_ports = set(win.ports)
        if mirror and win.symmetry is None and win.tun_in == {} and not win.blocked and \
                {(t[0][0], H - 1 - t[0][1]) for t in fixed_ports} == {t[0] for t in fixed_ports} and mkey in seen:
            continue
        seen.add(key)
        w = copy.copy(win)
        w.ports = dict(win.ports)
        for i, c in enumerate(combo):
            w.ports.update(win.port_options[i][c])
        w.port_options = []
        out.append(w)
    return out


def prove(win, binary, procs=4, timeout=None, enc='binary', mirror=False, log=print, lemmas=False):
    """Cube and conquer: solve every cube of win with an external SAT binary, procs at a time. Stops at the first
    layout found. Returns dict(status, time, grid?, cubes, done)."""
    import concurrent.futures as cf
    from . import model
    t0 = time.time()
    cs = cubes(win, mirror)
    res = dict(status='UNKNOWN', cubes=len(cs), done=0)

    running, stop = [], [False]

    def one(w):
        if stop[0]:
            return dict(status='CANCELLED', time=0)
        mod = model.Model(w)
        if lemmas:                      # implied local flow rules (lazy.add_flow_lemmas): ~20% faster per cube
            from . import lazy
            lazy.add_flow_lemmas(mod, w)
        return solve(mod, engine=binary, timeout=timeout, enc=enc, procs=running)

    with cf.ThreadPoolExecutor(procs) as ex:
        futs = {ex.submit(one, w): k for k, w in enumerate(cs)}
        unknown = False
        for f in cf.as_completed(futs):
            r = f.result()
            res['done'] += 1
            if log:
                log('  cube %d/%d %s %.1fs' % (futs[f] + 1, len(cs), r['status'], r['time']))
            if r['status'] == 'FEASIBLE':
                res.update(status='FEASIBLE', grid=r['grid'], machines=r.get('machines'), filters=r.get('filters'), window=cs[futs[f]])
                stop[0] = True
                for g in futs:
                    g.cancel()
                for p in running:
                    if p.poll() is None:
                        p.kill()
                break
            if r['status'] == 'UNKNOWN':
                unknown = True
        else:
            res['status'] = 'UNKNOWN' if unknown else 'INFEASIBLE'
    res['time'] = time.time() - t0
    return res
