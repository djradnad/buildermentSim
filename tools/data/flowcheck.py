"""Re-check boxes the staged search ruled out with the complete flow model (cube and conquer over port placements).
The staged model only knows split-then-merge networks: it rejects john's 4:4 7x7 t6t6fg, so its 'nothing fits' is
no proof. Appends one JSON line per box to --out.

    python tools/data/flowcheck.py --boxes 3:5x9,3:4x10 --timeout 900 --out ~/data/flowcheck.jsonl
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from bmsolver import spec as S, cnf, bench  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--boxes', required=True, help='n:WxH[:sym],...')
    ap.add_argument('--timeout', type=float, default=900, help='per cube')
    ap.add_argument('--procs', type=int, default=2)
    ap.add_argument('--engine', default='~/solvers/kissat')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    out = os.path.expanduser(a.out)
    done = set()
    if os.path.exists(out):
        done = {json.loads(l)['box'] for l in open(out)}
    for box in a.boxes.split(','):
        if box in done:
            continue
        parts = box.split(':')
        n = int(parts[0])
        W, H = map(int, parts[1].split('x'))
        sym = len(parts) > 2 and parts[2] == 'sym'
        win = S.Spec([S.Group('E', n)], [S.Group('W', n)]).window(W, H, symmetry='y' if sym else None)
        t = time.time()
        if win is None:
            r = dict(status='INFEASIBLE', note='ports do not fit')
        else:
            r = cnf.prove(win, os.path.expanduser(a.engine), procs=a.procs, timeout=a.timeout, log=None)
        rec = dict(box=box, status=r['status'], time=round(time.time() - t, 1), cubes=r.get('cubes'))
        if 'grid' in r:
            g = r['grid']
            rec['grid'] = [''.join(g.get((x, y)) or '..' for x in range(W)) for y in range(H)]
            rec['ok'] = bench.verify(g, r['window'])
        with open(out, 'a') as fh:
            fh.write(json.dumps(rec) + '\n')
        print(box, rec['status'], rec['time'], rec.get('ok'), flush=True)


if __name__ == '__main__':
    main()
