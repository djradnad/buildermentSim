"""Learn from every "no" (john 2026-11-18): when a network cannot be placed, ask which of its parts already make it
impossible, with the belts of every other part left open (they may stop or start anywhere). That set of parts is a
CORE. Any later network that contains the core (same part kinds, same belts between them and to the ports) is ruled
out with no solve. Sound: the open-ended problem is looser than every network that contains the core. A narrower box
is only harder, so a core found at width W also holds at every width below W.

    python tools/routing/netcore.py --nets ~/data/nets3b.jsonl --W 17 --H 3 --out ~/data/cores3.jsonl
"""
import argparse
import json
import os
import sys
import time
import networkx as nx
from networkx.algorithms.isomorphism import DiGraphMatcher
from ortools.sat.python import cp_model
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from bmsolver import spec as S, netplace as NP  # noqa: E402


def nodes_of(net):
    return [('S', [a], list(b)) for a, b in net['split']] + [('M', list(a), [b]) for a, b in net['merge']]


def core_graph(net, keep):
    """The core as a typed multigraph: kept nodes, all ports, and the belts among them (other belts dropped)."""
    nodes = nodes_of(net)
    head, tail = {}, {}
    g = nx.MultiDiGraph()
    for e in net['src']:
        g.add_node(('i', e), t='in'); head[e] = ('i', e)
    for e in net['snk']:
        g.add_node(('o', e), t='out'); tail[e] = ('o', e)
    for j in keep:
        kind, ins, outs = nodes[j]
        g.add_node(('n', j), t='%s%d%d' % (kind, len(ins), len(outs)))
        for e in ins:
            tail[e] = ('n', j)
        for e in outs:
            head[e] = ('n', j)
    for e in range(net['edges']):
        if e in head and e in tail:
            g.add_edge(head[e], tail[e])
    return g


def full_graph(net):
    return core_graph(net, range(len(nodes_of(net))))


def _simple(g):
    """multigraph -> digraph with edge counts (for the matcher)"""
    h = nx.DiGraph()
    h.add_nodes_from(g.nodes(data=True))
    for u, v in g.edges():
        if h.has_edge(u, v):
            h[u][v]['c'] += 1
        else:
            h.add_edge(u, v, c=1)
    return h


def contains(net_g, core_g):
    """Does the network contain the core? (typed subgraph monomorphism, edge counts at least the core's)"""
    gm = DiGraphMatcher(_simple(net_g), _simple(core_g), node_match=lambda a, b: a['t'] == b['t'],
                        edge_match=lambda a, b: a['c'] >= b['c'])
    return gm.subgraph_is_monomorphic()


def solve_open(net, win, present, timeout, workers=1):
    """Place net with only the nodes in `present` required (others open). Returns status, core (list of nodes)."""
    mod = NP.build(net, win, open_ends=True)
    m = mod.m
    R = mod.R
    m.ClearAssumptions()
    for j, r in R.items():
        if j in present:
            m.AddAssumption(r)
        else:
            m.Add(r == 0)
    sv = cp_model.CpSolver()
    sv.parameters.max_time_in_seconds = timeout
    sv.parameters.num_workers = workers
    st = sv.Solve(m)
    name = sv.StatusName(st)
    core = None
    if st == cp_model.INFEASIBLE:
        idx = {r.Index(): j for j, r in R.items()}
        core = sorted(idx[i] for i in sv.SufficientAssumptionsForInfeasibility() if i in idx)
    return name, core


def learn(net, win, timeout=900, shrink_timeout=120, workers=1, log=print):
    """INFEASIBLE -> a small core (assumption core, then drop nodes one by one while it stays INFEASIBLE)."""
    n_all = len(nodes_of(net))
    t0 = time.time()
    st, core = solve_open(net, win, set(range(n_all)), timeout, workers)
    log('  open solve: %s %.0fs core %s' % (st, time.time() - t0, core))
    if st != 'INFEASIBLE':
        return st, None
    core = list(core)
    for j in list(core):                            # deletion pass: a smaller core rules out more networks
        trial = [x for x in core if x != j]
        st2, c2 = solve_open(net, win, set(trial), shrink_timeout, workers)
        if st2 == 'INFEASIBLE':
            core = sorted(set(c2) & set(trial)) if c2 else trial
            log('  drop %d -> core %s' % (j, core))
    return 'INFEASIBLE', core


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--nets', required=True)
    ap.add_argument('--W', type=int, required=True)
    ap.add_argument('--H', type=int, required=True)
    ap.add_argument('--timeout', type=float, default=900)
    ap.add_argument('--shrink-timeout', type=float, default=120)
    ap.add_argument('--workers', type=int, default=1)
    ap.add_argument('--out', required=True, help='jsonl: one record per network (status, core or skip reason)')
    a = ap.parse_args()
    out = os.path.expanduser(a.out)
    done, cores = set(), []
    if os.path.exists(out):
        for l in open(out):
            d = json.loads(l)
            if (d['W'], d['H']) == (a.W, a.H) or (d['H'] == a.H and d['W'] >= a.W and d.get('core') is not None):
                if d.get('core') is not None:
                    cores.append(core_graph(d['net'], d['core']))
            if (d['W'], d['H']) == (a.W, a.H):
                done.add(d['i'])
    nets = [json.loads(l) for l in open(os.path.expanduser(a.nets)) if l.strip()]
    win = None
    for i, d in enumerate(nets):
        if i in done:
            continue
        net, n = d['net'], d['n']
        win = S.Spec([S.Group('E', n)], [S.Group('W', n)]).window(a.W, a.H)
        g = full_graph(net)
        hit = next((c for c, cg in enumerate(cores) if contains(g, cg)), None)
        rec = dict(W=a.W, H=a.H, i=i, net=net)
        if hit is not None:
            rec.update(status='INFEASIBLE', by_core=hit)
            print('net %d: ruled out by core %d' % (i, hit), flush=True)
        else:
            t0 = time.time()
            st, core = learn(net, win, a.timeout, a.shrink_timeout, a.workers,
                             log=lambda s: print(s, flush=True))
            rec.update(status=st, core=core, time=round(time.time() - t0, 1))
            if core is not None:
                cores.append(core_graph(net, core))
            print('net %d: %s %.0fs core %s of %d parts' % (i, st, time.time() - t0, core, len(nodes_of(net))),
                  flush=True)
        with open(out, 'a') as fh:
            fh.write(json.dumps(rec) + '\n')


if __name__ == '__main__':
    main()
