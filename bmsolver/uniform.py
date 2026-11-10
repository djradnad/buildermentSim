"""Uniform-share balancer search: a pure true/false model that covers far more networks than the staged one.

Every link carries a SET of inputs and one SHARE (1, 1/2, 1/3, 1/4, 1/6, ...): the link holds that share of every
input in its set, and nothing of the others. Three local rules keep this exact:

    belt / underground, one input      copies the label
    belt / exit, several inputs        a merge: all inputs have the SAME share and pairwise DISJOINT sets;
                                       the output has the union at that share
    splitter with k outputs (1, 2, 3)  every output has the same set at share / k
    a link's load = |set| * share <= 1 (a belt holds one belt)
    input lane i = ({i}, share 1); every output lane = (all inputs, share 1/M)

No stages and no piece counts: merges may chain in any order and unequal groups may meet (john's 4:4 7x7 t6t6fg
merges a 3/4 belt of three inputs with a 1/4 piece of the fourth, which the staged model (radix.py) rejects).
Shares only fall, so only shares 2^-a 3^-b that still divide 1/M are needed. Every staged network is a uniform one.

Not covered: networks where a belt carries unequal shares of different inputs (loop-back designs, 5:5, 7:7), and a
splitter whose own outputs meet again. So INFEASIBLE here is not a proof; found layouts are checked exactly.

    python -m bmsolver.uniform --n 4 --size 7x7 --engine ~/solvers/kissat
"""
import argparse
import time

from .model import Model
from .netplace import _port_terms


def levels(M):
    """share levels (a, b) = 2^-a 3^-b down to 1/M, or None when M has another prime factor"""
    a = b = 0
    while M % 2 == 0:
        M //= 2
        a += 1
    while M % 3 == 0:
        M //= 3
        b += 1
    if M != 1:
        return None
    return [(i, j) for i in range(a + 1) for j in range(b + 1)], (a, b)


def build(win, N, M=None, pass_splitters=True, same_merge=False):
    """Model(win, flows=False) plus a set and a share on every link and port term. N inputs, M outputs."""
    M = M or N
    LV, target = levels(M)
    NL = len(LV)
    at = {v: k for k, v in enumerate(LV)}
    mod = Model(win, flows=False)
    m, X = mod.m, mod.X
    terms = _port_terms(mod, win)
    act, sh, mem = {}, {}, {}

    def new_term(t, a):
        act[t] = a
        sh[t] = [m.NewBoolVar('') for _ in range(NL)]
        mem[t] = [m.NewBoolVar('') for _ in range(N)]
        if a is True:
            m.AddExactlyOne(sh[t])
        else:
            m.Add(sum(sh[t]) == a)
            for v in mem[t]:
                m.AddImplication(v, a)
        for x, (i, j) in enumerate(LV):               # a belt holds one belt: |set| * share <= 1
            cap = 2 ** i * 3 ** j
            if cap < N:
                m.Add(sum(mem[t]) <= cap).OnlyEnforceIf(sh[t][x])

    for i, (p, q, l, kind) in enumerate(mod.links):
        new_term(('L', i), l)
    lane = {}
    ins_fixed = [k for k, sp in win.ports.items() if (sp[0] if isinstance(sp, tuple) else sp) == 'in']
    for i, (p, d) in enumerate(ins_fixed):
        lane[p, None] = i
    base = len(ins_fixed)
    for group, lits in zip(win.port_options, mod.option_lits):
        n_in = 0
        for opt, lit in zip(group, lits):
            ks = [k for k, sp in opt.items() if sp[0] == 'in']
            n_in = max(n_in, len(ks))
            for i, (p, d) in enumerate(ks):
                lane[p, id(lit)] = base + i
        base += n_in
    assert base == N, 'the window has %d input lanes, the network %d' % (base, N)

    tin = {p: [('L', i) for i in mod.lin[p]] for p in mod.cells}
    tout = {p: [('L', i) for i in mod.lout[p]] for p in mod.cells}
    for j, (p, kind, l) in enumerate(terms):
        t = ('P', j)
        new_term(t, l)
        (tin if kind == 'in' else tout)[p].append(t)
        if kind == 'out':
            m.Add(sh[t][at[target]] == l)
            for i in range(N):
                m.Add(mem[t][i] == l)
            continue
        m.Add(sh[t][0] == l)
        d = lane[p, None if l is True else id(l)]
        for i in range(N):
            m.Add(mem[t][i] == (l if i == d else 0))

    if win.symmetry == 'y':
        mid = {(win.H - 1) // 2, win.H // 2} if win.quasi else set()
        fr = win.sym_free | {win.mirror(p) for p in win.sym_free}
        touches = lambda p, q: any(r is not None and (r[1] in mid or r in fr) for r in (p, q))
        assert not win.port_options, 'symmetric search needs fixed ports (--at)'
        idx = {(p, q, kind): i for i, (p, q, l, kind) in enumerate(mod.links)}
        pidx = {(p, kind): j for j, (p, kind, l) in enumerate(terms)}
        pairs = []
        for i, (p, q, l, kind) in enumerate(mod.links):
            mp = win.mirror(p) if p is not None else None
            mq = win.mirror(q) if q is not None else None
            j = idx.get((mp, mq, kind))
            if j is not None and j >= i and not touches(p, q):
                pairs.append((('L', i), ('L', j)))
        for j, (p, kind, l) in enumerate(terms):
            k = pidx.get((win.mirror(p), kind))
            if k is not None and k >= j and not touches(p, None):
                pairs.append((('P', j), ('P', k)))
        for t, u in pairs:
            for x in range(NL):
                m.Add(sh[t][x] == sh[u][x])
            for i in range(N):
                m.Add(mem[t][i] == mem[u][N - 1 - i])

    nact = lambda ts: sum(1 if act[t] is True else act[t] for t in ts)
    neg = lambda a: [] if a is True else [a.Not()]
    mod.sh, mod.mem, mod.act = sh, mem, act
    for p in mod.cells:
        I, O = tin[p], tout[p]
        empty = X[p, 'E']
        is_sp = m.NewBoolVar('')
        m.Add(is_sp == sum(X[p, 'S%d' % o] for o in range(4)))
        TS = [m.NewBoolVar('') for _ in range(NL)]       # the share the tile hands on (before a split)
        TM = [m.NewBoolVar('') for _ in range(N)]        # the union of the input sets
        m.Add(sum(TS) + empty == 1)
        if same_merge and len(I) > 1:
            # second kind of merge: every input carries the SAME set and the shares add up (a splitter's own
            # outputs meeting again, or two pieces of one mixture): 1/4 + 1/4 = 1/2, 1/6 + 1/3 = 1/2, ...
            same = m.NewBoolVar('')
            m.AddImplication(same, is_sp.Not())
            val = [M // (2 ** i * 3 ** j) for i, j in LV]
            m.Add(sum(val[x] * sh[t][x] for t in I for x in range(NL)) == sum(val[x] * TS[x] for x in range(NL))).OnlyEnforceIf(same)
            m.Add(nact(I) >= 2).OnlyEnforceIf(same)
            for t in I:
                na = neg(act[t])
                for i in range(N):
                    m.AddBoolOr(na + [same.Not(), mem[t][i].Not(), TM[i]])
                    m.AddBoolOr(na + [same.Not(), mem[t][i], TM[i].Not()])
                    m.AddBoolOr([mem[t][i].Not(), TM[i]])
            for i in range(N):
                m.Add(sum(mem[t][i] for t in I) == TM[i]).OnlyEnforceIf(same.Not())
                m.AddBoolOr([mem[t][i] for t in I] + [TM[i].Not()])
            for x in range(NL):
                for t in I:
                    m.AddBoolOr([same, sh[t][x].Not(), TS[x]])
                m.AddBoolOr([same] + [sh[t][x] for t in I] + [TS[x].Not()])
        else:
            for x in range(NL):
                for t in I:
                    m.AddImplication(sh[t][x], TS[x])
                m.AddBoolOr([sh[t][x] for t in I] + [TS[x].Not()])
            for i in range(N):
                m.Add(sum(mem[t][i] for t in I) == TM[i])     # inputs disjoint
        nin, nout = nact(I), nact(O)
        m.Add(nin <= 1).OnlyEnforceIf(is_sp)
        k2, k3 = m.NewBoolVar(''), m.NewBoolVar('')       # a splitter with 2 / 3 outputs
        m.AddImplication(k2, is_sp)
        m.AddImplication(k3, is_sp)
        m.Add(nout == 2).OnlyEnforceIf(k2)
        m.Add(nout == 3).OnlyEnforceIf(k3)
        m.Add(nout <= 1).OnlyEnforceIf([k2.Not(), k3.Not()])
        if not pass_splitters:
            m.AddBoolOr([is_sp.Not(), k2, k3])
        for x, (i, j) in enumerate(LV):
            if (i + 1, j) not in at:
                m.AddImplication(k2, TS[x].Not())
            if (i, j + 1) not in at:
                m.AddImplication(k3, TS[x].Not())
        for u in O:
            na = neg(act[u])
            for i in range(N):
                m.AddBoolOr(na + [mem[u][i].Not(), TM[i]])
                m.AddBoolOr(na + [mem[u][i], TM[i].Not()])
            for x, (i, j) in enumerate(LV):
                m.AddBoolOr(na + [k2, k3, TS[x].Not(), sh[u][x]])
                if (i + 1, j) in at:
                    m.AddBoolOr(na + [k2.Not(), TS[x].Not(), sh[u][at[i + 1, j]]])
                if (i, j + 1) in at:
                    m.AddBoolOr(na + [k3.Not(), TS[x].Not(), sh[u][at[i, j + 1]]])
    return mod


def solve(win, N, M=None, engine='cadical195', timeout=None, pass_splitters=True, same_merge=False):
    t0 = time.time()
    mod = build(win, N, M, pass_splitters, same_merge)
    tb = time.time() - t0
    if engine == 'cpsat':
        from ortools.sat.python import cp_model
        sv = cp_model.CpSolver()
        if timeout:
            sv.parameters.max_time_in_seconds = timeout
        sv.parameters.num_workers = 4
        st = sv.Solve(mod.m)
        res = dict(status=sv.StatusName(st), time=time.time() - t0)
        if st in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            res['grid'] = mod.grid(sv)
            res['options'] = mod.chosen_options(sv)
        return res
    from . import cnf
    r = cnf.solve(mod, engine=engine, timeout=timeout)
    r['model_build'] = tb
    return r


def main(argv=None):
    import os
    from . import spec as S, bench
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, required=True)
    ap.add_argument('--m', type=int, default=None)
    ap.add_argument('--size', required=True, help='WxH, comma separated list allowed')
    ap.add_argument('--engine', default='cadical195')
    ap.add_argument('--timeout', type=float, default=None)
    ap.add_argument('--at', default=None, help='fixed first lane offsets "in,out" (default: free)')
    ap.add_argument('--sym', action='store_true')
    a = ap.parse_args(argv)
    M = a.m or a.n
    eng = os.path.expanduser(a.engine)
    for size in a.size.split(','):
        W, H = map(int, size.split('x'))
        if a.at:
            ia, oa = map(int, a.at.split(','))
            win = S.Spec([S.Group('E', a.n, at=ia)], [S.Group('W', M, at=oa)]).window(W, H, symmetry='y' if a.sym else None)
        else:
            win = S.Spec([S.Group('E', a.n)], [S.Group('W', M)]).window(W, H)
        r = solve(win, a.n, M, eng, a.timeout)
        ok = ''
        if 'grid' in r:
            ok = '%s pcs=%d' % (bench.verify(r['grid'], win, r.get('options')), sum(1 for c in r['grid'].values() if c))
            for y in range(H):
                print(''.join(r['grid'].get((x, y)) or '..' for x in range(W)))
        print('%d:%d %dx%d uniform: %s %.1fs (vars %s clauses %s) %s' % (
            a.n, M, W, H, r['status'], r['time'], r.get('vars'), r.get('clauses'), ok), flush=True)


if __name__ == '__main__':
    main()
