"""The exact layout model: a W x H window of tiles, solved with OR-Tools CP-SAT.

Every tile holds nothing or one piece (see pieces.py). Two tiles are linked when one pushes into the other and the
other accepts from that side; an underground entrance is linked to the first exit of the same direction within reach.
Flows are integers: one full belt = D units, one counter per commodity (an input lane, or a mixture). On every link
the commodities add up to at most one belt, and at least one unit flows (a link that carries nothing would be a piece
that isn't needed or a belt that backs up). Every tile passes on exactly what it gets. A splitter sends the same
amount of every commodity through each of its links, which is what the game does once the network has settled
(it alternates between the sides that accept).

Everything that crosses the window boundary is a port. Ports are keyed by (tile, d): the tile on the edge and the
direction d that points out of the window across that edge.
    ('in', vec)     an outside piece pushes vec into the tile from that side; the tile must accept it
    ('out', vec)    the tile must push exactly vec across that edge
    'deny_in'       something outside could push in from there: the tile must not accept from that side
    'deny_out'      something outside accepts from there: the tile must not push across
    (default)       nothing outside: belts and underground exits may not face that edge
Blocked tiles (machines, other layouts) behave like the outside: ports may sit on their edges too.

Windows cut out of an existing layout (improve.py) can also have tunnels crossing their edge:
    tun_in[(q, d)] = vec       an entrance at q outside sends vec; its exit must be the first one inside in reach
    tun_out[(q, d)] = vec      an exit at q outside must get vec from entrances inside (whose first exit it is)
    no_ug = {(p, d)}           an outside tunnel runs under p in direction d: no same-direction ends at p
"""
import time
from ortools.sat.python import cp_model

from .pieces import DIRS, UG_REACH, MIRROR_Y, MIRROR_X


class Window:
    def __init__(self, W, H, ncom, D, ports=None):
        self.W, self.H, self.ncom, self.D = W, H, ncom, D
        self.ports = dict(ports or {})
        self.port_options = []      # groups of optional port runs: [[{(tile, d): ('in'|'out', vec)}, ...], ...]
                                    # exactly one option of each group is used (free I/O positions)
        self.blocked = set()        # tiles that can't hold pieces
        self.fixed = {}             # tile -> code (or None for empty) forced
        self.hint = {}              # tile -> code, a starting point for the search
        self.tun_in, self.tun_out, self.no_ug = {}, {}, set()
        self.symmetry = None        # None, 'y' (top/bottom mirror) or 'x' (left/right mirror)
        self.quasi = 0              # with symmetry: up to this many tiles in the middle row(s) may face another
                                    # way than their mirror image (1 = one turned splitter, the usual quasi)
        self.closed_edges = False   # splitters may not face a window edge that has no port
        self.clear_col = None       # a column that must become deletable: only west belts (no side inputs) or
                                    # empty tiles that would not join their neighbours once the column is gone
        self.max_splitters = None
        self.allow_underground = True

    def inside(self, p):
        return 0 <= p[0] < self.W and 0 <= p[1] < self.H

    def mirror(self, p, d=None):
        x, y = p
        if self.symmetry == 'y':
            q = (x, self.H - 1 - y)
            return q if d is None else (q, MIRROR_Y[d])
        q = (self.W - 1 - x, y)
        return q if d is None else (q, MIRROR_X[d])


class Model:
    """Builds the CP-SAT model of a window. After solving, grid(solver) reads the layout back."""

    def __init__(self, win, objective=False, weights=None, turn_weight=0, flows=True):
        self.win = win
        m = self.m = cp_model.CpModel()
        W, H, D, nc = win.W, win.H, win.D, win.ncom
        cells = self.cells = [(x, y) for x in range(W) for y in range(H) if (x, y) not in win.blocked]
        cellset = set(cells)
        free = lambda p: p in cellset
        add = lambda p, d, n=1: (p[0] + n * DIRS[d][0], p[1] + n * DIRS[d][1])
        kinds = 'BS' + ('IO' if win.allow_underground else '')

        # ---- piece choice per tile
        X = self.X = {}
        for p in cells:
            lits = [m.NewBoolVar('E%s' % (p,))]
            X[p, 'E'] = lits[0]
            for k in kinds:
                for o in range(4):
                    X[p, k + str(o)] = m.NewBoolVar('%s%d%s' % (k, o, p))
                    lits.append(X[p, k + str(o)])
            m.AddExactlyOne(lits)
            if p in win.fixed:
                m.Add(X[p, win.fixed[p] or 'E'] == 1)

        def V(p, k, o):
            return X.get((p, k + str(o % 4)))

        nz = lambda lst: [v for v in lst if v is not None]

        def pushers(p, d):
            """literals for 'the piece at p pushes in direction d'"""
            return nz([V(p, 'B', d), V(p, 'S', d), V(p, 'S', d + 1), V(p, 'S', d + 3), V(p, 'O', d)])

        def acceptors(q, d):
            """literals for 'the piece at q accepts items moving in direction d'"""
            return nz([V(q, 'B', o) for o in range(4) if o != (d + 2) % 4] + [V(q, 'S', d), V(q, 'I', d)])

        for (p, d) in win.no_ug:
            for v in nz([V(p, 'I', d), V(p, 'O', d)]):
                m.Add(v == 0)

        # ---- links
        links = self.links = []           # (from tile or None, to tile, literal, kind)
        lin = {p: [] for p in cells}
        lout = {p: [] for p in cells}

        def new_link(p, q, lit, kind):
            links.append((p, q, lit, kind))
            if p is not None:
                lout[p].append(len(links) - 1)
            if q is not None:
                lin[q].append(len(links) - 1)
            return len(links) - 1

        for p in cells:
            for d in range(4):
                q = add(p, d)
                if not free(q):
                    continue
                push, acc = pushers(p, d), acceptors(q, d)
                l = m.NewBoolVar('')
                m.Add(l <= sum(push))
                m.Add(l <= sum(acc))
                m.Add(l >= sum(push) + sum(acc) - 1)
                new_link(p, q, l, 'push')
                for v in nz([V(p, 'B', d), V(p, 'O', d)]):
                    m.AddImplication(v, l)          # a belt or exit must hand its items on

        cx = win.clear_col
        if cx is not None:
            for y in range(H):
                p = (cx, y)
                if p not in cellset:
                    continue
                for k, v in X.items():
                    if k[0] == p and k[1] not in ('E', 'B1'):
                        m.Add(v == 0)
                for a in pushers((cx + 1, y), 1):
                    for b in acceptors((cx - 1, y), 1):
                        m.AddBoolOr([X[p, 'E'].Not(), a.Not(), b.Not()])
            for p, q, l, kind in links:
                if p is not None and q is not None and p[0] == q[0] == cx:
                    m.Add(l == 0)               # no side inputs inside the column

        tin_vec, tout_links = {}, {}
        if win.allow_underground:
            feeds = {(q, d): [] for q in cells for d in range(4)}
            for p in cells:
                for d in range(4):
                    ent = V(p, 'I', d)
                    pairs = []
                    for n in range(1, UG_REACH + 1):
                        q = add(p, d, n)
                        between = nz([V(add(p, d, j), 'O', d) for j in range(1, n) if free(add(p, d, j))])
                        if not win.inside(q):
                            if (q, d) in win.tun_out:   # an exit outside the window, kept as it is
                                pk = m.NewBoolVar('')
                                m.AddImplication(pk, ent)
                                for b in between:
                                    m.AddImplication(pk, b.Not())
                                m.AddBoolOr([ent.Not()] + between).OnlyEnforceIf(pk.Not())
                                tout_links.setdefault((q, d), []).append(new_link(p, None, pk, 'tout'))
                                pairs.append(pk)
                            break
                        if not free(q):
                            continue
                        pk = m.NewBoolVar('')
                        m.AddImplication(pk, ent)
                        m.AddImplication(pk, V(q, 'O', d))
                        for b in between:
                            m.AddImplication(pk, b.Not())
                        m.AddBoolOr([ent.Not(), V(q, 'O', d).Not()] + between).OnlyEnforceIf(pk.Not())
                        feeds[q, d].append(new_link(p, q, pk, 'ug'))
                        pairs.append(pk)
                    if pairs:
                        m.Add(sum(pairs) == 1).OnlyEnforceIf(ent)
                    else:
                        m.Add(ent == 0)
            for (q0, d), vec in win.tun_in.items():
                cand = []
                for n in range(1, UG_REACH + 1):
                    q = add(q0, d, n)
                    if not free(q):
                        continue
                    between = nz([V(add(q0, d, j), 'O', d) for j in range(1, n) if free(add(q0, d, j))])
                    pk = m.NewBoolVar('')
                    m.AddImplication(pk, V(q, 'O', d))
                    for b in between:
                        m.AddImplication(pk, b.Not())
                    m.AddBoolOr([V(q, 'O', d).Not()] + between).OnlyEnforceIf(pk.Not())
                    i = new_link(None, q, pk, 'tin')
                    feeds[q, d].append(i)
                    tin_vec[i] = vec
                    cand.append(pk)
                m.Add(sum(cand) == 1)
            for (q, d), fe in feeds.items():
                if fe:
                    m.Add(sum(links[i][2] for i in fe) >= 1).OnlyEnforceIf(V(q, 'O', d))
                else:
                    m.Add(V(q, 'O', d) == 0)

        # ---- ports (window edges and edges of blocked tiles)
        inflow = {p: [0] * nc for p in cells}
        outflow = {p: [0] * nc for p in cells}
        self.port_active = {}             # (tile, d) -> True or literal
        optional = {}                     # (tile, d) -> [(literal, kind, vec)]
        self.option_lits = []
        for group in win.port_options:
            lits = [m.NewBoolVar('opt') for _ in group]
            m.AddExactlyOne(lits)
            self.option_lits.append(lits)
            for lit, opt in zip(lits, group):
                for key, (kind, vec) in opt.items():
                    optional.setdefault(key, []).append((lit, kind, vec))
        splitter_edge = {}                # (tile, d) -> literal or True when a port edge is used (outputs)
        for p in cells:
            for d in range(4):
                if free(add(p, d)):
                    continue
                back = (d + 2) % 4        # items crossing inwards move in direction back
                spec = win.ports.get((p, d))
                if (p, d) in optional:
                    opts = optional[p, d]
                    if spec is not None:            # a fixed port already uses this edge
                        for l, _, _ in opts:
                            m.Add(l == 0)
                    else:
                        u_in, u_out = m.NewBoolVar(''), m.NewBoolVar('')
                        m.Add(u_in == sum(l for l, k, _ in opts if k == 'in'))
                        m.Add(u_out == sum(l for l, k, _ in opts if k == 'out'))
                        m.Add(u_in + u_out <= 1)        # free groups never share a lane
                        used = m.NewBoolVar('')
                        m.Add(used == u_in + u_out)
                        self.port_active[p, d] = used
                        m.AddBoolOr(pushers(p, d)).OnlyEnforceIf(u_out)
                        for v in nz([V(p, 'B', d), V(p, 'O', d)]):
                            m.AddImplication(v, u_out)  # belts may only face the edge at an output
                        m.AddBoolOr(acceptors(p, back)).OnlyEnforceIf(u_in)
                        for l, k, vec in opts:
                            if k == 'out':
                                outflow[p] = [a + c * l for a, c in zip(outflow[p], vec)]
                            else:
                                inflow[p] = [a + c * l for a, c in zip(inflow[p], vec)]
                        splitter_edge[p, d] = u_out
                        if win.closed_edges:            # no splitter face out of an unused edge
                            for v in nz([V(p, 'S', o) for o in (d, d + 1, d + 3)]):
                                m.AddImplication(v, u_out)
                        continue
                kind = spec[0] if isinstance(spec, tuple) else spec
                if kind == 'out':
                    m.AddBoolOr(pushers(p, d))
                    outflow[p] = [a + c for a, c in zip(outflow[p], spec[1])]
                    self.port_active[p, d] = True
                    splitter_edge[p, d] = True
                else:
                    for v in nz([V(p, 'B', d), V(p, 'O', d)]):
                        m.Add(v == 0)
                    if kind == 'deny_out' or (win.closed_edges and kind is None):
                        for v in nz([V(p, 'S', o) for o in (d, d + 1, d + 3)]):
                            m.Add(v == 0)
                if kind == 'in':
                    m.AddBoolOr(acceptors(p, back))
                    inflow[p] = [a + c for a, c in zip(inflow[p], spec[1])]
                    self.port_active[p, d] = True
                elif kind == 'deny_in':
                    for v in acceptors(p, back):
                        m.Add(v == 0)

        self.lin, self.lout, self.splitter_edge, self.optional = lin, lout, splitter_edge, optional
        self.inflow, self.outflow = inflow, outflow
        if flows:
            self._flows(win, links, lin, lout, inflow, outflow, splitter_edge, tin_vec, tout_links, V, nz)

        # ---- symmetry
        if win.symmetry:
            ax = win.symmetry
            table = MIRROR_Y if ax == 'y' else MIRROR_X
            n = H if ax == 'y' else W
            middle = {(n - 1) // 2, n // 2} if win.quasi else set()
            turned = []                         # quasi: mirror pairs in the middle allowed to differ in direction
            for p in cells:
                q = win.mirror(p)
                if q < p:
                    continue
                if q not in cellset:
                    m.Add(X[p, 'E'] == 1)
                    continue
                m.Add(X[p, 'E'] == X[q, 'E'])
                t = None
                if (p[1] if ax == 'y' else p[0]) in middle:
                    t = m.NewBoolVar('turned')
                    turned.append(t)
                    for k in kinds:             # still the same kind of piece, maybe facing another way
                        m.Add(sum(X[p, k + str(o)] for o in range(4)) == sum(X[q, k + str(o)] for o in range(4)))
                for k in kinds:
                    for o in range(4):
                        c = m.Add(X[p, k + str(o)] == X[q, k + str(table[o])])
                        if t is not None:
                            c.OnlyEnforceIf(t.Not())
            if turned:
                m.Add(sum(turned) <= int(win.quasi))
            as_int = lambda v: 1 if v is True else 0 if v is None else v
            for (p, d), a in self.port_active.items():     # ports are used in mirror pairs
                if (p[1] if ax == 'y' else p[0]) in middle and d in ((1, 3) if ax == 'y' else (0, 2)):
                    continue                                # quasi: a port on the middle row may be alone
                b = self.port_active.get(win.mirror(p, d))
                if a is True and b is True:
                    continue
                m.Add(as_int(a) == as_int(b))

        # ---- optional limits and the objective
        splitters = [X[p, 'S%d' % o] for p in cells for o in range(4)]
        self.n_splitters = sum(splitters)
        if win.max_splitters is not None:
            m.Add(self.n_splitters <= win.max_splitters)
        for (x, y) in cells:                    # a 2x2 ring of belts only feeds itself: never part of a layout
            ring = [(x, y), (x + 1, y), (x + 1, y + 1), (x, y + 1)]
            if all(r in cellset for r in ring):
                m.AddBoolOr([X[ring[0], 'B3'].Not(), X[ring[1], 'B2'].Not(), X[ring[2], 'B1'].Not(), X[ring[3], 'B0'].Not()])
                m.AddBoolOr([X[ring[0], 'B2'].Not(), X[ring[3], 'B3'].Not(), X[ring[2], 'B0'].Not(), X[ring[1], 'B1'].Not()])
        if objective:
            # fewest pieces; a belt is preferred to a splitter or tunnel end when both work
            tw = weights or {'B': 2, 'S': 5, 'I': 9, 'O': 9}
            cost = sum(tw[k[0]] * v for (p, k), v in X.items() if k != 'E')
            if turn_weight:                     # tie-break: straight runs, fewest turns
                turns = []
                for p in cells:
                    for d in range(4):
                        q = add(p, d)
                        if not free(q):
                            continue
                        for e in range(4):
                            if e in (d, (d + 2) % 4):
                                continue
                            for sv in nz([V(p, 'B', d), V(p, 'O', d)]):
                                t = m.NewBoolVar('')
                                m.Add(t >= sv + V(q, 'B', e) - 1)
                                turns.append(t)
                cost = cost * 1000 + turn_weight * sum(turns)
            m.Minimize(cost)
        for p, k in win.hint.items():
            if (p, k or 'E') in X:
                m.AddHint(X[p, k or 'E'], 1)

    def _flows(self, win, links, lin, lout, inflow, outflow, splitter_edge, tin_vec, tout_links, V, nz):
        m, X, cells, D, nc = self.m, self.X, self.cells, win.D, win.ncom
        # ---- integer flows
        F = self.F = {}
        for i, (p, q, l, kind) in enumerate(links):
            tot = []
            for c in range(nc):
                f = m.NewIntVar(0, D, '')
                m.Add(f == 0).OnlyEnforceIf(l.Not())
                F[i, c] = f
                tot.append(f)
            m.Add(sum(tot) <= D)
            m.Add(sum(tot) >= 1).OnlyEnforceIf(l)
            if kind == 'tin':
                for c in range(nc):
                    m.Add(F[i, c] == tin_vec[i][c]).OnlyEnforceIf(l)
        for key, vec in win.tun_out.items():
            ls = tout_links.get(key, [])
            if not ls:
                m.Add(0 == 1)               # nothing inside can reach that exit
                continue
            for c in range(nc):
                m.Add(sum(F[i, c] for i in ls) == vec[c])
        for p in cells:
            for c in range(nc):
                m.Add(inflow[p][c] + sum(F[i, c] for i in lin[p]) == outflow[p][c] + sum(F[i, c] for i in lout[p]))
            for i in lin[p] + lout[p]:
                m.AddImplication(X[p, 'E'], links[i][2].Not())
            sp = nz([V(p, 'S', o) for o in range(4)])
            if not sp:
                continue
            outs = [i for i in lout[p] if links[i][3] == 'push']
            for c in range(nc):                 # equal share through every used output of a splitter
                share = m.NewIntVar(0, D, '')
                for i in outs:
                    for s in sp:
                        m.Add(F[i, c] == share).OnlyEnforceIf([s, links[i][2]])
                for d in range(4):
                    if (p, d) in splitter_edge:
                        cond = [] if splitter_edge[p, d] is True else [splitter_edge[p, d]]
                        for s in nz([V(p, 'S', o) for o in (d, d + 1, d + 3)]):
                            m.Add(share == outflow[p][c]).OnlyEnforceIf([s] + cond)


    def grid(self, solver):
        return {p: k for (p, k), v in self.X.items() if k != 'E' and solver.Value(v)}

    def chosen_options(self, solver):
        return [next(j for j, l in enumerate(lits) if solver.Value(l)) for lits in self.option_lits]


class _Progress(cp_model.CpSolverSolutionCallback):
    def __init__(self, model, on_solution):
        super().__init__()
        self.model, self.on_solution, self.t0 = model, on_solution, time.time()

    def on_solution_callback(self):
        g = {p: k for (p, k), v in self.model.X.items() if k != 'E' and self.Value(v)}
        self.on_solution(g, self.ObjectiveValue(), time.time() - self.t0)


def solve(win, timeout=60.0, workers=4, objective=False, on_solution=None, log=False, **kw):
    """Solve one window. Returns dict(status, time, grid (if found), pieces, options).
    status: OPTIMAL / FEASIBLE (found), INFEASIBLE (proven impossible), UNKNOWN (ran out of time).
    on_solution(grid, objective, seconds) is called for every improving solution found."""
    t = time.time()
    mod = Model(win, objective=objective, **kw)
    sv = cp_model.CpSolver()
    sv.parameters.max_time_in_seconds = timeout
    sv.parameters.num_workers = workers
    sv.parameters.log_search_progress = log
    cb = _Progress(mod, on_solution) if on_solution else None
    st = sv.Solve(mod.m, cb)
    res = dict(status=sv.StatusName(st), time=time.time() - t, W=win.W, H=win.H)
    if st in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        res['grid'] = mod.grid(sv)
        res['pieces'] = len(res['grid'])
        res['options'] = mod.chosen_options(sv)
    return res
