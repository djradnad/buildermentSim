"""Box with a passing belt (john 2026-11-15): an n:n balancer in n+k rows where k extra belts pass straight
through (rows n..n+k-1), for composing p:p at p tall from boxes. Full flow model, width sweep down from --wmax.

    python tools/routing/boxpass.py --n 6 --pass 1 --wmax 24 --wmin 10 --timeout 900
"""
import argparse
import json
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from bmsolver.model import Window  # noqa: E402
from bmsolver import model, bench  # noqa: E402
from bmsolver.pieces import mirror_code  # noqa: E402

EDGE = {'W': 1, 'N': 0, 'E': 3, 'S': 2}


def solve(n, k, W, D, timeout):
    H = n + k
    w = Window(W, H, H, D)
    ins = [[D if j == i else 0 for j in range(H)] for i in range(H)]
    outs = [[D // n if j < n else 0 for j in range(H)] for i in range(n)] + [list(ins[n + i]) for i in range(k)]
    w.port_options = [[{((0, i), EDGE['W']): ('in', v) for i, v in enumerate(ins)}],
                      [{((W - 1, i), EDGE['E']): ('out', v) for i, v in enumerate(outs)}]]
    r = model.solve(w, timeout=timeout, workers=int(os.environ.get('WORKERS', 4)))
    if 'grid' in r:
        g = r['grid']
        return r['status'], [''.join((mirror_code(g[(W - 1 - x, y)], 'x') if g.get((W - 1 - x, y)) else '..')
                                     for x in range(W)) for y in range(H)]
    return r['status'], None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, required=True)
    ap.add_argument('--pass', dest='k', type=int, default=1)
    ap.add_argument('--wmax', type=int, default=24)
    ap.add_argument('--wmin', type=int, default=8)
    ap.add_argument('--D', type=int, default=0)
    ap.add_argument('--timeout', type=float, default=900)
    a = ap.parse_args()
    D = a.D or 6 * a.n
    for W in range(a.wmax, a.wmin - 1, -1):
        st, rows = solve(a.n, a.k, W, D, a.timeout)
        print('%d:%d + %d passing, %dx%d: %s' % (a.n, a.n, a.k, W, a.n + a.k, st), flush=True)
        if rows:
            print('\n'.join(rows), flush=True)
            with open(os.path.expanduser('~/data/boxpass.jsonl'), 'a') as fh:
                fh.write(json.dumps(dict(n=a.n, k=a.k, W=W, H=a.n + a.k, rows=rows)) + '\n')
        elif st != 'UNKNOWN':
            break


if __name__ == '__main__':
    main()
