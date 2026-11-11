"""Cube and conquer with a bandit choosing the solver setup per cube (the AE-Kissat-MAB idea at our level).

Each cube (one choice of port positions, cnf.cubes) is a separate CNF. Arms are solver setups (engine + options +
lemma families). A UCB1 bandit picks the arm for the next cube from the times seen so far (reward = 1 - t / T for
a cube finished in t seconds of a slice T, 0 for a timeout); a cube that times out is re-queued with a doubled
slice and the next arm. Workers are processes, so the 4 cores work in parallel.

    python -m bmsolver.mab --case bal3_7x5 --procs 4
    python -m bmsolver.mab --case bal3_7x5 --table      # every arm on every cube (to see what a bandit can gain)
"""
import argparse
import math
import multiprocessing as mp
import time

from . import bench, cnf

ARMS = {
    'cad': ('cadical195', None, ''),
    'cad_local': ('cadical195', None, 'local'),
    'cad_unsat': ('cadical195', {'stabilize': 0, 'walk': 0}, 'local'),
    'cad_sat': ('cadical195', {'stabilizeonly': 1}, 'local'),
    'kis_local': ('kissat404', None, 'local'),
}


def _worker(args, q):
    q.put(_solve_cube(args))


class Job:
    """one cube solve in its own process; killed at its time limit (pysat cannot interrupt Kissat or CaDiCaL)"""

    def __init__(self, args):
        self.args, self.t0 = args, time.time()
        self.q = mp.Queue()
        self.p = mp.Process(target=_worker, args=(args, self.q), daemon=True)
        self.p.start()
        self.result = None

    def poll(self):
        if self.result is None:
            try:
                self.result = self.q.get_nowait()
            except Exception:
                if time.time() - self.t0 > self.args[3] + 5:      # + build time allowance
                    self.p.kill()
                    case, k, arm, T = self.args
                    self.result = dict(case=case, cube=k, arm=arm, time=T, status='UNKNOWN')
        if self.result is not None:
            self.p.join(0.1)
        return self.result

    def kill(self):
        self.p.kill()


def run_jobs(jobs, procs, on_done=None):
    """run (case, cube, arm, timeout) jobs, procs at a time; on_done(result) -> True stops everything"""
    todo, running, out = list(jobs), [], []
    while todo or running:
        while todo and len(running) < procs:
            running.append(Job(todo.pop(0)))
        time.sleep(0.05)
        for j in list(running):
            r = j.poll()
            if r is not None:
                running.remove(j)
                out.append(r)
                if on_done and on_done(r):
                    for x in running:
                        x.kill()
                    return out
    return out


def _solve_cube(args):
    case, k, arm, timeout = args
    from pysat.solvers import Solver
    from . import guide
    win = _cube(case, k)
    engine, cfg, lemmas = ARMS[arm]
    mod, tr, layout = guide.build(win, lemmas)
    t0 = time.time()
    s = Solver(name=engine, bootstrap_with=tr.f.clauses)
    if cfg:
        s.solver.configure(cfg) if hasattr(s.solver, 'configure') else None
    sat = s.solve()
    t = time.time() - t0
    out = dict(case=case, cube=k, arm=arm, time=t, status='SAT' if sat else 'UNSAT' if sat is False else 'UNKNOWN')
    if sat:
        out['grid'] = mod.grid(cnf._Values(tr, s.get_model()))
    s.delete()
    return out


_CACHE = {}


def _cube(case, k):
    if case not in _CACHE:
        name, mk, W, H, exp, tier = {c[0]: c for c in bench.CASES}[case]
        _CACHE[case] = cnf.cubes(mk().window(W, H), mirror=True)
    return _CACHE[case][k]


def ncubes(case):
    _cube(case, 0)
    return len(_CACHE[case])


def table(case, arms, timeout, procs):
    n = ncubes(case)
    jobs = [(case, k, a, timeout) for k in range(n) for a in arms]
    rows = run_jobs(jobs, procs)
    by = {(r['cube'], r['arm']): r for r in rows}
    print('cube  ' + '  '.join('%12s' % a for a in arms))
    for k in range(n):
        print('%4d  ' % k + '  '.join('%7.1f %-4s' % (by[k, a]['time'], by[k, a]['status'][:4]) for a in arms))
    tot = {a: sum(by[k, a]['time'] for k in range(n)) for a in arms}
    vbs = sum(min(by[k, a]['time'] for a in arms) for k in range(n))
    print('total ' + '  '.join('%12.1f' % tot[a] for a in arms) + '   virtual best %.1f' % vbs)
    return rows


def bandit(case, arms, procs, slice0=60, total=3600, log=print):
    """UCB1 over arms; cubes in a queue; stops at the first SAT cube or when all are UNSAT."""
    n = ncubes(case)
    t0 = time.time()
    queue = [(k, slice0) for k in range(n)]
    tried = {k: [] for k in range(n)}
    stats = {a: [0, 0.0] for a in arms}       # pulls, reward sum

    def pick(k):
        fresh = [a for a in arms if stats[a][0] == 0 and a not in tried[k]]
        if fresh:
            return fresh[0]
        N = sum(v[0] for v in stats.values()) or 1
        cand = [a for a in arms if a not in tried[k]] or arms
        return max(cand, key=lambda a: stats[a][1] / max(1, stats[a][0]) + math.sqrt(2 * math.log(N) / max(1, stats[a][0])))
    status = 'UNSAT'
    running = []
    while (queue or running) and time.time() - t0 < total:
        while queue and len(running) < procs:
            k, T = queue.pop(0)
            a = pick(k)
            tried[k].append(a)
            running.append(Job((case, k, a, T)))
        time.sleep(0.05)
        for j in list(running):
            r = j.poll()
            if r is None:
                continue
            running.remove(j)
            k, a, T = r['cube'], r['arm'], j.args[3]
            st = stats[a]
            st[0] += 1
            st[1] += 0 if r['status'] == 'UNKNOWN' else 1 - min(1, r['time'] / T)
            if log:
                log('  cube %d arm %-10s %-7s %6.1fs (slice %d)' % (k, a, r['status'], r['time'], T))
            if r['status'] == 'SAT':
                for x in running:
                    x.kill()
                return dict(status='SAT', time=time.time() - t0, grid=r['grid'], cube=k, stats=stats)
            if r['status'] == 'UNKNOWN':
                queue.append((k, 2 * T))
    if queue or running:
        status = 'UNKNOWN'
        for x in running:
            x.kill()
    return dict(status=status, time=time.time() - t0, stats=stats)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--case', required=True)
    ap.add_argument('--arms', default='cad,cad_local,cad_unsat,kis_local')
    ap.add_argument('--procs', type=int, default=4)
    ap.add_argument('--timeout', type=float, default=600)
    ap.add_argument('--table', action='store_true')
    ap.add_argument('--slice', type=float, default=60)
    a = ap.parse_args(argv)
    arms = a.arms.split(',')
    if a.table:
        table(a.case, arms, a.timeout, a.procs)
    else:
        r = bandit(a.case, arms, a.procs, a.slice, a.timeout)
        print('%s bandit %s %.1fs %s' % (a.case, r['status'], r['time'], {k: (v[0], round(v[1], 2)) for k, v in r['stats'].items()}))


if __name__ == '__main__':
    main()
