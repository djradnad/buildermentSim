"""Reference answers with CP-SAT (bmsolver.model.solve, 1 worker). Usage: python3 ref.py TIMEOUT [JOBS] [filter]
Writes refs.json {case: {status, time, grid?}} and specs/ka_<slug>.json. Every found grid is exact-checked.
Runs at most JOBS (default 2) single-worker solver processes at once."""
import json
import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor

from common import save, exact_check, grid_text
from cases import cases

HERE = os.path.dirname(os.path.abspath(__file__))
REFS = os.path.join(HERE, 'refs.json')


def slug(name):
    return re.sub(r'[^A-Za-z0-9]+', '_', name).strip('_')


def one(args):
    name, timeout = args
    from bmsolver import model
    win = cases()[name]
    r = model.solve(win, timeout=timeout, workers=1)
    st = {'OPTIMAL': 'FOUND', 'FEASIBLE': 'FOUND'}.get(r['status'], r['status'])
    res = dict(status=st, time=round(r['time'], 2))
    if 'grid' in r:
        ok, why = exact_check(win, r['grid'])
        res['grid'] = grid_text(r['grid'], win.W, win.H).split('\n')
        res['exact'] = ok
        res['exact_why'] = why
    return name, res


def main():
    timeout = float(sys.argv[1]) if len(sys.argv) > 1 else 60
    jobs = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    filt = sys.argv[3] if len(sys.argv) > 3 else ''
    refs = json.load(open(REFS)) if os.path.exists(REFS) else {}
    cs = cases()
    todo = [n for n in cs if filt in n and (n not in refs or refs[n]['status'] == 'UNKNOWN' and refs[n]['time'] < timeout - 1)]
    for n in cs:
        save(cs[n], os.path.join(HERE, 'specs', 'ka_%s.json' % slug(n)), name=n)
    with ProcessPoolExecutor(jobs) as ex:
        for name, res in ex.map(one, [(n, timeout) for n in todo]):
            refs[name] = res
            print('%-40s %-10s %6.1fs %s' % (name, res['status'], res['time'], res.get('exact_why', '')), flush=True)
            json.dump(refs, open(REFS, 'w'), indent=0)


if __name__ == '__main__':
    main()
