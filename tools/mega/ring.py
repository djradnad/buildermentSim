"""First ring draft from john's own blocks, in the order of his example yxy4vn.

    top band   steel std + graphite, steel ALT, steel rod            (yxy4vn as it is)
    left       graphite + carbon fiber column                        (yxy4vn as it is)
    right      concrete, wood frames, planks                         (srb44l)
    bottom     tiled stack: copper ingot / wire / heat sink, iron ingot / plating (cut from k3xv5h, flipped)

The blocks are placed around a hole for the core. Writes the rows, a picture, the hole size and a game-sim run
of the whole ring with every typed input lane fed.

    python3 tools/mega/ring.py [hole width] [hole height] [sim minutes]
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
from bmsolver.pieces import KIND_OF_ID

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


def build(hole_w=140, hole_h=140):
    steel = jmod.load('yxy4vn')
    xa, xb, ya, yb = box(steel)
    k3 = jmod.load('k3xv5h')
    stack = flip_v(cut(k3, -60, 200, -200, -44))
    conc = jmod.load('srb44l')
    # yxy4vn: band rows ya..-28, column x xa..-63; the hole starts right of the column and under the band
    hx, hy = -62, -27
    rows = [list(r) for r in steel]
    cw = box(conc)[1] - box(conc)[0] + 1
    rows += place(conc, max(xb + 1, hx + hole_w), ya)
    rows += place(stack, hx, hy + hole_h)
    return rows, (hx, hy, hole_w, hole_h)


def main():
    hw = int(sys.argv[1]) if len(sys.argv) > 1 else 140
    hh = int(sys.argv[2]) if len(sys.argv) > 2 else 140
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


if __name__ == '__main__':
    main()
