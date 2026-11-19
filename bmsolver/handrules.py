"""Hand-proven layout facts as optional extra constraints for the exact models (all OFF by default).

Every constraint here is IMPLIED by the game rules (it never removes a valid layout); it is only there to help the
solver find a contradiction sooner. Turn them on with the env var BM_RULES, a comma list of tokens:

    f1      one surface link per tile pair: the two tiles on either side of a line never feed each other   (model)
    p1      8-line law: in one row, any 8 consecutive vertical lines hold at most 5 "doubles" (a line with both a
            surface link and a tunnel in the same direction)                                               (model)
    static  node/tile pruning: a splitter or merge node only on tiles and facings where it can get all its inputs
            and give all its outputs (edge rows for 3-way splitters, port columns and their labels)    (netplace)
    adj3    a 3-way splitter node and any node on a neighbouring tile are joined by an edge                (netplace)
    hop3    no hop end facing across the line to a neighbouring 3-way splitter (wall rule: nothing passes a
            3-way splitter's column on the surface except into or out of it, and no hop ends there)   (netplace)

Proofs (see the project notes 33_formulas.md and rules_speed.md; measured: none is faster, so all stay off):
  f1:  F1. A tile that pushes across a line toward its neighbour never accepts from that side (a belt never
       accepts from its front, an exit accepts nothing, a splitter only from its back).
  p1:  P1. The doubles of one tunnel group (exit x, last entrance e, e - x <= 7) lie on lines x+2 .. e-1 (an exit
       accepts nothing from the surface, an entrance pushes nothing onto it), at most 5 lines; the next group in
       that row and direction has its exit at >= e+1, so its doubles start at >= e+3: >= 3 double-free lines
       between groups. Holds in both horizontal directions (the game is mirror symmetric).
  static: a splitter accepts only from its back and pushes front/left/right; a belt accepts from back and sides and
       pushes front; an exit accepts only from its tunnel. A tile with a fixed 'in' port must accept from it, one
       with a fixed 'out' port must push across it (that output then carries a src / snk label). A node with k
       outputs (inputs) needs k usable sides. A tile next to one with a fixed 'in' port that a node pushes into
       has two inputs, so it must be a merge node that takes a src label and one of the node's labels.
  adj3: a 3-way splitter uses all four sides (3 outputs into the neighbours, input from the back), so every
       neighbour exchanges items with it on that link: a neighbouring node takes one of its labels or gives it
       its input label.
  hop3: for the same reason every neighbour of a 3-way splitter exchanges items with it along the axis of the
       two tiles; a hop end facing across that axis never does (an entrance accepts only from its back and
       pushes nothing on the surface, an exit accepts nothing and pushes only forward). In a 3-row box with the
       splitter in the middle row this is "no hop ends in that column" (vertical hop ends in edge rows cannot
       link at all).
"""
import os

from .pieces import DIRS, UG_REACH


def enabled(tok):
    return tok in [t.strip() for t in os.environ.get('BM_RULES', '').split(',')]


def _special(win):
    return bool(win.tun_in or win.tun_out or win.open_west or win.arm_cap or win.machines or win.no_ug)


# ------------------------------------------------------------------ geometry rules on Model (pieces and links)

def model_rules(mod, win):
    """Add the enabled geometry rules to a built Model (called at the end of Model.__init__)."""
    if _special(win):
        return
    if enabled('f1'):
        _f1(mod)
    if enabled('p1'):
        _p1(mod, win)


def _f1(mod):
    m = mod.m
    idx = {}
    for i, (p, q, l, kind) in enumerate(mod.links):
        if kind == 'push':
            idx[p, q] = l
    for (p, q), l in idx.items():
        if p < q and (q, p) in idx:
            m.AddBoolOr([l.Not(), idx[q, p].Not()])


def _p1(mod, win):
    m = mod.m
    W, H = win.W, win.H
    push = {}
    for i, (p, q, l, kind) in enumerate(mod.links):
        if kind == 'push' and p[1] == q[1]:
            push[p, q] = l
    for d in (1, 3):                         # west, east
        for y in range(H):
            dbl = {}
            for line in range(1, W):         # between column line-1 and column line
                a, b = ((line, y), (line - 1, y)) if d == 1 else ((line - 1, y), (line, y))
                s = push.get((a, b))
                if s is None:
                    continue
                tun = []
                for i, (p, q, l, kind) in enumerate(mod.links):
                    if kind != 'ug' or p[1] != y or q[1] != y:
                        continue
                    if d == 1 and q[0] <= line - 1 < line <= p[0]:
                        tun.append(l)
                    if d == 3 and p[0] <= line - 1 < line <= q[0]:
                        tun.append(l)
                if not tun:
                    continue
                v = m.NewBoolVar('')
                for t in tun:                # v >= s + t - 1
                    m.AddBoolOr([s.Not(), t.Not(), v])
                dbl[line] = v
            lines = sorted(dbl)
            if len(lines) <= 5:
                continue
            for a in range(1, W):
                win8 = [dbl[l] for l in range(a, a + 8) if l in dbl]
                if len(win8) > 5:
                    m.Add(sum(win8) <= 5)


# ------------------------------------------------------------------ network rules on netplace models

def _side(mod, win, p, d):
    """What is across side d of tile p: 'cell', 'in', 'out', ('opt', kinds) or 'none'."""
    q = (p[0] + DIRS[d][0], p[1] + DIRS[d][1])
    if q in mod._cellset:
        return 'cell'
    spec = win.ports.get((p, d))
    if spec is not None:
        kind = spec[0] if isinstance(spec, tuple) else spec
        if kind in ('in', 'out'):
            return kind
        return 'none'
    opts = mod.optional.get((p, d), [])
    if opts:
        return ('opt', frozenset(k for _, k, _ in opts))
    return 'none'


def _nb(p, d):
    return (p[0] + DIRS[d][0], p[1] + DIRS[d][1])


def static_allowed(mod, win, nodes, src, snk):
    """{(p, j): [piece codes]} the facings in which node j can stand on tile p (empty list: never)."""
    mod._cellset = set(mod.cells)
    src, snk = set(src), set(snk)
    # tiles with a fixed 'in' port: whoever pushes into them from another side makes them a merge node with a src
    # label among its inputs
    inport = {p for p in mod.cells for d in range(4) if _side(mod, win, p, d) == 'in'}
    merge_ins = [set(ins) for kind, ins, outs in nodes if kind == 'M']
    res = {}
    for p in mod.cells:
        sides = [_side(mod, win, p, d) for d in range(4)]
        fixed_in = [d for d in range(4) if sides[d] == 'in']
        fixed_out = [d for d in range(4) if sides[d] == 'out']
        for j, (kind, ins, outs) in enumerate(nodes):
            n_src_in = sum(1 for e in ins if e in src)
            n_snk_out = sum(1 for e in outs if e in snk)

            def out_ok(d):                   # can this output direction carry one of the node's outputs?
                s = sides[d]
                if s == 'cell':
                    q = _nb(p, d)
                    if q in inport:
                        return 'cell' if any(mi & src and mi & set(outs) for mi in merge_ins) else None
                    return 'cell'
                if s == 'out':
                    return 'port'
                if isinstance(s, tuple) and 'out' in s[1]:
                    return 'port'
                return None

            def in_ok(d):
                s = sides[d]
                if s == 'cell':
                    return 'cell'
                if s == 'in' or (isinstance(s, tuple) and 'in' in s[1]):
                    return 'port'
                return None
            ok = []
            if kind == 'S':
                for o in range(4):
                    back = (o + 2) % 4
                    pushes = {o, (o + 1) % 4, (o + 3) % 4}
                    if any(d != back for d in fixed_in) or any(d not in pushes for d in fixed_out):
                        continue
                    b = in_ok(back)
                    if b is None or (b == 'port' and sides[back] != 'cell' and n_src_in < 1):
                        continue
                    if len(fixed_out) > n_snk_out:
                        continue
                    us = [out_ok(d) for d in pushes]
                    cells = sum(1 for u in us if u == 'cell')
                    ports = sum(1 for u in us if u == 'port')
                    if cells + min(ports, n_snk_out) < len(outs):
                        continue
                    ok.append('S%d' % o)
            else:
                for o in range(4):
                    back = (o + 2) % 4
                    if any(d != o for d in fixed_out):
                        continue
                    f = out_ok(o)
                    if f is None or (f == 'port' and n_snk_out < 1):
                        continue
                    if len(fixed_out) and n_snk_out < 1:
                        continue
                    # belt: accepts from back and both sides
                    if not any(d == o for d in fixed_in):
                        accs = [in_ok(d) for d in range(4) if d != o]
                        cells = sum(1 for u in accs if u == 'cell')
                        ports = sum(1 for u in accs if u == 'port')
                        if len(fixed_in) <= n_src_in and cells + min(ports, n_src_in) >= len(ins):
                            ok.append('B%d' % o)
                    # exit: only from entrances behind it, none from a fixed in port
                    if not fixed_in and (p, 'O%d' % o) in mod.X:
                        cand = 0
                        for n in range(1, UG_REACH + 1):
                            q = (p[0] + n * DIRS[back][0], p[1] + n * DIRS[back][1])
                            if q in mod._cellset:
                                cand += 1
                            elif q in win.walls or not win.inside(q):
                                break
                        if cand >= len(ins):
                            ok.append('O%d' % o)
            res[p, j] = ok
    return res


def related(a, b):
    """Some edge (or class) runs from node a to node b or back."""
    return bool(set(a[2]) & set(b[1])) or bool(set(b[2]) & set(a[1]))


def net_rules(mod, win, nodes, A, src, snk, counts=None):
    """Add the enabled network rules. nodes: [(kind 'S'|'M', in labels, out labels)], A[p, j] node j on tile p;
    counts[j] how many copies of node j (mix-class types), default 1."""
    if _special(win):
        return
    m, X = mod.m, mod.X
    counts = counts or [1] * len(nodes)
    cellset = set(mod.cells)
    if enabled('static'):
        al = static_allowed(mod, win, nodes, src, snk)
        for (p, j), ok in al.items():
            if not ok:
                m.Add(A[p, j] == 0)
            else:
                m.AddBoolOr([X[p, c] for c in ok]).OnlyEnforceIf(A[p, j])
    three = [j for j, (kind, ins, outs) in enumerate(nodes) if kind == 'S' and len(outs) == 3]
    if enabled('adj3'):
        for j in three:
            for j2 in range(len(nodes)):
                if j2 == j and counts[j] < 2:
                    continue
                if related(nodes[j], nodes[j2]):
                    continue
                for p in mod.cells:
                    for d in range(4):
                        q = _nb(p, d)
                        if q in cellset and (j2 != j or p < q):
                            m.AddBoolOr([A[p, j].Not(), A[q, j2].Not()])
    if enabled('hop3'):
        for j in three:
            for p in mod.cells:
                for d in range(4):
                    q = _nb(p, d)
                    if q not in cellset:
                        continue
                    for o in ((d + 1) % 4, (d + 3) % 4):
                        for k in 'IO':
                            v = X.get((q, '%s%d' % (k, o)))
                            if v is not None:
                                m.AddImplication(A[p, j], v.Not())


def prefilter(net, win, classes=False):
    """None, or a reason why net cannot be placed in win, found without any SAT call: some node has no tile it
    can stand on (static rule), or the 3-way splitters (pairwise unrelated ones never adjacent) and then all nodes
    (one per tile) cannot be given distinct tiles. Sound: every check is a relaxation of the placement model."""
    from .model import Model
    if _special(win):
        return None
    mod = Model(win, flows=False)
    if classes:
        from .netplace import classify
        C = classify(net)
        cls = C['cls']
        nodes, counts = [], []
        for (kind, ins, outs), cnt in C['types'].items():
            nodes.append((kind, list(ins), list(outs)))
            counts.append(cnt)
        src, snk = {cls[e] for e in net['src']}, {cls[e] for e in net['snk']}
    else:
        nodes = [('S', [a], list(b)) for a, b in net['split']] + [('M', list(a), [b]) for a, b in net['merge']]
        counts = [1] * len(nodes)
        src, snk = set(net['src']), set(net['snk'])
    al = static_allowed(mod, win, nodes, src, snk)
    tiles = {j: [p for p in mod.cells if al[p, j]] for j in range(len(nodes))}
    for j, ts in tiles.items():
        if not ts:
            return 'node %d %s has no tile' % (j, nodes[j][0])
    # copies: expand types into instances
    inst = [j for j in range(len(nodes)) for _ in range(counts[j])]
    three = [i for i, j in enumerate(inst) if nodes[j][0] == 'S' and len(nodes[j][2]) == 3]
    adj = lambda p, q: abs(p[0] - q[0]) + abs(p[1] - q[1]) == 1
    import networkx as nx

    def matching_ok(fixed):
        g = nx.Graph()
        rest = [i for i in range(len(inst)) if i not in fixed]
        used = set(fixed.values())
        for i in rest:
            for p in tiles[inst[i]]:
                if p not in used:
                    g.add_edge(('n', i), ('t', p))
        if not rest:
            return True
        mt = nx.bipartite.maximum_matching(g, top_nodes=[('n', i) for i in rest if ('n', i) in g])
        return sum(1 for i in rest if ('n', i) in mt) == len(rest)
    budget = [20000]

    def rec(k, fixed):
        budget[0] -= 1
        if budget[0] < 0:
            return True                      # give up: no verdict
        if k == len(three):
            return matching_ok(fixed)
        i = three[k]
        for p in tiles[inst[i]]:
            if p in fixed.values():
                continue
            if any(adj(p, fixed[i2]) and not related(nodes[inst[i]], nodes[inst[i2]]) for i2 in fixed):
                continue
            fixed[i] = p
            if rec(k + 1, fixed):
                return True
            del fixed[i]
        return False
    if not rec(0, {}):
        return '3-way splitters / nodes cannot get distinct allowed tiles'
    return None
