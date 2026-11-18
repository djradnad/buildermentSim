"""Network first, then placement: lay out a FIXED splitter network with a pure true/false model.

The flow model (model.py) decides pieces and integer flows together. Here the network is given: which splitters
there are, which belt merges, and which edge runs from where to where. Every link then just carries one edge
label, so the model has no numbers at all, which SAT solvers handle best.

    net = extract(grid, win)          # the network of a solved layout
    r = place(net, win2)              # does that network fit in another window?

A network is a dict:
    edges      number of edges (labelled 0..edges-1)
    src        edges that enter from input ports (one per input)
    snk        edges that leave through output ports (one per output)
    split      [(in_edge, [out_edges])]      one per splitter
    merge      [([in_edges], out_edge)]      one per belt tile that takes from more than one side, or
                                             underground exit fed by more than one entrance
Ports are interchangeable: any input port may carry any src edge, any output port any snk edge.

Rules on top of model.py's pieces and links (exact game links):
    a splitter tile is one splitter node: its input carries the node's in-edge, its outputs exactly its out-edges
    a belt tile is a merge node (several inputs) or passes its single input's edge on
    underground entrances and exits pass their edge on (one entrance per exit)
A label path cannot loop back into itself (a belt that is not a merge has one input), so every edge runs from its
start node to its end node. A node's outputs (a merge's inputs) must carry different edges: without that, edges can
be duplicated in a closed cascade and the layout realises a different network. Not used for proofs on its own: a proof needs every possible network (see search).
"""
import time
from ortools.sat.python import cp_model

from .model import Model
from .pieces import DIRS


def _port_terms(mod, win):
    """[(tile, kind 'in'|'out', literal or True)] for every possible port use."""
    terms = []
    cellset = set(mod.cells)
    for p in mod.cells:
        for d in range(4):
            q = (p[0] + DIRS[d][0], p[1] + DIRS[d][1])
            if q in cellset:
                continue
            spec = win.ports.get((p, d))
            if spec is not None:
                kind = spec[0] if isinstance(spec, tuple) else spec
                if kind in ('in', 'out'):
                    terms.append((p, kind, True))
                continue
            for lit, kind, vec in mod.optional.get((p, d), []):
                terms.append((p, kind, lit))
    return terms


def extract(grid, win):
    """The network of a finished layout (grid: tile -> code) in its window."""
    w = _copy_window(win)
    w.fixed = {p: grid.get(p) for p in [(x, y) for x in range(win.W) for y in range(win.H)] if p not in win.blocked}
    # with flows the model settles which port rows are used; a layout that doesn't work as the spec asks (only
    # while testing) falls back to pieces and links alone, where the port rows may be read ambiguously
    for flows in (True, False):
        mod = Model(w, flows=flows)
        sv = cp_model.CpSolver()
        sv.parameters.max_time_in_seconds = 60
        st = sv.Solve(mod.m)
        if st in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            break
    assert st in (cp_model.OPTIMAL, cp_model.FEASIBLE), 'layout does not fit its window'
    val = lambda l: True if l is True else bool(sv.Value(l))
    links = [(p, q) for (p, q, l, kind) in mod.links if val(l)]
    terms = [(p, k) for (p, k, l) in _port_terms(mod, w) if val(l)]
    ins = {p: [] for p in mod.cells}       # tile -> in-terms: ('L', link index) or ('P', term index)
    outs = {p: [] for p in mod.cells}
    for i, (p, q) in enumerate(links):
        outs[p].append(('L', i))
        ins[q].append(('L', i))
    for j, (p, k) in enumerate(terms):
        (ins if k == 'in' else outs)[p].append(('P', j))
    code = lambda p: grid.get(p) or ''
    is_node = lambda p: code(p).startswith('S') or (code(p)[:1] in ('B', 'O') and len(ins[p]) > 1)
    edge_of = {}                           # out-term -> edge
    net = dict(edges=0, src=[], snk=[], split=[], merge=[])
    end_of = {}                            # edge -> ('node', tile) or ('snk', term)

    def walk(term):
        e = net['edges']
        net['edges'] += 1
        edge_of[term] = e
        while True:
            if term[0] == 'P':                      # leaves through an output port
                net['snk'].append(e)
                end_of[e] = ('snk', term)
                return e
            q = links[term[1]][1]
            if is_node(q):
                end_of[e] = ('node', q)
                return e
            assert len(outs[q]) == 1, 'a chain tile with %d outputs at %s' % (len(outs[q]), q)
            term = outs[q][0]

    for j, (p, k) in enumerate(terms):
        if k == 'in':
            # the input enters at p; if p is a node the edge has no belts, else follow p's output
            e = net['edges']
            if is_node(p):
                net['edges'] += 1
                end_of[e] = ('node', p)
            else:
                assert len(outs[p]) == 1
                e = walk(outs[p][0])
            net['src'].append(e)
            edge_of[('P', j)] = e
    node_tiles = [p for p in mod.cells if code(p) and is_node(p) and (ins[p] or outs[p])]   # idle pieces dropped
    for p in node_tiles:
        for t in outs[p]:
            walk(t)
    # in-edges of each node: the edges that end there
    node_in = {p: [] for p in node_tiles}
    for e, (kind, x) in end_of.items():
        if kind == 'node':
            node_in[x].append(e)
    for p in node_tiles:
        out_e = [edge_of[t] for t in outs[p]]
        if code(p).startswith('S'):
            assert len(node_in[p]) == 1, (p, code(p), node_in[p], ins[p])
            net['split'].append((node_in[p][0], out_e))
        else:
            assert len(out_e) == 1
            net['merge'].append((node_in[p], out_e[0]))
    return contract(net)


def contract(net):
    """Fold splitters with a single output into their edge (they pass items on like a belt) and renumber."""
    split = [list(x) for x in net['split']]
    alias = {}
    keep = []
    for a, b in split:
        if len(b) == 1:
            alias[b[0]] = a                 # out edge b is the same edge as in edge a
        else:
            keep.append((a, b))
    def root(e):
        while e in alias:
            e = alias[e]
        return e
    used = sorted({root(e) for e in range(net['edges'])} & (
        {root(e) for e in net['src'] + net['snk']} | {root(a) for a, b in keep} | {root(x) for a, b in keep for x in b}
        | {root(x) for a, b in net['merge'] for x in a} | {root(b) for a, b in net['merge']}))
    num = {e: i for i, e in enumerate(used)}
    r = lambda e: num[root(e)]
    return dict(edges=len(used), src=[r(e) for e in net['src']], snk=[r(e) for e in net['snk']],
                split=[(r(a), [r(x) for x in b]) for a, b in keep],
                merge=[([r(x) for x in a], r(b)) for a, b in net['merge']])


def _copy_window(win):
    import copy
    return copy.copy(win)


def build(net, win):
    """The placement model of net in win: Model(win, flows=False) plus edge labels.
    Every link carries its edge number in binary (k bits); rules are plain clauses."""
    mod = Model(win, flows=False)
    m, X = mod.m, mod.X
    E = net['edges']
    k = max(1, (E - 1).bit_length())
    links = mod.links
    terms = _port_terms(mod, win)
    act, bits = {}, {}                                  # term -> active literal (or True), its label bits
    for i, (p, q, l, kind) in enumerate(links):
        act['L', i] = l
        bits['L', i] = [m.NewBoolVar('') for _ in range(k)]
    src, snk = net['src'], net['snk']
    PL = {}
    for j, (p, kind, l) in enumerate(terms):
        t = ('P', j)
        act[t] = l
        bits[t] = [m.NewBoolVar('') for _ in range(k)]
        vs = []
        for e in (src if kind == 'in' else snk):
            v = PL[t, e] = m.NewBoolVar('')
            vs.append(v)
            for b in range(k):                          # v -> bits spell e
                m.AddImplication(v, bits[t][b] if (e >> b) & 1 else bits[t][b].Not())
        m.Add(sum(vs) == (1 if l is True else l))
    for e in set(src) | set(snk):                       # every port edge used exactly once
        m.Add(sum(v for (t, e2), v in PL.items() if e2 == e) == 1)
    tin = {p: [('L', i) for i in mod.lin[p]] for p in mod.cells}
    tout = {p: [('L', i) for i in mod.lout[p]] for p in mod.cells}
    for j, (p, kind, l) in enumerate(terms):
        (tin if kind == 'in' else tout)[p].append(('P', j))

    def neg(conds):
        return [c.Not() for c in conds if c is not True]

    def same(conds, t, u):
        for b in range(k):
            m.AddBoolOr(neg(conds) + [bits[t][b].Not(), bits[u][b]])
            m.AddBoolOr(neg(conds) + [bits[t][b], bits[u][b].Not()])

    def member(conds, t, allowed):
        # forbid every value outside allowed with prefix cubes (high bit first): a prefix no allowed value starts
        # with is one short clause, so about k * len(allowed) clauses instead of 2^k (memory on 6+ rows)
        allowed = set(allowed)
        def rec(prefix, depth):
            b = k - 1 - depth
            for bit in (0, 1):
                pre = prefix + [(b, bit)]
                lo = sum(v << bb for bb, v in pre)
                hit = [a for a in allowed if all(((a >> bb) & 1) == v for bb, v in pre)]
                if not hit:
                    m.AddBoolOr(neg(conds) + [bits[t][bb].Not() if v else bits[t][bb] for bb, v in pre])
                elif depth + 1 < k:
                    rec(pre, depth + 1)
        rec([], 0)

    def differ(conds, t, u, values):
        for v in values:
            m.AddBoolOr(neg(conds) + [x for b in range(k) for x in
                                      ((bits[t][b].Not(), bits[u][b].Not()) if (v >> b) & 1 else (bits[t][b], bits[u][b]))])

    nact = lambda ts: sum(1 if act[t] is True else act[t] for t in ts)
    A = {}
    nodes = [('S', [a], list(b)) for a, b in net['split']] + [('M', list(a), [b]) for a, b in net['merge']]
    for p in mod.cells:
        sp = [X[p, 'S%d' % o] for o in range(4)]
        bl = [X[p, 'B%d' % o] for o in range(4)]
        for j, (kind, ins_e, outs_e) in enumerate(nodes):
            v = A[p, j] = m.NewBoolVar('')
            ol = [X[p, 'O%d' % o] for o in range(4) if (p, 'O%d' % o) in X]
            m.AddBoolOr(sp if kind == 'S' else bl + ol).OnlyEnforceIf(v)
            m.Add(nact(tin[p]) == len(ins_e)).OnlyEnforceIf(v)
            m.Add(nact(tout[p]) == len(outs_e)).OnlyEnforceIf(v)
            for t in tin[p]:
                member([v, act[t]], t, set(ins_e))
            for t in tout[p]:
                member([v, act[t]], t, set(outs_e))
            # each of the node's edges once: a splitter's outputs (a merge's inputs) carry different edges
            many = tout[p] if kind == 'S' else tin[p]
            for x in range(len(many)):
                for y in range(x + 1, len(many)):
                    differ([v, act[many[x]], act[many[y]]], many[x], many[y], outs_e if kind == 'S' else ins_e)
        msum = sum(A[p, j] for j, n in enumerate(nodes) if n[0] == 'M')
        ssum = sum(A[p, j] for j, n in enumerate(nodes) if n[0] == 'S')
        # a belt that is not a merge node, a splitter with one output, or an underground end: one input, passes
        # its edge on
        plain = m.NewBoolVar('')
        others = bl + sp + [X[p, kk + str(o)] for kk in 'IO' for o in range(4) if (p, kk + str(o)) in X]
        m.Add(plain + msum + ssum == sum(others))
        m.Add(nact(tin[p]) == 1).OnlyEnforceIf(plain)
        m.Add(nact(tout[p]) == 1).OnlyEnforceIf(plain)
        for t in tin[p]:
            for u in tout[p]:
                same([plain, act[t], act[u]], t, u)
    for j in range(len(nodes)):
        m.Add(sum(A[p, j] for p in mod.cells) == 1)
    mod.A, mod.bits = A, bits
    return mod


def build1(net, win):
    """Same rules as build(), one-hot edge labels instead of binary bits: one boolean per (link, edge). Membership is
    one clause per (node, tile, link) and 'outputs differ' is node-free (no tile may send or take one edge twice),
    so the model stays small for 6+ rows (the binary model ran out of memory on a 6:6 at 27x6)."""
    mod = Model(win, flows=False)
    m, X = mod.m, mod.X
    E = net['edges']
    links = mod.links
    terms = _port_terms(mod, win)
    act, L = {}, {}
    for i, (p, q, l, kind) in enumerate(links):
        act['L', i] = l
    for j, (p, kind, l) in enumerate(terms):
        act['P', j] = l
    src, snk = set(net['src']), set(net['snk'])
    port_ok = {}
    for j, (p, kind, l) in enumerate(terms):
        port_ok['P', j] = src if kind == 'in' else snk
    for t, l in act.items():
        allowed = port_ok.get(t, range(E))
        L[t] = {e: m.NewBoolVar('') for e in allowed}
        m.Add(sum(L[t].values()) == (1 if l is True else l))
    for e in src | snk:                                  # every port edge used exactly once
        m.Add(sum(L[t][e] for t in L if t[0] == 'P' and e in L[t]) == 1)
    tin = {p: [('L', i) for i in mod.lin[p]] for p in mod.cells}
    tout = {p: [('L', i) for i in mod.lout[p]] for p in mod.cells}
    for j, (p, kind, l) in enumerate(terms):
        (tin if kind == 'in' else tout)[p].append(('P', j))
    lit = lambda t, e: L[t].get(e)
    nact = lambda ts: sum(1 if act[t] is True else act[t] for t in ts)
    for p in mod.cells:                                  # no tile sends or takes one edge twice
        for ts in (tin[p], tout[p]):
            for e in range(E):
                vs = [lit(t, e) for t in ts if lit(t, e) is not None]
                if len(vs) > 1:
                    m.AddAtMostOne(vs)
    A = {}
    nodes = [('S', [a], list(b)) for a, b in net['split']] + [('M', list(a), [b]) for a, b in net['merge']]
    for p in mod.cells:
        sp = [X[p, 'S%d' % o] for o in range(4)]
        bl = [X[p, 'B%d' % o] for o in range(4)]
        for j, (kind, ins_e, outs_e) in enumerate(nodes):
            v = A[p, j] = m.NewBoolVar('')
            ol = [X[p, 'O%d' % o] for o in range(4) if (p, 'O%d' % o) in X]
            m.AddBoolOr(sp if kind == 'S' else bl + ol).OnlyEnforceIf(v)
            m.Add(nact(tin[p]) == len(ins_e)).OnlyEnforceIf(v)
            m.Add(nact(tout[p]) == len(outs_e)).OnlyEnforceIf(v)
            for ts, es in ((tin[p], ins_e), (tout[p], outs_e)):
                for t in ts:
                    ok = [lit(t, e) for e in es if lit(t, e) is not None]
                    cond = [v.Not()] + ([act[t].Not()] if act[t] is not True else [])
                    m.AddBoolOr(cond + ok)
        msum = sum(A[p, j] for j, n in enumerate(nodes) if n[0] == 'M')
        ssum = sum(A[p, j] for j, n in enumerate(nodes) if n[0] == 'S')
        plain = m.NewBoolVar('')
        others = bl + sp + [X[p, kk + str(o)] for kk in 'IO' for o in range(4) if (p, kk + str(o)) in X]
        m.Add(plain + msum + ssum == sum(others))
        m.Add(nact(tin[p]) == 1).OnlyEnforceIf(plain)
        m.Add(nact(tout[p]) == 1).OnlyEnforceIf(plain)
        # a plain tile passes its edge on: tile edge vars T[e] tie its one input and one output
        T = {e: m.NewBoolVar('') for e in range(E)}
        m.Add(sum(T.values()) == 1).OnlyEnforceIf(plain)
        for t in tin[p] + tout[p]:
            for e in range(E):
                x = lit(t, e)
                if x is None:
                    continue
                cond = [plain.Not()] + ([act[t].Not()] if act[t] is not True else [])
                m.AddBoolOr(cond + [x.Not(), T[e]])
                m.AddBoolOr(cond + [x, T[e].Not()])
    for j in range(len(nodes)):
        m.Add(sum(A[p, j] for p in mod.cells) == 1)
    mod.A, mod.L = A, L
    return mod


def shares(net):
    """For every src edge, the share of it that reaches each snk edge (exact fractions)."""
    from fractions import Fraction as F
    out = []
    order = {e: i for i, e in enumerate(net['snk'])}
    for s0 in net['src']:
        flow = [F(0)] * net['edges']
        for _ in range(4 * net['edges'] + 50):       # loops converge only in the limit; enough for the check
            new = [F(0)] * net['edges']
            new[s0] = F(1)
            for a, b in net['split']:
                for x in b:
                    new[x] += flow[a] / len(b)
            for a, b in net['merge']:
                new[b] += sum(flow[x] for x in a)
            flow = new
        out.append([flow[e] for e in net['snk']])
    return out


def place(net, win, timeout=60, workers=1, engine='cpsat', onehot=None):
    """Is there a layout of net in win? Returns dict(status, time, grid?, same_network?).
    A found layout is read back with extract() and its network compared with net."""
    import os
    if onehot is None:
        onehot = os.environ.get('NETPLACE_ONEHOT', '0') == '1'
    res = _place(net, win, timeout, workers, engine, onehot)
    if 'grid' in res:
        try:
            got = extract(res['grid'], win)
            res['same_network'] = (got['edges'], len(got['split']), len(got['merge'])) == \
                (net['edges'], len(net['split']), len(net['merge']))
        except AssertionError as e:
            res['same_network'] = False
            res['extract_error'] = str(e)
    return res


def _place(net, win, timeout, workers, engine, onehot=False):
    t0 = time.time()
    mod = (build1 if onehot else build)(net, win)
    if engine == 'cpsat':
        sv = cp_model.CpSolver()
        sv.parameters.max_time_in_seconds = timeout
        sv.parameters.num_workers = workers
        st = sv.Solve(mod.m)
        res = dict(status=sv.StatusName(st), time=time.time() - t0)
        if st in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            res['grid'] = mod.grid(sv)
        return res
    from . import cnf
    return cnf.solve(mod, engine=engine, timeout=timeout)
