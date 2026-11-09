"""Staged balancer search: lay out ANY split-then-merge balancer network with a pure true/false model.

A staged N:N balancer (N = a_1 * a_2 * ... * a_S) works in S stages. In stage j every belt is split a_j ways, and
then a_j of those pieces are merged into one belt again. Stage j's merges must join pieces whose input sets are
disjoint. Then by induction every belt after stage j carries an equal share of exactly a_1*...*a_j inputs, and after
the last stage every belt carries an equal share of all N. (The wiki 9:9 is such a network: 9 splitters, 9 three-way
merges, 9 splitters, 9 merges.) This rule is local, so the model never fixes a network. The solver picks the network
(which inputs meet where, in what order) together with the placement:

    every link carries a LEVEL  0 = an input belt, 1 = a stage-1 piece, 2 = a stage-1 merged belt, ...,
                                2S = a finished (balanced) belt
                   and a SET    which inputs it carries (N bits)
    belt / underground tile with one input:   copies its label
    belt / exit with several inputs:          a merge: inputs at level 2j+1 with sets pairwise disjoint; each
                                              carries a count of pieces joined so far (merges may chain: 2 then
                                              1 more); output = the union, at level 2j+2 once the count is a_j
    splitter with one output:                 copies its label (passes items on like a belt)
    splitter with several outputs:            input at level 2j, exactly a_j outputs, each at level 2j+1, same set
    input lane i carries level 0 and set {i}; every output lane carries level 2S.

No numbers are involved, which SAT solvers handle far better than the flow model's adders. The search covers every
staged network (any merge pattern, any order) but not networks that loop back or mix levels, so an INFEASIBLE answer
proves only that no STAGED balancer fits. Found layouts are checked with the exact flow check (bench.verify).

    python -m bmsolver.radix --n 9 --size 19x15 --engine ~/solvers/kissat
"""
import argparse
import math
import time

from .model import Model
from .netplace import _port_terms


def factorisations(n, parts=None):
    """stage arities for n: [(3, 3)] for 9, [(2, 2)] for 4, [(2, 3), (3, 2)] for 6 (each part 2 or 3)"""
    if n == 1:
        return [()]
    out = []
    for a in (2, 3):
        if n % a == 0:
            out += [(a,) + r for r in factorisations(n // a)]
    return out


def build(win, arity):
    """Model(win, flows=False) plus a level and a set on every link and port term."""
    mod = Model(win, flows=False)
    m, X = mod.m, mod.X
    N = math.prod(arity)
    S = len(arity)
    NL = 2 * S + 1
    terms = _port_terms(mod, win)

    act, lev, mem, cnt = {}, {}, {}, {}

    def new_term(t, a):
        act[t] = a
        lev[t] = [m.NewBoolVar('') for _ in range(NL)]
        mem[t] = [m.NewBoolVar('') for _ in range(N)]
        # pieces of stage j already merged together (1 .. a_j - 1), one-hot when the level is 2j+1
        cnt[t] = {}
        for j, aj in enumerate(arity):
            cnt[t][j] = {k: m.NewBoolVar('') for k in range(1, aj)}
            m.Add(sum(cnt[t][j].values()) == lev[t][2 * j + 1])
        if a is True:
            m.AddExactlyOne(lev[t])
        else:
            m.Add(sum(lev[t]) == a)
            for v in mem[t]:
                m.AddImplication(v, a)

    for i, (p, q, l, kind) in enumerate(mod.links):
        new_term(('L', i), l)
    # lane of every input port term (its place in its group); input lane i carries set {i}
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
            m.Add(lev[t][NL - 1] == l)
            continue
        m.Add(lev[t][0] == l)
        d = lane[p, None if l is True else id(l)]
        for i in range(N):
            if i == d:
                m.Add(mem[t][i] == l)
            else:
                m.Add(mem[t][i] == 0)

    nact = lambda ts: sum(1 if act[t] is True else act[t] for t in ts)
    neg = lambda a: [] if a is True else [a.Not()]
    mod.lev, mod.mem, mod.act = lev, mem, act
    for p in mod.cells:
        I, O = tin[p], tout[p]
        empty = X[p, 'E']
        is_sp = m.NewBoolVar('')
        m.Add(is_sp == sum(X[p, 'S%d' % o] for o in range(4)))
        TL = [m.NewBoolVar('') for _ in range(NL)]       # the level of the tile's inputs (all the same)
        TM = [m.NewBoolVar('') for _ in range(N)]        # the union of their sets
        m.Add(sum(TL) + empty == 1)
        for x in range(NL):
            for t in I:
                m.AddImplication(lev[t][x], TL[x])
            m.AddBoolOr([lev[t][x] for t in I] + [TL[x].Not()])
        for i in range(N):
            m.Add(sum(mem[t][i] for t in I) == TM[i])     # inputs disjoint
        nin, nout = nact(I), nact(O)
        many_in, many_out = m.NewBoolVar(''), m.NewBoolVar('')
        m.Add(nin >= 2).OnlyEnforceIf(many_in)
        m.Add(nin <= 1).OnlyEnforceIf(many_in.Not())
        m.Add(nout >= 2).OnlyEnforceIf(many_out)
        m.Add(nout <= 1).OnlyEnforceIf(many_out.Not())
        split = m.NewBoolVar('')                          # a splitter with several outputs
        merge = m.NewBoolVar('')                          # a belt or exit with several inputs
        m.AddBoolAnd([is_sp, many_out]).OnlyEnforceIf(split)
        m.AddBoolOr([is_sp.Not(), many_out.Not(), split])
        m.AddImplication(is_sp, many_in.Not())
        m.Add(merge == many_in)
        m.AddImplication(split, TL[NL - 1].Not())
        copy = m.NewBoolVar('')
        m.Add(copy + split + merge + empty == 1)
        for j, a in enumerate(arity):
            m.Add(nout == a).OnlyEnforceIf([split, TL[2 * j]])
            m.AddImplication(split, TL[2 * j + 1].Not())
            m.AddImplication(merge, TL[2 * j].Not())
        m.AddImplication(merge, TL[NL - 1].Not())
        # the tile's input piece count (copies only: one input)
        TC = {}
        for j, a in enumerate(arity):
            for k in range(1, a):
                v = TC[j, k] = m.NewBoolVar('')
                for t in I:
                    m.AddImplication(cnt[t][j][k], v)
                m.AddBoolOr([cnt[t][j][k] for t in I] + [v.Not()])
        # merges of stage j: K = pieces joined; K == a_j makes a full belt of the next level
        mres = {}                                         # (j, K) -> literal: this merge joins K pieces of stage j
        for j, a in enumerate(arity):
            mt = m.NewBoolVar('')
            m.AddBoolAnd([merge, TL[2 * j + 1]]).OnlyEnforceIf(mt)
            m.AddBoolOr([merge.Not(), TL[2 * j + 1].Not(), mt])
            eq = {K: m.NewBoolVar('') for K in range(2, a + 1)}
            m.Add(sum(eq.values()) == mt)
            K = sum(k * cnt[t][j][k] for t in I for k in range(1, a))
            m.Add(K == sum(Kv * e for Kv, e in eq.items())).OnlyEnforceIf(mt)
            for Kv, e in eq.items():
                mres[j, Kv] = e
        for u in O:
            na = neg(act[u])
            for i in range(N):
                m.AddBoolOr(na + [mem[u][i].Not(), TM[i]])
                m.AddBoolOr(na + [mem[u][i], TM[i].Not()])
            for x in range(NL):
                m.AddBoolOr(na + [copy.Not(), TL[x].Not(), lev[u][x]])
            for (j, k), v in TC.items():
                m.AddBoolOr(na + [copy.Not(), v.Not(), cnt[u][j][k]])
            for j, a in enumerate(arity):
                m.AddBoolOr(na + [split.Not(), TL[2 * j].Not(), lev[u][2 * j + 1]])
                if a > 1:
                    m.AddBoolOr(na + [split.Not(), TL[2 * j].Not(), cnt[u][j][1]])
                for Kv in range(2, a + 1):
                    e = mres[j, Kv]
                    if Kv == a:
                        m.AddBoolOr(na + [e.Not(), lev[u][2 * j + 2]])
                    else:
                        m.AddBoolOr(na + [e.Not(), cnt[u][j][Kv]])
    return mod


def solve(win, arity, engine='cadical195', timeout=None, log=print):
    t0 = time.time()
    mod = build(win, arity)
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
    from .design import Design
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, required=True)
    ap.add_argument('--size', required=True, help='WxH, comma separated list allowed')
    ap.add_argument('--arity', default=None, help='e.g. 3,3 (default: every factorisation into 2s and 3s)')
    ap.add_argument('--engine', default='cadical195')
    ap.add_argument('--timeout', type=float, default=None)
    ap.add_argument('--out', default=None, help='write found layouts here (one file per size)')
    a = ap.parse_args(argv)
    ars = [tuple(int(x) for x in a.arity.split(','))] if a.arity else factorisations(a.n)
    eng = os.path.expanduser(a.engine)
    for size in a.size.split(','):
        W, H = map(int, size.split('x'))
        for ar in ars:
            win = S.balancer(a.n).window(W, H)
            r = solve(win, ar, eng, a.timeout)
            ok = ''
            if 'grid' in r:
                ok = bench.verify(r['grid'], win, r.get('options'))
                pcs = sum(1 for c in r['grid'].values() if c)
                ok = '%s pcs=%d S=%d UG=%d' % (ok, pcs, sum(1 for c in r['grid'].values() if c and c[0] == 'S'),
                                              sum(1 for c in r['grid'].values() if c and c[0] in 'IO'))
                if a.out:
                    path = os.path.join(a.out, 'bal%d_%dx%d_%s.txt' % (a.n, W, H, ''.join(map(str, ar))))
                    with open(path, 'w') as fh:
                        fh.write(Design(r['grid']).to_text() if hasattr(Design, 'to_text') else repr(r['grid']))
            print('%d:%d %dx%d arity %s: %s %.1fs (vars %s clauses %s) %s' % (
                a.n, a.n, W, H, ','.join(map(str, ar)), r['status'], r['time'], r.get('vars'), r.get('clauses'), ok),
                flush=True)


if __name__ == '__main__':
    main()
