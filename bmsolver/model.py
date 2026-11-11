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

import json as _json
import os as _os
with open(_os.path.join(_os.path.dirname(__file__), 'machines.json')) as _f:
    MACHINES = _json.load(_f)       # machine geometry per building id (tools/machine_geometry.mjs)


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
        self.sym_free = set()       # with symmetry: tiles left out of it (e.g. an output corner that may differ)
        self.quasi = 0              # with symmetry: up to this many mirror pairs in the middle row(s) may differ:
                                    # turned another way, or a splitter facing a belt (1 = the usual quasi)
        self.closed_edges = False   # splitters may not face a window edge that has no port
        self.clear_col = None       # a column that must become deletable: only west belts (no side inputs) or
                                    # empty tiles that would not join their neighbours once the column is gone
        self.max_splitters = None
        self.allow_underground = True
        self.arm_cap = None         # robotic arms allowed when set: most flow units one arm moves (game: 15/30/60/90
                                    # items/min at tier 1-4); an arm takes from the tile behind it and puts in front
        self.arm_tier = 4
        self.arm_drop_alone = True  # a belt piece an arm drops onto takes no other flow (see _arm_flows)
        self.line_ports = set()     # (tile, d) out ports where an arm line may go on (into the next copy of a
                                    # tiled module, where more arms take the rest)
        self.cuts = None            # experiment: None, 'card' (enough links cross every row/column line for the
                                    # net flow), 'flow' (per-item net flow across every line), 'both'
        self.machines = []          # machine types to place: dicts {bid, count, need: vec, make: vec, rots: None|[o]}
                                    # need/make: commodity units one machine takes in / puts out (exactly)

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

    def __init__(self, win, objective=False, weights=None, turn_weight=0, flows=True, pure_feeds=True):
        self.win = win
        m = self.m = cp_model.CpModel()
        W, H, D, nc = win.W, win.H, win.D, win.ncom
        cells = self.cells = [(x, y) for x in range(W) for y in range(H) if (x, y) not in win.blocked]
        cellset = set(cells)
        free = lambda p: p in cellset
        add = lambda p, d, n=1: (p[0] + n * DIRS[d][0], p[1] + n * DIRS[d][1])
        kinds = 'BS' + ('IO' if win.allow_underground else '') + ('A' if win.arm_cap else '')

        # ---- piece choice per tile
        X = self.X = {}
        for p in cells:
            lits = [m.NewBoolVar('E%s' % (p,))]
            X[p, 'E'] = lits[0]
            for k in kinds:
                for o in range(4):
                    X[p, k + str(o)] = m.NewBoolVar('%s%d%s' % (k, o, p))
                    lits.append(X[p, k + str(o)])
            if win.machines:
                X[p, 'M'] = m.NewBoolVar('M%s' % (p,))      # part of a machine
                lits.append(X[p, 'M'])
            m.AddExactlyOne(lits)
            if p in win.fixed:
                m.Add(X[p, win.fixed[p] or 'E'] == 1)

        def V(p, k, o):
            return X.get((p, k + str(o % 4)))

        nz = lambda lst: [v for v in lst if v is not None]

        # ---- machines: a placement literal per (type, anchor, rotation) that fits; ports from the game's geometry
        self.placements = []              # (type index, anchor, o, literal, tiles, inputs [(tile, dir)], output (tile, dir))
        mpush, macc = {}, {}
        if win.machines:
            cover = {p: [] for p in cells}
            for ti, mt in enumerate(win.machines):
                geo = MACHINES[str(mt['bid'])]
                lits_t = []
                for r in geo['rots']:
                    if mt.get('rots') is not None and r['o'] not in mt['rots']:
                        continue
                    for ax in range(-4, W + 4):
                        for ay in range(-4, H + 4):
                            tiles = [(ax + dx, ay + dy) for dx, dy in r['tiles']]
                            if not all(t in cellset and t not in win.fixed for t in tiles):
                                continue
                            y = m.NewBoolVar('Y%d' % ti)
                            ins = [((ax + a, ay + b), d) for a, b, d in r['inputs']]
                            outs = [((ax + a, ay + b), d) for a, b, d in r['outputs']]
                            if win.closed_edges and any(not win.inside(add(t, d)) and (t, d) not in win.ports
                                                        for t, d in outs):
                                continue        # its output would push into whatever is beyond the edge
                            self.placements.append((ti, (ax, ay), r['o'], y, tiles, ins, outs))
                            lits_t.append(y)
                            for t in tiles:
                                cover[t].append(y)
                            for t, d in outs:
                                mpush.setdefault((t, d), []).append(y)
                            for t, d in ins:
                                macc.setdefault(t, []).append((y, d))
                m.Add(sum(lits_t) == mt['count'])
            for p in cells:
                m.Add(sum(cover[p]) == X[p, 'M'])

        def pushers(p, d):
            """literals for 'the piece at p pushes in direction d'"""
            return nz([V(p, 'B', d), V(p, 'S', d), V(p, 'S', d + 1), V(p, 'S', d + 3), V(p, 'O', d)]) + mpush.get((p, d), [])

        def acceptors(q, d):
            """literals for 'the piece at q accepts items moving in direction d'"""
            return nz([V(q, 'B', o) for o in range(4) if o != (d + 2) % 4] + [V(q, 'S', d), V(q, 'I', d)]) + \
                [y for y, s in macc.get(q, []) if d == (s + 2) % 4]    # a machine input only takes items moving
                                                                         # straight in across its side (World.findOutput)

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
                    if not win.arm_cap:             # a belt or exit must hand its items on (with arms a belt
                        m.AddImplication(v, l)      # may dead-end: arms then take everything that reaches it)

        # ---- robotic arms: take from behind (a belt piece or any machine tile, World.armTick), put in front (a belt
        # piece or a machine tile, World.armDrop); one item kind each (the arm's filter)
        self.arm_links = []
        if win.arm_cap:
            carry = lambda q: nz([V(q, k, o) for k in 'BSIO' for o in range(4)]) + ([X[q, 'M']] if (q, 'M') in X else [])
            for p in cells:
                for o in range(4):
                    a = V(p, 'A', o)
                    src, dst = add(p, (o + 2) % 4), add(p, o)
                    if not (free(src) and free(dst)):
                        m.Add(a == 0)
                        continue
                    m.AddBoolOr(carry(src)).OnlyEnforceIf(a)
                    m.AddBoolOr([v for v in carry(dst) if v not in nz([V(dst, 'S', k) for k in range(4)])]).OnlyEnforceIf(a)
                    # (no drops onto splitters: which segment takes the item depends on the game's quadtree order)
                    self.arm_links.append((p, new_link(src, dst, a, 'arm')))

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
        self.line_out = {}                # tile -> flow leaving through line ports (arm lines that go on outside)
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
                                if (p, d) in win.line_ports:
                                    self.line_out[p] = self.line_out.get(p, 0) + sum(vec) * l
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
                    if (p, d) in win.line_ports:
                        self.line_out[p] = self.line_out.get(p, 0) + sum(spec[1])
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

        # an arm sees a belt piece only through a flagged belt segment (World.buildSegments / segAt): a lone belt
        # (nothing in or out), an underground entrance nothing feeds (its tunnel segment is unflagged), an exit that
        # hands nothing on, or a splitter nothing feeds (the arm would see one output segment only, so items it
        # drops go to that output alone) has none
        def port_lits(q, want):
            out = []
            for d in range(4):
                spec = win.ports.get((q, d))
                if spec is not None and (spec[0] if isinstance(spec, tuple) else spec) == want:
                    return None                         # a fixed port: always connected
                out += [l for l, k, _ in optional.get((q, d), []) if k == want]
            return out
        for p, i in self.arm_links:
            a = links[i][2]
            for q in (links[i][0], links[i][1]):
                fin, fout = port_lits(q, 'in'), port_lits(q, 'out')
                if fin is not None:
                    fin += [links[j][2] for j in lin[q] if links[j][3] in ('push', 'ug', 'tin')]
                if fout is not None:
                    fout += [links[j][2] for j in lout[q] if links[j][3] == 'push']
                for k, need in (('B', None if fin is None or fout is None else fin + fout),
                                ('I', fin), ('S', fin), ('O', fout)):
                    if need is None:
                        continue
                    for o in range(4):
                        v = V(q, k, o)
                        if v is not None:
                            m.AddBoolOr(need).OnlyEnforceIf([a, v])
        self.lin, self.lout, self.splitter_edge, self.optional = lin, lout, splitter_edge, optional
        self.optional_ports = optional
        self._machine_links = (lin, lout, inflow, outflow)
        self.inflow, self.outflow = inflow, outflow
        if flows:
            self._flows(win, links, lin, lout, inflow, outflow, splitter_edge, tin_vec, tout_links, V, nz)
            if self.placements:
                self._machine_flows(win, links, lin, lout, inflow, outflow, pure_feeds)
            if self.arm_links:
                self._arm_flows(win)
        if win.cuts and not self.placements and not win.tun_in and not win.tun_out:
            self._cuts(win, links, inflow, outflow, win.cuts, flows)

        # ---- symmetry
        if win.symmetry:
            ax = win.symmetry
            table = MIRROR_Y if ax == 'y' else MIRROR_X
            n = H if ax == 'y' else W
            middle = {(n - 1) // 2, n // 2} if win.quasi else set()
            turned = []                         # quasi: mirror pairs in the middle allowed to differ in direction
            for p in cells:
                q = win.mirror(p)
                if q < p or p in win.sym_free or q in win.sym_free:
                    continue
                if q not in cellset:
                    m.Add(X[p, 'E'] == 1)
                    continue
                m.Add(X[p, 'E'] == X[q, 'E'])
                t = None
                if (p[1] if ax == 'y' else p[0]) in middle:
                    t = m.NewBoolVar('turned')
                    turned.append(t)
                    # still the same kind of piece, maybe facing another way; or a splitter whose mirror image
                    # is a plain belt (the wiki 1:even splitters: the input splitter's mirror tile is a belt)
                    for k in kinds.replace('S', '').replace('B', ''):
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
                if p in win.sym_free or win.mirror(p) in win.sym_free:
                    continue
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
            tw = weights or {'B': 2, 'S': 5, 'I': 9, 'O': 9, 'A': 9}
            cost = sum(tw[k[0]] * v for (p, k), v in X.items() if k[0] in tw)     # machines are fixed in number
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
                ct = m.Add(inflow[p][c] + sum(F[i, c] for i in lin[p]) == outflow[p][c] + sum(F[i, c] for i in lout[p]))
                if (p, 'M') in X:
                    ct.OnlyEnforceIf(X[p, 'M'].Not())
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


    def _cuts(self, win, links, inflow, outflow, mode, flows):
        """Redundant constraints across every line between two columns (and two rows): the items entering the window
        on one side minus those leaving there must cross the line. 'flow': per item, as an equality of link flows.
        'card': at least ceil(net / D) links cross the line in the needed direction (pure clauses, needs the net to
        be fixed, i.e. no free port positions on that side)."""
        m, D, nc = self.m, win.D, win.ncom
        for axis, n in ((0, win.W), (1, win.H)):
            for x in range(n - 1):              # line between x and x+1 on this axis
                side = [p for p in self.cells if p[axis] > x]
                net = [sum(inflow[p][c] for p in side) - sum(outflow[p][c] for p in side) for c in range(nc)]
                fwd = [i for i, (p, q, l, k) in enumerate(links) if p[axis] > x >= q[axis]]   # from far to near
                back = [i for i, (p, q, l, k) in enumerate(links) if q[axis] > x >= p[axis]]
                if mode in ('flow', 'both') and flows:
                    for c in range(nc):
                        m.Add(sum(self.F[i, c] for i in fwd) - sum(self.F[i, c] for i in back) == net[c])
                if mode in ('card', 'both'):
                    # net total across the line: fixed ports plus, per free-port group, its smallest / largest share
                    sgn = lambda kind: 1 if kind == 'in' else -1 if kind == 'out' else 0
                    on = lambda t: t[axis] > x
                    fixed = sum(sgn(sp[0] if isinstance(sp, tuple) else sp) * sum(sp[1]) for (t, d), sp in win.ports.items()
                                if on(t) and isinstance(sp, tuple))
                    lo = hi = fixed
                    for group in win.port_options:
                        shares = [sum(sgn(k) * sum(v) for (t, d), (k, v) in opt.items() if on(t)) for opt in group]
                        lo, hi = lo + min(shares), hi + max(shares)
                    if lo > 0:
                        m.Add(sum(links[i][2] for i in fwd) >= -(-lo // D))
                    elif hi < 0:
                        m.Add(sum(links[i][2] for i in back) >= -(-(-hi) // D))

    def _machine_flows(self, win, links, lin, lout, inflow, outflow, pure_feeds=True):
        """A placed machine takes in exactly need and puts out exactly make, summed over its port tiles.

        pure_feeds: every belt link into a machine carries one item kind. The game stops a whole belt segment when
        its front item is refused (World.accept: a machine holds at most one craft of each ingredient), so a mixed
        belt into a machine stalls as soon as one ingredient fills up; averaged flows can't see that."""
        m, F, nc = self.m, self.F, win.ncom
        if pure_feeds and nc > 1:
            done = set(i for p, i in self.arm_links)       # arms carry one kind anyway
            for ti, a, o, y, tiles, ins, outs in self.placements:
                for t, d in ins:
                    for i in lin[t]:
                        if i in done:
                            continue
                        done.add(i)
                        bs = [m.NewBoolVar('') for c in range(nc)]
                        for c in range(nc):
                            m.Add(F[i, c] == 0).OnlyEnforceIf(bs[c].Not())
                        m.AddAtMostOne(bs)
        # a machine hands its product to its output port's belt every tick it can (Machine.tick -> pushOut, before
        # arms move), so arms taking from the same machine get only what the belt refuses: no fixed share. A machine
        # puts out through its port or through arms, never both.
        arm_from = {}
        for p, i in self.arm_links:
            arm_from.setdefault(links[i][0], []).append(links[i][2])
        for ti, a, o, y, tiles, ins, outs in self.placements:
            pushes = [links[i][2] for t, d in outs for i in lout[t] if links[i][3] == 'push']
            arms = [l for t in tiles for l in arm_from.get(t, [])]
            for lp in pushes:
                for la in arms:
                    m.AddBoolOr([lp.Not(), la.Not(), y.Not()])
        # a machine marked partial may run underfilled: it takes fill/G of its need and makes fill/G of its make
        # (G = gcd of its rates, so every share stays a whole number of flow units); its output is never blocked,
        # so it settles at its input rate
        from math import gcd
        self.fill = {}
        for k, (ti, a, o, y, tiles, ins, outs) in enumerate(self.placements):
            mt = win.machines[ti]
            need, make = list(mt['need']), list(mt['make'])
            if mt.get('partial'):
                G = 0
                for v in need + make:
                    G = gcd(G, v)
                f = m.NewIntVar(1, G, 'fill')
                self.fill[k] = (f, G)
                need = [v // G * f for v in need]
                make = [v // G * f for v in make]
            in_tiles = out_tiles = tiles          # ports plus arms, which reach any tile of a machine
            for c in range(nc):
                m.Add(sum(inflow[t][c] for t in in_tiles) + sum(F[i, c] for t in in_tiles for i in lin[t])
                      == need[c]).OnlyEnforceIf(y)
                m.Add(sum(outflow[t][c] for t in out_tiles) + sum(F[i, c] for t in out_tiles for i in lout[t])
                      == make[c]).OnlyEnforceIf(y)

    def _arm_flows(self, win):
        """Arm flow limits, and arm lines: an arm takes items from a belt only as they pass while it is idle, so
        on a belt that carries items on to anything else the split between arm and belt is not a fixed share.
        A belt piece an arm takes from must be on an arm line: everything that reaches it ends in arms (the
        line goes on only into more line pieces and may dead-end; no port, machine or tunnel out of the window)."""
        m, F, nc, X, links = self.m, self.F, win.ncom, self.X, self.links
        L = self.line = {}
        for q in self.cells:
            L[q] = m.NewBoolVar('L')
            # (no splitters: on a line that backs up a splitter's shares follow what each side can take, not halves)
            belt = [X[q, k + str(o)] for k in 'BIO' for o in range(4) if (q, k + str(o)) in X]
            m.AddBoolOr(belt).OnlyEnforceIf(L[q])
            if any(not isinstance(t, int) or t for t in self.outflow[q]):
                m.Add(sum(self.outflow[q]) - self.line_out.get(q, 0) == 0).OnlyEnforceIf(L[q])
        # a lane that goes on into the next copy is an arm line where it leaves: in the last copy it dead-ends
        for (t, d) in win.line_ports:
            if t not in L:
                continue
            if (t, d) in win.ports:
                m.Add(L[t] == 1)
            for l, k, _ in self.optional_ports.get((t, d), []):
                if k == 'out':
                    m.AddImplication(l, L[t])
        # one item kind per line: an item that passes the last arm taking its kind waits at the dead end forever
        # and in a mixed line soon blocks the items behind it
        K = {q: [m.NewBoolVar('K') for c in range(nc)] for q in self.cells}
        for q in self.cells:
            m.AddAtMostOne(K[q])
        for i, (p, q, l, kind) in enumerate(links):
            if kind == 'arm' or p is None:
                continue
            if q is None:
                m.AddImplication(L[p], l.Not())
                continue
            m.AddBoolOr([L[q], l.Not()]).OnlyEnforceIf(L[p])
            # and upstream: a belt or splitter feeding a line is line too (only ports and machine pushes start
            # one). A plain belt feeding a line stalls when the line backs up, and so does anything dropping on it
            m.AddBoolOr([L[p], l.Not()] + ([X[p, 'M']] if (p, 'M') in X else [])).OnlyEnforceIf(L[q])
            for c in range(nc):
                m.Add(F[i, c] == 0).OnlyEnforceIf([L[p], K[p][c].Not()])
                m.AddImplication(K[p][c], K[q][c]).OnlyEnforceIf([L[p], l])
        # no item loop through an arm: arms pick up only part of what passes, so a loop with an arm fills up with
        # circulating items (flows alone allow any amount going round). Levels never drop along a link and rise
        # across every arm.
        R = len(self.cells)
        P = {q: m.NewIntVar(0, R, 'P') for q in self.cells}
        for i, (p, q, l, kind) in enumerate(links):
            if p is None or q is None:
                continue
            m.Add(P[q] >= P[p] + (1 if kind == 'arm' else 0)).OnlyEnforceIf(l)
        # line names: a line takes the index of the tile it starts at (a port or a machine push) and keeps it
        # along every link, so two lines never merge. All arm drops of one item into one machine take from one
        # line: arms take greedily, so a machine fed from two lines draws an unfixed share from each, and a lane
        # that goes on into more copies or machines comes up short downstream
        idx = {q: k for k, q in enumerate(self.cells)}
        N = {q: m.NewIntVar(0, R - 1, 'N') for q in self.cells}
        feeds_in = {q: [] for q in self.cells}
        for i, (p, q, l, kind) in enumerate(links):
            if kind == 'arm' or p is None or q is None:
                continue
            e = m.NewBoolVar('e')
            m.AddBoolAnd([l, L[p]]).OnlyEnforceIf(e)
            m.AddBoolOr([l.Not(), L[p].Not(), e])
            m.Add(N[q] == N[p]).OnlyEnforceIf(e)
            feeds_in[q].append(e)
        for q in self.cells:
            st = m.NewBoolVar('st')
            m.Add(N[q] == idx[q]).OnlyEnforceIf(st)
            m.AddBoolOr([st] + feeds_in[q]).OnlyEnforceIf(L[q])
        self.arm_kind = {}
        drops = []
        for p, i in self.arm_links:
            m.Add(sum(F[i, c] for c in range(nc)) <= win.arm_cap)
            bs = [m.NewBoolVar('') for c in range(nc)]
            for c in range(nc):
                m.Add(F[i, c] == 0).OnlyEnforceIf(bs[c].Not())
            m.AddAtMostOne(bs)
            self.arm_kind[i] = bs
            src, dst, a = links[i][0], links[i][1], links[i][2]
            m.AddBoolOr([L[src]] + ([X[src, 'M']] if (src, 'M') in X else [])).OnlyEnforceIf(a)
            # no drops onto an arm line: it backs up, so the dropping arm waits for gaps and loses rate
            m.AddImplication(a, L[dst].Not())
            for c in range(nc):
                m.AddImplication(bs[c], K[src][c]).OnlyEnforceIf(L[src])
            drops.append((i, src, dst, a, bs))
        # an arm drops only into a gap at its drop point (World.armDrop -> canDrop, else it waits a tick), so onto a
        # belt that carries other items its rate depends on phase. The sim (evenly fed belt, T4 arm): onto a plain
        # belt a 60/min arm drops all 60 at 180/min, 0 at 300/min, and a 90/min arm gets 77 at 120/min. Onto an
        # underground entrance or exit an arm of up to 75/min drops everything at any load up to a full belt, a
        # 90/min arm loses 3-10/min. So: a plain belt piece an arm drops onto takes nothing else (no belt link in,
        # no input port); an arm dropping onto an underground piece moves at most 75/min; one arm per piece.
        if win.arm_drop_alone:
            ug_cap = int(75 * win.D // 480)            # flow units in 75 items/min (a belt = D units = 480/min)
            into = {}
            for i, src, dst, a, bs in drops:
                into.setdefault(dst, []).append((i, a))
            for q, arms in into.items():
                mach = [X[q, 'M'].Not()] if (q, 'M') in X else []
                ugs = [X[q, k + str(o)] for k in 'IO' for o in range(4) if (q, k + str(o)) in X]
                ug = m.NewBoolVar('ug')
                if ugs:
                    m.AddMaxEquality(ug, ugs)
                else:
                    m.Add(ug == 0)
                if len(arms) > 1:
                    m.Add(sum(a for i, a in arms) <= 1).OnlyEnforceIf(mach)
                for i, a in arms:
                    m.Add(sum(F[i, c] for c in range(nc)) <= ug_cap).OnlyEnforceIf([a, ug])
                    plain = [a, ug.Not()] + mach
                    for j in self.lin[q]:
                        if links[j][3] != 'arm':
                            for c in range(nc):
                                m.Add(F[j, c] == 0).OnlyEnforceIf(plain)
                    for v in self.inflow[q]:
                        if isinstance(v, int):
                            if v:
                                m.AddBoolOr([x.Not() for x in plain])
                        else:
                            m.Add(v == 0).OnlyEnforceIf(plain)
        # what arms drop from a line onto belts carries that line's name on to wherever it goes (D = carries
        # greedy drops). Every way of one item into one machine, direct drop or belt, comes from one line
        D = {q: m.NewBoolVar('D') for q in self.cells}
        N2 = {q: m.NewIntVar(0, R - 1, 'N2') for q in self.cells}
        mtile = {}
        for k, (ti, an, o, y, tiles, ins, outs) in enumerate(self.placements):
            for t in tiles:
                mtile.setdefault(t, []).append(k)
        V = {}

        def name_of(k, c):
            if (k, c) not in V:
                V[k, c] = m.NewIntVar(0, R - 1, 'V')
            return V[k, c]
        for i, src, dst, a, bs in drops:
            mach = [X[dst, 'M']] if (dst, 'M') in X else []
            # onto a belt: dst carries the name (a machine tile takes it below instead)
            if mach:
                m.AddImplication(D[dst], X[dst, 'M'].Not())
            m.AddBoolOr([D[dst]] + mach).OnlyEnforceIf([a, L[src]])
            m.Add(N2[dst] == N[src]).OnlyEnforceIf([a, L[src]] + [x.Not() for x in mach])
            for k in mtile.get(dst, []):
                y = self.placements[k][3]
                for c in range(nc):
                    if win.machines[self.placements[k][0]]['need'][c]:
                        m.Add(N[src] == name_of(k, c)).OnlyEnforceIf([y, a, bs[c], L[src]])
        for i, (p, q, l, kind) in enumerate(links):
            if kind == 'arm' or p is None or q is None:
                continue
            if q in mtile:
                for k in mtile[q]:
                    ti, y = self.placements[k][0], self.placements[k][3]
                    for c in range(nc):
                        if not win.machines[ti]['need'][c]:
                            continue
                        g = m.NewBoolVar('g')
                        m.Add(F[i, c] == 0).OnlyEnforceIf(g.Not())
                        m.Add(N2[p] == name_of(k, c)).OnlyEnforceIf([y, l, D[p], g])
                if (q, 'M') in X:
                    m.AddImplication(D[q], X[q, 'M'].Not())
                    m.AddBoolOr([D[q], X[q, 'M']]).OnlyEnforceIf([D[p], l])
                    m.Add(N2[q] == N2[p]).OnlyEnforceIf([D[p], l, X[q, 'M'].Not()])
                    continue
            m.AddImplication(D[p], D[q]).OnlyEnforceIf(l)
            m.Add(N2[q] == N2[p]).OnlyEnforceIf([D[p], l])

    def add_hint(self, r):
        """Search hint from a result dict: its pieces, machines and port options."""
        g = r.get('grid', {})
        for (p, k), v in self.X.items():
            if k not in ('E', 'M'):
                self.m.AddHint(v, int(g.get(p) == k))
        want = set(tuple(x) for x in r.get('machines', []))
        for ti, a, o, y, *_ in self.placements:
            self.m.AddHint(y, int((ti, a, o) in want))
        for lits, j in zip(self.option_lits, r.get('options') or []):
            for jj, l in enumerate(lits):
                self.m.AddHint(l, int(jj == j))

    def arm_filters(self, solver):
        """{arm tile: item index it carries} for the placed arms."""
        out = {}
        for p, i in self.arm_links:
            if solver.Value(self.links[i][2]):
                out[p] = next(c for c, b in enumerate(self.arm_kind[i]) if solver.Value(b))
        return out

    def machines_chosen(self, solver):
        """[(type index, anchor, rotation)] of the placed machines."""
        return [(ti, a, o) for ti, a, o, y, *_ in self.placements if solver.Value(y)]

    def machine_fill(self, solver):
        """{anchor: share of full rate} for placed partial machines that run underfilled."""
        out = {}
        for k, (f, G) in getattr(self, 'fill', {}).items():
            ti, a, o, y = self.placements[k][:4]
            if solver.Value(y) and solver.Value(f) < G:
                out[a] = solver.Value(f) / G
        return out

    def grid(self, solver):
        return {p: k for (p, k), v in self.X.items() if k not in ('E', 'M') and solver.Value(v)}

    def chosen_options(self, solver):
        return [next(j for j, l in enumerate(lits) if solver.Value(l)) for lits in self.option_lits]


class _Progress(cp_model.CpSolverSolutionCallback):
    def __init__(self, model, on_solution):
        super().__init__()
        self.model, self.on_solution, self.t0 = model, on_solution, time.time()

    def on_solution_callback(self):
        g = {p: k for (p, k), v in self.model.X.items() if k != 'E' and self.Value(v)}
        self.on_solution(g, self.ObjectiveValue(), time.time() - self.t0)


def solve(win, timeout=60.0, workers=4, objective=False, on_solution=None, log=False, hint=None, **kw):
    """Solve one window. Returns dict(status, time, grid (if found), pieces, options).
    status: OPTIMAL / FEASIBLE (found), INFEASIBLE (proven impossible), UNKNOWN (ran out of time).
    on_solution(grid, objective, seconds) is called for every improving solution found.
    hint: an earlier result (grid, machines, options) to start the search from, e.g. a near problem's answer."""
    t = time.time()
    mod = Model(win, objective=objective, **kw)
    if hint:
        mod.add_hint(hint)
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
        res['machines'] = mod.machines_chosen(sv)
        res['filters'] = mod.arm_filters(sv) if mod.arm_links else {}
        res['fill'] = mod.machine_fill(sv)
    return res
