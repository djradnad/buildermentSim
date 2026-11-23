"""Run bmown with extra options on the known-answer set (tests/specs/ka_*.json, refs in tests/refs.json).
  python3 ownsolver/bench.py OUT.json --timeout 10 --jobs 2 [--filter TEXT] -- [bmown options]
Prints a summary vs the CP-SAT references; any contradiction (own INFEASIBLE vs ref FOUND, or own FOUND that fails
the exact check) is printed as WRONG."""
import json, os, re, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'tests'))
from common import from_json, parse_grid, exact_check  # noqa

def slug(name):
    return re.sub(r'[^A-Za-z0-9]+', '_', name).strip('_')

def main():
    argv = sys.argv[1:]
    extra = []
    if '--' in argv:
        i = argv.index('--'); extra = argv[i + 1:]; argv = argv[:i]
    out = argv[0]
    timeout = float(argv[argv.index('--timeout') + 1]) if '--timeout' in argv else 10
    jobs = int(argv[argv.index('--jobs') + 1]) if '--jobs' in argv else 2
    filt = argv[argv.index('--filter') + 1] if '--filter' in argv else ''
    refs = json.load(open(os.path.join(HERE, 'tests', 'refs.json')))
    names = [n for n in refs if filt in n and os.path.exists(os.path.join(HERE, 'tests', 'specs', 'ka_%s.json' % slug(n)))]

    def one(name):
        path = os.path.join(HERE, 'tests', 'specs', 'ka_%s.json' % slug(name))
        t = time.time()
        p = subprocess.run([os.path.join(HERE, 'bmown'), path, '--timeout', str(timeout)] + extra,
                           capture_output=True, text=True)
        lines = p.stdout.strip().split('\n')
        st = lines[0].split()[1] if lines and lines[0].startswith('STATUS') else 'ERROR'
        r = dict(status=st, time=time.time() - t, stats=lines[-1] if lines else '')
        if st == 'FOUND':
            js = json.load(open(path))
            win = from_json(js)
            r['exact'] = exact_check(win, parse_grid('\n'.join(lines[1:1 + js['H']])))[0]
        return name, r
    res = {}
    with ThreadPoolExecutor(jobs) as ex:
        for name, r in ex.map(one, names):
            res[name] = r
    json.dump(res, open(out, 'w'), indent=1)
    wrong = []
    solved = {'own': 0, 'ref': 0}
    tot = {'own': 0.0, 'ref': 0.0}
    both = 0
    for n, r in res.items():
        ref = refs[n]
        if r['status'] == 'FOUND' and not r.get('exact'):
            wrong.append((n, 'FOUND fails exact check'))
        if r['status'] == 'INFEASIBLE' and ref['status'] == 'FOUND':
            wrong.append((n, 'own INFEASIBLE, ref FOUND'))
        if r['status'] in ('FOUND', 'INFEASIBLE'): solved['own'] += 1
        if ref['status'] in ('FOUND', 'INFEASIBLE'): solved['ref'] += 1
        if r['status'] != 'UNKNOWN' and ref['status'] != 'UNKNOWN':
            both += 1; tot['own'] += r['time']; tot['ref'] += ref['time']
        if r['status'] == 'FOUND' and ref['status'] == 'INFEASIBLE':
            print('NOTE own FOUND where ref INFEASIBLE (finer flows?):', n)
    print('cases %d, solved own %d ref %d; on %d both-solved: own %.1fs ref %.1fs' % (len(res), solved['own'], solved['ref'], both, tot['own'], tot['ref']))
    for n, r in sorted(res.items(), key=lambda kv: -kv[1]['time'])[:12]:
        print('  %6.2f %-10s ref %-10s %6.2f  %s' % (r['time'], r['status'], refs[n]['status'], refs[n]['time'], n))
    for w in wrong: print('WRONG', w)

main()
