"""Fast belt router for many nets between fixed blocks: negotiated congestion (the method FPGA routers use).

Every net is routed by A* on exact piece moves (belt straight / turn, underground hop of 1..7 tiles, also under
blocks and under other belts). Nets may overlap at first; tiles wanted by several nets get more expensive every
round, so the nets that have a cheap way around move and the ones that need the tile keep it. A finished result
has no overlap and is checked piece by piece by bmsolver.place.trace. It is a search, not a proof: when it fails,
bmsolver.place.route (exact) can decide the stuck region.

    route(W, H, nets, blocked, sources, sinks) -> (ok, result, seconds)
"""
import heapq
import time

from .pieces import UG_REACH, opposite, step, DIRS


def route(W, H, nets, blocked=(), sources=(), sinks=(), reach=UG_REACH, rounds=60, hop_cost=0.5, turn_cost=0.05,
          open_cost=0.0, log=None, timeout=None, fixed_ug=(), no_ug=()):
    """sources: (tile, d, net) items enter the tile moving d; sinks: (tile, d, net) the piece there pushes d.
    hop_cost: extra price of one hop (john prefers plain belts when equal); open_cost: extra price of a tile
    with no block next to it (routes hug buildings). fixed_ug: (tile, d) of underground pieces that stay (a new
    hop may not pass one of its own direction); no_ug: (tile, d) where no new entrance / exit of direction d may
    go (the tiles between a fixed entrance and its exit)."""
    t0 = time.time()
    blocked = set(blocked)
    fixed_ug, no_ug = set(fixed_ug), set(no_ug)
    inside = lambda p: 0 <= p[0] < W and 0 <= p[1] < H
    free = lambda p: inside(p) and p not in blocked
    start = {n: (q, d) for q, d, n in sources}
    goal = {n: (p, d) for p, d, n in sinks}
    reserved = {}                                # port tiles belong to their net
    for n in range(nets):
        reserved[start[n][0]] = n
        reserved[goal[n][0]] = n
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

    def tile_cost(p, n):
        r = reserved.get(p)
        if r is not None and r != n:
            return None
        others = len(use.get(p, ())) - (n in use.get(p, ()))
        return (1 + base.get(p, 0) + hist.get(p, 0)) * (1 + pres * others)

    def astar(n, strict=False):
        (s, sd), (g, gd) = start[n], goal[n]
        h = lambda p: abs(p[0] - g[0]) + abs(p[1] - g[1])
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
            if p == g and (d == 4 or d != opposite(gd)):
                out = {} if d == 4 else {g: 'B%d' % gd}       # last belt (or exit) pushes into the sink
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
                if not free(q) or (q == g and e == opposite(gd)):
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
                        if r == g and d == gd:                      # the exit itself pushes into the sink
                            nc = c + tr + hop_cost + clash + (k - 1)
                            if nc < best.get((g, 4), 1e18):
                                best[g, 4] = nc
                                prev[g, 4] = ((p, d), {p: 'I%d' % d, r: 'O%d' % d})
                                heapq.heappush(heap, (nc, nc, g, 4))
                        q = step(r, d)
                        if free(q) and r != g and not (q == g and d == opposite(gd)):
                            tq = tile_cost(q, n)
                            if tq is not None:
                                nc = c + tr + tq + hop_cost + clash + (k - 1)
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
            return True, dict(tiles=tiles, placed=[], pieces=len(tiles), rounds=it + 1, by_net=paths), time.time() - t0
        for p, ns in use.items():
            if len(ns) > 1:
                hist[p] = hist.get(p, 0) + 0.5 * (len(ns) - 1)
        pres *= 1.6
        todo = sorted(bad)
        if timeout is not None and time.time() - t0 > timeout:
            break
    return False, dict(reason='still clashing', nets=sorted(conflicts())), time.time() - t0
