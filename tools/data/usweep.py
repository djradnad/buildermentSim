"""Uniform-share search of one box over every port placement in parallel (mirror placements skipped). Stops at the
first verified layout. Appends a JSON line per placement to --out.

    python tools/data/usweep.py --n 4 --size 7x7 --procs 4 --timeout 1800 --out ~/data/usweep.jsonl
"""
import argparse
import concurrent.futures as cf
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from bmsolver import uniform, spec as S, bench  # noqa: E402


def kill_tree(root):
    """stop every solver process started below this one (grandchildren of the pool workers)"""
    import signal
    kids = {}
    for d in os.listdir('/proc'):
        if d.isdigit():
            try:
                kids.setdefault(int(open('/proc/%s/stat' % d).read().rsplit(')', 1)[1].split()[1]), []).append(int(d))
            except OSError:
                pass
    todo, seen = [root], []
    while todo:
        for c in kids.get(todo.pop(), []):
            seen.append(c)
            todo.append(c)
    for c in reversed(seen):
        try:
            os.kill(c, signal.SIGKILL)
        except OSError:
            pass


def one(a):
    n, m, W, H, ia, oa, sym, eng, timeout = a
    win = S.Spec([S.Group('E', n, at=ia)], [S.Group('W', m, at=oa)]).window(W, H, symmetry='y' if sym else None)
    if win is None:
        return dict(n=n, m=m, W=W, H=H, at=[ia, oa], sym=sym, status='INFEASIBLE', time=0)
    r = uniform.solve(win, n, m, eng, timeout)
    rec = dict(n=n, m=m, W=W, H=H, at=[ia, oa], sym=sym, status=r['status'], time=round(r['time'], 1), model='uniform')
    if 'grid' in r:
        rec['ok'] = bool(bench.verify(r['grid'], win, r.get('options')))
        rec['grid'] = [''.join(r['grid'].get((x, y)) or '..' for x in range(W)) for y in range(H)]
    return rec


def sweep(n, m, W, H, sym, eng, timeout, procs, out=None, log=print, stop_first=True):
    kn, km = H - n, H - m
    if sym:
        pairs = [(kn // 2, km // 2)] if kn % 2 == 0 and km % 2 == 0 and kn >= 0 and km >= 0 else []
    else:
        pairs = [(ia, oa) for ia in range(kn + 1) for oa in range(km + 1) if (ia, oa) <= (kn - ia, km - oa)]
    found, unknown, t0 = None, False, time.time()
    ex = cf.ProcessPoolExecutor(procs)
    if True:
        futs = [ex.submit(one, (n, m, W, H, ia, oa, sym, eng, timeout)) for ia, oa in pairs]
        for f in cf.as_completed(futs):
            rec = f.result()
            if out:
                with open(out, 'a') as fh:
                    fh.write(json.dumps(rec) + '\n')
            unknown |= rec['status'] == 'UNKNOWN'
            if rec.get('ok') and found is None:
                found = rec
                if stop_first:
                    for g in futs:
                        g.cancel()
                    kill_tree(os.getpid())
                    break
    ex.shutdown(wait=False, cancel_futures=True)
    st = 'FEASIBLE' if found else 'UNKNOWN' if unknown else 'INFEASIBLE'
    if log:
        log('%d:%d %dx%d%s %s %.0fs%s' % (n, m, W, H, ' sym' if sym else '', st, time.time() - t0,
                                           ' at %s' % found['at'] if found else ''), flush=True)
        if found:
            log('\n'.join(found['grid']), flush=True)
    return st, found


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, required=True)
    ap.add_argument('--m', type=int, default=None)
    ap.add_argument('--size', required=True)
    ap.add_argument('--sym', action='store_true')
    ap.add_argument('--procs', type=int, default=8, help='placements at once (more than the cores is fine: fits come fast, misses are slow)')
    ap.add_argument('--timeout', type=float, default=1800)
    ap.add_argument('--engine', default='~/solvers/kissat')
    ap.add_argument('--out', default=None)
    a = ap.parse_args()
    for size in a.size.split(','):
        W, H = map(int, size.split('x'))
        sweep(a.n, a.m or a.n, W, H, a.sym, os.path.expanduser(a.engine), a.timeout, a.procs,
              os.path.expanduser(a.out) if a.out else None)
