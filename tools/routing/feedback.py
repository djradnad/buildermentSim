"""Feedback balancer (2026-11-15, john: others build 7:7 as 8:8 with one output fed back): take an M:M chain and
return its last output to its last input along an extra row underneath (hops eastward, kept empty under splitters),
giving an (M-1):(M-1) balancer M+1 tall. shrink.py then squeezes rows and columns.

    python tools/routing/feedback.py --m 8 --grid ~/data/plan_n8.txt --out ~/data/fb_77.txt
"""
import argparse
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'data'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
import relax as R  # noqa: E402
from bmsolver import spec as S, bench  # noqa: E402


def feedback(g, W, M):
    out = {(x + 1, y): c for (x, y), c in g.items()}
    W2, H2 = W + 2, M + 1
    for y in range(M - 1):
        out[(0, y)] = 'B1'; out[(W2 - 1, y)] = 'B1'
    out[(0, M - 1)] = 'B2'                      # last output turns down
    out[(W2 - 1, M)] = 'B0'; out[(W2 - 1, M - 1)] = 'B1'   # and comes back up into the last input
    # return run on row M: x=0 is a belt turning east (fed from above), then x=1..W2-2 laid eastward
    L = W2 - 2
    exposed = {x for x in range(1, W2 - 1) if R.pushes_into(out, x, M)}
    seq = R.lay(L, {L - x for x in exposed})              # lay westward over L tiles, then mirror
    if seq is None:
        return None, W2, H2
    m = {'B1': 'B3', 'I1': 'I3', 'O1': 'O3'}
    for i, c in enumerate(seq):
        if c:
            out[(L - i, M)] = m[c]
    out[(0, M)] = 'B3'
    return out, W2, H2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--m', type=int, required=True)
    ap.add_argument('--grid', required=True)
    ap.add_argument('--out', default=None)
    a = ap.parse_args()
    from shrink import auto_unit
    g, W, H = R.parse([l.rstrip('\n') for l in open(os.path.expanduser(a.grid)) if l.strip()])
    g2, W2, H2 = feedback(g, W, a.m)
    n = a.m - 1
    win = S.Spec([S.Group('E', n, at=0)], [S.Group('W', n, at=0)], unit_mult=auto_unit(n, g2, W2, H2)).window(W2, H2)
    ok = win is not None and bench.verify(g2, win, None)
    print('%d:%d from %d:%d with feedback, %dx%d, exact %s' % (n, n, a.m, a.m, W2, H2, ok))
    print('\n'.join(R.rows_of(g2, W2, H2)))
    if a.out and ok:
        open(os.path.expanduser(a.out), 'w').write('\n'.join(R.rows_of(g2, W2, H2)) + '\n')


if __name__ == '__main__':
    main()
