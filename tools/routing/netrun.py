"""Place every enumerated network (netenum.py output) in an N:N box with bmsolver/netplace.py; results appended
as JSON lines (status per network, grid when found). Networks already tried at this size are skipped.

    python tools/routing/netrun.py --nets ~/data/nets3.jsonl --W 17 --H 3 --out ~/data/netrun3.jsonl
"""
import argparse
import json
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from bmsolver import spec as S, netplace as NP  # noqa: E402


def key(net):
    return json.dumps(net, sort_keys=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--nets', required=True)
    ap.add_argument('--W', type=int, required=True)
    ap.add_argument('--H', type=int, required=True)
    ap.add_argument('--timeout', type=float, default=600)
    ap.add_argument('--workers', type=int, default=2)
    ap.add_argument('--part', default='0/1', help='k/m: only networks with index %% m == k (parallel runs)')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    k, mm = [int(v) for v in a.part.split('/')]
    done = set()
    if os.path.exists(os.path.expanduser(a.out)):
        for l in open(os.path.expanduser(a.out)):
            d = json.loads(l)
            if (d['W'], d['H']) == (a.W, a.H):
                done.add(d['key'])
    nets = [json.loads(l) for l in open(os.path.expanduser(a.nets)) if l.strip()]
    for i, d in enumerate(nets):
        if i % mm != k:
            continue
        net, n = d['net'], d['n']
        kk = key(net)
        if kk in done:
            continue
        win = S.Spec([S.Group('E', n)], [S.Group('W', n)]).window(a.W, a.H)
        r = NP.place(net, win, timeout=a.timeout, workers=a.workers)
        rec = dict(W=a.W, H=a.H, i=i, s=d['s'], key=kk, status=r['status'], time=round(r['time'], 1),
                   same=r.get('same_network'))
        if 'grid' in r:
            g = r['grid']
            rec['rows'] = [''.join(g.get((x, y)) or '..' for x in range(a.W)) for y in range(a.H)]
        print('net %d (s=%d) %dx%d: %s %.0fs' % (i, d['s'], a.W, a.H, r['status'], r['time']), flush=True)
        if 'rows' in rec:
            print('\n'.join(rec['rows']), flush=True)
        with open(os.path.expanduser(a.out), 'a') as fh:
            fh.write(json.dumps(rec) + '\n')


if __name__ == '__main__':
    main()
