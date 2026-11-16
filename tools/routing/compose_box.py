"""Compose from compact boxes (john 2026-11-15): use whole proven balancer boxes instead of long 3-row pieces.
p:p for a prime p = (p-1):(p-1) box on rows 0..p-2 with row p-1 passing, loop section on rows p-2, p-1, the box
again. Exact check, then shrink.py.

    python tools/routing/compose_box.py --n 7 --box ~/data/box66_28x6.txt --out ~/data/n77_box.txt
"""
import argparse
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'data'))
import planner as P  # noqa: E402
import relax as R  # noqa: E402
import nchain  # noqa: E402


def box_block(rows, N):
    """box rows on top, the remaining rows pass as westward stretches laid around its splitters"""
    W = len(rows[0]) // 2
    g = {}
    for y, r in enumerate(rows):
        for x in range(W):
            c = r[2 * x:2 * x + 2]
            if c != '..':
                g[(x, y)] = c
    st = [(y, 0, W - 1) for y in range(len(rows), N)]
    g = R.relay(g, W, N, st)
    return [[g.get((x, y)) for x in range(W)] for y in range(N)] if g else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, required=True)
    ap.add_argument('--box', required=True)
    ap.add_argument('--out', default=None)
    a = ap.parse_args()
    N = a.n
    box = [l.rstrip('\n') for l in open(os.path.expanduser(a.box)) if l.strip()]
    b = box_block(box, N)
    sec = P.section(N, N, N - 2, False)
    if b is None or sec is None:
        raise SystemExit('could not lay the passing row or find the loop section')
    for x_low in (False, True):
        sec = P.section(N, N, N - 2, x_low)
        blocks = [b, sec, b]                    # east to west
        full = [sum((blk[y] for blk in reversed(blocks)), []) for y in range(N)]
        ok = nchain.check(full, N)
        print('%d:%d from boxes, %d wide, x_low=%s, exact %s' % (N, N, len(full[0]), x_low, ok), flush=True)
        if ok:
            if a.out:
                open(os.path.expanduser(a.out), 'w').write('\n'.join(''.join(c or '..' for c in r) for r in full) + '\n')
            return


if __name__ == '__main__':
    main()
