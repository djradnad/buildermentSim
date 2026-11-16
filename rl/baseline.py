"""Scripted speedrun baseline for EconEnv: a greedy planner that chases the cheapest available research.

Not optimal; it shows the environment can be finished and gives RL a time to beat (like Factoriax's scripted
Easy Rocket run). usage: python -m rl.baseline [--world 2CCJhI] [--goal end_game]"""
import argparse

import numpy as np

from .econ import (A_BUILD, A_EXTRACT, A_LAB, A_UPGRADE, A_VAULT, A_WAIT, CLASSES, IX, ITEMS, NK, NR, R_IN, R_OUT,
                   R_TELE, RECIPES, RES_ITEM, SELL, TECH_COST, TECHS, EconEnv)

PRODUCER = {}
for j, r in enumerate(RECIPES):
    for n in r['out']:
        PRODUCER.setdefault(IX[n], []).append(j)
RAW = {it: x for x, it in enumerate(RES_ITEM)}


def plan_action(env, b, horizon_min=8.0):
    m = env.action_mask()[b]
    if env.labs[b] == 0:
        return A_LAB if m[A_LAB] else A_WAIT
    avail = env._available()[b]
    # value of each available tech = gold worth of what is still missing; chase the cheapest, but also the
    # main line (goal prerequisites) gets priority through a smaller weight
    rem = np.maximum(TECH_COST - env.prog[b], 0)
    worth = (rem * np.maximum(SELL, 1)).sum(1)
    anc = ancestors(env.goal)
    order = [i for i in np.argsort(worth) if avail[i] and i in anc] + \
        [i for i in np.argsort(worth) if avail[i] and i not in anc and USEFUL(i)]
    # upgrades are cheap speed: take them when they cost under a minute of income
    for k in range(NK):
        if m[A_UPGRADE + k] and env._up_cost(k)[b] < max(10 * env.gold_rate[b], 0.5 * env.gold[b]):
            return A_UPGRADE + k
    D = np.zeros(len(ITEMS))
    for ti in order[:2]:
        D += rem[ti] / horizon_min                           # items/min into the lab to finish in the horizon
    a = build_for(env, b, D, m)
    if a is not None:
        return a
    if env.labs[b] < 3 and m[A_LAB] and env.gold_rate[b] > 0:
        return A_LAB
    if m[A_VAULT]:
        return A_VAULT
    return A_WAIT


_ANC = {}


def ancestors(g):
    if g not in _ANC:
        from .econ import TECH_REQ
        seen, st = {g}, [g]
        while st:
            for r in np.nonzero(TECH_REQ[st.pop()])[0]:
                if r not in seen: seen.add(int(r)); st.append(int(r))
        _ANC[g] = seen
    return _ANC[g]


def USEFUL(i):
    from .econ import TECH_UPGRADE
    return i in TECH_UPGRADE or TECHS[i]['row'] in ('gold_vault', 'belt_speed')


def build_for(env, b, D, m):
    """calculator-style: gross item rates for lab demand D through the main (non-alt) recipes, machine and
    extractor counts for them; build the shallowest shortfall first so new machines are fed."""
    from .econ import ITEM_ORDER, NX
    G = D.copy()
    need_r = np.zeros(NR)
    for i in reversed(ITEM_ORDER):                           # deepest products first
        if G[i] <= 1e-9 or i in RAW:
            continue
        js = MAIN.get(i)
        if js is None:
            continue
        j = js
        per = env.cpm[j, min(env.tier[b, max(env_class(j), 0)], 3)] * R_OUT[j, i]
        n = G[i] / per
        need_r[j] += n
        G += n * env.cpm[j, min(env.tier[b, max(env_class(j), 0)], 3)] * R_IN[j]
    for i in ITEM_ORDER:
        if i in RAW and G[i] > 0:
            x = RAW[i]
            rate = 50 if x == 6 else env.ext_rate[env.tier[b, 0]]
            if env.n_ext[b, x] < np.ceil(G[i] / rate - 1e-9):
                return A_EXTRACT + x if m[A_EXTRACT + x] else A_WAIT
        js = MAIN.get(i)
        if js is not None and need_r[js] > 0 and env.n_rec[b, js] < np.ceil(need_r[js] - 1e-9):
            return A_BUILD + js if m[A_BUILD + js] else A_WAIT
    return None


def env_class(j):
    from .econ import R_CLASS
    return R_CLASS[j]


MAIN = {}
for _j, _r in enumerate(RECIPES):
    if not _r['alt']:
        for _n in _r['out']:
            MAIN[IX[_n]] = _j


def supply(env, b, i, want, m, depth):
    """an action that raises the flow of item i, or None (= nothing to do / can't afford: wait)"""
    if depth > 12:
        return None
    have = env.deliver[b, i]
    if have >= want:
        return None
    if i in RAW:
        x = RAW[i]
        return A_EXTRACT + x if m[A_EXTRACT + x] else 'wait'
    js = [j for j in PRODUCER.get(i, []) if not RECIPES[j]['alt'] and not R_TELE[j]] or PRODUCER.get(i, [])
    for j in js:
        if env.n_rec[b, j] > 0 and env.util[b, j] < 0.999:
            # a machine is starved: feed its scarcest input
            ins = np.nonzero(R_IN[j])[0]
            k = min(ins, key=lambda q: env.deliver[b, q] / max(R_IN[j, q], 1))
            rate = env.cpm[j, 0] * R_IN[j, k]
            r = supply(env, b, int(k), env.deliver[b, k] + rate, m, depth + 1)
            if r is not None:
                return r
        if m[A_BUILD + j]:
            return A_BUILD + j
        rec_ok = (env.done_t[b][:, None] & __import__('rl.econ', fromlist=['x']).TECH_UNLOCK_RECIPE).any(0)[j]
        if rec_ok:
            return 'wait'
    return None


def run(world='2CCJhI', goal='end_game', verbose=True, **kw):
    env = EconEnv(batch=1, world=world, goal=goal, **kw)
    env.reset()
    log, last = [], 0
    while not env.finished[0]:
        a = plan_action(env, 0)
        if a == 'wait' or a is None:
            a = A_WAIT
        _, r, d, info = env.step(np.array([a]))
        n = int(env.done_t[0].sum())
        if n != last:
            got = [TECHS[i]['name'] for i in np.nonzero(env.done_t[0])[0]]
            log.append((env.t[0] / 60, n))
            last = n
    return env, log


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--world', default='2CCJhI')
    ap.add_argument('--goal', default='end_game')
    a = ap.parse_args()
    env, log = run(a.world, a.goal)
    print('finished' if env.done_t[0, env.goal] else 'not finished', 'at %.1f min' % (env.t[0] / 60),
          'techs', int(env.done_t[0].sum()))
    for t, n in log[::5]:
        print('%7.1f min  %d techs' % (t, n))
