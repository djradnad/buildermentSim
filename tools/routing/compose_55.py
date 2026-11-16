"""5:5 at 5 tall from pieces (2026-11-15): 4:4 on rows 0-3 (row 4 hops through), a 5-row loop section
(belts 0-2 squeezed past, belts 3,4 mixed 1/5-4/5 so row 4 ends 4/5 q + 1/5 x), 4:4 on rows 0-3 again.

    python tools/routing/compose_55.py --section ~/data/gadgets15/mix5.jsonl --out ~/data/n55_chain.txt
"""
import argparse
import json
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
import nchain  # noqa: E402
from bmsolver.pieces import mirror_code  # noqa: E402

P44 = [('H', 0), ('H', 2), ('H', 1), ('X', 0), ('X', 2), ('H', 1)]


def section(path, W=None):
    """last traced mix5 section from the gadget log, turned to inputs east / outputs west"""
    best = None
    for l in open(os.path.expanduser(path)):
        r = json.loads(l)
        if r['traced'] and (W is None or r['W'] == W):
            best = r
    g = {tuple(map(int, k.split(','))): c for k, c in best['grid'].items()}
    W, H = best['W'], best['H']
    return [[mirror_code(g[(W - 1 - x, y)], 'x') if (W - 1 - x, y) in g else None for x in range(W)] for y in range(H)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--section', default='~/data/gadgets15/mix5.jsonl')
    ap.add_argument('--out', default=None)
    a = ap.parse_args()
    N = 5
    blocks = [nchain.place(op, r, N) for op, r in P44] + [section(a.section)] + [nchain.place(op, r, N) for op, r in P44]
    full = [sum((b[y] for b in reversed(blocks)), []) for y in range(N)]
    ok = nchain.check(full, N)
    rows = [''.join(c or '..' for c in row) for row in full]
    print('5:5 at 5 tall, chain %d wide, exact check %s' % (len(full[0]), ok))
    print('\n'.join(rows))
    if a.out and ok:
        open(os.path.expanduser(a.out), 'w').write('\n'.join(rows) + '\n')


if __name__ == '__main__':
    main()
