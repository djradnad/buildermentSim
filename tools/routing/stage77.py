"""3:3 shapes with fewer splitters inside the 7:7 (john 2026-11-15): the compact site 3:3 boxes (10x4, 8x5, 7x6)
use 3 splitters where the 3-row 18x3 needs 6. Inside a 7-tall 7:7 a 3:3 may borrow rows from its neighbours as
long as their belts still pass (hops). Two window solves, splitter count capped:

  piece4: a 3:3 plus one passing belt in 4 rows
  stage7: the whole first stage, two 3:3s (rows 0-2, 3-5) plus row 6 passing, in 7 rows

Commodities: one per input belt.

    python tools/routing/stage77.py --what stage7 --wmin 8 --wmax 17 --splitters 6
"""
import argparse
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from bmsolver.model import Window  # noqa: E402
from bmsolver import model  # noqa: E402
from bmsolver.pieces import mirror_code  # noqa: E402

EDGE = {'W': 1, 'N': 0, 'E': 3, 'S': 2}


def problem(what, D):
    t = D // 3
    e = lambda k, i: [D * (j == i) for j in range(k)]
    if what == 'piece4':                        # commodities a0, a1, a2, x; the passing belt on row 3
        return 4, 4, [e(4, i) for i in range(4)], [[t, t, t, 0]] * 3 + [e(4, 3)]
    # stage7: a0..a2, b0..b2, x
    return 7, 7, [e(7, i) for i in range(7)], [[t] * 3 + [0] * 4] * 3 + [[0] * 3 + [t] * 3 + [0]] * 3 + [e(7, 6)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--what', default='stage7', choices=['piece4', 'stage7', 'boxpass'])
    ap.add_argument('--wmin', type=int, default=8)
    ap.add_argument('--wmax', type=int, default=17)
    ap.add_argument('--splitters', type=int, default=None)
    ap.add_argument('--D', type=int, default=12)
    ap.add_argument('--timeout', type=float, default=1800)
    ap.add_argument('--workers', type=int, default=2)
    ap.add_argument('--down', action='store_true', help='walk widths down from wmax, keep going while feasible')
    ap.add_argument('--out', default=None)
    ap.add_argument('--shape', default=None, help='piece4: a 3:3 box grid (inputs east) whose splitters are pinned')
    ap.add_argument('--rows', default='1,2,3,0:0,1,2,3', help='piece4: box input rows + pass row : box output rows + '
                    'pass row (game frame, west side outputs)')
    ap.add_argument('--dx', default='0', help='piece4 --shape: columns added east of the shape (comma list to try)')
    ap.add_argument('--rot', default='0', choices=['0', 'cw', 'ccw'], help='turn the shape 90 degrees (john)')
    ap.add_argument('--dy', default='0', help='rows above the placed shape (comma list to try)')
    a = ap.parse_args()
    H, nc, ins, outs = problem(a.what, a.D)
    if a.what == 'piece4':
        ri, ro = [[int(v) for v in h.split(',')] for h in a.rows.split(':')]
        D, t = a.D, a.D // 3
        e = lambda i: [D * (j == i) for j in range(4)]
        ins = [None] * 4; outs = [None] * 4
        for k, y in enumerate(ri):
            ins[y] = e(k)
        for k, y in enumerate(ro):
            outs[y] = [t, t, t, 0] if k < 3 else e(3)
    if a.what == 'boxpass':                     # a 3:3 box shape inside 7 rows, the other 4 belts pass in order
        H = 7
        ri, ro = [[int(v) for v in h.split(',')] for h in a.rows.split(':')]
        pi = [y for y in range(H) if y not in ri]; po = [y for y in range(H) if y not in ro]
        nc, D, t = 7, a.D, a.D // 3
        e = lambda i: [D * (j == i) for j in range(nc)]
        ins = [None] * H; outs = [None] * H
        for k, y in enumerate(ri):
            ins[y] = e(k); outs[ro[k]] = [t, t, t, 0, 0, 0, 0]
        for k, (y, z) in enumerate(zip(pi, po)):
            ins[y] = e(3 + k); outs[z] = e(3 + k)
    shape = None
    if a.shape:
        rows = [l.rstrip('\n') for l in open(os.path.expanduser(a.shape)) if l.strip()]
        SW = len(rows[0]) // 2
        shape = {(SW - 1 - x, y): mirror_code(rows[y][2 * x:2 * x + 2], 'x') for y in range(len(rows))
                 for x in range(SW) if rows[y][2 * x:2 * x + 2] != '..'}     # solver frame: inputs at x = 0
        SH = len(rows)
        if a.rot == 'cw':                       # (x, y) -> (SH-1-y, x); N->E, W->N, S->W, E->S
            shape = {(SH - 1 - y, x): c[0] + str((int(c[1]) + 3) % 4) for (x, y), c in shape.items()}
            SW, SH = SH, SW
        elif a.rot == 'ccw':
            shape = {(y, SW - 1 - x): c[0] + str((int(c[1]) + 1) % 4) for (x, y), c in shape.items()}
            SW, SH = SH, SW
    widths = range(a.wmax, a.wmin - 1, -1) if a.down else range(a.wmin, a.wmax + 1)
    jobs = [(W, dx, dy) for W in widths for dx in ([int(v) for v in a.dx.split(',')] if shape else [0])
            for dy in ([int(v) for v in a.dy.split(',')] if shape else [0])
            if not shape or (dx + SW <= W and dy + SH <= H)]
    for W, dx, dy in jobs:
        w = Window(W, H, nc, a.D)
        w.max_splitters = a.splitters
        if shape:
            for (x, y), c in shape.items():
                if c[0] == 'S':
                    w.fixed[(x + dx, y + dy)] = c
                w.hint[(x + dx, y + dy)] = c
        w.port_options = [[{((0, i), EDGE['W']): ('in', v) for i, v in enumerate(ins)}],
                          [{((W - 1, i), EDGE['E']): ('out', v) for i, v in enumerate(outs)}]]
        r = model.solve(w, timeout=a.timeout, workers=a.workers)
        print('%s %s %dx%d dx %d dy %d splitters<=%s: %s' % (a.what, a.rot, W, H, dx, dy, a.splitters, r['status']), flush=True)
        if 'grid' in r:
            g = r['grid']
            rows = [''.join(mirror_code(g[(W - 1 - x, y)], 'x') if g.get((W - 1 - x, y)) else '..' for x in range(W))
                    for y in range(H)]
            print('\n'.join(rows), flush=True)
            if a.out:
                open(os.path.expanduser(a.out) + '.%d' % W, 'w').write('\n'.join(rows) + '\n')
            if not a.down:
                return
        elif a.down:
            return


if __name__ == '__main__':
    main()
