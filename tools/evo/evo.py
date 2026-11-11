"""Evolve SAT solver heuristics on our own layout CNFs (the AESAT / AE-Kissat-MAB method, with Claude as the LLM).

A candidate is one replaced C source file of a base solver (e.g. src/restart.c of AE-Kissat-MAB, whose
restart_mab() picks VSIDS or CHB by a UCB bandit). It is compiled into its own copy of the base build (only the
changed object recompiles) and scored on a training set of DIMACS files with known answers:

    PAR-2 = sum of run times, a timeout counting 2 x the limit; a wrong answer disqualifies the candidate.
    SAT answers are checked against the CNF (every clause satisfied by the printed model).

    python tools/evo/evo.py build  --base ~/sc25/x/AE_kissat2025_MAB/AE_kissat2025_MAB --name v1 --file src/restart.c=cand/v1_restart.c
    python tools/evo/evo.py score  --solver ~/evo/v1/build/kissat --set train.txt --timeout 300 --procs 4
    python tools/evo/evo.py table  --solvers a,b,c --set train.txt                # compare several solvers

train.txt: one line per instance, "path expected" (expected = SAT / UNSAT).
"""
import argparse
import concurrent.futures as cf
import os
import shutil
import subprocess
import sys
import time

EVO = os.path.expanduser(os.environ.get('EVO_DIR', '~/evo'))


def build(base, name, files):
    """copy base (with its build/ objects), replace files {relpath: source}, make; returns the binary path"""
    dst = os.path.join(EVO, name)
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(base, dst, symlinks=True)
    for rel, src in files.items():
        shutil.copy(src, os.path.join(dst, rel))
        os.utime(os.path.join(dst, rel))
    r = subprocess.run(['make', '-C', os.path.join(dst, 'build'), '-j2', 'kissat'], capture_output=True, text=True)
    binary = os.path.join(dst, 'build', 'kissat')
    if r.returncode != 0 or not os.path.exists(binary):
        sys.stderr.write(r.stdout[-3000:] + r.stderr[-3000:])
        raise RuntimeError('build failed for %s' % name)
    return binary


def check_model(cnf_path, lits):
    val = set(lits)
    with open(cnf_path) as fh:
        for line in fh:
            if line[0] in 'pc':
                continue
            c = [int(x) for x in line.split()[:-1]]
            if c and not any(l in val for l in c):
                return False
    return True


def run_one(solver, path, timeout):
    t0 = time.time()
    try:
        p = subprocess.run([solver, path], capture_output=True, text=True, timeout=timeout)
        out = p.stdout
    except subprocess.TimeoutExpired:
        return 'TIMEOUT', timeout, None
    t = time.time() - t0
    st, model = 'UNKNOWN', []
    for line in out.splitlines():
        if line.startswith('s '):
            st = 'SAT' if 'UNSAT' not in line and 'SATISFIABLE' in line else 'UNSAT' if 'UNSATISFIABLE' in line else 'UNKNOWN'
        elif line.startswith('v '):
            model += [int(x) for x in line[2:].split() if x != '0']
    return st, t, model


def score(solver, items, timeout, procs, log=print, check=True):
    """items: [(path, expected)]. Returns dict(par2, solved, wrong, times)"""
    res = {}
    with cf.ThreadPoolExecutor(procs) as ex:
        futs = {ex.submit(run_one, solver, p, timeout): (p, e) for p, e in items}
        for f in cf.as_completed(futs):
            p, e = futs[f]
            st, t, model = f.result()
            wrong = False
            if st in ('SAT', 'UNSAT') and e in ('SAT', 'UNSAT') and st != e:
                wrong = True
            if st == 'SAT' and check and model is not None and not check_model(p, model):
                wrong = True
            res[p] = (st, t, wrong)
            if log:
                log('  %-40s %-8s %7.1fs%s' % (os.path.basename(p), st, t, '  WRONG' if wrong else ''))
    par2 = sum(t if st in ('SAT', 'UNSAT') else 2 * timeout for st, t, w in res.values())
    return dict(par2=par2, solved=sum(st in ('SAT', 'UNSAT') for st, t, w in res.values()),
                wrong=sum(w for st, t, w in res.values()), times=res)


def load_set(path):
    items = []
    for line in open(path):
        line = line.split('#')[0].split()
        if line:
            items.append((os.path.expanduser(line[0]), line[1] if len(line) > 1 else None))
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['build', 'score', 'table'])
    ap.add_argument('--base')
    ap.add_argument('--name')
    ap.add_argument('--file', action='append', default=[], help='relpath=source')
    ap.add_argument('--solver')
    ap.add_argument('--solvers')
    ap.add_argument('--set')
    ap.add_argument('--timeout', type=float, default=300)
    ap.add_argument('--procs', type=int, default=4)
    ap.add_argument('--csv', default=None, help='append per-instance rows: solver,instance,status,time,wrong')
    a = ap.parse_args()
    if a.cmd == 'build':
        print(build(os.path.expanduser(a.base), a.name, dict(kv.split('=', 1) for kv in a.file)))
    elif a.cmd == 'score':
        r = score(os.path.expanduser(a.solver), load_set(a.set), a.timeout, a.procs)
        print('PAR2 %.1f solved %d wrong %d' % (r['par2'], r['solved'], r['wrong']))
    else:
        items = load_set(a.set)
        for s in a.solvers.split(','):
            r = score(os.path.expanduser(s), items, a.timeout, a.procs, log=None)
            if a.csv:
                with open(a.csv, 'a') as fh:
                    for path, (st, t, w) in r['times'].items():
                        fh.write('%s,%s,%s,%.2f,%d\n' % (os.path.basename(s.rstrip('/')) if 'evo' not in s else s.split('/')[-3], os.path.basename(path), st, t, w))
            print('%-40s PAR2 %8.1f solved %d/%d wrong %d' % (os.path.basename(s), r['par2'], r['solved'], len(items), r['wrong']), flush=True)


if __name__ == '__main__':
    main()
