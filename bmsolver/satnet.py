"""Netplace (fixed network placement) solved by an external SAT solver, with the CNF written directly.

netplace.build() is a pure true/false model. Built on CP-SAT it costs a proto plus a 20-45 s clause-by-clause
translation (cnf.BinTranslation) before kissat can start. Here the same model code runs on CnfModel, a stand-in for
cp_model.CpModel that turns every constraint into clauses as it is added (cardinality constraints by pairwise /
ladder / sequential-counter / totalizer encodings). The rules are the same lines of code (Model + build), so the CNF
says exactly what the CP-SAT model says.

    r = place(net, win, timeout=300, seeds=(0, 1))         # FEASIBLE / INFEASIBLE / UNKNOWN; finds are checked exactly
    r = prove(net, win, workdir)                            # INFEASIBLE with a DRAT proof checked by drat-trim
    r = ladder(net, win, budget=600)                        # finds: 'eastle=0', 'eastle=2', full, short seeded tries
    r = widths(net, H, n, Wmax, Wmin)                       # one CNF at Wmax, width W by "east columns are pads"

netplace.place(..., engine='kissat') or NETPLACE_ENGINE=kissat routes netplace.place here. CP-SAT stays the default.
Restrictions (tokens in `restrict`): 'mono' (no east-pushing piece), 'eastle=K' (at most K links point east,
surface or tunnel). A restriction's INFEASIBLE proves nothing; place() and ladder() report them in 'restricted'.
Every SAT answer is decoded to a grid and checked with netplace.check_layout (exact n:n shares of the extracted
network); a failed check is reported as status 'BADMODEL', never as a find.
"""
import os
import subprocess
import tempfile
import time

# a kissat built with options (seeds, --time); the /root/evo build is configured with --no-options, so with it
# a seed s > 0 runs on a copy of the CNF with the clauses shuffled by s instead
KISSAT = os.environ.get('BM_KISSAT') or next((b for b in ('/root/solvers/kissat', '/root/evo/v2_ts/build/kissat')
                                              if os.path.exists(b)), 'kissat')
DRAT_TRIM = os.environ.get('BM_DRAT_TRIM', '/root/solvers/drat-trim')


# ---------------------------------------------------------------- a CpModel stand-in that writes clauses

class Lit:
    """A boolean variable (v > 0) or its negation (v < 0), as in DIMACS."""
    __slots__ = ('v',)

    def __init__(self, v):
        self.v = v

    def Not(self):
        return Lit(-self.v)

    def Index(self):
        return abs(self.v)

    def __hash__(self):
        return hash(self.v)

    def __repr__(self):
        return 'Lit(%d)' % self.v

    # arithmetic gives linear expressions
    def _e(self):
        return Expr([(self, 1)], 0)

    def __add__(self, o): return self._e() + o
    def __radd__(self, o): return self._e() + o
    def __sub__(self, o): return self._e() - o
    def __rsub__(self, o): return (-1) * self._e() + o
    def __mul__(self, c): return self._e() * c
    def __rmul__(self, c): return self._e() * c
    def __neg__(self): return self._e() * -1
    def __eq__(self, o): return Cmp(self, o, '==')
    def __le__(self, o): return Cmp(self, o, '<=')
    def __ge__(self, o): return Cmp(self, o, '>=')
    def __ne__(self, o): return Cmp(self, o, '!=')


class Expr:
    __slots__ = ('terms', 'const')

    def __init__(self, terms, const):
        self.terms, self.const = terms, const

    @staticmethod
    def of(x):
        if isinstance(x, Expr):
            return x
        if isinstance(x, Lit):
            return x._e()
        if isinstance(x, (bool, int)):
            return Expr([], int(x))
        raise TypeError('not a linear term: %r' % (x,))

    def __add__(self, o):
        o = Expr.of(o)
        return Expr(self.terms + o.terms, self.const + o.const)

    __radd__ = __add__

    def __sub__(self, o):
        return self + Expr.of(o) * -1

    def __rsub__(self, o):
        return self * -1 + o

    def __mul__(self, c):
        if not isinstance(c, int):
            raise TypeError('only integer coefficients')
        return Expr([(l, a * c) for l, a in self.terms], self.const * c)

    __rmul__ = __mul__

    def __neg__(self):
        return self * -1

    def __eq__(self, o): return Cmp(self, o, '==')
    def __le__(self, o): return Cmp(self, o, '<=')
    def __ge__(self, o): return Cmp(self, o, '>=')
    def __ne__(self, o): return Cmp(self, o, '!=')
    __hash__ = None


class Cmp:
    """lhs op rhs, before it is handed to Add()."""
    __slots__ = ('a', 'b', 'op')

    def __init__(self, a, b, op):
        self.a, self.b, self.op = a, b, op

    def __bool__(self):                 # `lit in list` compares literals by identity of the variable, as CP-SAT does
        if isinstance(self.a, Lit) and isinstance(self.b, Lit) and self.op in ('==', '!='):
            return (self.a.v == self.b.v) == (self.op == '==')
        raise TypeError('a linear constraint has no truth value')


class _Ct:
    __slots__ = ('cm', 'kind', 'data', 'guard')

    def __init__(self, cm, kind, data):
        self.cm, self.kind, self.data, self.guard = cm, kind, data, []

    def OnlyEnforceIf(self, *lits):
        for x in lits:
            if isinstance(x, (list, tuple)):
                self.guard += list(x)
            else:
                self.guard.append(x)
        return self


class CnfModel:
    """The subset of cp_model.CpModel that Model(flows=False), netplace.build and the restrictions use.
    Constraints are stored (OnlyEnforceIf may follow Add) and turned into clauses by clauses()."""

    def __init__(self):
        self.nv = 0
        self.cts = []
        self.out = None

    # variables
    def NewBoolVar(self, name=''):
        self.nv += 1
        return Lit(self.nv)

    def NewIntVar(self, lo, hi, name=''):
        raise NotImplementedError('CnfModel is pure true/false (the flow model needs CP-SAT or cnf.BinTranslation)')

    def _new(self):
        self.nv += 1
        return self.nv

    # constraints
    def _ct(self, kind, data):
        c = _Ct(self, kind, data)
        self.cts.append(c)
        return c

    def Add(self, c):
        if isinstance(c, bool):
            return self._ct('or', [] if not c else [True])
        if not isinstance(c, Cmp):
            raise TypeError('Add() wants a comparison, got %r' % (c,))
        if c.op == '!=':
            raise NotImplementedError('!=')
        return self._ct('lin', (Expr.of(c.a) - Expr.of(c.b), c.op))

    def AddBoolOr(self, lits):
        return self._ct('or', list(lits))

    def AddBoolAnd(self, lits):
        return self._ct('and', list(lits))

    def AddImplication(self, a, b):
        return self._ct('or', [a.Not(), b])

    def AddExactlyOne(self, lits):
        return self._ct('lin', (sum(lits, Expr([], 0)) - 1, '=='))

    def AddAtMostOne(self, lits):
        return self._ct('lin', (sum(lits, Expr([], 0)) - 1, '<='))

    def AddHint(self, var, value):
        pass

    def Minimize(self, e):
        raise NotImplementedError('no objective in CnfModel')

    # ---- clause generation
    def clauses(self):
        """All clauses (lists of ints). Computed once; constraints added afterwards are not included."""
        if self.out is not None:
            return self.out
        self.out = out = []
        for c in self.cts:
            ng = []
            skip = False
            for g in c.guard:
                if g is True:
                    continue
                if g is False:
                    skip = True
                    break
                ng.append(-g.v)
            if skip:
                continue
            if c.kind == 'or':
                cl = []
                sat = False
                for x in c.data:
                    if x is True:
                        sat = True
                        break
                    if x is False or x is None:
                        continue
                    cl.append(x.v)
                if not sat:
                    out.append(ng + cl)
            elif c.kind == 'and':
                for x in c.data:
                    if x is True:
                        continue
                    out.append(ng + ([] if x is False else [x.v]))
            else:
                e, op = c.data
                self._linear(e, op, ng)
        return out

    def _linear(self, e, op, ng):
        """sum(terms) + const op 0, as clauses guarded by ng (the negated enforcement literals)."""
        coef = {}
        const = e.const
        for l, a in e.terms:
            if a == 0:
                continue
            if l.v > 0:
                coef[l.v] = coef.get(l.v, 0) + a
            else:                                   # a * not x = a - a * x
                const += a
                coef[-l.v] = coef.get(-l.v, 0) - a
        lits = []                                   # positive coefficients only
        for v, a in coef.items():
            if a > 0:
                lits += [v] * a
            elif a < 0:                             # a * x = a + (-a) * not x
                const += a
                lits += [-v] * (-a)
        K = -const                                  # sum(lits) op K
        if op in ('<=', '=='):
            self._le(lits, K, ng)
        if op in ('>=', '=='):
            self._ge(lits, K, ng)

    def _le(self, lits, K, ng):
        out, n = self.out, len(lits)
        if K >= n:
            return
        if K < 0:
            out.append(list(ng))
            return
        if K == 0:
            for x in lits:
                out.append(ng + [-x])
            return
        if K == n - 1:
            out.append(ng + [-x for x in lits])
            return
        if K == 1:
            if n <= 6:
                for i in range(n):
                    for j in range(i + 1, n):
                        out.append(ng + [-lits[i], -lits[j]])
                return
            s_prev = None                           # ladder: s_i = some of lits[0..i] is true
            for i, x in enumerate(lits):
                if s_prev is not None:
                    out.append(ng + [-s_prev, -x])
                if i < n - 1:
                    s = self._new()
                    out.append([-x, s])
                    if s_prev is not None:
                        out.append([-s_prev, s])
                    s_prev = s
            return
        if K <= n - K:                              # sequential counter (Sinz): s[i][j] = at least j+1 of lits[0..i]
            prev = None
            for i, x in enumerate(lits):
                if prev is not None:
                    out.append(ng + [-x, -prev[K - 1]])
                if i == n - 1:
                    break
                cur = [self._new() for _ in range(K)]
                out.append([-x, cur[0]])
                if prev is not None:
                    for j in range(K):
                        out.append([-prev[j], cur[j]])
                    for j in range(1, K):
                        out.append([-x, -prev[j - 1], cur[j]])
                prev = cur
            return
        self._ge([-x for x in lits], n - K, ng)

    def _ge(self, lits, K, ng):
        out, n = self.out, len(lits)
        if K <= 0:
            return
        if K > n:
            out.append(list(ng))
            return
        if K == 1:
            out.append(ng + list(lits))
            return
        if K == n:
            for x in lits:
                out.append(ng + [x])
            return
        if n - K < K:                               # at least K true = at most n-K false
            self._le([-x for x in lits], n - K, ng)
            return
        o = self._totalizer(lits, K)                # o[j] true only if at least j+1 lits are true
        out.append(ng + [o[K - 1]])

    def _totalizer(self, lits, cap):
        """Output literals o[0..min(n,cap)-1]; o[j] -> at least j+1 of lits (the 'down' half, all _ge needs)."""
        if len(lits) == 1:
            return [lits[0]]
        h = len(lits) // 2
        a, b = self._totalizer(lits[:h], cap), self._totalizer(lits[h:], cap)
        m = min(len(a) + len(b), cap)
        r = [self._new() for _ in range(m)]
        out = self.out
        # r[k] means at least k+1 true. a + b <= k (a <= i and b <= j for some i + j = k) forbids it, so
        # r[k] -> a[i] or b[j]; a literal beyond a child's outputs is dropped (that child has at most that many:
        # a capped child has cap > k >= i outputs, so only uncapped children run out)
        for k in range(m):
            for i in range(0, k + 1):
                j = k - i
                cl = [-r[k]]
                if i < len(a):
                    cl.append(a[i])
                if j < len(b):
                    cl.append(b[j])
                out.append(cl)
        return r

    def write(self, path, extra=()):
        cls = self.clauses()
        with open(path, 'w') as f:
            f.write('p cnf %d %d\n' % (self.nv, len(cls) + len(extra)))
            f.write(''.join(' '.join(map(str, c)) + ' 0\n' for c in cls))
            f.write(''.join(' '.join(map(str, c)) + ' 0\n' for c in extra))
        return path


class Values:
    """solver.Value() over a SAT model, for Model.grid()."""

    def __init__(self, model):
        self.s = set(x for x in model if x > 0)

    def Value(self, lit):
        if lit is True:
            return 1
        return int((lit.v in self.s) if lit.v > 0 else (-lit.v not in self.s))

    BooleanValue = Value


# ---------------------------------------------------------------- building

def east_links(mod):
    """Links that move items east (surface pushes and tunnels)."""
    return [l for (p, q, l, kind) in mod.links if p is not None and q is not None and q[0] > p[0]]


def restrict_model(mod, restrict):
    """Apply restriction tokens (see the module docstring) to a built model (CP-SAT or CnfModel)."""
    from .netplace import mono_west
    for tok in restrict or ():
        if tok == 'mono':
            mono_west(mod)
        elif tok.startswith('eastle='):
            mod.m.Add(sum(east_links(mod)) <= int(tok.split('=')[1]))
        elif tok:
            raise ValueError('unknown restriction %r' % tok)


def build_cnf(net, win, restrict=(), assign=None):
    """netplace.build on a CnfModel. Returns the model (mod.m is the CnfModel)."""
    from .netplace import build
    mod = build(net, win, assign=assign, cpmodel=CnfModel())
    restrict_model(mod, restrict)
    return mod


# ---------------------------------------------------------------- running kissat

def parse(out):
    st = [l for l in out.splitlines() if l.startswith('s ')]
    if not st:
        return 'UNKNOWN', None
    if 'UNSATISFIABLE' in st[0]:
        return 'INFEASIBLE', None
    if 'SATISFIABLE' not in st[0]:
        return 'UNKNOWN', None
    vals = []
    for l in out.splitlines():
        if l.startswith('v '):
            vals += [int(x) for x in l[2:].split() if x != '0']
    return 'FEASIBLE', vals


_OPTS = {}


def has_options(binary):
    """Does this kissat take --seed / --time (not built with --no-options)?"""
    if binary not in _OPTS:
        d = tempfile.mkdtemp(prefix='satnet_')
        f = os.path.join(d, 't.cnf')
        open(f, 'w').write('p cnf 1 1\n1 0\n')
        r = subprocess.run([binary, '--seed=1', '--time=5', '-q', f], capture_output=True)
        _OPTS[binary] = r.returncode == 10
    return _OPTS[binary]


def shuffled(path, seed):
    """A copy of a DIMACS file with its clauses in a seeded random order (diversity without --seed)."""
    import random
    lines = open(path).read().splitlines()
    head, body = lines[0], lines[1:]
    random.Random(seed).shuffle(body)
    out = path + '.s%d' % seed
    open(out, 'w').write(head + '\n' + '\n'.join(body) + '\n')
    return out


def run_kissat(path, seed=0, timeout=None, proof=None, binary=None, opts=()):
    """One kissat run on a DIMACS file. Returns (status, model or None, seconds)."""
    binary = binary or KISSAT
    args = [binary]
    if has_options(binary):
        args += ['--seed=%d' % seed, '-q'] + list(opts)
        if timeout:
            args.append('--time=%d' % max(1, int(timeout)))
    elif seed:
        path = shuffled(path, seed)
    args.append(path)
    if proof:
        args.append(proof)
    t0 = time.time()
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        out = p.communicate(timeout=(timeout + 5) if timeout else None)[0]
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()
        return 'UNKNOWN', None, time.time() - t0
    st, model = parse(out or '')
    return st, model, time.time() - t0


def drat_check(cnf_path, proof_path, timeout=3600):
    """drat-trim on a formula and a (binary or text) DRAT proof. Returns (verified, seconds, last output lines)."""
    t0 = time.time()
    try:
        r = subprocess.run([DRAT_TRIM, cnf_path, proof_path, '-t', str(int(timeout))], capture_output=True,
                           text=True, timeout=timeout + 60)
        out = r.stdout
    except subprocess.TimeoutExpired:
        return False, time.time() - t0, 'timeout'
    ok = any(l.strip() == 's VERIFIED' for l in out.splitlines())
    return ok, time.time() - t0, '\n'.join(out.strip().splitlines()[-4:])


def _decode(mod, win, model, n):
    from .netplace import check_layout, extract
    g = mod.grid(Values(model))
    res = dict(grid=g, valid=check_layout(g, win, n))
    return res


def _workdir(workdir):
    return workdir or tempfile.mkdtemp(prefix='satnet_')


def place(net, win, timeout=300, seeds=(0,), restrict=(), workdir=None, log=None, assign=None):
    """Is there a layout of net in win? Tries the seeds one after another (each gets `timeout`), stops at the
    first decided answer. Returns dict(status, time, build, seed, grid?, valid?, restricted?)."""
    t0 = time.time()
    mod = build_cnf(net, win, restrict, assign)
    d = _workdir(workdir)
    path = mod.m.write(os.path.join(d, 'place.cnf'))
    tb = time.time() - t0
    res = dict(status='UNKNOWN', build=round(tb, 2), vars=mod.m.nv, clauses=len(mod.m.out), runs=[])
    if restrict:
        res['restricted'] = ','.join(restrict)
    for s in seeds:
        st, model, t = run_kissat(path, s, timeout)
        res['runs'].append((s, st, round(t, 1)))
        if log:
            log('kissat seed %d: %s %.1fs' % (s, st, t))
        if st == 'UNKNOWN':
            continue
        res.update(status=st, seed=s, solve=round(t, 1))
        if model:
            res.update(_decode(mod, win, model, len(net['src'])))
            if not res['valid']:
                res['status'] = 'BADMODEL'          # never report an unchecked find
        break
    res['time'] = round(time.time() - t0, 1)
    return res


def prove(net, win, workdir=None, timeout=None, seed=0, check_timeout=3600, keep_proof=False, log=None):
    """INFEASIBLE with a checked DRAT proof (full model, no restriction). Returns dict(status, verified, ...)."""
    t0 = time.time()
    mod = build_cnf(net, win)
    d = _workdir(workdir)
    path = mod.m.write(os.path.join(d, 'prove.cnf'))
    proof = os.path.join(d, 'prove.drat')
    tb = time.time() - t0
    st, model, t = run_kissat(path, seed, timeout, proof=proof)
    res = dict(status=st, build=round(tb, 2), solve=round(t, 1), vars=mod.m.nv, clauses=len(mod.m.out), cnf=path)
    if log:
        log('kissat (proof): %s %.1fs' % (st, t))
    if st == 'INFEASIBLE':
        res['proof_bytes'] = os.path.getsize(proof)
        ok, tc, tail = drat_check(path, proof, check_timeout)
        res.update(verified=ok, check=round(tc, 1), drat_tail=tail)
    elif model:
        res.update(_decode(mod, win, model, len(net['src'])))
    if not keep_proof and os.path.exists(proof):
        os.remove(proof)
    res['time'] = round(time.time() - t0, 1)
    return res


# ---------------------------------------------------------------- finds: restriction ladder as a portfolio

DEFAULT_RUNGS = (('eastle=0',), ('eastle=2',), ())


def ladder(net, win, rungs=DEFAULT_RUNGS, seeds=(0, 1, 2), budget=600, first=20, grow=2.0, workdir=None, log=None):
    """Finds only. Each round runs every (rung, seed) pair for the round's timeout (first, first*grow, ...), so
    short tries come first and heavy-tailed runs are cut. Rung CNFs are built once. An INFEASIBLE rung is
    dropped (and, for the unrestricted rung, ends the search: that one is a real answer).
    Returns the first valid find, or dict(status='UNKNOWN') when the budget runs out."""
    t0 = time.time()
    d = _workdir(workdir)
    built = []
    for i, r in enumerate(rungs):
        mod = build_cnf(net, win, r)
        built.append((r, mod, mod.m.write(os.path.join(d, 'rung%d.cnf' % i))))
    tries = []
    alive = list(range(len(built)))
    tmo = first
    while alive:
        for i in list(alive):
            r, mod, path = built[i]
            for s in seeds:
                left = budget - (time.time() - t0)
                if left <= 1:
                    return dict(status='UNKNOWN', time=round(time.time() - t0, 1), tries=tries)
                st, model, t = run_kissat(path, s, min(tmo, left))
                tries.append((','.join(r) or 'full', s, round(tmo), st, round(t, 1)))
                if log:
                    log('ladder %s seed %d (%ds): %s %.1fs' % (','.join(r) or 'full', s, tmo, st, t))
                if st == 'FEASIBLE':
                    res = dict(status='FEASIBLE', rung=','.join(r) or 'full', seed=s, time=round(time.time() - t0, 1),
                               tries=tries)
                    res.update(_decode(mod, win, model, len(net['src'])))
                    if res['valid']:
                        return res
                    res['status'] = 'BADMODEL'
                    return res
                if st == 'INFEASIBLE':
                    alive.remove(i)
                    if not r:
                        return dict(status='INFEASIBLE', time=round(time.time() - t0, 1), tries=tries)
                    break
        tmo = int(tmo * grow)
    return dict(status='UNKNOWN', time=round(time.time() - t0, 1), tries=tries)


# ---------------------------------------------------------------- widths from one CNF (pad-column assumptions)

def pad_window(spec, W, H):
    """The window, with the input rows that a pad column carries (east-edge input ports)."""
    win = spec.window(W, H)
    if win.port_options:
        raise ValueError('pad columns need fixed port rows')
    rows = sorted(p[1] for (p, d), sp in win.ports.items()
                  if d == 3 and p[0] == W - 1 and (sp[0] if isinstance(sp, tuple) else sp) == 'in')
    outs_east = [1 for (p, d), sp in win.ports.items() if d != 1 and (sp[0] if isinstance(sp, tuple) else sp) == 'out']
    if outs_east or len(rows) == 0:
        raise ValueError('pad columns need inputs on the east edge and outputs on the west edge only')
    return win, rows


def widths(net, spec, H, Wmax, Wmin, timeout=600, seed=0, workdir=None, log=None, proof=False, engine='kissat'):
    """Decide widths Wmax, Wmax-1, ... down to Wmin with ONE CNF built at Wmax (kissat run per width; the
    assumptions are passed as unit clauses appended to the same file, so the formula is the same base clauses).
    Column x is a pad when every input row holds a west belt B1 and every other row is empty. Width W is decided
    by forcing columns W..Wmax-1 to be pads: a layout of width W padded on its input side by straight belts is a
    layout at Wmax with the same network, and a padded Wmax layout with its pads cut off is a width-W layout
    (the pad belts act exactly like the input ports). Walks down while FEASIBLE; stops at the first INFEASIBLE
    or UNKNOWN. Returns [(W, status, seconds, valid or None)].
    engine 'kissat': one build for all widths, a fresh kissat run per width (kissat is not incremental).
    engine 'cadical': one incremental CaDiCaL (pysat) solving under the pad assumptions, so clauses learnt at one
    width are kept for the next (no DRAT proof in this mode; timeout per width via interrupt)."""
    win, rows = pad_window(spec, Wmax, H)
    t0 = time.time()
    mod = build_cnf(net, win)
    X = mod.X
    pad = {}
    for x in range(Wmax):
        t = mod.m.NewBoolVar('pad%d' % x)
        pad[x] = t
        for y in range(H):
            mod.m.AddImplication(t, X[(x, y), 'B1' if y in rows else 'E'])
    d = _workdir(workdir)
    base = mod.m.clauses()
    tb = time.time() - t0
    if log:
        log('built Wmax=%d: %d vars %d clauses in %.1fs' % (Wmax, mod.m.nv, len(base), tb))
    out = []
    if engine == 'cadical':
        return _widths_inc(mod, pad, net, spec, H, Wmax, Wmin, timeout, base, log)
    for W in range(Wmax, Wmin - 1, -1):
        path = mod.m.write(os.path.join(d, 'w%d.cnf' % W), extra=[[pad[x].v] for x in range(W, Wmax)])
        pf = os.path.join(d, 'w%d.drat' % W) if proof else None
        st, model, t = run_kissat(path, seed, timeout, proof=pf)
        valid = None
        if model:
            g = {p: c for p, c in mod.grid(Values(model)).items() if p[0] < W}
            from .netplace import check_layout
            valid = check_layout(g, spec.window(W, H), len(net['src']))
            if not valid:
                st = 'BADMODEL'
        rec = (W, st, round(t, 1), valid)
        if proof and st == 'INFEASIBLE':
            ok, tc, tail = drat_check(path, pf)
            rec = rec + (ok,)
            os.remove(pf)
        out.append(rec)
        if log:
            log('W=%d: %s %.1fs valid=%s' % rec[:4])
        if st != 'FEASIBLE':
            break
    return out



def _widths_inc(mod, pad, net, spec, H, Wmax, Wmin, timeout, base, log):
    import threading
    from pysat.solvers import Cadical195
    from .netplace import check_layout
    out = []
    with Cadical195(bootstrap_with=base) as S:
        for W in range(Wmax, Wmin - 1, -1):
            assume = [pad[x].v for x in range(W, Wmax)]
            t0 = time.time()
            timer = threading.Timer(timeout, S.interrupt)
            timer.start()
            r = S.solve_limited(assumptions=assume, expect_interrupt=True)
            timer.cancel()
            S.clear_interrupt()
            t = time.time() - t0
            st = 'FEASIBLE' if r is True else 'INFEASIBLE' if r is False else 'UNKNOWN'
            valid = None
            if r is True:
                g = {p: c for p, c in mod.grid(Values(S.get_model())).items() if p[0] < W}
                valid = check_layout(g, spec.window(W, H), len(net['src']))
                if not valid:
                    st = 'BADMODEL'
            out.append((W, st, round(t, 1), valid))
            if log:
                log('W=%d: %s %.1fs valid=%s' % out[-1])
            if st != 'FEASIBLE':
                break
    return out


# ---------------------------------------------------------------- completeness check

def pin(grid, win, timeout=120):
    """A finished layout with every tile fixed: is it accepted by the direct CNF of its own network?
    (The standing check that the CNF path rules out no real layout.) Returns the kissat status."""
    from .netplace import extract
    net = extract(grid, win)
    import copy
    w = copy.copy(win)
    w.fixed = {(x, y): grid.get((x, y)) for x in range(win.W) for y in range(win.H) if (x, y) not in win.blocked}
    mod = build_cnf(net, w)
    d = tempfile.mkdtemp(prefix='satnet_')
    st, model, t = run_kissat(mod.m.write(os.path.join(d, 'pin.cnf')), 0, timeout)
    return st
