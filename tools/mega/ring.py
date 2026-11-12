"""First ring draft from john's own blocks, in the order of his example yxy4vn.

    top band   steel std + graphite, steel ALT, steel rod            (yxy4vn as it is)
    left       graphite + carbon fiber column                        (yxy4vn as it is)
    right      concrete, wood frames, planks                         (srb44l)
    bottom     tiled stack as staircases (stair.py, john's vc04aj / mbl5k9 style), plan-sized and flipped:
               sand/glass + tungsten ore, then copper ingot / wire / heat sink + iron ingot / plating

The blocks are placed around a hole for the core. Writes the rows, a picture, the hole size and a game-sim run
of the whole ring with every typed input lane fed.

    python3 tools/mega/ring.py [width] [height] [sim minutes]
"""
import json
import os
import sys

sys.path.insert(0, '.')
sys.path.insert(0, 'tools/mega')
import bppic
import jmod
import lanemod
import reroute_core as rc
from bmsolver.pieces import KIND_OF_ID, DIRS

FV = {0: 2, 2: 0, 1: 1, 3: 3}
ARM = 3012


def cells_of(r):
    return [(r[1], r[2])] if r[0] in KIND_OF_ID or r[0] == ARM else rc.footprint(r)


def cut(rows, x0, x1, y0, y1):
    """rows whose tiles all lie in the box"""
    return [list(r) for r in rows if all(x0 <= c[0] <= x1 and y0 <= c[1] <= y1 for c in cells_of(r))]


def flip_v(rows):
    out = []
    for r in rows:
        if r[0] in KIND_OF_ID or r[0] == ARM:
            out.append([r[0], r[1], -r[2], FV[r[3]]] + list(r[4:]))
        else:
            fp = rc.footprint(r)
            mx, my = min(c[0] for c in fp), max(c[1] for c in fp)
            out.append(lanemod.machine_row(r[0], mx, -my, FV[r[3]], r[5], r[4] + 1) [:6] + [r[6] if len(r) > 6 else 0])
    return out


def box(rows):
    cs = [c for r in rows for c in cells_of(r)]
    xs = [c[0] for c in cs]
    ys = [c[1] for c in cs]
    return min(xs), max(xs), min(ys), max(ys)


def place(rows, x, y):
    """shift so the block's top-left tile lands on (x, y)"""
    xa, xb, ya, yb = box(rows)
    return [[r[0], r[1] - xa + x, r[2] - ya + y] + list(r[3:]) for r in rows]


TR = {0: 1, 1: 0, 2: 3, 3: 2}


def transpose(rows):
    """mirrored on the diagonal (x and y swapped)"""
    out = []
    for r in rows:
        if r[0] in KIND_OF_ID or r[0] == ARM:
            out.append([r[0], r[2], r[1], TR[r[3]]] + list(r[4:]))
        else:
            fp = rc.footprint(r)
            mx, my = min(c[0] for c in fp), min(c[1] for c in fp)
            out.append(lanemod.machine_row(r[0], my, mx, TR[r[3]], r[5], r[4] + 1)[:6] + [r[6] if len(r) > 6 else 0])
    return out


def size(rows):
    b = box(rows)
    return b[1] - b[0] + 1, b[3] - b[2] + 1


def insert_period(rows, at, period, times=1, axis=0):
    """repeat the slice [at, at + period) along x (axis 0) or y (axis 1) `times` more times.
    The slice must equal the next one tile for tile and no building may straddle its borders."""
    ax = 1 + axis

    def sig(lo):
        out = set()
        for r in rows:
            cs = cells_of(r)
            if all(lo <= c[axis] < lo + period for c in cs):
                out.add((r[0], r[ax] - lo, r[3 - ax if False else (2 if axis == 0 else 1)], r[3], r[4], r[5]))
            elif any(lo <= c[axis] < lo + period for c in cs):
                raise ValueError('building straddles the slice at %d: %s' % (lo, r))
        return out
    a, b = sig(at), sig(at + period)
    if a != b:
        raise ValueError('slices differ at %d (+%d): %d tiles' % (at, period, len(a ^ b)))
    out = []
    for r in rows:
        lo = min(c[axis] for c in cells_of(r))
        if lo >= at + period:
            r = list(r)
            r[ax] += period * times
            out.append(r)
        else:
            out.append(list(r))
            if lo >= at:
                for k in range(1, times + 1):
                    c = list(r)
                    c[ax] += period * k
                    out.append(c)
    return out


def steel_block():
    """john's yxy4vn sized to the plan by repeating his own periods: 3 more rod periods (5 columns each: forge
    pairs with a rod workshop column), 1 more graphite / carbon fiber period (5 rows), and the gap between the
    two rod belts moved so neither carries more than a belt (5 rod columns left, 6 right)"""
    rows = jmod.load('yxy4vn')
    rows = insert_period(rows, 63, 5, 3)
    rows = insert_period(rows, -22, 5, 1, axis=1)
    rows = [r for r in rows if not (r[0] == 3000 and r[1] == 70 and r[2] == -27)]
    rows.append([3000, 55, -27, 1, 0, -1, 0])
    return rows


def graphite_out(units=4):
    """graphite for the core (1.28 belts): my hop-lane forge units (4 forges, 160/min from a coal and a log belt),
    upright with the lanes entering at the bottom edge and each collector leaving at the top"""
    import core
    import rawmod
    rec = core.recipes()['Graphite'][0]
    out = []
    for k in range(units):
        rows = [list(r) for r in rawmod.forge(rec['id'], 4, rec)['rows']]
        for r in rows:
            if r[0] == 3000 and r[2] == 2:
                r[3] = 3                                     # collector runs away from the lane entrances
        out += place(flip_v(transpose(rows)), 3 * k, 0)
    return out


def uranium_out(units=2):
    """enriched uranium: 4 furnaces per uranium ore belt, fed by arms from the passing belt (rawmod.arms), upright
    like the graphite units"""
    import core
    import rawmod
    rec = core.recipes()['Enriched_Uranium'][0]
    out = []
    for k in range(units):
        rows = [list(r) for r in rawmod.arms(3006, rec['id'], 4)['rows']]
        for r in rows:
            if r[0] == 3000 and r[2] == 3:
                r[3] = 3
        out += place(flip_v(transpose(rows)), 5 * k, 0)
    return out


def build(W=200, H=200):
    """john's k3xv5h arrangement in a W x H square, with his yxy4vn steel order:
    left    steel column (yxy4vn band turned upright: rods on top, ALT steel, std steel + graphite at the bottom corner)
    bottom  graphite + carbon fiber strip (yxy4vn column, now running right from that corner)
    right   concrete / frames / planks (srb44l) down to the bottom edge: logs come in from below, stone from the right
    top     tiled stack (copper, iron); the sand/glass + tungsten stack stands upright in the top right corner"""
    steel = flip_v(transpose(steel_block()))
    stack = jmod.load('data/ring/north_stair.json')
    glass = transpose(flip_v(jmod.load('data/ring/east_stair.json')))
    conc = jmod.load('srb44l')
    sw, sh = size(steel)
    kw, kh = size(stack)
    gw, gh = size(glass)
    cw, ch = size(conc)
    rows = place(steel, 0, H - sh)
    # the stack starts right of whatever part of the steel column reaches up into its rows
    band = 1 + max([c[0] for r in rows for c in cells_of(r) if c[1] < kh] or [23])
    rows += place(stack, band, 0)
    rows += place(glass, W - gw, 0)
    rows += place(conc, W - cw, H - ch)
    gr = graphite_out()
    rows += place(gr, W - cw - size(gr)[0] - 1, H - size(gr)[1])
    ur = uranium_out()
    rows += place(ur, W - cw - size(gr)[0] - size(ur)[0] - 3, H - size(ur)[1])
    hole = (band, kh, W - cw - band, H - kh - 24)
    return rows, hole


def main():
    hw = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    hh = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    minutes = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    rows, hole = build(hw, hh)
    seen = {}
    clash = 0
    for r in rows:
        for c in cells_of(r):
            clash += c in seen
            seen[c] = 1
    xa, xb, ya, yb = box(rows)
    print('ring %dx%d (x %d..%d, y %d..%d), hole %dx%d at (%d, %d), %d buildings, %d overlapping tiles' % (
        xb - xa + 1, yb - ya + 1, xa, xb, ya, yb, hole[2], hole[3], hole[0], hole[1], len(rows), clash))
    os.makedirs(os.path.expanduser('~/data'), exist_ok=True)
    json.dump({'rows': rows, 'hole': hole}, open(os.path.expanduser('~/data/ring_draft.json'), 'w'))
    rec = jmod.recipes()
    bppic.picture(rows, rec, os.path.expanduser('~/data/ring_draft.png'), 4)
    for name, (n, a, b, c, d) in sorted(bppic.table(rows, rec).items()):
        print('   %-18s %4d machines' % (name, n))
    if minutes:
        ins, machines, item = jmod.assign(rows)
        import collections
        print('input lanes: ' + ', '.join('%d %s' % (n, k) for k, n in collections.Counter(item.values()).items()))
        rep = jmod.sim(rows, ins, item, minutes, minutes)
        out = collections.defaultdict(float)
        for s in rep['sinks'].values():
            for k, v in s.items():
                out[k] += v
        print('leaves: ' + ', '.join('%s %.0f/min (%.2f belts)' % (k, v, v / 480) for k, v in sorted(out.items())))
        raw = set(item.values())
        ex = []
        for p, d in jmod.OUTS:
            q1 = (p[0] + DIRS[d][0], p[1] + DIRS[d][1])
            cand = [(p[0] + n * DIRS[d][0], p[1] + n * DIRS[d][1]) for n in (2, 1, 0)]
            cand += [(q1[0] + 2 * DIRS[dd][0], q1[1] + 2 * DIRS[dd][1]) for dd in ((d + 1) % 4, (d + 3) % 4)]   # led out sideways
            for c in cand:
                got = rep['sinks'].get('%d,%d' % c)
                if got is not None:
                    for k, v in got.items():
                        if k not in raw and v > 0.5:
                            ex.append([p[0], p[1], d, k, round(v, 1)])
                    break
        fed = collections.defaultdict(float)
        for f in rep['fedRates']:
            fed[f['kind']] += f['perMin']
        print('taken: ' + ', '.join('%s %.2f belts' % (k, v / 480) for k, v in sorted(fed.items())))
        by = collections.defaultdict(lambda: [0, 0.0])
        for m in rep['machines']:
            r = rec.get(m['recipe'])
            if r:
                n = r['out'][0]['item'] + (' ALT' if r.get('alt') else '')
                by[n][0] += 1
                by[n][1] += m['perMin'] * r['out'][0]['amount']
        for n, (c, v) in sorted(by.items()):
            print('   %-18s %4d machines make %8.1f/min' % (n, c, v))
        json.dump({'rows': rows, 'hole': hole, 'exits': ex,
                   'inputs': [[p[0], p[1], d, item[i]] for i, (p, d, k) in enumerate(ins) if item.get(i)]},
                  open(os.path.expanduser('~/data/ring_typed_%dx%d.json' % (hw, hh)), 'w'))
        for e in sorted(ex, key=lambda e: (e[3], e[0], e[1])):
            print('   exit', e)


if __name__ == '__main__':
    main()
