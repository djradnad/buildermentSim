"""Abstract splitter netlist search (no geometry) under the game's rules: a splitter has ONE input belt (which may
merge up to 3 belts: back + both sides) and splits it equally over 2 or 3 used outputs; every belt <= 1 full belt.
Finds the fewest splitters giving a target mix of full input belts, e.g. the 5:5 loop piece:

    python tools/routing/netlist_cp.py --target 1,4/4,1 --unit 120
"""
import argparse
import itertools
from fractions import Fraction
from ortools.sat.python import cp_model


def solve(n_in, target, s, U, timeout):
    m = cp_model.CpModel()
    K = n_in
    ports = [('in', a) for a in range(n_in)] + [('sp', i, j) for i in range(s) for j in range(3)]
    sinks = [('sp', i) for i in range(s)] + [('out', b) for b in range(len(target))]
    e = {(p, t): m.NewBoolVar('') for p in ports for t in sinks}
    fe = {(p, t, k): m.NewIntVar(0, U, '') for p in ports for t in sinks for k in range(K)}
    f = {(p, k): m.NewIntVar(0, U, '') for p in ports for k in range(K)}
    used = {}
    for p in ports:
        tot = sum(e[p, t] for t in sinks)
        if p[0] == 'in':
            m.Add(tot == 1)
            for k in range(K):
                m.Add(f[p, k] == (U if k == p[1] else 0))
        else:
            used[p] = m.NewBoolVar('')
            m.Add(tot == used[p])
            for k in range(K):
                m.Add(f[p, k] == 0).OnlyEnforceIf(used[p].Not())
        for t in sinks:
            for k in range(K):
                m.Add(fe[p, t, k] <= U * e[p, t])
        for k in range(K):
            m.Add(sum(fe[p, t, k] for t in sinks) == f[p, k])
    for t in sinks:
        m.Add(sum(e[p, t] for p in ports) <= 3)
        m.Add(sum(e[p, t] for p in ports) >= 1)
    for i in range(s):
        t = ('sp', i)
        inn = [sum(fe[p, t, k] for p in ports) for k in range(K)]
        m.Add(sum(inn) <= U)
        three = m.NewBoolVar('')
        sl = [used[('sp', i, j)] for j in range(3)]
        m.Add(sum(sl) == 3).OnlyEnforceIf(three)
        m.Add(sum(sl) == 2).OnlyEnforceIf(three.Not())
        m.Add(sl[0] == 1); m.Add(sl[1] == 1)
        for j in range(3):
            p = ('sp', i, j)
            for k in range(K):
                m.Add(2 * f[p, k] == inn[k]).OnlyEnforceIf([used[p], three.Not()])
                m.Add(3 * f[p, k] == inn[k]).OnlyEnforceIf([used[p], three])
        for j in range(3):                      # no splitter output straight back into itself
            m.Add(e[('sp', i, j), t] == 0)
    for b, vec in enumerate(target):
        t = ('out', b)
        if vec is None:                          # free output row: any mix, exactly one full belt
            m.Add(sum(fe[p, t, k] for p in ports for k in range(K)) == U)
            continue
        for k in range(K):
            m.Add(sum(fe[p, t, k] for p in ports) == vec[k])
    for i in range(s - 1):                      # symmetry: splitter order by input belt count (weak)
        pass
    sv = cp_model.CpSolver()
    sv.parameters.max_time_in_seconds = timeout
    sv.parameters.num_workers = 8
    st = sv.Solve(m)
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return sv.StatusName(st), None
    net = {}
    for p in ports:
        for t in sinks:
            if sv.Value(e[p, t]):
                net[p] = (t, [sv.Value(f[p, k]) for k in range(K)])
    return 'FOUND', net


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--target', default='1,4/4,1')
    ap.add_argument('--unit', type=int, default=120)
    ap.add_argument('--max', type=int, default=8)
    ap.add_argument('--timeout', type=float, default=600)
    a = ap.parse_args()
    rows = [None if r == '*' else list(map(int, r.split(','))) for r in a.target.split('/')]
    den = sum(next(r for r in rows if r))
    target = [r and [v * a.unit // den for v in r] for r in rows]
    for s in range(1, a.max + 1):
        st, net = solve(len(rows), target, s, a.unit, a.timeout)
        print(s, st, flush=True)
        if net:
            for p, (t, fl) in sorted(net.items()):
                print('  ', p, '->', t, [str(Fraction(v, a.unit)) for v in fl])
            return


if __name__ == '__main__':
    main()
