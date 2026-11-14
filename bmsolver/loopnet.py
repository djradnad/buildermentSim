"""Loop balancers (5:5, 7:7, ...) as a true/false model: the uniform-share model (uniform.py) plus loop lanes.

john 2026-11-13: cover all the ways to reach a balance, incl. smaller splits and feedback loops like the 1/5 trick.
Our 5:5 12x10 (catalogue) splits a fully mixed belt in three and feeds one third back; uniform.py can't see that.

Every link carries a SET of lanes and one MASS s: s belts of every lane in the set (a real input lane holds 1 belt
in all; a loop lane holds whatever its loop point took). Local rules as in uniform.py:
    belt / underground, one input      copies the label
    merge                              equal masses, disjoint sets -> the union
    splitter with k outputs            same set, mass / k
    load |set| * s <= 1
A label holding every real lane is UNIFORM: whatever loop lanes it also holds, those carry uniform mixtures too, so
the belt holds the same amount of each real input. That gives two more rules:
    loop point j   a belt or underground tile (not a splitter) with one input whose label is uniform (set S, mass s); it hands on loop lane j
                   alone at mass s * |S| (the whole belt, renamed). One loop point per loop lane.
    output port    a uniform label of total mass 1: s = 1 / (N + number of loop lanes in the set)
Then every output gets 1/N of every real input (each loop lane in it adds s/N of each). Masses are 2^-a 3^-b / q
with q from {1, N, N+1, ..., N+loops} where q is not of that form already. INFEASIBLE here is NOT a proof.

    python -m bmsolver.loopnet --n 5 --size 12x10 --engine ~/solvers/kissat
"""
import argparse
import os
import time
from fractions import Fraction as Fr

from .model import Model
from .netplace import _port_terms


def smooth(q):
    while q % 2 == 0:
        q //= 2
    while q % 3 == 0:
        q //= 3
    return q == 1


def levels(N, loops, A, B):
    qs = sorted({1} | {q for q in range(N, N + loops + 1) if not smooth(q)})
    vals = sorted({Fr(1, 2 ** a * 3 ** b * q) for a in range(A + 1) for b in range(B + 1) for q in qs}, reverse=True)
    return vals


def build(win, N, loops=1, A=3, B=2):
    NR, NT = N, N + loops
    LV = levels(N, loops, A, B)
    at = {v: k for k, v in enumerate(LV)}
    NL = len(LV)
    mod = Model(win, flows=False)
    m, X = mod.m, mod.X
    terms = _port_terms(mod, win)
    act, sh, mem = {}, {}, {}

    def new_term(t, a):
        act[t] = a
        sh[t] = [m.NewBoolVar('') for _ in range(NL)]
        mem[t] = [m.NewBoolVar('') for _ in range(NT)]
        if a is True:
            m.AddExactlyOne(sh[t])
        else:
            m.Add(sum(sh[t]) == a)
            for v in mem[t]:
                m.AddImplication(v, a)
        for x, v in enumerate(LV):
            cap = int(1 / v)
            if cap < NT:
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
    assert base == NR, 'the window has %d input lanes, the network %d' % (base, NR)
    one = at[Fr(1)]
    oL = [at[Fr(1, NR + c)] for c in range(loops + 1)]   # output mass with c loop lanes in its set

    tin = {p: [('L', i) for i in mod.lin[p]] for p in mod.cells}
    tout = {p: [('L', i) for i in mod.lout[p]] for p in mod.cells}
    for j, (p, kind, l) in enumerate(terms):
        t = ('P', j)
        new_term(t, l)
        (tin if kind == 'in' else tout)[p].append(t)
        if kind == 'out':
            for i in range(NR):
                m.Add(mem[t][i] == l)
            for x in range(NL):
                if x not in oL:
                    m.Add(sh[t][x] == 0)
            m.Add(sum(mem[t][NR:]) == sum(c * sh[t][oL[c]] for c in range(loops + 1)))
            continue
        m.Add(sh[t][one] == l)
        d = lane[p, None if l is True else id(l)]
        for i in range(NT):
            m.Add(mem[t][i] == (l if i == d else 0))

    nact = lambda ts: sum(1 if act[t] is True else act[t] for t in ts)
    neg = lambda a: [] if a is True else [a.Not()]
    loop_pts = []
    for p in mod.cells:
        I, O = tin[p], tout[p]
        empty = X[p, 'E']
        is_sp = m.NewBoolVar('')
        m.Add(is_sp == sum(X[p, 'S%d' % o] for o in range(4)))
        TS = [m.NewBoolVar('') for _ in range(NL)]
        TM = [m.NewBoolVar('') for _ in range(NT)]
        m.Add(sum(TS) + empty == 1)
        for x in range(NL):
            for t in I:
                m.AddImplication(sh[t][x], TS[x])
            m.AddBoolOr([sh[t][x] for t in I] + [TS[x].Not()])
        for i in range(NT):
            m.Add(sum(mem[t][i] for t in I) == TM[i])     # inputs disjoint
        nin, nout = nact(I), nact(O)
        m.Add(nin <= 1).OnlyEnforceIf(is_sp)
        k2, k3 = m.NewBoolVar(''), m.NewBoolVar('')
        m.AddImplication(k2, is_sp)
        m.AddImplication(k3, is_sp)
        m.Add(nout == 2).OnlyEnforceIf(k2)
        m.Add(nout == 3).OnlyEnforceIf(k3)
        m.Add(nout <= 1).OnlyEnforceIf([k2.Not(), k3.Not()])
        k1 = m.NewBoolVar('')
        m.AddBoolOr([k1, k2, k3])
        m.AddBoolOr([k1.Not(), k2.Not()])
        m.AddBoolOr([k1.Not(), k3.Not()])
        m.AddBoolOr([k2.Not(), k3.Not()])
        for x, v in enumerate(LV):
            if v / 2 not in at:
                m.AddImplication(k2, TS[x].Not())
            if v / 3 not in at:
                m.AddImplication(k3, TS[x].Not())
        # loop points sit on this tile's out terms: out term u hands on loop lane j alone, the whole of what u
        # would have carried (TS / k of the uniform input set), renamed
        Ru = {u: [m.NewBoolVar('') for _ in range(loops)] for u in O}
        Rany = {}
        for u in O:
            loop_pts.append(Ru[u])
            Rany[u] = m.NewBoolVar('')
            m.Add(sum(Ru[u]) == Rany[u])
            for v in neg(act[u]):
                m.AddImplication(Rany[u], v.Not())
        R = m.NewBoolVar('')
        for u in O:
            m.AddImplication(Rany[u], R)
        m.AddBoolOr([Rany[u] for u in O] + [R.Not()])
        for i in range(NR):
            m.AddImplication(R, TM[i])
        CNT = [m.NewBoolVar('') for _ in range(loops + 1)]   # loop lanes in the input set, when R
        m.Add(sum(CNT) == R)
        m.Add(sum(TM[NR:]) == sum(c * CNT[c] for c in range(loops + 1))).OnlyEnforceIf(R)
        for x, v in enumerate(LV):                        # renamed mass (s / k) * |S|, |S| = NR + loop lanes in it
            for kk, kl in ((1, k1), (2, k2), (3, k3)):
                for c in range(loops + 1):
                    y = at.get(v / kk * (NR + c))
                    cond = [TS[x].Not(), CNT[c].Not(), kl.Not()]
                    for u in O:
                        if y is None:
                            m.AddBoolOr(cond + [Rany[u].Not()])
                        else:
                            m.AddBoolOr(cond + [Rany[u].Not(), sh[u][y]])
        for u in O:
            for j in range(loops):
                for i in range(NT):
                    m.AddBoolOr([Ru[u][j].Not(), mem[u][i] if i == NR + j else mem[u][i].Not()])
        for u in O:
            na = neg(act[u]) + [Rany[u]]
            for i in range(NT):
                m.AddBoolOr(na + [mem[u][i].Not(), TM[i]])
                m.AddBoolOr(na + [mem[u][i], TM[i].Not()])
            for x, v in enumerate(LV):
                m.AddBoolOr(na + [k2, k3, TS[x].Not(), sh[u][x]])
                if v / 2 in at:
                    m.AddBoolOr(na + [k2.Not(), TS[x].Not(), sh[u][at[v / 2]]])
                if v / 3 in at:
                    m.AddBoolOr(na + [k3.Not(), TS[x].Not(), sh[u][at[v / 3]]])
    for j in range(loops):
        m.Add(sum(Rj[j] for Rj in loop_pts) == 1)
    mod.sh, mod.mem, mod.act, mod.levels = sh, mem, act, LV
    return mod


def solve(win, N, loops=1, engine='cadical195', timeout=None, A=3, B=2):
    t0 = time.time()
    mod = build(win, N, loops, A, B)
    tb = time.time() - t0
    from . import cnf
    r = cnf.solve(mod, engine=engine, timeout=timeout)
    r['model_build'] = tb
    return r


def main(argv=None):
    from . import spec as S, bench
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, required=True)
    ap.add_argument('--size', required=True)
    ap.add_argument('--engine', default='cadical195')
    ap.add_argument('--timeout', type=float, default=None)
    ap.add_argument('--loops', type=int, default=1)
    ap.add_argument('--A', type=int, default=3, help='largest power of 2 in a mass denominator')
    ap.add_argument('--B', type=int, default=2, help='largest power of 3')
    a = ap.parse_args(argv)
    eng = os.path.expanduser(a.engine)
    for size in a.size.split(','):
        W, H = map(int, size.split('x'))
        win = S.Spec([S.Group('E', a.n)], [S.Group('W', a.n)]).window(W, H)
        r = solve(win, a.n, a.loops, eng, a.timeout, a.A, a.B)
        ok = ''
        if 'grid' in r:
            ok = '%s pcs=%d' % (bench.verify(r['grid'], win, r.get('options')), sum(1 for c in r['grid'].values() if c))
            for y in range(H):
                print(''.join(r['grid'].get((x, y)) or '..' for x in range(W)))
        print('%d:%d %dx%d loopnet: %s %.1fs (vars %s clauses %s) %s' % (
            a.n, a.n, W, H, r['status'], r['time'], r.get('vars'), r.get('clauses'), ok), flush=True)


if __name__ == '__main__':
    main()
