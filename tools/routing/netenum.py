"""Every N:N splitter network under the game's rules (john 2026-11-18: all arithmetic, all orders, loops and
loop-backs alike, then place each network on the whole box with bmsolver/netplace.py and keep the smallest).

A network: n input belts, s splitters, n outputs. A splitter takes ONE belt, which may merge up to 3 belts, and
splits it equally over 2 or 3 outputs; every belt carries at most one full belt; every output is one full belt with
1/n of every input. Loops are allowed (any graph); flows are steady state in units of 1/U, so loops whose shares
need finer units than U are missed (raise --unit). Networks equal up to relabelling are kept once.

    python tools/routing/netenum.py --n 3 --splitters 3-6 --unit 36 --out ~/data/nets3.jsonl
"""
import argparse
import json
import os
import sys
from fractions import Fraction as F
from ortools.sat.python import cp_model
import networkx as nx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))


def model(n, s, U, max_merge=3):
    m = cp_model.CpModel()
    ports = [('in', a) for a in range(n)] + [('sp', i, j) for i in range(s) for j in range(3)]
    sinks = [('sp', i) for i in range(s)] + [('out', b) for b in range(n)]
    e = {(p, t): m.NewBoolVar('') for p in ports for t in sinks}
    f = {(p, k): m.NewIntVar(0, U, '') for p in ports for k in range(n)}
    fe = {(p, t, k): m.NewIntVar(0, U, '') for p in ports for t in sinks for k in range(n)}
    used, three = {}, {}
    for p in ports:
        tot = sum(e[p, t] for t in sinks)
        if p[0] == 'in':
            m.Add(tot == 1)
            for k in range(n):
                m.Add(f[p, k] == (U if k == p[1] else 0))
        else:
            used[p] = m.NewBoolVar('')
            m.Add(tot == used[p])
            for k in range(n):
                m.Add(f[p, k] == 0).OnlyEnforceIf(used[p].Not())
        for t in sinks:
            for k in range(n):
                m.Add(fe[p, t, k] <= U * e[p, t])
        for k in range(n):
            m.Add(sum(fe[p, t, k] for t in sinks) == f[p, k])
    for t in sinks:
        m.Add(sum(e[p, t] for p in ports) <= max_merge)
        m.Add(sum(e[p, t] for p in ports) >= 1)
    sidx = {t: i for i, t in enumerate(sinks)}
    for i in range(s):
        t = ('sp', i)
        inn = [sum(fe[p, t, k] for p in ports) for k in range(n)]
        m.Add(sum(inn) <= U)
        th = three[i] = m.NewBoolVar('')
        sl = [used[('sp', i, j)] for j in range(3)]
        m.Add(sl[0] == 1)
        m.Add(sl[1] == 1)
        m.Add(sl[2] == th)
        for j in range(3):
            p = ('sp', i, j)
            for k in range(n):
                m.Add(2 * f[p, k] == inn[k]).OnlyEnforceIf([used[p], th.Not()])
                m.Add(3 * f[p, k] == inn[k]).OnlyEnforceIf([used[p], th])
            m.Add(e[p, t] == 0)                 # no output straight back into its own splitter
        # slots are interchangeable: their sinks in order
        idx = []
        for j in range(3):
            p = ('sp', i, j)
            v = m.NewIntVar(0, len(sinks), '')
            m.Add(v == sum(sidx[tt] * e[p, tt] for tt in sinks) + len(sinks) * (1 - used[p]))
            idx.append(v)
        m.Add(idx[0] <= idx[1])
        m.Add(idx[1] <= idx[2])
        # a splitter must split something real: it takes flow
        m.Add(sum(inn) >= 1)
    for b in range(n):
        t = ('out', b)
        for k in range(n):
            m.Add(sum(fe[p, t, k] for p in ports) == U // n)
    # outputs are interchangeable: order them by their first source
    first = []
    for b in range(n):
        t = ('out', b)
        v = m.NewIntVar(0, len(ports), '')
        for j, p in enumerate(ports):
            m.Add(v <= j).OnlyEnforceIf(e[p, t])
        first.append(v)
    for b in range(n - 1):
        m.Add(first[b] < first[b + 1])
    return m, ports, sinks, e, used


def to_net(n, s, ports, sinks, conn):
    """conn: port -> sink. netplace format (edges, src, snk, split, merge)."""
    eid = {p: i for i, p in enumerate(p for p in ports if p in conn)}
    E = len(eid)
    feeds = {t: [p for p in ports if conn.get(p) == t] for t in sinks}
    split, merge, into = [], [], {}
    for t in sinks:
        src = feeds[t]
        if len(src) == 1:
            into[t] = eid[src[0]]
        else:
            into[t] = E
            merge.append(([eid[p] for p in src], E))
            E += 1
    for i in range(s):
        outs = [eid[('sp', i, j)] for j in range(3) if ('sp', i, j) in eid]
        split.append((into[('sp', i)], outs))
    return dict(edges=E, src=[eid[('in', a)] for a in range(n)], snk=[into[('out', b)] for b in range(n)],
                split=split, merge=merge)


def graph(net):
    g = nx.MultiDiGraph()
    E = net['edges']
    head, tail = {}, {}                          # edge -> node it leaves / enters
    for a in net['src']:
        g.add_node(('i', a), t='in'); head[a] = ('i', a)
    for b in net['snk']:
        g.add_node(('o', b), t='out'); tail[b] = ('o', b)
    for j, (a, bs) in enumerate(net['split']):
        g.add_node(('s', j), t='s%d' % len(bs)); tail[a] = ('s', j)
        for x in bs:
            head[x] = ('s', j)
    for j, (as_, b) in enumerate(net['merge']):
        g.add_node(('m', j), t='m'); head[b] = ('m', j)
        for x in as_:
            tail[x] = ('m', j)
    for x in range(E):
        g.add_edge(head[x], tail[x])
    return g


def shares_ok(net, n):
    from bmsolver.netplace import shares
    try:
        sh = shares(net)
    except Exception:
        return False
    return all(abs(float(v) - 1 / n) < 1e-9 for r in sh for v in r)


class Collect(cp_model.CpSolverSolutionCallback):
    def __init__(self, n, s, ports, sinks, e, cap, seen, out, log):
        super().__init__()
        self.n, self.s, self.ports, self.sinks, self.e = n, s, ports, sinks, e
        self.cap, self.seen, self.out, self.log = cap, seen, out, log
        self.raw = 0

    def on_solution_callback(self):
        self.raw += 1
        conn = {p: t for (p, t), v in self.e.items() if self.Value(v)}
        net = to_net(self.n, self.s, self.ports, self.sinks, conn)
        g = graph(net)
        h = nx.weisfeiler_lehman_graph_hash(nx.DiGraph(g), node_attr='t')
        nm = lambda a, b: a['t'] == b['t']
        for g2 in self.seen.get(h, []):
            if nx.is_isomorphic(g, g2, node_match=nm):
                break
        else:
            if not shares_ok(net, self.n):
                return
            self.seen.setdefault(h, []).append(g)
            self.out.append(net)
            self.log(net)
        if len(self.out) >= self.cap:
            self.StopSearch()


def enumerate_nets(n, s, U, cap=1000, timeout=600, log=print, max_merge=3):
    m, ports, sinks, e, used = model(n, s, U, max_merge)
    sv = cp_model.CpSolver()
    sv.parameters.enumerate_all_solutions = True
    sv.parameters.num_workers = 1
    sv.parameters.max_time_in_seconds = timeout
    out, seen = [], {}
    cb = Collect(n, s, ports, sinks, e, cap, seen, out, log)
    st = sv.Solve(m, cb)
    return out, sv.StatusName(st), cb.raw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, required=True)
    ap.add_argument('--splitters', default='3-6')
    ap.add_argument('--unit', type=int, default=36)
    ap.add_argument('--cap', type=int, default=1000)
    ap.add_argument('--timeout', type=float, default=600)
    ap.add_argument('--out', default=None)
    a = ap.parse_args()
    lo, hi = [int(v) for v in a.splitters.split('-')] if '-' in a.splitters else [int(a.splitters)] * 2
    fh = open(os.path.expanduser(a.out), 'a') if a.out else None
    for s in range(lo, hi + 1):
        def log(net, s=s):
            if fh:
                fh.write(json.dumps(dict(n=a.n, s=s, net=net)) + '\n'); fh.flush()
        nets, st, raw = enumerate_nets(a.n, s, a.unit, a.cap, a.timeout, log)
        print('%d:%d with %d splitters: %d networks (%d raw solutions, %s)' % (a.n, a.n, s, len(nets), raw, st),
              flush=True)


if __name__ == '__main__':
    main()
