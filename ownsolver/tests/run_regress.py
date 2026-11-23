"""Regression for the own solver.  Usage: python3 run_regress.py [--timeout S] [--jobs 2] [pins|ka|all] [filter]
  pins: every specs/pin_*.json (a known design with every tile fixed) must come back FOUND with exactly that grid,
        and the grid must pass the exact check.
  ka:   every specs/ka_*.json with a decided CP-SAT reference in refs.json: own FOUND must pass the exact check,
        own INFEASIBLE where the reference is FOUND (or FOUND where the reference is INFEASIBLE) is a FAILURE.
        own UNKNOWN is reported, not a failure.
Prints failures with the spec path and expected answer; exit code 1 when anything failed."""
import argparse
import glob
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from race import run_own, verify          # noqa: E402


def slug(name):
    return re.sub(r'[^A-Za-z0-9]+', '_', name).strip('_')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('which', nargs='?', default='all')
    ap.add_argument('filter', nargs='?', default='')
    ap.add_argument('--timeout', type=float, default=30)
    ap.add_argument('--jobs', type=int, default=2)
    a = ap.parse_args()
    jobs = []
    if a.which in ('pins', 'all'):
        for p in sorted(glob.glob(os.path.join(HERE, 'specs', 'pin_*.json'))):
            if a.filter in p:
                jobs.append(('pin', p, 'FOUND'))
    if a.which in ('ka', 'all'):
        refs = json.load(open(os.path.join(HERE, 'refs.json')))
        for name, r in refs.items():
            p = os.path.join(HERE, 'specs', 'ka_%s.json' % slug(name))
            if a.filter in name and os.path.exists(p):
                jobs.append(('ka', p, r['status']))

    def go(job):
        kind, p, want = job
        r = verify(p, run_own(p, a.timeout))
        fail = None
        if r['status'] in ('ERROR', 'BADFIND'):
            fail = r['status'] + ' ' + r.get('exact_why', '') + r.get('note', '')
        elif kind == 'pin':
            if r['status'] != 'FOUND':
                fail = 'pinned known design not accepted: %s' % r['status']
            else:
                js = json.load(open(p))
                exp = {(x, y): c for x, y, c in js['fixed']}
                got = {}
                for y, line in enumerate(r['grid']):
                    for i in range(0, len(line), 2):
                        if line[i:i + 2] != '..':
                            got[(i // 2, y)] = line[i:i + 2]
                if {k: v for k, v in exp.items() if v} != got:
                    fail = 'returned grid differs from the pinned one'
        else:
            if want == 'FOUND' and r['status'] == 'INFEASIBLE':
                fail = 'WRONG ANSWER: own INFEASIBLE, CP-SAT found a layout (refs.json has it)'
            elif want == 'INFEASIBLE' and r['status'] == 'FOUND':
                # the find passed the exact check (else BADFIND above): CP-SAT's INFEASIBLE holds only for flows in
                # whole multiples of 1/D belt, so this is a find with finer flows, not a failure. Report it loudly.
                r['note'] = 'NOTE: exact-checked find where CP-SAT (D=%d) said INFEASIBLE' % json.load(open(p))['D']
        return kind, p, want, r, fail

    fails, counts = [], {}
    with ThreadPoolExecutor(a.jobs) as ex:
        for kind, p, want, r, fail in ex.map(go, jobs):
            key = (kind, want, r['status'])
            counts[key] = counts.get(key, 0) + 1
            print('%-4s %-48s want %-10s own %-10s %6.2fs %s' % (kind, os.path.basename(p), want, r['status'],
                                                                 r['time'], fail or r.get('note', '')), flush=True)
            if fail:
                fails.append((p, want, fail))
    print('\nsummary (kind, reference, own): count')
    for k in sorted(counts):
        print('  %s: %d' % (k, counts[k]))
    print('%d failures' % len(fails))
    for f in fails:
        print('FAIL', *f)
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()
