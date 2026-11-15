"""7:7 from 3:3 boxes (john 2026-11-15): stage = two 3:3 boxes stacked (rows 0-2, 3-5, row 6 passing), then a
mixer M that turns rows a,a,a,b,b,b into six (a+b)/2 (solved as a small gadget: rows inside each group are
already equal), then the 1/7 loop section on rows 5,6, then the stage again. Exact check.

    python tools/routing/compose_33.py --box ~/data/box33_18x3.txt --out ~/data/n77_b33.txt
"""
import argparse
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'data'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
import planner as P  # noqa: E402
import relax as R  # noqa: E402
import nchain  # noqa: E402
from bmsolver.model import Window  # noqa: E402
from bmsolver import model  # noqa: E402
from bmsolver.pieces import mirror_code  # noqa: E402

EDGE = {'W': 1, 'N': 0, 'E': 3, 'S': 2}


def parse(rows):
    W = len(rows[0]) // 2
    return {(x, y): rows[y][2 * x:2 * x + 2] for y in range(len(rows)) for x in range(W)
            if rows[y][2 * x:2 * x + 2] != '..'}, W


def stage(box, N=7):
    g, W = parse(box)
    out = dict(g)
    flip = {'0': '2', '2': '0', '1': '1', '3': '3'}
    # the lower box upside down: its bottom row meets the upper box's bottom row, splitter beside splitter, so
    # neither spills into the other (a splitter takes nothing from the side)
    out.update({(x, 5 - y): c[0] + flip[c[1]] for (x, y), c in g.items()})
    out = R.relay(out, W, N, [(6, 0, W - 1)])
    return [[out.get((x, y)) for x in range(W)] for y in range(N)] if out else None


def mixer(N=7, D=12, wmin=3, wmax=14, timeout=300):
    """rows 0-2 carry a, 3-5 carry b, row 6 x: out rows 0-5 (a+b)/2 each, row 6 x (commodities a, b, x)"""
    for W in range(wmin, wmax + 1):
        w = Window(W, N, 3, D)
        ins = [[D, 0, 0]] * 3 + [[0, D, 0]] * 3 + [[0, 0, D]]
        outs = [[D // 2, D // 2, 0]] * 6 + [[0, 0, D]]
        w.port_options = [[{((0, i), EDGE['W']): ('in', v) for i, v in enumerate(ins)}],
                          [{((W - 1, i), EDGE['E']): ('out', v) for i, v in enumerate(outs)}]]
        r = model.solve(w, timeout=timeout, workers=int(os.environ.get('WORKERS', 2)))
        print('mixer %dx%d: %s' % (W, N, r['status']), flush=True)
        if 'grid' in r:
            g = r['grid']
            return [[mirror_code(g[(W - 1 - x, y)], 'x') if g.get((W - 1 - x, y)) else None for x in range(W)]
                    for y in range(N)]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--box', required=True)
    ap.add_argument('--out', default=None)
    a = ap.parse_args()
    N = 7
    box = [l.rstrip('\n') for l in open(os.path.expanduser(a.box)) if l.strip()]
    S1 = stage(box)
    M = mixer()
    if S1 is None or M is None:
        raise SystemExit('no stage or mixer')
    for x_low in (False, True):
        L = P.section(N, N, N - 2, x_low)
        blocks = [S1, M, L, S1, M]                # east to west
        full = [sum((b[y] for b in reversed(blocks)), []) for y in range(N)]
        ok = nchain.check(full, N)
        print('7:7 from 3:3 boxes, %d wide, exact %s' % (len(full[0]), ok), flush=True)
        if ok:
            if a.out:
                open(os.path.expanduser(a.out), 'w').write('\n'.join(''.join(c or '..' for c in r) for r in full) + '\n')
            return


if __name__ == '__main__':
    main()
