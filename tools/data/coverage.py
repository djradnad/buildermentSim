"""Coverage of the N:N balancer solves: for every box W x H (asym and sym) what we know.

    F  a layout was found and checked
    P  proven impossible by the complete flow model (flowcheck.jsonl: every port placement)
    s  only the staged (split-then-merge) model found nothing: NOT a proof
    ?  tried, undecided (time ran out)
    .  never tried
    f, p  implied: a box holding a found box (one wider: add a belt column; asym one taller: add a row; sym two
          taller) also fits; a box inside a proven-impossible one is impossible too

    python tools/data/coverage.py 2,3,4
"""
import collections
import json
import os
import sys


def load():
    box = collections.defaultdict(lambda: collections.defaultdict(set))   # (n, sym) -> (W, H) -> statuses
    place = collections.defaultdict(lambda: collections.defaultdict(dict))  # placements tried per box
    for f in ('front.jsonl', 'front_sym.jsonl', 'scored.jsonl', 'polished.jsonl'):
        p = os.path.expanduser('~/data/' + f)
        if not os.path.exists(p):
            continue
        for l in open(p):
            r = json.loads(l)
            if 'n' not in r or 'W' not in r:
                continue
            sym = bool(r.get('sym'))
            k = (r['n'], sym)
            st = r.get('status', 'FEASIBLE' if r.get('grid') or r.get('text') else '')
            if st in ('FEASIBLE', 'OPTIMAL') and r.get('ok', True):
                box[k][r['W'], r['H']].add('F')
            elif st == 'INFEASIBLE':
                place[k][r['W'], r['H']][tuple(r.get('at', ()))] = 'I'
            elif st == 'UNKNOWN':
                place[k][r['W'], r['H']].setdefault(tuple(r.get('at', ())), 'U')
    for k, d in place.items():
        n, sym = k
        for (W, H), pl in d.items():
            if 'F' in box[k][W, H]:
                continue
            kk = H - n
            need = 1 if sym else len([(a, b) for a in range(kk + 1) for b in range(kk + 1) if (a, b) <= (kk - a, kk - b)])
            if len(pl) >= need and all(v == 'I' for v in pl.values()):
                box[k][W, H].add('s')
            else:
                box[k][W, H].add('?')
    p = os.path.expanduser('~/data/flowcheck.jsonl')
    if os.path.exists(p):
        for l in open(p):
            r = json.loads(l)
            parts = r['box'].split(':')
            n = int(parts[0])
            W, H = map(int, parts[1].split('x'))
            sym = len(parts) > 2
            if r['status'] == 'INFEASIBLE':
                box[n, sym][W, H].add('P')
            elif r.get('ok'):
                box[n, sym][W, H].add('F')
            elif r['status'] == 'UNKNOWN':
                box[n, sym][W, H].add('?')
    for n, W, H, sym in KNOWN_FOUND:
        box[n, sym][W, H].add('F')
    return box


KNOWN_FOUND = [(4, 7, 7, False)]     # 4:4 7x7 bp 2ydazb (flow model cube run 2026-11-04; splitters face non-port edges)


def infer(d, sym):
    """f/p by containment (see the top)"""
    st = {k: mark(v) for k, v in d.items()}
    F = [k for k, v in st.items() if v == 'F']
    P = [k for k, v in st.items() if v == 'P']
    step = 2 if sym else 1
    out = {}
    Wm = max([W for W, H in d] + [1]) + 1
    Hm = max([H for W, H in d] + [1]) + 1
    for W in range(1, Wm):
        for H in range(1, Hm):
            if (W, H) in st and st[W, H] in 'FP':
                continue
            if any(W >= a and H >= b and (H - b) % step == 0 for a, b in F):
                out[W, H] = 'f'
            elif any(W <= a and H <= b and (H - b) % step == 0 for a, b in P):
                out[W, H] = 'p'
    return out


def mark(s):
    for c in 'FPs?':
        if c in s:
            return c
    return '.'


def table(n, sym, box):
    d = box[n, sym]
    if not d:
        print('%d:%d %s: no data' % (n, n, 'sym' if sym else 'asym'))
        return
    Wm = max(W for W, H in d) + 1
    Hm = max(H for W, H in d) + 1
    inf = infer(d, sym)
    print('%d:%d %s   (rows = height, columns = width)' % (n, n, 'sym' if sym else 'asym'))
    print('     ' + ''.join('%3d' % W for W in range(1, Wm)))
    for H in range(1, Hm):
        print('  %2d ' % H + ''.join('  ' + (inf.get((W, H)) or mark(d.get((W, H), ()))) for W in range(1, Wm)))


if __name__ == '__main__':
    box = load()
    for n in map(int, sys.argv[1].split(',')):
        for sym in (False, True):
            table(n, sym, box)
            print()
