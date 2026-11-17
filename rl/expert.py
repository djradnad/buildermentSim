"""Expert iteration for per-item speedruns (AlphaZero-style, without the tree statistics):

  1. exact uniform-cost search (rl/search.py) solves the shallow items -> best runs
  2. the policy network learns to imitate every best run found so far (target item is in the observation)
  3. for deeper items, a policy-guided beam search: each run in the beam tries its top-k policy moves plus the
     event wait; runs are ranked by game time with dominance pruning; the fastest run that makes the target is kept
  4. repeat 2-3; every item's best time only goes down, and the policy gets better at guiding the search

usage: python -m rl.expert --rounds 6 --out runs/expert"""
import argparse, json, os, pickle, time

import jax
import jax.numpy as jnp
import numpy as np
import optax

from . import econ as E
from .macro import M_DEL, M_GEMS, M_X1, NM, MacroEnv
from .ppo import make, net
from .search import describe, get, key_of, put, rows, search

LADDER = ['Wood Log', 'Iron Ore', 'Copper Ore', 'Stone', 'Coal', 'Wood Plank', 'Wood Frame', 'Iron Ingot',
          'Copper Ingot', 'Iron Gear', 'Copper Wire', 'Sand', 'Iron Plating', 'Electromagnet', 'Silicon',
          'Metal Frame', 'Heat Sink', 'Glass', 'Logic Circuit', 'Graphite', 'Steel', 'Computer']


def _req_techs():
    """techs needed before each item can be made (recipe unlocks, building unlocks, ingredients, prerequisites)"""
    rec_tech = {}
    for i in range(E.NT):
        for j in np.nonzero(E.TECH_UNLOCK_RECIPE[i])[0]:
            rec_tech[int(j)] = i
    memo = {}

    def anc(t, acc):
        if t in acc: return
        acc.add(t)
        for r in np.nonzero(E.TECH_REQ[t])[0]: anc(int(r), acc)

    def need(item):
        if item in memo: return memo[item]
        memo[item] = set()
        acc = set()
        js = [j for j in E.PRODUCERS_OF[item] if not E.RECIPES[j]['alt']]
        if js:
            j = js[0]
            if j in rec_tech: anc(rec_tech[j], acc)
            k = E.R_CLASS[j]
            if k in E.CLASS_UNLOCK_TECH: anc(E.CLASS_UNLOCK_TECH[k], acc)
            for i in np.nonzero(E.R_IN[j])[0]: acc |= need(int(i))
        memo[item] = acc
        return acc
    return {E.ITEMS[i]: sorted(need(i)) for i in range(E.NI)}


REQ = _req_techs()


def heuristic(e, i, target):
    """rough seconds still needed: research items missing for the target's required techs, at the current lab
    rate per item (at least 15/min), summed over those techs"""
    ts = [t for t in REQ[target] if not e.done_t[i, t]]
    if not ts:
        return 0.0
    rem = np.maximum(E.TECH_COST[ts] - e.prog[i, ts], 0)
    rate = np.maximum(e.lab_in[i], 15.0) / 60.0
    return float((rem / rate[None]).max(1).sum())


def make_env(batch, targets, minutes):
    return MacroEnv(batch=batch, goal='items', targets=targets, max_minutes=minutes, milestone_bonus=0, tech_bonus=0)


ALLOWED = np.ones(NM, bool)
ALLOWED[1:3] = False
ALLOWED[M_GEMS] = False
ALLOWED[M_DEL:M_X1] = False


def replay(target, path, minutes):
    """(obs, mask, action) for one run, and the time it reaches"""
    env = make_env(1, [target], minutes)
    env.reset()
    env.e.target[:] = E.IX[target]
    data = []
    for a in path:
        data.append((env.obs()[0], env.action_mask()[0], a))
        env.step(np.array([a]))
    return data, float(env.e.first[0, E.IX[target]])


def fit(data, params=None, mean=None, var=None, epochs=40, lr=1e-3):
    obs = np.stack([d[0] for d in data]); mask = np.stack([d[1] for d in data]); act = np.array([d[2] for d in data])
    if mean is None:
        mean, var = obs.mean(0), obs.var(0) + 1e-6
    o = ((obs - mean) / np.sqrt(var)).clip(-10, 10).astype(np.float32)
    if params is None:
        params = make(o.shape[1], jax.random.PRNGKey(0), na=NM)
    opt = optax.adam(lr); st = opt.init(params)

    def loss(p, o, m, a):
        logits, _ = net(p, o, m)
        return -jax.nn.log_softmax(logits)[jnp.arange(a.shape[0]), a].mean()

    @jax.jit
    def step(p, st, o, m, a):
        l, g = jax.value_and_grad(loss)(p, o, m, a)
        u, st = opt.update(g, st, p)
        return optax.apply_updates(p, u), st, l

    for ep in range(epochs):
        for mb in np.array_split(np.random.permutation(len(act)), max(1, len(act) // 256)):
            params, st, l = step(params, st, o[mb], mask[mb], act[mb])
    logits, _ = net(params, o, mask)
    acc = float((np.asarray(logits).argmax(1) == act).mean())
    return params, mean, var, acc


def beam(target, params, mean, var, width=128, topk=6, minutes=60, max_depth=400, batch=256):
    env = make_env(batch, [target], minutes)
    env.reset()
    env.e.target[:] = E.IX[target]
    R = rows(env)
    ti = E.IX[target]
    frontier = [(0.0, get(env, 0, R), [])]
    best = (np.inf, None)
    seen = {}
    pol = jax.jit(lambda o, m: net(params, o, m)[0])
    for depth in range(max_depth):
        if not frontier:
            break
        # policy scores for every frontier run
        for i, (_, st, _) in enumerate(frontier):
            put(env, i, R, st)
        n = len(frontier)
        o = ((env.obs()[:n] - mean) / np.sqrt(var)).clip(-10, 10).astype(np.float32)
        m = env.action_mask()[:n] & ALLOWED
        logits = np.asarray(pol(o, m))
        jobs = []
        for i, (t, st, path) in enumerate(frontier):
            cand = [a for a in np.argsort(-logits[i]) if m[i, a]][:topk]
            if m[i, 0] and 0 not in cand:
                cand.append(0)
            jobs += [(st, path, int(a)) for a in cand]
        children = []
        for c in range(0, len(jobs), batch):
            chunk = jobs[c:c + batch]
            for i, (st, path, a) in enumerate(chunk):
                put(env, i, R, st)
            for i in range(len(chunk), batch):
                put(env, i, R, chunk[0][0])
            env.step(np.array([a for _, _, a in chunk] + [0] * (batch - len(chunk))))
            for i, (st, path, a) in enumerate(chunk):
                f = env.e.first[i, ti]
                if np.isfinite(f):
                    if f < best[0]:
                        best = (float(f), path + [a])
                    continue
                if env.e.finished[i] or env.e.t[i] >= best[0]:
                    continue
                children.append((float(env.e.t[i]), get(env, i, R), path + [a], float(env.e.gold[i]),
                                 float(env.e.t[i]) + heuristic(env.e, i, target)))
        # dominance + keep the earliest runs; a run counts progress by researched techs and items made
        keep = []
        for t, st, path, g, f in sorted(children, key=lambda c: c[4]):
            k = key_of(st, R)
            if k in seen and any(t0 <= t and g0 >= g for t0, g0 in seen[k]):
                continue
            seen.setdefault(k, []).append((t, g))
            keep.append((t, st, path))
        frontier = keep[:width]                                # ranked by time + estimated time still needed
    return best


def main(rounds=6, out='runs/expert', exact_nodes=60000, minutes=60):
    os.makedirs(out, exist_ok=True)
    best = {}                               # item -> (seconds, path, source)
    path_db = os.path.join(out, 'best.json')
    if os.path.exists(path_db):
        best = {k: tuple(v) for k, v in json.load(open(path_db)).items()}
    for it in LADDER[:9]:
        if it not in best:
            t, path = search(it, max_nodes=exact_nodes, max_minutes=minutes, verbose=False)
            if path is None:
                continue
            if path:
                best[it] = (float(t), path, 'exact')
                print('exact', it, t, flush=True)
    params = mean = var = None
    for r in range(rounds):
        data = []
        for it, (t, path, src) in best.items():
            d, t2 = replay(it, path, minutes)
            data += d
        params, mean, var, acc = fit(data, params, mean, var)
        print('round %d: %d moves, imitation accuracy %.3f' % (r, len(data), acc), flush=True)
        for it in LADDER:
            t0 = time.time()
            t, path = beam(it, params, mean, var, minutes=minutes)
            old = best.get(it, (np.inf,))[0]
            if path and t < old:
                best[it] = (t, path, 'beam r%d' % r)
            if it in best and best[it][2] != 'exact':          # local search on the best list found so far
                t2, p2 = polish(it, best[it][1], minutes, rounds=15)
                if t2 < best[it][0]:
                    best[it] = (t2, p2, best[it][2].split('+')[0] + '+polish')
            print('  %-14s beam %7.1f s  best %7.1f s  (%.0f s search)' % (it, t, best.get(it, (np.inf,))[0],
                                                                          time.time() - t0), flush=True)
            json.dump(best, open(path_db, 'w'))
        pickle.dump(dict(params=jax.tree_util.tree_map(np.asarray, params), mean=mean, var=var),
                    open(os.path.join(out, 'policy.pkl'), 'wb'))
    return best


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--rounds', type=int, default=6)
    ap.add_argument('--out', default='runs/expert')
    a = ap.parse_args()
    b = main(a.rounds, a.out)
    for it, (t, path, src) in b.items():
        print('%-14s %7.1f s  %-8s %s' % (it, t, src, ' > '.join(describe(path))))


def run_paths(target, paths, minutes=60):
    """replay many decision lists at once; returns the time each first makes the target (inf if never).
    After its list ends a run keeps taking event waits, so a list may stop early."""
    n = len(paths)
    env = make_env(n, [target], minutes)
    env.reset()
    env.e.target[:] = E.IX[target]
    ti = E.IX[target]
    L = max(len(p) for p in paths)
    for k in range(L + 40):
        acts = np.array([p[k] if k < len(p) else 0 for p in paths])
        env.step(acts)
        if np.isfinite(env.e.first[:, ti]).all() or env.e.finished.all():
            break
    return env.e.first[:, ti].copy()


def polish(target, path, minutes=60, rounds=30, batch=256, rng=None):
    """local search on a decision list: drop, duplicate, swap neighbours, x1<->x4, insert an extra line or
    extractor; keep any change that makes the target sooner"""
    from .macro import M_ADD1, M_ADD4, M_UP, M_X1, M_X4
    rng = rng or np.random.default_rng(0)
    best_t = run_paths(target, [path], minutes)[0]
    pool = sorted(set([a for a in path if a not in (0,)]))
    for r in range(rounds):
        muts = []
        for _ in range(batch):
            p = list(path)
            kind = rng.integers(6)
            i = rng.integers(len(p)) if p else 0
            if kind == 0 and len(p) > 1: del p[i]
            elif kind == 1: p.insert(i, p[i])
            elif kind == 2 and len(p) > 1:
                j = min(i + 1, len(p) - 1); p[i], p[j] = p[j], p[i]
            elif kind == 3:
                a = p[i]
                if M_ADD1 <= a < M_ADD4: p[i] = a + (M_ADD4 - M_ADD1)
                elif M_ADD4 <= a < M_UP: p[i] = a - (M_ADD4 - M_ADD1)
                elif M_X1 <= a < M_X4: p[i] = a + (M_X4 - M_X1)
                elif M_X4 <= a < M_X4 + E.NX: p[i] = a - (M_X4 - M_X1)
            elif kind == 4: p.insert(i, int(rng.choice(pool)))
            else: p.insert(i, 0)
            muts.append(p)
        ts = run_paths(target, muts, minutes)
        k = int(np.argmin(ts))
        if ts[k] < best_t - 1e-9:
            best_t, path = float(ts[k]), muts[k]
    return float(best_t), path
