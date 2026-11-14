"""Shrink a found N:N layout one column or row at a time (band LNS): delete column c, keep every tile outside a
band of b columns around it fixed, and let the complete flow model re-solve the band. A hit is checked exactly and
shrunk again. Good for big ratios (5:5, 6:6, ...) where a whole-box solve runs out of time.

    python tools/data/shrink.py --n 5 --grid file.txt --timeout 60 --out ~/data/shrink_5.jsonl
The grid file holds one row per line in the catalogue format ('..' empty, 2 chars per tile), inputs east, outputs
west.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from bmsolver import spec as S, model, bench  # noqa: E402


def parse(rows):
    g = {}
    for y, row in enumerate(rows):
        for x in range(0, len(row), 2):
            c = row[x:x + 2]
            if c != '..':
                g[(x // 2, y)] = c
    return g, len(rows[0]) // 2, len(rows)


def rows_of(g, W, H):
    return [''.join(g.get((x, y)) or '..' for x in range(W)) for y in range(H)]


def drop(g, axis, c):
    out = {}
    for (x, y), v in g.items():
        k = (x, y)[axis]
        if k == c:
            continue
        if k > c:
            x, y = (x - 1, y) if axis == 0 else (x, y - 1)
        out[x, y] = v
    return out


def attempt(n, g, W, H, axis, c, b, timeout, workers):
    g2 = drop(g, axis, c)
    W2, H2 = (W - 1, H) if axis == 0 else (W, H - 1)
    win = S.Spec([S.Group('E', n)], [S.Group('W', n)]).window(W2, H2)
    if win is None:
        return None
    lo, hi = c - b // 2, c - b // 2 + b
    win.fixed = {p: g2.get(p) for p in [(x, y) for x in range(W2) for y in range(H2)]
                 if not (lo <= p[axis] < hi)}
    r = model.solve(win, timeout=timeout, workers=workers)
    if 'grid' in r and bench.verify(r['grid'], win, r.get('options')):
        return r['grid'], W2, H2
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, required=True)
    ap.add_argument('--grid', required=True)
    ap.add_argument('--timeout', type=float, default=60)
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--bands', default='4,6,8')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    g, W, H = parse([l.rstrip('\n') for l in open(a.grid) if l.strip()])
    improved = True
    while improved:
        improved = False
        # shrink the longer side first (most square first)
        axes = (0, 1) if W >= H else (1, 0)
        for axis in axes:
            n_lines = W if axis == 0 else H
            for b in map(int, a.bands.split(',')):
                for c in range(1, n_lines - 1):
                    t = time.time()
                    r = attempt(a.n, g, W, H, axis, c, b, a.timeout, a.workers)
                    print('%dx%d drop %s %d band %d: %s %.0fs' % (W, H, 'col' if axis == 0 else 'row', c, b,
                                                                  'ok' if r else '-', time.time() - t), flush=True)
                    if r:
                        g, W, H = r
                        with open(os.path.expanduser(a.out), 'a') as fh:
                            fh.write(json.dumps(dict(n=a.n, W=W, H=H, grid=rows_of(g, W, H))) + '\n')
                        print('\n'.join(rows_of(g, W, H)), flush=True)
                        improved = True
                        break
                if improved:
                    break
            if improved:
                break


if __name__ == '__main__':
    main()
