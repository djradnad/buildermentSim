"""Real-data test of the fast router: take a finished factory layout (blueprint rows), keep every machine, arm,
splitter, merge and every belt an arm touches, rip out all plain connecting belt runs, and route them again.

    python tools/mega/reroute_core.py /mnt/project-files/builderment/core/core_c134.json [out.json]

Prints how many runs were ripped, the time, and old vs new piece counts; the new layout is traced piece by piece.
"""
import json
import sys
import time

sys.path.insert(0, '.')
from bmsolver import negotiate, place
from bmsolver.design import Design
from bmsolver.pieces import KIND_OF_ID, UG_REACH, opposite, step

SIZE = {3007: (2, 2), 3008: (3, 3), 3009: (2, 2), 3010: (4, 4), 3013: (3, 3), 3014: (3, 3), 3038: (2, 2), 3039: (3, 4)}


def footprint(r):
    bid, x, y, o = r[0], r[1], r[2], (r[3] + 2) % 4       # non-belt buildings are stored turned 180 degrees
    w, l = SIZE.get(bid, (1, 1))
    out = []
    for ox in range(-((w - 1) // 2), w // 2 + 1):
        for oy in range(-((l - 1) // 2), l // 2 + 1):
            out.append({0: (x + ox, y + oy), 1: (x + oy, y - ox), 2: (x - ox, y - oy), 3: (x - oy, y + ox)}[o])
    return out


def load(path):
    rows = json.load(open(path))['rows']
    belts = {(r[1], r[2]): KIND_OF_ID[r[0]] + str(r[3]) for r in rows if r[0] in KIND_OF_ID}
    solid, arms = set(), []
    for r in rows:
        if r[0] in KIND_OF_ID:
            continue
        solid.update(footprint(r))
        if r[0] == 3012:
            arms.append(((r[1], r[2]), r[3]))
    return rows, belts, solid, arms


def runs(belts, solid, arms):
    """-> (nets [(tiles, source (tile, d), sink (tile, d))], anchored tiles)"""
    des = Design(belts)
    links = des.links()
    outs, ins = {}, {}
    for p, q, kind in links:
        outs.setdefault(p, []).append((q, kind))
        ins.setdefault(q, []).append((p, kind))
    anchored = set()
    for (p, o) in arms:
        for d in range(4):                                  # an arm touches the tiles in line with it; keep all 4
            q = step(p, d)
            if q in belts:
                anchored.add(q)
    for p, c in belts.items():
        if c[0] == 'S' or len(ins.get(p, ())) != 1 or len(outs.get(p, ())) != 1:
            anchored.add(p)
    changed = True
    while changed:                                          # a hop stays whole
        changed = False
        for p, q, kind in links:
            if kind == 'ug' and (p in anchored) != (q in anchored):
                anchored |= {p, q}
                changed = True
    nets, used = [], set()
    for p, q, kind in links:
        if p in anchored and q not in anchored and kind == 'push':
            d = int(belts[p][1]) if belts[p][0] != 'S' else None
            if d is None:
                d = next(e for e in range(4) if step(p, e) == q)
            tiles, cur = [], q
            while cur not in anchored:
                tiles.append(cur)
                cur = outs[cur][0][0]
            last = tiles[-1]
            if len(tiles) < 3 or outs[last][0][1] != 'push':
                anchored.update(tiles)
                continue
            nets.append((tiles, (q, d), (last, int(belts[last][1]))))
            used.update(tiles)
    loose = [p for p in belts if p not in anchored and p not in used]      # loops / runs with no fixed start
    anchored.update(loose)
    return nets, anchored


def main(path, out=None):
    rows, belts, solid, arms = load(path)
    nets, anchored = runs(belts, solid, arms)
    xs = [p[0] for p in list(belts) + list(solid)]
    ys = [p[1] for p in list(belts) + list(solid)]
    x0, y0 = min(xs), min(ys)
    W, H = max(xs) - x0 + 1, max(ys) - y0 + 1
    sh = lambda p: (p[0] - x0, p[1] - y0)
    blocked = {sh(p) for p in solid} | {sh(p) for p in anchored}
    fixed_ug, no_ug = set(), set()
    for p in anchored:
        c = belts[p]
        if c[0] in 'IO':
            fixed_ug.add((sh(p), int(c[1])))
        if c[0] == 'I':
            d = int(c[1])
            for k in range(1, UG_REACH + 1):
                q = step(p, d, k)
                if belts.get(q) == 'O%d' % d and q in anchored:
                    break
                no_ug.add((sh(q), d))
    sources = [(sh(s), d, i) for i, (t, (s, d), e) in enumerate(nets)]
    sinks = [(sh(e), d, i) for i, (t, s, (e, d)) in enumerate(nets)]
    old = sum(len(t) for t, s, e in nets)
    print('%dx%d, %d buildings, %d belt pieces; %d fixed, %d connecting runs with %d pieces ripped out'
          % (W, H, len(rows), len(belts), len(anchored), len(nets), old), flush=True)
    swap = []
    if '--swap' in sys.argv:                                # neighbouring sinks of one direction = one module's lanes
        at = {e: (d, i) for e, d, i in sinks}
        seen = set()
        for e in at:
            if e in seen:
                continue
            grp, todo = [e], [e]
            seen.add(e)
            while todo:
                p = todo.pop()
                for dd in range(4):
                    q = step(p, dd)
                    if q in at and q not in seen and at[q][0] == at[p][0]:
                        seen.add(q)
                        grp.append(q)
                        todo.append(q)
            if len(grp) > 1:
                swap.append([at[q][1] for q in grp])
        print('%d lane groups with %d runs may swap ends' % (len(swap), sum(map(len, swap))))
    ok, res, secs = negotiate.route(W, H, len(nets), blocked, sources, sinks, fixed_ug=fixed_ug, no_ug=no_ug, swap=swap,
                                    open_cost=0.2, log=lambda s: print('  ' + s, flush=True), timeout=900)
    if not ok:
        print('NOT ROUTED in %.1f s: %s' % (secs, str(res)[:300]))
        return
    place.trace(dict(W=W, H=H, blocked=blocked, sources=sources, sinks=res['sinks'], nets=len(nets), reach=UG_REACH), res)
    print('routed and traced in %.1f s (%d rounds): %d pieces, the hand-made runs had %d' % (secs, res['rounds'], res['pieces'], old))
    longer = sum(1 for i, (t, s, e) in enumerate(nets) if len(res['by_net'][i]) > len(t))
    shorter = sum(1 for i, (t, s, e) in enumerate(nets) if len(res['by_net'][i]) < len(t))
    print('%d runs shorter, %d longer, %d equal' % (shorter, longer, len(nets) - shorter - longer))
    kinds = lambda cs: (sum(c[0] == 'B' for c in cs), sum(c[0] == 'I' for c in cs))
    print('belts / hops: before %s, after %s' % (kinds([belts[p] for t, s, e in nets for p in t]), kinds(res['tiles'].values())))
    if out:
        keep = [r for r in rows if r[0] not in KIND_OF_ID or (r[1], r[2]) in anchored]
        from bmsolver.pieces import BUILDING_ID
        new = [[BUILDING_ID[c[0]], x + x0, y + y0, int(c[1]), 0, -1, 0] for (x, y), c in sorted(res['tiles'].items())]
        json.dump({'meta': {'name': 'rerouted', 'count': len(keep) + len(new)}, 'rows': keep + new}, open(out, 'w'))
        print('saved', out)


if __name__ == '__main__':
    main(*sys.argv[1:3])
