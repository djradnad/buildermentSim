"""Exact module placement + belt routing in one pure true/false model (CNF, any SAT solver).

A MODULE is a fixed rectangle of buildings with ports on its edge. A NET joins one output port to one input port
with belts and underground hops. The solver picks a position (and turn / mirror) for every module and every belt
piece at once, so "no room to route" and "modules overlap" are learned by the same solver, and an UNSAT answer is
a proof that the modules cannot be placed and routed in that box.

Why it is fast: no flow numbers at all. Every piece only carries a net number (a few bits) which each belt copies
to the next tile, exactly like the set labels of bmsolver.uniform. One belt holds one net, a tile has one way in
and one way out, so every net is a single path and the whole model is short clauses.

    cell      E empty | M module | B0-3 belt | I0-3 underground in | O0-3 underground out
    module    exactly one position; it covers its tiles with M; ports become sources / sinks next to it
    in / out  every belt, entrance and module input has exactly one feeder; a piece only pushes into something
              that accepts it (belt not head-on, entrance from behind, the right module input)
    hop       entrance pairs with an exit 1..7 tiles ahead, no same-direction entrance / exit in between
    label     net number bits, copied along every push and hop, fixed at ports

Loose belt loops are harmless and stripped from the answer. Two modules may touch port to port (same net only).
"""
import os
import subprocess
import tempfile
import time

from .pieces import DIRS, UG_REACH, opposite, step


class Module:
    """w x h rectangle. ports: (dx, dy, d, kind, net): kind 'out' = the tile (dx, dy) pushes out in direction d,
    kind 'in' = the tile (dx, dy) takes items moving in direction d. A port tile lies on the edge it uses."""

    def __init__(self, name, w, h, ports, turns=(0,), mirror=False, fixed=None):
        self.name, self.w, self.h, self.ports = name, w, h, list(ports)
        self.turns, self.mirror, self.fixed = tuple(turns), mirror, fixed

    def variants(self):
        """distinct (w, h, ports) after turning (quarter turns, direction d -> d + k) and mirroring"""
        out, seen = [], set()
        for mir in ((False, True) if self.mirror else (False,)):
            for k in self.turns:
                w, h, ports = self.w, self.h, []
                for dx, dy, d, kind, net in self.ports:
                    if mir:
                        dx, d = w - 1 - dx, {1: 3, 3: 1}.get(d, d)
                    ports.append((dx, dy, d, kind, net))
                for _ in range(k % 4):                         # (x, y) -> (y, w - 1 - x), d -> d + 1
                    ports = [(dy, w - 1 - dx, (d + 1) % 4, kind, net) for dx, dy, d, kind, net in ports]
                    w, h = h, w
                key = (w, h, tuple(sorted(ports)))
                if key not in seen:
                    seen.add(key)
                    out.append((w, h, ports, k, mir))
        return out


class CNF:
    def __init__(self):
        self.n = 0
        self.cl = []

    def var(self):
        self.n += 1
        return self.n

    def add(self, *lits):
        self.cl.append(lits)

    def amo(self, lits):
        lits = list(lits)
        if len(lits) <= 5:
            for i in range(len(lits)):
                for j in range(i):
                    self.cl.append((-lits[i], -lits[j]))
            return
        prev = None                                    # sequential counter
        for i, x in enumerate(lits):
            if prev is not None:
                self.cl.append((-x, -prev))
            if i < len(lits) - 1:
                s = self.var()
                self.cl.append((-x, s))
                if prev is not None:
                    self.cl.append((-prev, s))
                prev = s

    def any_of(self, lits):
        """a new variable that is true exactly when one of lits is"""
        lits = list(lits)
        if len(lits) == 1:
            return lits[0]
        v = self.var()
        for x in lits:
            self.cl.append((-x, v))
        self.cl.append(tuple(lits) + (-v,))
        return v

    def write(self, path):
        with open(path, 'w') as f:
            f.write('p cnf %d %d\n' % (self.n, len(self.cl)))
            f.write(''.join(' '.join(map(str, c)) + ' 0\n' for c in self.cl))


def build(W, H, modules, nets, blocked=(), sources=(), sinks=(), reach=UG_REACH, hops=True, max_pieces=None,
          allowed=None, togo=None, route_sel=False):
    """nets: number of nets (labels 0..nets-1). blocked: tiles nothing may use. sources: (tile, d, net) = items
    of that net enter the tile moving in direction d from outside / a blocked tile; sinks: (tile, d, net) = the
    piece on that tile must push in direction d (off the grid or into a blocked tile) carrying that net.
    allowed: {tile: set of (net, j)} and togo: {net: {tile: pieces still needed}} (see corridors): the label
    then also counts j = the detour used so far, which rules out loops and long wanders; None = any net anywhere.
    route_sel: one switch variable per net (cnf.info['sel']) that turns its sink on, for unsat cores."""
    c = CNF()
    blocked = set(blocked)
    cells = [(x, y) for y in range(H) for x in range(W) if (x, y) not in blocked
             and (allowed is None or allowed.get((x, y)))]
    sel = {k: c.var() for k in range(nets)} if route_sel else {}
    cset = set(cells)
    E, M, B, I, O, lab = {}, {}, {}, {}, {}, {}
    have_mod = bool(modules)
    for p in cells:
        E[p] = c.var()
        if have_mod:
            M[p] = c.var()
        for d in range(4):
            B[p, d] = c.var()
            if hops:
                I[p, d] = c.var()
                O[p, d] = c.var()
        kinds = [E[p]] + ([M[p]] if have_mod else []) + [B[p, d] for d in range(4)]
        if hops:
            kinds += [I[p, d] for d in range(4)] + [O[p, d] for d in range(4)]
        c.add(*kinds)
        for i in range(len(kinds)):
            for j in range(i):
                c.add(-kinds[i], -kinds[j])
        lab[p] = {k: c.var() for k in ([(n, 0) for n in range(nets)] if allowed is None else sorted(allowed[p]))}
        c.amo(lab[p].values())
        c.add(E[p], *([M[p]] if have_mod else []), *lab[p].values())

    def is_net(p, net, first=False):
        return [v for (n, j), v in lab[p].items() if n == net and (j == 0 or not first)]

    def copy(pre, p, q, cost=1):
        for (n, j), v in lab[p].items():
            j2 = j if togo is None else j + cost + togo[n].get(q, 1 << 30) - togo[n][p]
            v2 = lab[q].get((n, j2))
            if v2 is None:
                c.add(*pre, -v)
            else:
                c.add(*pre, -v, v2)

    # ---- modules: positions, cover, port literals
    src_at, in_at = {}, {}                    # (q, d) -> {net: [position literals]}
    pos = []                                  # per module: [(lit, x, y, variant index)]
    cover = {}                                # (module, p) -> [position literals]
    for mi, mod in enumerate(modules):
        lits = []
        for vi, (w, h, ports, k, mir) in enumerate(mod.variants()):
            for y in range(H - h + 1):
                for x in range(W - w + 1):
                    if mod.fixed is not None and mod.fixed != (x, y, k, mir):
                        continue
                    tiles = [(x + i, y + j) for j in range(h) for i in range(w)]
                    if any(t not in cset for t in tiles):
                        continue
                    ok = True
                    for dx, dy, d, kind, net in ports:
                        q = (x + dx, y + dy)
                        o = step(q, d) if kind == 'out' else step(q, opposite(d))
                        if o not in cset:
                            ok = False
                    if not ok:
                        continue
                    v = c.var()
                    lits.append((v, x, y, vi))
                    for t in tiles:
                        cover.setdefault((mi, t), []).append(v)
                    for dx, dy, d, kind, net in ports:
                        q = (x + dx, y + dy)
                        if kind == 'out':
                            src_at.setdefault((step(q, d), d), {}).setdefault(net, []).append(v)
                        else:
                            in_at.setdefault((q, d), {}).setdefault(net, []).append(v)
        if not lits:
            c.add()                            # no position at all: unsatisfiable
        c.add(*[v for v, *_ in lits])
        c.amo([v for v, *_ in lits])
        pos.append(lits)
    per_cell = {}
    for (mi, t), vs in cover.items():
        cv = c.any_of(vs)
        c.add(-cv, M[t])
        per_cell.setdefault(t, []).append(cv)
    for t in cells:
        if have_mod:
            cvs = per_cell.get(t, [])
            c.amo(cvs)
            c.add(-M[t], *cvs)

    # ---- fixed sources / sinks at blocked tiles or the box edge
    for q, d, net in sources:
        assert q in cset, 'source tile %s is not free' % (q,)
        src_at.setdefault((q, d), {}).setdefault(net, []).append(None)
    sink_at = {}
    for p, d, net in sinks:
        assert p in cset, 'sink tile %s is not free' % (p,)
        sink_at[p, d] = net

    def net_lits(table, key):
        """{net: literal or True} for the port candidates at key"""
        out = {}
        for net, vs in table.get(key, {}).items():
            out[net] = True if None in vs else c.any_of(vs)
        return out

    # ---- pushes
    out = {}                                   # (p, d) -> literal: the piece at p pushes in direction d
    for p in cells:
        for d in range(4):
            out[p, d] = c.any_of([B[p, d], O[p, d]]) if hops else B[p, d]
    inn = {}                                   # (q, d) -> literal: items move in direction d into q
    for q in cells:
        for d in range(4):
            p = step(q, opposite(d))
            srcs = net_lits(src_at, (q, d))
            ins = net_lits(in_at, (q, d))
            cand = []
            if p in cset:
                cand.append(out[p, d])
            fixed_src = any(v is True for v in srcs.values())
            cand += [v for v in srcs.values() if v is not True]
            if fixed_src:
                il = None                      # always fed
            elif cand:
                il = c.any_of(cand)
            else:
                il = 0                         # never fed
            inn[q, d] = il
            # whoever is fed must accept it
            acc = [B[q, e] for e in range(4) if e != opposite(d)] + ([I[q, d]] if hops else [])
            acc += [v for v in ins.values()]
            if il is None:
                c.add(*[-sel[n] for n, v in srcs.items() if v is True and route_sel], *acc)
            elif il:
                c.add(-il, *acc)
            # labels at ports
            for net, v in srcs.items():        # a source of this net feeds q: the piece on q carries it
                pre = [] if v is True else [-v]
                mq = [M[q]] if have_mod else []
                c.add(*pre, *mq, *([-sel[net]] if route_sel else []), *is_net(q, net, True))
                for net2, v2 in ins.items():   # module touching module: same net only
                    if net2 != net:
                        c.add(*pre, -v2)
            for net, v in ins.items():         # module input here: it is fed, and by the right net
                if il is None:
                    pass
                elif il:
                    c.add(-v, il)
                else:
                    c.add(-v)
                if p in cset:
                    mp = [M[p]] if have_mod else []
                    c.add(-v, *mp, *is_net(p, net))
    # a push off the grid / into a blocked tile is only allowed at a sink
    for p in cells:
        for d in range(4):
            q = step(p, d)
            if q not in cset:
                if (p, d) in sink_at:
                    c.add(out[p, d])
                    net = sink_at[p, d]
                    c.add(*([-sel[net]] if route_sel else []), *is_net(p, net))
                else:
                    c.add(-out[p, d])
    # ---- one way in
    for q in cells:
        ins = [inn[q, d] for d in range(4)]
        if any(v is None for v in ins):
            for v in ins:
                if v:
                    c.add(-v)                  # a fixed source is the only feeder
        else:
            live = [v for v in ins if v]
            mq = [M[q]] if have_mod else []
            for i in range(len(live)):
                for j in range(i):
                    c.add(-live[i], -live[j], *mq)
        for e in range(4):
            feeders = [inn[q, d] for d in range(4) if d != opposite(e)]
            if not any(v is None for v in feeders):
                c.add(-B[q, e], *[v for v in feeders if v])
            if hops:
                v = inn[q, e]
                if v is not None:
                    c.add(-I[q, e], *([v] if v else []))
    # ---- labels along pushes
    for p in cells:
        for d in range(4):
            q = step(p, d)
            if q in cset:
                mq = [M[q]] if have_mod else []
                copy([-out[p, d]] + mq, p, q)
    # ---- underground hops
    if hops:
        into = {}                              # exit (q, d) -> hop literals ending there
        for p in cells:
            for d in range(4):
                hs = []
                for k in range(1, reach + 1):
                    q = step(p, d, k)
                    if q not in cset:
                        if (q[0] < 0 or q[1] < 0 or q[0] >= W or q[1] >= H):
                            break
                        continue               # hop under a blocked / unused tile is fine
                    if togo is not None and not any(
                            (n, j + k + togo[n].get(q, 1 << 30) - togo[n][p]) in lab[q] for n, j in lab[p]):
                        continue               # no net could use this hop within its room
                    h = c.var()
                    hs.append(h)
                    into.setdefault((q, d), []).append(h)
                    c.add(-h, I[p, d])
                    c.add(-h, O[q, d])
                    for j in range(1, k):
                        r = step(p, d, j)
                        if r in cset:
                            c.add(-h, -I[r, d])
                            c.add(-h, -O[r, d])
                    copy([-h], p, q, k)
                c.add(-I[p, d], *hs)
                c.amo(hs)
        for q in cells:
            for d in range(4):
                hs = into.get((q, d), [])
                c.add(-O[q, d], *hs)
                c.amo(hs)
    if max_pieces is not None:
        from pysat.card import CardEnc, EncType
        enc = CardEnc.atmost(lits=[-E[p] for p in cells], bound=max_pieces + 0, top_id=c.n, encoding=EncType.totalizer)
        c.n = max(c.n, enc.nv)
        c.cl.extend(tuple(x) for x in enc.clauses)
    c.info = dict(W=W, H=H, cells=cells, E=E, M=M, B=B, I=I, O=O, lab=lab, pos=pos, modules=modules, sel=sel,
                  hops=hops, sources=list(sources), sinks=list(sinks), blocked=blocked, nets=nets, reach=reach)
    return c


def run(cnf, engine='cadical195', timeout=None, seed=None):
    """-> (status, set of true variables or None, seconds). engine: a pysat solver name or a solver binary."""
    t0 = time.time()
    if os.sep in engine:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'm.cnf')
            cnf.write(path)
            cmd = [os.path.expanduser(engine), '-q', path]
            if seed is not None:
                cmd.insert(1, '--seed=%d' % seed)
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                return 'UNKNOWN', None, time.time() - t0
        if r.returncode == 20:
            return 'INFEASIBLE', None, time.time() - t0
        if r.returncode != 10:
            return 'UNKNOWN', None, time.time() - t0
        true = set()
        for line in r.stdout.split('\n'):
            if line.startswith('v '):
                true.update(int(x) for x in line[2:].split() if int(x) > 0)
        return 'FEASIBLE', true, time.time() - t0
    from pysat.solvers import Solver
    with Solver(name=engine, bootstrap_with=cnf.cl) as s:
        ok = s.solve()
        if not ok:
            return 'INFEASIBLE', None, time.time() - t0
        return 'FEASIBLE', {x for x in s.get_model() if x > 0}, time.time() - t0


def decode(cnf, true):
    """-> dict(placed=[(module, x, y, w, h, ports, turn, mirror)], tiles={p: code}) with loose loops stripped"""
    g = cnf.info
    placed = []
    for mod, lits in zip(g['modules'], g['pos']):
        vs = mod.variants()
        for v, x, y, vi in lits:
            if v in true:
                w, h, ports, k, mir = vs[vi]
                placed.append((mod, x, y, w, h, ports, k, mir))
    tiles = {}
    for p in g['cells']:
        for d in range(4):
            if g['B'][p, d] in true:
                tiles[p] = 'B%d' % d
            if g['hops'] and g['I'][p, d] in true:
                tiles[p] = 'I%d' % d
            if g['hops'] and g['O'][p, d] in true:
                tiles[p] = 'O%d' % d
    res = dict(placed=placed, tiles=tiles)
    paths = trace(g, res)
    used = {p for path in paths.values() for p in path}
    res['tiles'] = {p: t for p, t in tiles.items() if p in used}
    res['paths'] = paths
    return res


def trace(g, res):
    """Independent check: follow every net from its source piece by piece to its sink. -> {net: [tiles]}.
    Raises AssertionError when anything is wrong (overlap, wrong end, belt pushing into nothing)."""
    W, H = g['W'], g['H']
    tiles = res['tiles']
    owner = {}
    starts, ends = {}, {}
    for mod, x, y, w, h, ports, k, mir in res['placed']:
        for j in range(h):
            for i in range(w):
                t = (x + i, y + j)
                assert 0 <= t[0] < W and 0 <= t[1] < H and t not in g['blocked'], 'module off the box'
                assert t not in owner and t not in tiles, 'overlap at %s' % (t,)
                owner[t] = mod.name
        for dx, dy, d, kind, net in ports:
            q = (x + dx, y + dy)
            if kind == 'out':
                assert net not in starts
                starts[net] = (step(q, d), d)
            else:
                assert net not in ends
                ends[net] = (q, d)
    for q, d, net in g['sources']:
        starts[net] = (q, d)
    for p, d, net in g['sinks']:
        ends[net] = (step(p, d), d)
    assert set(starts) == set(ends) == set(range(g['nets'])), 'every net needs one source and one sink'
    paths, seen = {}, set()
    for net in range(g['nets']):
        q, d = starts[net]
        path = []
        while (q, d) != ends[net]:
            code = tiles.get(q)
            assert code is not None, 'net %d runs into nothing at %s' % (net, q)
            assert q not in seen, 'tile %s used twice' % (q,)
            seen.add(q)
            path.append(q)
            k, o = code[0], int(code[1])
            if k == 'B':
                assert o != opposite(d), 'head-on belt at %s' % (q,)
            elif k == 'I':
                assert o == d, 'entrance %s entered from the side' % (q,)
                for n in range(1, g['reach'] + 1):
                    r = step(q, o, n)
                    c2 = tiles.get(r)
                    if c2 == 'O%d' % o:
                        break
                    assert c2 != 'I%d' % o, 'entrance in the way at %s' % (r,)
                else:
                    raise AssertionError('entrance %s has no exit' % (q,))
                assert r not in seen
                seen.add(r)
                path.append(r)
                q = r
            else:
                raise AssertionError('net %d pushed into an exit at %s' % (net, q))
            q, d = step(q, o), o
        paths[net] = path
    return paths


def picture(g, res):
    """text picture: modules as letters, belts as arrows, entrances/exits as n w s e / N W S E"""
    rows = [['.'] * g['W'] for _ in range(g['H'])]
    for x, y in g['blocked']:
        if 0 <= x < g['W'] and 0 <= y < g['H']:
            rows[y][x] = '#'
    for i, (mod, x, y, w, h, ports, k, mir) in enumerate(res['placed']):
        ch = chr(ord('a') + i % 26) if len(mod.name) != 1 else mod.name
        for j in range(h):
            for ii in range(w):
                rows[y + j][x + ii] = ch
    for (x, y), code in res['tiles'].items():
        rows[y][x] = {'B': '^<v>', 'I': 'nwse', 'O': 'NWSE'}[code[0]][int(code[1])]
    return '\n'.join(''.join(r) for r in rows)


def corridors(W, H, nets, blocked, sources, sinks, slack, reach=UG_REACH):
    """-> (allowed {tile: set of (net, detour so far)}, togo {net: {tile: pieces still needed}}).
    A net may only use tiles on some route at most slack[net] pieces longer than its shortest one. A step to a
    free neighbour costs 1, a hop of k tiles costs k (so hops and plain belts cost the same per tile)."""
    import heapq
    blocked = set(blocked)

    def dist(start):
        best = {start: 0}
        heap = [(0, start)]
        while heap:
            dd, p = heapq.heappop(heap)
            if dd > best[p]:
                continue
            for d in range(4):
                for k in range(1, reach + 1):
                    q = step(p, d, k)
                    if not (0 <= q[0] < W and 0 <= q[1] < H):
                        break
                    if q not in blocked and dd + k < best.get(q, 1 << 30):
                        best[q] = dd + k
                        heapq.heappush(heap, (dd + k, q))
        return best
    start = {net: q for q, d, net in sources}
    end = {net: p for p, d, net in sinks}
    allowed, togo = {}, {}
    for net in range(nets):
        a, b = dist(start[net]), dist(end[net])
        togo[net] = b
        if end[net] not in a:
            continue
        D = a[end[net]]
        for p in a:
            if p in b:
                for j in range(a[p] + b[p] - D, slack[net] + 1):
                    allowed.setdefault(p, set()).add((net, j))
    return allowed, togo


def route(W, H, nets, blocked=(), sources=(), sinks=(), engine='cadical195', timeout=None, steps=(0, 2, 4, 8, 16, 32),
          reach=UG_REACH, log=None):
    """Route every net between fixed blocks. Every net starts on its shortest routes only; when the solver
    proves a group of nets cannot all be routed that way (an unsat core), only those nets get more room.
    -> (status, result, seconds). INFEASIBLE is returned only when the nets in a core are all at the last step."""
    from pysat.solvers import Solver
    t0 = time.time()
    level = [0] * nets
    rounds = 0
    while True:
        rounds += 1
        slack = [steps[l] for l in level]
        al, togo = corridors(W, H, nets, blocked, sources, sinks, slack, reach)
        cnf = build(W, H, [], nets, blocked=blocked, sources=sources, sinks=sinks, reach=reach, allowed=al,
                    togo=togo, route_sel=True)
        sel = cnf.info['sel']
        with Solver(name=engine, bootstrap_with=cnf.cl) as s:
            ok = s.solve(assumptions=list(sel.values()))
            if ok:
                true = {x for x in s.get_model() if x > 0}
                break
            core = set(s.get_core() or [])
        bad = [k for k, v in sel.items() if v in core]
        if log:
            log('round %d: %d nets clash at their current room, widening them (%.1f s)' % (rounds, len(bad), time.time() - t0))
        grow = [k for k in bad if level[k] < len(steps) - 1]
        if not grow:
            return 'INFEASIBLE', None, time.time() - t0
        for k in grow:
            level[k] += 1
        if timeout is not None and time.time() - t0 > timeout:
            return 'UNKNOWN', None, time.time() - t0
    res = decode(cnf, true)
    res['info'] = cnf.info
    res['pieces'] = len(res['tiles'])
    res['rounds'] = rounds
    res['slack'] = [steps[l] for l in level]
    return 'FEASIBLE', res, time.time() - t0


def solve(W, H, modules, nets, engine='cadical195', timeout=None, **kw):
    """-> (status, result or None, seconds, build seconds). The result is traced before it is returned."""
    t0 = time.time()
    cnf = build(W, H, modules, nets, **kw)
    tb = time.time() - t0
    status, true, secs = run(cnf, engine, timeout)
    if status != 'FEASIBLE':
        return status, None, secs, tb
    res = decode(cnf, true)
    res['info'] = cnf.info
    res['pieces'] = len(res['tiles'])
    return status, res, secs, tb
