"""Fast belt router for many nets between fixed blocks: negotiated congestion (the method FPGA routers use).

Every net is routed by A* on exact piece moves (belt straight / turn, underground hop of 1..7 tiles, also under
blocks and under other belts). Nets may overlap at first; tiles wanted by several nets get more expensive every
round, so the nets that have a cheap way around move and the ones that need the tile keep it. A finished result
has no overlap and is checked piece by piece by bmsolver.place.trace. It is a search, not a proof: when it fails,
bmsolver.place.route (exact) can decide the stuck region.

    route(W, H, nets, blocked, sources, sinks) -> (ok, result, seconds)
"""
import heapq
import os
import subprocess
import time

from .pieces import UG_REACH, opposite, step, DIRS


_BIN = None


def _binary():
    """the compiled router (built on first use next to the source; None when there is no compiler)"""
    global _BIN
    if _BIN is None:
        src = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'negotiate.cpp')
        exe = os.path.join(os.path.expanduser('~/.cache'), 'bm_negotiate_cpp')
        try:
            if not os.path.exists(exe) or os.path.getmtime(exe) < os.path.getmtime(src):
                os.makedirs(os.path.dirname(exe), exist_ok=True)
                subprocess.run(['g++', '-O2', '-std=c++17', '-o', exe, src], check=True, capture_output=True)
            _BIN = exe
        except Exception:
            _BIN = False
    return _BIN


def _route_cpp(W, H, nets, blocked, sources, sinks, reach, rounds, hop_cost, turn_cost, open_cost, log, timeout,
               fixed_ug, no_ug, swap, join):
    t0 = time.time()
    join = dict(join or {})
    start = {n: (q, d) for q, d, n in sources}
    goal = {n: (p, d) for p, d, n in sinks}
    team = {n: n for n in range(nets)}
    for grp in swap:
        for n in grp:
            team[n] = min(grp)
    L = ['%d %d %d %d %d' % (W, H, nets, reach, rounds),
         '%r %r %r %r %d' % (hop_cost, turn_cost, open_cost, timeout or 0, 1 if log else 0)]
    blocked = list(blocked)
    L.append(str(len(blocked)))
    L += ['%d %d' % p for p in blocked]
    L += ['%d %d %d' % (start[n][0][0], start[n][0][1], start[n][1]) for n in range(nets)]
    L += ['-1 -1 -1' if n in join else '%d %d %d' % (goal[n][0][0], goal[n][0][1], goal[n][1]) for n in range(nets)]
    L += ['%d %d' % (team[n], join.get(n, -1)) for n in range(nets)]
    for ug in (list(fixed_ug), list(no_ug)):
        L.append(str(len(ug)))
        L += ['%d %d %d' % (p[0], p[1], d) for p, d in ug]
    pr = subprocess.run([_BIN], input='\n'.join(L) + '\n', capture_output=True, text=True)
    if log:
        for line in pr.stderr.splitlines():
            log(line)
    out = pr.stdout.split('\n')
    if not out or not out[0].startswith('OK'):
        reason = out[0][5:] if out and out[0].startswith('FAIL') else 'router crashed: ' + pr.stderr[-200:]
        bad = [int(x) for x in out[2:] if x.strip()] if len(out) > 2 else []
        res = dict(reason=reason)
        if reason == 'still clashing':
            res['nets'] = bad
        return False, res, time.time() - t0
    rounds_used = int(out[0].split()[1])
    i = 1
    tiles, by_net, snk = {}, {}, []
    for n in range(nets):
        _, ex, ey, ed, cnt = map(int, out[i].split())
        i += 1
        pieces = {}
        for k in range(cnt):
            x, y, c, d = out[i].split()
            pieces[(int(x), int(y))] = c + d
            i += 1
        by_net[n] = pieces
        tiles.update(pieces)
        snk.append(((ex, ey), ed, n))
    return True, dict(tiles=tiles, placed=[], pieces=len(tiles), rounds=rounds_used, by_net=by_net, sinks=snk), time.time() - t0


def route(W, H, nets, blocked=(), sources=(), sinks=(), reach=UG_REACH, rounds=60, hop_cost=0.5, turn_cost=0.05,
          open_cost=0.0, log=None, timeout=None, fixed_ug=(), no_ug=(), swap=(), join=None):
    """sources: (tile, d, net) items enter the tile moving d; sinks: (tile, d, net) the piece there pushes d.
    hop_cost: extra price of one hop (john prefers plain belts when equal); open_cost: extra price of a tile
    with no block next to it (routes hug buildings). fixed_ug: (tile, d) of underground pieces that stay (a new
    hop may not pass one of its own direction); no_ug: (tile, d) where no new entrance / exit of direction d may
    go (the tiles between a fixed entrance and its exit). swap: groups of nets whose sinks are interchangeable
    (a module's input lanes that can each take any of its resources): the router picks who ends where and
    reports it as result['sinks'] in the same form as the sinks argument. join: {net: other net} = this net has
    no sink of its own, it ends by pushing into a belt of the other net from the side (two collectors of one item
    joining; the caller makes sure the sum fits one belt)."""
    t0 = time.time()
    if os.environ.get('BM_ROUTER', 'cpp') == 'cpp' and _binary():
        return _route_cpp(W, H, nets, blocked, sources, sinks, reach, rounds, hop_cost, turn_cost, open_cost, log,
                          timeout, fixed_ug, no_ug, swap, join)
    blocked = set(blocked)
    fixed_ug, no_ug = set(fixed_ug), set(no_ug)
    inside = lambda p: 0 <= p[0] < W and 0 <= p[1] < H
    free = lambda p: inside(p) and p not in blocked
    start = {n: (q, d) for q, d, n in sources}
    goal = {n: (p, d) for p, d, n in sinks}
    team = {n: n for n in range(nets)}           # nets that may end on each other's sinks share a team
    for grp in swap:
        for n in grp:
            team[n] = min(grp)
    goals = {}
    join = dict(join or {})
    for n in range(nets):
        if n not in join:
            goals.setdefault(team[n], {})[goal[n][0]] = goal[n][1]
    reserved = {}                                # port tiles belong to their net (or its team)
    for n in range(nets):
        reserved[start[n][0]] = ('s', n)
        if n not in join:
            reserved[goal[n][0]] = ('g', team[n])
    base = {}
    if open_cost:
        for y in range(H):
            for x in range(W):
                p = (x, y)
                if p not in blocked and not any(step(p, d) in blocked for d in range(4)):
                    base[p] = open_cost
    hist = {}
    use = {}                                     # tile -> set of nets
    ugs = {}                                     # (tile, d) -> nets with an entrance / exit of direction d there
    paths = {}                                   # net -> {tile: code}
    pres = 0.5
    ended = {}                                   # net -> the sink tile it took
    ended_dir = {}

    def join_goals(n):
        """tiles next to a belt of the net we join, with the direction that pushes into it (not head-on)"""
        G = {}
        pm = paths.get(join[n], {})
        for t, code in pm.items():
            if code[0] != 'B':
                continue
            e = int(code[1])
            for g in range(4):
                if g == opposite(e):
                    continue
                q = step(t, opposite(g))
                if free(q) and q not in pm and reserved.get(q) in (None, ('s', n)):
                    G.setdefault(q, g)
        return G

    def tile_cost(p, n):
        r = reserved.get(p)
        if r is not None and r != ('s', n) and r != ('g', team[n]):
            return None
        others = len(use.get(p, ())) - (n in use.get(p, ()))
        return (1 + base.get(p, 0) + hist.get(p, 0)) * (1 + pres * others)

    def astar(n, strict=False):
        s, sd = start[n]
        G = join_goals(n) if n in join else goals[team[n]]
        if not G:
            return None
        if n in join:
            h = lambda p: 0
        elif len(G) == 1:
            (g0, _), = G.items()
            h = lambda p: abs(p[0] - g0[0]) + abs(p[1] - g0[1])
        else:
            h = lambda p: min(abs(p[0] - g[0]) + abs(p[1] - g[1]) for g in G)
        c0 = tile_cost(s, n)
        best = {(s, sd): c0}
        prev = {}
        heap = [(c0 + h(s), c0, s, sd)]

        def mine(key, *cells):                   # does the route so far already use one of these tiles?
            while key in prev:
                key, pieces = prev[key]
                if any(x in pieces for x in cells):
                    return True
            return False
        while heap:
            f, c, p, d = heapq.heappop(heap)
            if c > best.get((p, d), 1e18):
                continue
            if p in G and (d == 4 or d != opposite(G[p])):
                out = {} if d == 4 else {p: 'B%d' % G[p]}
                ended[n] = p       # last belt (or exit) pushes into the sink
                ended_dir[n] = G[p]
                key = (p, d)
                count = len(out)
                while key in prev:
                    key, pieces = prev[key]
                    out.update(pieces)
                    count += len(pieces)
                if count != len(out):                               # the route crossed itself: search strictly
                    assert not strict
                    return astar(n, True)
                return out
            if d == 4:
                continue
            for e in range(4):                                      # a belt on p facing e
                if e == opposite(d):
                    continue
                q = step(p, e)
                if not free(q) or (q in G and e == opposite(G[q])):
                    continue
                tc = tile_cost(q, n)
                if tc is None or (strict and mine((p, d), q)):
                    continue
                nc = c + tc + (turn_cost if e != d else 0)
                if nc < best.get((q, e), 1e18):
                    best[q, e] = nc
                    prev[q, e] = ((p, d), {p: 'B%d' % e})
                    heapq.heappush(heap, (nc + h(q), nc, q, e))
            clash = 0.0                                             # an entrance on p (facing d), exit k ahead
            for k in range(1, reach + 1):
                r = step(p, d, k)
                if not inside(r) or (p, d) in no_ug or (r, d) in fixed_ug:
                    break
                if free(r) and (r, d) not in no_ug:
                    tr = tile_cost(r, n)
                    if tr is not None and not (strict and mine((p, d), r, step(r, d))):
                        if r in G and d == G[r]:                      # the exit itself pushes into the sink
                            nc = c + tr + hop_cost + clash + (k - 1) * (1 + open_cost)
                            if nc < best.get((r, 4), 1e18):
                                best[r, 4] = nc
                                prev[r, 4] = ((p, d), {p: 'I%d' % d, r: 'O%d' % d})
                                heapq.heappush(heap, (nc, nc, r, 4))
                        q = step(r, d)
                        if free(q) and r not in G and not (q in G and d == opposite(G[q])):
                            tq = tile_cost(q, n)
                            if tq is not None:
                                nc = c + tr + tq + hop_cost + clash + (k - 1) * (1 + open_cost)
                                if nc < best.get((q, d), 1e18):
                                    best[q, d] = nc
                                    prev[q, d] = ((p, d), {p: 'I%d' % d, r: 'O%d' % d})
                                    heapq.heappush(heap, (nc + h(q), nc, q, d))
                # another net's entrance / exit of the same direction in between would steal the hop
                if any(m != n for m in ugs.get((r, d), ())):
                    clash += 4 * (1 + pres)
        return None

    def add(n, pieces):
        paths[n] = pieces
        for p, code in pieces.items():
            use.setdefault(p, set()).add(n)
            if code[0] in 'IO':
                ugs.setdefault((p, int(code[1])), set()).add(n)

    def rip(n):
        for p, code in paths.pop(n, {}).items():
            use[p].discard(n)
            if code[0] in 'IO':
                ugs[p, int(code[1])].discard(n)

    def conflicts():
        bad = set()
        for p, ns in use.items():
            if len(ns) > 1:
                bad |= ns
        for n, pieces in paths.items():                             # hop stealing
            for p, code in pieces.items():
                if code[0] == 'I':
                    d = int(code[1])
                    for k in range(1, reach + 1):
                        r = step(p, d, k)
                        if pieces.get(r) == 'O%d' % d:
                            break
                        ms = [m for m in ugs.get((r, d), ()) if m != n]
                        if ms:
                            bad.add(n)
                            bad.update(ms)
        return bad

    todo = list(range(nets))
    for it in range(rounds):
        todo = set(todo)
        todo |= {n for n, m in join.items() if m in todo}           # a moved belt takes its joiners along
        todo = sorted(todo, key=lambda n: (n in join, n))
        for n in todo:
            rip(n)
            pieces = astar(n)
            if pieces is None:
                return False, dict(reason='net %d has no route at all' % n), time.time() - t0
            add(n, pieces)
        bad = conflicts()
        if log:
            log('round %d: %d nets rerouted, %d still clash (%.1f s)' % (it + 1, len(todo), len(bad), time.time() - t0))
        if not bad:
            tiles = {}
            for n, pieces in paths.items():
                tiles.update(pieces)
            return True, dict(tiles=tiles, placed=[], pieces=len(tiles), rounds=it + 1, by_net=paths,
                              sinks=[(ended[n], ended_dir[n], n) for n in range(nets)]), time.time() - t0
        for p, ns in use.items():
            if len(ns) > 1:
                hist[p] = hist.get(p, 0) + 0.5 * (len(ns) - 1)
        pres *= 1.6
        todo = sorted(bad)
        if timeout is not None and time.time() - t0 > timeout:
            break
    return False, dict(reason='still clashing', nets=sorted(conflicts())), time.time() - t0
