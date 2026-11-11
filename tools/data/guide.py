"""First learned guide: predict which tiles a layout leaves empty, then search inside the predicted shape first.

Train: every found layout in the sweep data gives one example per tile (features of the tile and the box, label =
occupied or not). Model: scikit-learn gradient boosting (no GPU needed).

Use (guess and fall back, so answers stay exact): block the K tiles the model is most sure stay empty and solve. A
layout found that way is a real layout of the full box. If the guess is INFEASIBLE, halve K and try again; K = 0
is the plain full search, so nothing is ever lost except the time of failed guesses.

    python tools/data/guide.py train --data ~/data/sweepv_*.jsonl --model ~/data/guide.pkl
    python tools/data/guide.py eval  --model ~/data/guide.pkl --cases 4:9x6:1:1,3:8x5:0:0 --timeout 600
"""
import argparse
import glob
import json
import os
import pickle
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from bmsolver import radix, spec as S, bench, cnf  # noqa: E402


def band_dist(y, a, n):
    """signed distance from row y to the port band a..a+n-1 (0 inside)"""
    return y - a if y < a else y - (a + n - 1) if y > a + n - 1 else 0


def features(n, W, H, ia, oa, x, y):
    di, do = band_dist(y, ia, n), band_dist(y, oa, n)
    return [n, W, H, x, y, W - 1 - x, H - 1 - y, x / max(1, W - 1), y / max(1, H - 1), di, do, abs(di), abs(do),
            min(y, H - 1 - y), W * H / (n * n), (W - 1 - x) / max(1, W - 1)]


def load(paths, ns=None):
    X, Y = [], []
    for p in paths:
        for line in open(os.path.expanduser(p)):
            r = json.loads(line)
            if 'grid' not in r or not r.get('ok') or r.get('sym') or (ns and r['n'] not in ns):
                continue
            ia, oa = r['at']
            for y, row in enumerate(r['grid']):
                for x in range(r['W']):
                    X.append(features(r['n'], r['W'], r['H'], ia, oa, x, y))
                    Y.append(row[2 * x:2 * x + 2] != '..')
    return X, Y


def train(paths, model_path, ns=None):
    from sklearn.ensemble import HistGradientBoostingClassifier
    X, Y = load(paths, ns)
    clf = HistGradientBoostingClassifier(max_iter=300)
    clf.fit(X, Y)
    print('trained on %d tiles, %.1f%% occupied, train acc %.3f' % (len(Y), 100 * sum(Y) / len(Y), clf.score(X, Y)))
    with open(os.path.expanduser(model_path), 'wb') as fh:
        pickle.dump(clf, fh)


def guided_solve(clf, n, W, H, ia, oa, engine, timeout, k_frac=0.5, log=print, guess_cap=60):
    """returns (result, total time, K that worked)"""
    t0 = time.time()
    cells = [(x, y) for x in range(W) for y in range(H)]
    p_occ = clf.predict_proba([features(n, W, H, ia, oa, x, y) for x, y in cells])[:, 1]
    order = sorted(range(len(cells)), key=lambda i: p_occ[i])
    # never block a port tile
    ports = {(W - 1, ia + i) for i in range(n)} | {(0, oa + i) for i in range(n)}
    order = [i for i in order if cells[i] not in ports]
    K = int(k_frac * len(order))
    while True:
        win = S.Spec([S.Group('E', n, at=ia)], [S.Group('W', n, at=oa)]).window(W, H)
        win.blocked = {cells[i] for i in order[:K]}
        left = None if timeout is None else max(1, timeout - (time.time() - t0))
        if K:                                   # a guess gets a short budget: a slow guess is a bad guess
            left = guess_cap if left is None else min(left, guess_cap)
        r = radix.solve(win, radix.factorisations(n)[0], engine, left)
        if log:
            log('    K=%d %s %.1fs' % (K, r['status'], r['time']))
        if r['status'] == 'FEASIBLE' or K == 0:
            if 'grid' in r:
                full = S.Spec([S.Group('E', n, at=ia)], [S.Group('W', n, at=oa)]).window(W, H)
                r['ok'] = bench.verify(r['grid'], full, r.get('options'))
            return r, time.time() - t0, K
        K = K // 2 if K > 4 else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['train', 'eval'])
    ap.add_argument('--data', nargs='*', default=[])
    ap.add_argument('--model', required=True)
    ap.add_argument('--ns', default=None)
    ap.add_argument('--cases', default='', help='n:WxH:in_at:out_at,...')
    ap.add_argument('--timeout', type=float, default=600)
    ap.add_argument('--kfrac', type=float, default=0.5)
    ap.add_argument('--engine', default='~/solvers/cadical')
    a = ap.parse_args()
    eng = os.path.expanduser(a.engine)
    ns = [int(x) for x in a.ns.split(',')] if a.ns else None
    if a.cmd == 'train':
        paths = [p for d in a.data for p in glob.glob(os.path.expanduser(d))]
        train(paths, a.model, ns)
        return
    clf = pickle.load(open(os.path.expanduser(a.model), 'rb'))
    for c in a.cases.split(','):
        n, wh, ia, oa = c.split(':')
        n, ia, oa = int(n), int(ia), int(oa)
        W, H = map(int, wh.split('x'))
        win = S.Spec([S.Group('E', n, at=ia)], [S.Group('W', n, at=oa)]).window(W, H)
        rp = radix.solve(win, radix.factorisations(n)[0], eng, a.timeout)
        rg, tg, K = guided_solve(clf, n, W, H, ia, oa, eng, a.timeout, a.kfrac)
        print('%s plain %s %.1fs | guided %s %.1fs (K=%d, verified %s)' % (
            c, rp['status'], rp['time'], rg['status'], tg, K, rg.get('ok')), flush=True)


if __name__ == '__main__':
    main()
