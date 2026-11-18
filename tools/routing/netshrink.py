"""Network-exact band shrink (john 2026-11-18): drop one column of a layout, pin every tile outside a band of
columns around the cut, and let the mix-class placer (netplace.build_cls) re-lay the band for the SAME parts. A find
is checked exact (extract + exact flows). Gives layouts only, never proofs.

    python tools/routing/netshrink.py --grid ~/data/box66_28x6_fixed.txt --n 6 --band 4 --timeout 120
"""
import argparse
import os
import sys
import time
from ortools.sat.python import cp_model
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from bmsolver import spec as S, netplace as NP  # noqa: E402


def read(path):
    rows = [l.strip() for l in open(os.path.expanduser(path)) if l.strip()]
    W, H = len(rows[0]) // 2, len(rows)
    return W, H, {(x, y): rows[y][2 * x:2 * x + 2] for y in range(H) for x in range(W) if rows[y][2 * x:2 * x + 2] != '..'}


def show(g, W, H):
    return '\n'.join(''.join(g.get((x, y)) or '..' for x in range(W)) for y in range(H))


def try_cut(net, g, W, H, n, c, band, timeout, workers):
    """drop column c, free columns [c-band, c+band) of the W-1 wide box"""
    win = S.Spec([S.Group('E', n)], [S.Group('W', n)]).window(W - 1, H)
    mod = NP.build_cls(net, win)
    lo, hi = max(0, c - band), min(W - 1, c + band)
    for x in range(W - 1):
        src = x if x < c else x + 1
        for y in range(H):
            code = g.get((src, y)) or 'E'
            v = mod.X.get(((x, y), code))
            if lo <= x < hi:
                if v is not None:
                    mod.m.AddHint(v, 1)
            elif v is None:
                return 'BADPIN', None
            else:
                mod.m.Add(v == 1)
    sv = cp_model.CpSolver()
    sv.parameters.num_workers = workers
    sv.parameters.max_time_in_seconds = timeout
    st = sv.Solve(mod.m)
    if st in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        g2 = mod.grid(sv)
        return ('FOUND' if NP.check_layout(g2, win, n) else 'FAKE'), g2
    return sv.StatusName(st), None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--grid', required=True)
    ap.add_argument('--n', type=int, required=True)
    ap.add_argument('--band', type=int, default=4)
    ap.add_argument('--timeout', type=float, default=120)
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--out', default=None)
    a = ap.parse_args()
    W, H, g = read(a.grid)
    win = S.Spec([S.Group('E', a.n)], [S.Group('W', a.n)]).window(W, H)
    net = NP.extract(g, win)
    print('%dx%d, %d splitters, %d merges' % (W, H, len(net['split']), len(net['merge'])), flush=True)
    while True:
        best = None
        for c in range(W):
            t0 = time.time()
            st, g2 = try_cut(net, g, W, H, a.n, c, a.band, a.timeout, a.workers)
            print('  cut col %d band %d: %s %.0fs' % (c, a.band, st, time.time() - t0), flush=True)
            if st == 'FOUND':
                best = g2
                break
        if best is None:
            print('no cut works at band %d' % a.band, flush=True)
            return
        W, g = W - 1, best
        print('FOUND %dx%d\n%s' % (W, H, show(g, W, H)), flush=True)
        if a.out:
            open(os.path.expanduser(a.out), 'w').write(show(g, W, H) + '\n')
        net = NP.extract(g, S.Spec([S.Group('E', a.n)], [S.Group('W', a.n)]).window(W, H))


if __name__ == '__main__':
    main()
