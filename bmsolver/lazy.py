"""Lazy flows (SAT modulo flows): solve the layout as pure SAT, check flows only for finished layouts.

cnf.BinTranslation turns every flow into binary adder circuits: ~99% of the CNF is arithmetic that a generic SAT
solver has to search through. Here the model is split in two, exactly:

  skeleton - every constraint of model.Model that uses only 0/1 variables (pieces, links, ports, machines, arms):
             a small CNF, solved incrementally by CaDiCaL;
  theory   - every constraint that touches an integer (flows, shares, rates): checked by CP-SAT with the skeleton's
             answer fixed by assumptions.

When the theory says no, CP-SAT names a subset of the fixed literals that already makes the flows impossible (its
infeasibility core); the clause "not all of these" goes back into the SAT solver, which keeps everything it learned.
Same rules, same answers as the full model (every clause added is implied by it), so SAT -> a valid layout and
UNSAT -> a proof for that window.

    python -m bmsolver.lazy --case bal3_8x5 --timeout 600
"""
import argparse
import os
import threading
import time

from ortools.sat.python import cp_model

from . import cnf, model, bench


def _refs(ct):
    """variable indices a constraint reads"""
    out = [abs(r) if r >= 0 else -r - 1 for r in ct.enforcement_literal]
    kind = ct.WhichOneof('constraint')
    body = getattr(ct, kind)
    if kind == 'linear':
        out += list(body.vars)
    elif kind in ('bool_or', 'bool_and', 'exactly_one', 'at_most_one', 'bool_xor'):
        out += [r if r >= 0 else -r - 1 for r in body.literals]
    else:
        raise NotImplementedError(kind)
    return out


class Skeleton(cnf.Translation):
    """CNF of the pure-Boolean constraints of a CP-SAT model; the others are listed in self.theory."""

    def __init__(self, cpmodel):
        p = self.proto = cnf._load(cpmodel)
        f = self.f = cnf.CNF()
        self.bool, self.ord, self.lo = {}, {}, {}
        isbool = [v.domain[0] == 0 and v.domain[-1] == 1 and len(v.domain) == 2 for v in p.variables]
        for i, b in enumerate(isbool):
            if b:
                self.bool[i] = f.new()
        self.theory = []
        in_skel, in_theory = set(), set()
        for ci, ct in enumerate(p.constraints):
            refs = _refs(ct)
            if all(isbool[i] for i in refs):
                getattr(self, '_' + ct.WhichOneof('constraint'))(ct)
                in_skel.update(refs)
            else:
                self.theory.append(ci)
                in_theory.update(i for i in refs if isbool[i])
        # literals the theory depends on: fixed by assumptions in every theory check
        self.shared = sorted(in_skel & in_theory)
        self.theory_only = sorted(in_theory - in_skel)


class Theory:
    """CP-SAT on the full model with the shared literals fixed by assumptions; returns None (flows exist) or a core
    (list of (var index, value) that is already impossible)."""

    def __init__(self, mod, shared, theory=None, workers=1):
        self.m = mod.m
        if theory is not None:          # a model with only the theory constraints (same variables and indices)
            p = cnf._load(mod.m)
            keep = [p.constraints[i] for i in theory]
            del p.constraints[:]
            p.constraints.extend(keep)
            p.ClearField('solution_hint')
            p.ClearField('objective')
            from google.protobuf import text_format
            self.m = cp_model.CpModel()
            self.m.Proto().parse_text_format(text_format.MessageToString(p))
        self.shared = shared
        self.vars = {i: self.m.GetBoolVarFromProtoIndex(i) for i in shared}
        self.workers = workers
        self.min_budget = 0
        self.calls, self.time = 0, 0.0

    def minimize(self, core, values, budget=200):
        """deletion-based shrink of a core: drop each literal that is not needed (budget = most solver calls)"""
        core = list(core)
        k = 0
        while k < len(core) and budget > 0:
            trial = core[:k] + core[k + 1:]
            st, s = self._solve(dict(trial), values, [i for i, v in trial])
            budget -= 1
            if st == cp_model.INFEASIBLE:
                kept = set(r if r >= 0 else -r - 1 for r in s.SufficientAssumptionsForInfeasibility())
                core = [(i, v) for i, v in trial if i in kept] if kept else trial
            else:
                k += 1
        return core

    def _solve(self, _, values, idx, timeout=60):
        m = self.m
        m.ClearAssumptions()
        m.AddAssumptions([self.vars[i] if values[i] else self.vars[i].Not() for i in idx])
        s = cp_model.CpSolver()
        s.parameters.num_workers = 1
        s.parameters.cp_model_presolve = False
        s.parameters.max_time_in_seconds = timeout
        return s.Solve(m), s

    def check(self, values, timeout=60):
        t0 = time.time()
        m = self.m
        m.ClearAssumptions()
        lits = []
        for i in self.shared:
            lits.append(self.vars[i] if values[i] else self.vars[i].Not())
        m.AddAssumptions(lits)
        s = cp_model.CpSolver()
        s.parameters.num_workers = 1
        s.parameters.cp_model_presolve = False    # with presolve the reported core can be wrong
        s.parameters.max_time_in_seconds = timeout
        st = s.Solve(m)
        self.calls += 1
        t_first = time.time() - t0
        self.time += t_first
        if st in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return None, s
        if st != cp_model.INFEASIBLE:
            raise RuntimeError('theory check gave %s' % s.StatusName(st))
        core = s.SufficientAssumptionsForInfeasibility()
        out = []
        for r in core:                  # indices of the assumed variables (the sign is the value we assumed)
            i = r if r >= 0 else -r - 1
            out.append((i, values[i]))
        if self.min_budget:
            out = self.minimize(out, values, self.min_budget)
            self.time += time.time() - t0 - t_first
        return out, s


def add_flow_lemmas(mod, win):
    """Boolean facts the flow constraints imply, added to the model so the skeleton knows them (they are redundant
    for the full model, so answers do not change). Local conservation: a non-machine tile with a used link (or an
    active input port) in must have a used link (or an active output port) out, and the other way round, because
    every used link carries at least one unit."""
    m, X = mod.m, mod.X
    lits_of = lambda key, want: [l for l, k, _ in mod.optional_ports.get(key, []) if k == want]
    n = 0
    for p in mod.cells:
        ins = [mod.links[i][2] for i in mod.lin[p]]
        outs = [mod.links[i][2] for i in mod.lout[p]]
        always_in = always_out = False
        for d in range(4):
            spec = win.ports.get((p, d))
            if spec is not None:
                kind = spec[0] if isinstance(spec, tuple) else spec
                always_in |= kind == 'in'
                always_out |= kind == 'out'
            ins += lits_of((p, d), 'in')
            outs += lits_of((p, d), 'out')
        guard = [X[p, 'M'].Not()] if (p, 'M') in X else []
        if not always_out:
            for l in ins if not always_in else []:
                m.AddBoolOr(outs).OnlyEnforceIf([l] + guard)
                n += 1
            if always_in:
                m.AddBoolOr(outs).OnlyEnforceIf(guard) if guard else m.AddBoolOr(outs)
                n += 1
        if not always_in:
            for l in outs if not always_out else []:
                m.AddBoolOr(ins).OnlyEnforceIf([l] + guard)
                n += 1
            if always_out:
                m.AddBoolOr(ins).OnlyEnforceIf(guard) if guard else m.AddBoolOr(ins)
                n += 1
    return n


def add_reach_lemmas(mod, win):
    """Every used link leads on to an output: a distance-to-exit label per tile (order encoded, d <= number of
    tiles). Implied by flow conservation for every part an input reaches (a region with net inflow needs an exit);
    parts no input reaches can be emptied without touching the rest, so a window has a layout with this rule iff it
    has one without it. Belt/splitter/underground windows only (machines and arms make and take items)."""
    if win.machines or win.arm_cap:
        return 0
    m = mod.m
    cells = mod.cells
    K = len(cells)
    exit_lit = {}
    for p in cells:
        lits, always = [], False
        for d in range(4):
            spec = win.ports.get((p, d))
            if spec is not None and (spec[0] if isinstance(spec, tuple) else spec) == 'out':
                always = True
            lits += [l for l, k, _ in mod.optional_ports.get((p, d), []) if k == 'out']
        lits += [mod.links[i][2] for i in mod.lout[p] if mod.links[i][1] is None]     # tunnel exits out of the window
        exit_lit[p] = True if always else lits
    R = {p: [m.NewBoolVar('') for k in range(K)] for p in cells}     # R[p][k] = tile p reaches an exit in <= k links
    for p in cells:
        r = R[p]
        for k in range(K - 1):
            m.AddImplication(r[k], r[k + 1])
        ex = exit_lit[p]
        if ex is True:
            m.Add(r[0] == 1)
            continue
        m.AddBoolOr(ex).OnlyEnforceIf(r[0]) if ex else m.Add(r[0] == 0)
        outs = [(mod.links[i][2], mod.links[i][1]) for i in mod.lout[p] if mod.links[i][1] is not None]
        for k in range(1, K):
            opts = list(ex)
            for l, q in outs:
                z = m.NewBoolVar('')
                m.AddImplication(z, l)
                m.AddImplication(z, R[q][k - 1])
                opts.append(z)
            m.AddBoolOr(opts).OnlyEnforceIf(r[k])
    for p, q, l, kind in mod.links:
        if q is not None:
            m.AddImplication(l, R[q][K - 1])
    return K * len(cells)


def _maxflow(n, edges, s, t):
    """Dinic on n nodes; edges [(u, v, cap)]. Returns (value, reachable-from-s set in the residual graph)."""
    g = [[] for _ in range(n)]
    to, cap = [], []
    for u, v, c in edges:
        g[u].append(len(to)); to.append(v); cap.append(c)
        g[v].append(len(to)); to.append(u); cap.append(0)
    flow = 0
    while True:
        lvl = [-1] * n
        lvl[s] = 0
        q = [s]
        for u in q:
            for e in g[u]:
                if cap[e] > 0 and lvl[to[e]] < 0:
                    lvl[to[e]] = lvl[u] + 1
                    q.append(to[e])
        if lvl[t] < 0:
            return flow, {u for u in range(n) if lvl[u] >= 0}
        it = [0] * n

        def dfs(u, f):
            if u == t:
                return f
            while it[u] < len(g[u]):
                e = g[u][it[u]]
                v = to[e]
                if cap[e] > 0 and lvl[v] == lvl[u] + 1:
                    d = dfs(v, min(f, cap[e]))
                    if d:
                        cap[e] -= d
                        cap[e ^ 1] += d
                        return d
                it[u] += 1
            return 0
        while True:
            f = dfs(s, 1 << 60)
            if not f:
                break
            flow += f


class CutCheck:
    """Necessary flow condition, checked fast: with every used link carrying up to D, the inputs must be able to
    send each commodity (and the total) to the outputs that want it. On failure the clause is: one of the unused
    links across the min cut gets used, or the port choice changes. Belt-only windows without tunnels."""

    def __init__(self, mod, win, sk):
        self.ok = not (win.machines or win.arm_cap or win.tun_in or win.tun_out)
        self.mod, self.win, self.sk = mod, win, sk
        cells = mod.cells
        self.idx = {p: i for i, p in enumerate(cells)}
        self.S, self.T = len(cells), len(cells) + 1
        self.links = [(self.idx[p], self.idx[q], sk.bool[l.Index()]) for p, q, l, k in mod.links
                      if p is not None and q is not None]
        self.fixed = []         # (cell, kind, vec)
        self.opt = []           # (sat lit, cell, kind, vec)
        for (t, d), spec in win.ports.items():
            if isinstance(spec, tuple) and spec[0] in ('in', 'out'):
                self.fixed.append((self.idx[t], spec[0], spec[1]))
        for (t, d), lst in mod.optional_ports.items():
            for l, kind, vec in lst:
                self.opt.append((sk.bool[l.Index()], self.idx[t], kind, vec))
        self.calls = self.fails = 0

    def check(self, pos):
        if not self.ok:
            return None
        self.calls += 1
        D, nc = self.win.D, self.win.ncom
        ports = list(self.fixed)
        chosen = []
        for lit, c, kind, vec in self.opt:
            if lit in pos:
                ports.append((c, kind, vec))
                chosen.append(lit)
        used = [(u, v) for u, v, l in self.links if l in pos]
        for c in list(range(nc)) + ([None] if nc > 1 else []):
            amt = (lambda vec: sum(vec)) if c is None else (lambda vec, c=c: vec[c])
            edges = [(u, v, D) for u, v in used]
            need = 0
            for cell, kind, vec in ports:
                a = amt(vec)
                if a:
                    edges.append((self.S, cell, a) if kind == 'in' else (cell, self.T, a))
                    need += a if kind == 'out' else 0
            val, side = _maxflow(len(self.idx) + 2, edges, self.S, self.T)
            if val < need:
                self.fails += 1
                cut = [l for u, v, l in self.links if u in side and v not in side and l not in pos]
                return cut + [-l for l in chosen]
        return None


class _Mixed:
    """values of a found layout: 0/1 variables from the SAT model, integers from the theory solution"""

    def __init__(self, sk, pos, cps):
        self.sk, self.pos, self.cps = sk, pos, cps

    def Value(self, var):
        i = var.Index() if hasattr(var, 'Index') else var.index
        if i in self.sk.bool:
            return int(self.sk.bool[i] in self.pos)
        return self.cps.Value(var)

    BooleanValue = Value


def solve(win, timeout=600, log=print, minimize_core=0, lemmas=True, reach=True, cuts=True):
    from pysat.solvers import Cadical195
    t0 = time.time()
    mod = model.Model(win)
    if lemmas:
        add_flow_lemmas(mod, win)
    if reach:
        add_reach_lemmas(mod, win)
    sk = Skeleton(mod.m)
    th = Theory(mod, sk.shared, sk.theory)
    th.min_budget = minimize_core
    cut = CutCheck(mod, win, sk) if cuts else None
    tb = time.time() - t0
    if log:
        log('skeleton: %d vars %d clauses, theory: %d constraints, %d shared literals, %d theory-only bools, build %.1fs'
            % (sk.f.nv, len(sk.f.clauses), len(sk.theory), len(sk.shared), len(sk.theory_only), tb))
    s = Cadical195(bootstrap_with=sk.f.clauses)
    stop = [False]

    def kill():
        stop[0] = True
        s.interrupt() if hasattr(s, '_interrupt_ok') else None
    timer = threading.Timer(timeout, kill)
    timer.start()
    it, ncore, tsat = 0, 0, 0.0
    res = dict(status='UNKNOWN')
    try:
        while not stop[0]:
            t1 = time.time()
            sat = s.solve_limited(expect_interrupt=True)
            tsat += time.time() - t1
            if sat is None:
                break
            if sat is False:
                res = dict(status='UNSAT')
                break
            mdl = s.get_model()
            pos = set(l for l in mdl if l > 0)
            values = {i: sk.bool[i] in pos for i in sk.shared}
            if cut is not None:
                cl = cut.check(pos)
                if cl is not None:
                    s.add_clause(cl)
                    it += 1
                    ncore += len(cl)
                    continue
            core, cps = th.check(values)
            it += 1
            if core is None:
                vals = _Mixed(sk, pos, cps)
                res = dict(status='SAT', grid=mod.grid(vals), options=mod.chosen_options(vals))
                break
            ncore += len(core)
            s.add_clause([-sk.bool[i] if v else sk.bool[i] for i, v in core])
            if log and it % 50 == 0:
                log('  iter %d  avg core %.1f  sat %.1fs theory %.1fs' % (it, ncore / it, tsat, th.time))
    finally:
        timer.cancel()
        s.delete()
    if cut is not None:
        res.update(cut_calls=cut.calls, cut_fails=cut.fails)
    res.update(time=time.time() - t0 - tb, build=tb, iters=it, avg_core=ncore / max(1, it), sat_time=tsat,
               theory_time=th.time, vars=sk.f.nv, clauses=len(sk.f.clauses))
    return res


FLOWPROP = os.path.expanduser(os.environ.get('BM_FLOWPROP', '~/solvers/flowprop'))


def solve_native(win, timeout=600, log=print, lemmas=True, reach=False, prop=True):
    """Lazy flows with the native solver (tools/flowprop: CaDiCaL + a C++ max-flow propagator on partial layouts).
    Exact flows are still checked here by CP-SAT on each finished layout; cores go back over the pipe."""
    import subprocess
    import tempfile
    t0 = time.time()
    mod = model.Model(win)
    if lemmas:
        add_flow_lemmas(mod, win)
    if reach:
        add_reach_lemmas(mod, win)
    sk = Skeleton(mod.m)
    th = Theory(mod, sk.shared, sk.theory)
    cut = CutCheck(mod, win, sk)
    prop = prop and cut.ok
    d = tempfile.mkdtemp()
    cnf.write_dimacs(sk.f, os.path.join(d, 'sk.cnf'))
    nc = win.ncom
    need = [0] * nc
    for (t, dd), spec in win.ports.items():
        if isinstance(spec, tuple) and spec[0] == 'out':
            need = [a + b for a, b in zip(need, spec[1])]
    for group in win.port_options:
        for key, (kind, vec) in group[0].items():
            if kind == 'out':
                need = [a + b for a, b in zip(need, vec)]
    with open(os.path.join(d, 'g.txt'), 'w') as fh:
        fh.write('D %d %d %d\n' % (win.D, nc, len(mod.cells)))
        fh.write('N %s\n' % ' '.join(map(str, need)))
        for u, v, l in cut.links:
            fh.write('L %d %d %d\n' % (u, v, l))
        for cell, kind, vec in cut.fixed:
            fh.write('F %d %d %s\n' % (cell, 0 if kind == 'in' else 1, ' '.join(map(str, vec))))
        for l, cell, kind, vec in cut.opt:
            fh.write('O %d %d %d %s\n' % (l, cell, 0 if kind == 'in' else 1, ' '.join(map(str, vec))))
    tb = time.time() - t0
    args = [FLOWPROP, os.path.join(d, 'sk.cnf'), os.path.join(d, 'g.txt')] + ([] if prop else ['--noprop'])
    p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    import threading
    timer = threading.Timer(timeout, p.kill)
    timer.start()
    res = dict(status='UNKNOWN')
    it = 0
    try:
        while True:
            line = p.stdout.readline()
            if not line:
                break
            if line.startswith('c'):
                continue
            if line.startswith('UNSAT'):
                res = dict(status='UNSAT')
                break
            if not line.startswith('SAT'):
                res['line'] = line.strip()
                break
            pos = set(int(x) for x in line.split()[1:-1])
            values = {i: sk.bool[i] in pos for i in sk.shared}
            core, cps = th.check(values)
            it += 1
            if core is None:
                vals = _Mixed(sk, pos, cps)
                res = dict(status='SAT', grid=mod.grid(vals), options=mod.chosen_options(vals))
                p.stdin.write('OK\n')
                p.stdin.flush()
                break
            p.stdin.write('C %s 0\n' % ' '.join(str(-sk.bool[i] if v else sk.bool[i]) for i, v in core))
            p.stdin.flush()
    finally:
        timer.cancel()
        try:
            p.stdin.close()
        except Exception:
            pass
        err = p.stderr.read()
        p.wait()
    res['dir'] = d
    res.update(time=time.time() - t0 - tb, build=tb, iters=it, theory_time=th.time, prop=prop,
               stats=err.strip().splitlines()[-1] if err.strip() else '')
    return res


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--case', required=True)
    ap.add_argument('--timeout', type=float, default=600)
    ap.add_argument('--nolemmas', action='store_true')
    ap.add_argument('--noreach', action='store_true')
    ap.add_argument('--nocuts', action='store_true')
    ap.add_argument('--native', action='store_true', help='tools/flowprop (CaDiCaL + C++ flow propagator)')
    ap.add_argument('--noprop', action='store_true', help='with --native: no propagator (pure lazy loop)')
    ap.add_argument('--min', type=int, default=0, help='core minimization budget (solver calls per core)')
    a = ap.parse_args(argv)
    cases = {c[0]: c for c in bench.CASES}
    for name in a.case.split(','):
        _, mk, W, H, exp, tier = cases[name]
        win = mk().window(W, H)
        if a.native:
            r = solve_native(win, a.timeout, lemmas=not a.nolemmas, reach=not a.noreach, prop=not a.noprop)
            ok = bench.verify(r['grid'], win, r.get('options')) if r['status'] == 'SAT' else ''
            print('%-14s native %-8s (expect %s) %8.2fs iters=%d theory=%.1fs verified=%s %s' % (
                name, r['status'], exp, r['time'], r['iters'], r['theory_time'], ok, r['stats']), flush=True)
            continue
        r = solve(win, a.timeout, minimize_core=a.min, lemmas=not a.nolemmas, reach=not a.noreach, cuts=not a.nocuts)
        ok = ''
        if r['status'] == 'SAT':
            ok = bench.verify(r['grid'], win, r.get('options')) if not getattr(win, 'factory', None) else '?'
        print('%-14s lazy  %-8s (expect %s) %8.2fs iters=%d avg_core=%.1f sat=%.1fs theory=%.1fs verified=%s cuts=%s/%s' % (
            name, r['status'], exp, r['time'], r['iters'], r['avg_core'], r['sat_time'], r['theory_time'], ok,
            r.get('cut_fails'), r.get('cut_calls')), flush=True)


if __name__ == '__main__':
    main()
