"""Polish the found layouts of the training data: fewest pieces (then fewest turns) for each one's ports, with the
staged SAT model (radix.py): ask for one piece fewer until that is ruled out or the time runs out. Every step is
checked with the exact flow check. Duplicates are dropped. Writes one JSON line per kept layout with `pieces`.

    python tools/data/polish.py --data '~/data/sweepv_*.jsonl' --out ~/data/polished.jsonl
"""
import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from bmsolver.design import Design  # noqa: E402
from bmsolver.improve import Layout  # noqa: E402
from bmsolver import radix, spec as S, bench, cnf  # noqa: E402


def fewer_pieces(r, engine, timeout, step_timeout=60):
    """(grid rows, pieces, proven_min) for record r"""
    import time
    t0 = time.time()
    n, W, H = r['n'], r['W'], r['H']
    ia, oa = r['at']
    sym = r.get('sym') is True
    best = r['grid']
    count = sum(1 for row in best for x in range(W) if row[2 * x:2 * x + 2] != '..')
    while time.time() - t0 < timeout:
        win = S.Spec([S.Group('E', n, at=ia)], [S.Group('W', n, at=oa)]).window(W, H, symmetry='y' if sym else None)
        mod = radix.build(win, radix.factorisations(n)[0], pass_splitters=False)
        mod.m.Add(sum(1 - mod.X[p, 'E'] for p in mod.cells) <= count - 1)
        res = cnf.solve(mod, engine=engine, timeout=min(step_timeout, max(1, timeout - (time.time() - t0))))
        if res['status'] == 'INFEASIBLE':
            return best, count, True
        if 'grid' not in res or not bench.verify(res['grid'], win, res.get('options')):
            break
        g = res['grid']
        best = [''.join(g.get((x, y)) or '..' for x in range(W)) for y in range(H)]
        count = sum(1 for c in g.values() if c)
    return best, count, False


def to_layout(r):
    W, H, n = r['W'], r['H'], r['n']
    ia, oa = r['at']
    tiles = {}
    for y, row in enumerate(r['grid']):
        for x in range(W):
            c = row[2 * x:2 * x + 2]
            if c != '..':
                tiles[(x, y)] = c
    ins = [((W - 1, ia + i), 1) for i in range(n)]     # items move west into the east edge
    outs = [((0, oa + i), 1) for i in range(n)]
    return Layout(Design(tiles), ins, outs, mix=n > 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--timeout', type=float, default=120, help='per layout')
    ap.add_argument('--engine', default='~/solvers/cadical')
    a = ap.parse_args()
    seen = set()
    out = os.path.expanduser(a.out)
    if os.path.exists(out):
        for line in open(out):
            r = json.loads(line)
            seen.add((r['n'], r['W'], r['H'], tuple(r['at']), r.get('sym'), tuple(r['src_grid'])))
    for path in sorted(glob.glob(os.path.expanduser(a.data))):
        for line in open(path):
            r = json.loads(line)
            if 'grid' not in r or not r.get('ok'):
                continue
            key = (r['n'], r['W'], r['H'], tuple(r['at']), r.get('sym'), tuple(r['grid']))
            if key in seen:
                continue
            seen.add(key)
            before = sum(1 for row in r['grid'] for x in range(r['W']) if row[2 * x:2 * x + 2] != '..')
            rows, pieces, proven = fewer_pieces(r, os.path.expanduser(a.engine), a.timeout)
            rec = dict(r, src_grid=r['grid'], grid=rows, pieces=pieces, pieces_before=before, min_proven=proven)
            with open(out, 'a') as fh:
                fh.write(json.dumps(rec) + '\n')
            print(key[:5], before, '->', pieces, 'proven' if proven else '', flush=True)


if __name__ == '__main__':
    main()
