"""Cube and conquer for one network placement (step 4 of john's pipeline): split the question "does this network fit
W x H" by which input row carries which network input (one cube per automorphism orbit), run the cubes in parallel,
record every finished cube so a restart carries on where it stopped. All cubes INFEASIBLE = the network does not fit;
any FEASIBLE cube = a layout.

    python tools/routing/netcube.py --nets ~/data/nets6c.jsonl --i 0 --W 27 --H 6 --procs 4 --out ~/data/cubes6.jsonl
"""
import argparse
import json
import os
import sys
import time
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from bmsolver import spec as S, netplace as NP  # noqa: E402


def run(args):
    net, n, W, H, cube, timeout = args
    win = S.Spec([S.Group('E', n)], [S.Group('W', n)]).window(W, H)
    r = NP.place(net, win, timeout=timeout, workers=1, assign={int(k): v for k, v in cube.items()})
    out = dict(status=r['status'], time=round(r['time'], 1))
    if 'grid' in r:
        g = r['grid']
        out['rows'] = [''.join(g.get((x, y)) or '..' for x in range(W)) for y in range(H)]
    return cube, out


def solve(net, n, W, H, procs=4, timeout=3600, out=None, tag='', log=print):
    rows = list(range(n))                        # spec windows put the inputs on rows 0..n-1
    cubes = NP.input_cubes(net, rows)
    done = {}
    if out and os.path.exists(os.path.expanduser(out)):
        for l in open(os.path.expanduser(out)):
            d = json.loads(l)
            if d['tag'] == tag and (d['W'], d['H']) == (W, H) and d['status'] in ('INFEASIBLE', 'OPTIMAL', 'FEASIBLE'):
                done[json.dumps(d['cube'], sort_keys=True)] = d
    todo = [c for c in cubes if json.dumps({str(k): v for k, v in c.items()}, sort_keys=True) not in done]
    log('%s %dx%d: %d cubes, %d already done' % (tag, W, H, len(cubes), len(cubes) - len(todo)))
    for d in done.values():
        if d['status'] in ('OPTIMAL', 'FEASIBLE'):
            return 'FEASIBLE', d.get('rows')
    t0 = time.time()
    unknown = 0
    with Pool(procs) as pool:
        for cube, res in pool.imap_unordered(run, [(net, n, W, H, {str(k): v for k, v in c.items()}, timeout)
                                                   for c in todo]):
            log('  cube %s: %s %.0fs' % (cube, res['status'], res['time']))
            if out:
                with open(os.path.expanduser(out), 'a') as fh:
                    fh.write(json.dumps(dict(tag=tag, W=W, H=H, cube=cube, **res)) + '\n')
            if res['status'] in ('OPTIMAL', 'FEASIBLE'):
                pool.terminate()
                return 'FEASIBLE', res.get('rows')
            if res['status'] != 'INFEASIBLE':
                unknown += 1
    log('%s %dx%d: %s in %.0fs' % (tag, W, H, 'UNKNOWN' if unknown else 'INFEASIBLE', time.time() - t0))
    return ('UNKNOWN' if unknown else 'INFEASIBLE'), None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--nets', required=True)
    ap.add_argument('--i', default='0', help='network indices, e.g. 0 or 0-9')
    ap.add_argument('--W', type=int, required=True)
    ap.add_argument('--H', type=int, required=True)
    ap.add_argument('--procs', type=int, default=4)
    ap.add_argument('--timeout', type=float, default=3600)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    nets = [json.loads(l) for l in open(os.path.expanduser(a.nets)) if l.strip()]
    lo, hi = ([int(v) for v in a.i.split('-')] + [None])[:2]
    for i in range(lo, (hi if hi is not None else lo) + 1):
        d = nets[i]
        st, rows = solve(d['net'], d['n'], a.W, a.H, a.procs, a.timeout, a.out, tag='%s#%d' % (
            os.path.basename(a.nets), i), log=lambda s: print(s, flush=True))
        print('net %d: %s' % (i, st), flush=True)
        if rows:
            print('\n'.join(rows), flush=True)


if __name__ == '__main__':
    main()
