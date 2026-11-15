"""Lane order search (john 2026-11-15: put cores side by side): balanced outputs don't care which input sits on
which row, so try every assignment of logical rows to physical rows (and both orders of each balance), route it,
pack moves on disjoint rows into shared columns, and keep the narrowest estimate. planner.py builds the winner.

    python tools/routing/lanesearch.py --n 7
"""
import argparse
import itertools
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))
import planner as P  # noqa: E402

WID = {'H': 6, 'T': 9, 'X': 6, 'L': 20}


def estimate(N, moves):
    """greedy packing by footprint only (3 rows a piece, loops take all rows)"""
    layers, last = [], {}
    for mv in moves:
        R = P.piece_rows(mv[0], mv[1], N)
        start = max([last[r] + 1 for r in R if r in last] + [0])
        placed = None
        for li in range(start, len(layers)):
            if not (layers[li][0] & R) and mv[0] != 'L' and layers[li][2] != 'L':
                layers[li][0].update(R); layers[li][1] = max(layers[li][1], WID[mv[0]]); placed = li
                break
        if placed is None:
            layers.append([set(R), WID[mv[0]], mv[0]]); placed = len(layers) - 1
        for r in R:
            last[r] = placed
    return sum(l[1] for l in layers), len(layers)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, required=True)
    a = ap.parse_args()
    N = a.n
    base = P.route(N, P.logical(list(range(N))))
    print('identity order: width %d, %d layers, %d moves' % (*estimate(N, base), len(base)))
    best = None
    for perm in itertools.permutations(range(N)):
        try:
            mv = P.route(N, P.logical(list(perm)))
        except Exception:
            continue
        w, nl = estimate(N, mv)
        if best is None or (w, len(mv)) < best[0]:
            best = ((w, len(mv)), perm, mv, nl)
            print('order %s: width %d, %d layers, %d moves' % (perm, w, nl, len(mv)), flush=True)
    print('best', best[1])


if __name__ == '__main__':
    main()
