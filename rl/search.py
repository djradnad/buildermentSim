"""Best possible time per item in the macro env, by uniform-cost search (the check john asked for).

Nodes are full env states; children = every legal macro decision (event wait only, no deletes or gems).
Priority = game time. A node is dropped when an earlier one had the same buildings, plan, tiers and research,
with no more time and no less gold (dominance). The first popped node that has made the target is optimal within
the macro moves (the same moves the AI has). usage: python -m rl.search "Wood Plank" [--max-nodes 200000]"""
import argparse
import heapq
import itertools

import numpy as np

from . import econ as E
from .macro import M_DEL, M_GEMS, M_X1, M_X4, NM, MacroEnv


def rows(env):
    """every per-run array of the env (macro + inner), for cloning"""
    out = []
    for obj in (env, env.e):
        for k, v in vars(obj).items():
            if isinstance(v, np.ndarray) and v.ndim >= 1 and v.shape[0] == env.B:
                out.append((obj, k))
    return out


def get(env, i, R):
    return [getattr(o, k)[i].copy() for o, k in R]


def put(env, i, R, st):
    for (o, k), v in zip(R, st):
        getattr(o, k)[i] = v


def key_of(st, R):
    d = dict(((o.__class__.__name__, k), v) for (o, k), v in zip(R, st))
    parts = []
    for name in ('n_rec', 'n_ext', 'n_free', 'tier', 'lab_at', 'vaults', 'done_t'):
        parts.append(d[('EconEnv', name)].tobytes())
    parts.append(np.round(d[('MacroEnv', 'plan_r')], 6).tobytes())
    parts.append(np.round(d[('MacroEnv', 'plan_x')], 6).tobytes())
    return b'|'.join(parts)


def search(target, batch=256, max_nodes=200000, max_minutes=120, world='2CCJhI', verbose=True):
    env = MacroEnv(batch=batch, goal='items', targets=[target], max_minutes=max_minutes, world=world,
                   milestone_bonus=0, tech_bonus=0)
    env.reset()
    R = rows(env)
    ti = E.IX[target]
    allowed = np.ones(NM, bool)
    allowed[1:3] = False                         # fixed waits: the event wait covers them
    allowed[M_GEMS] = False
    allowed[M_DEL:M_X1] = False
    start = get(env, 0, R)
    cnt = itertools.count()
    heap = [(0.0, 0.0, next(cnt), start, [])]
    seen = {}
    popped = 0
    while heap and popped < max_nodes:
        group = []
        while heap and len(group) < batch:
            t, _, _, st, path = heapq.heappop(heap)
            d = dict(((o.__class__.__name__, k), v) for (o, k), v in zip(R, st))
            if np.isfinite(d[('EconEnv', 'first')][ti]):
                if verbose:
                    print('best %.1f s after %d nodes' % (d[('EconEnv', 'first')][ti], popped))
                return d[('EconEnv', 'first')][ti], path
            k = key_of(st, R)
            g = d[('EconEnv', 'gold')]
            if k in seen and any(t0 <= t and g0 >= g for t0, g0 in seen[k]):
                continue
            seen.setdefault(k, []).append((t, g))
            group.append((t, st, path))
            popped += 1
        if not group:
            break
        # expand: one env row per (node, action)
        jobs = []
        for t, st, path in group:
            put(env, 0, R, st)
            m = env.action_mask()[0] & allowed
            for a in np.nonzero(m)[0]:
                jobs.append((st, path, int(a)))
        for c in range(0, len(jobs), batch):
            chunk = jobs[c:c + batch]
            for i, (st, path, a) in enumerate(chunk):
                put(env, i, R, st)
            for i in range(len(chunk), batch):
                put(env, i, R, chunk[0][0])
            acts = np.array([a for _, _, a in chunk] + [0] * (batch - len(chunk)))
            env.step(acts)
            for i, (st, path, a) in enumerate(chunk):
                if env.e.finished[i] and not np.isfinite(env.e.first[i, ti]):
                    continue
                ns = get(env, i, R)
                heapq.heappush(heap, (float(env.e.t[i]), -float(env.e.gold[i]), next(cnt), ns, path + [a]))
    return None, None


def describe(path):
    from .macro import M_ADD1, M_ADD4, M_CONN, M_DLAB, M_LAB, M_LABAT, M_PRE, M_UP, M_VAULT
    out = []
    for a in path:
        if a == 0: out.append('wait')
        elif M_ADD1 <= a < M_ADD4: out.append('+1 ' + E.RECIPES[a - M_ADD1]['key'])
        elif M_ADD4 <= a < M_UP: out.append('+4 ' + E.RECIPES[a - M_ADD4]['key'])
        elif M_UP <= a < M_LAB: out.append('upgrade ' + E.CLASSES[a - M_UP])
        elif a == M_LAB: out.append('lab')
        elif a == M_VAULT: out.append('vault')
        elif M_X1 <= a < M_X4: out.append('+1 extractor ' + E.ITEMS[E.RES_ITEM[a - M_X1]])
        elif M_X4 <= a < M_PRE: out.append('+4 extractor ' + E.ITEMS[E.RES_ITEM[a - M_X4]])
        elif M_PRE <= a < M_CONN: out.append('pre-extractor ' + E.ITEMS[E.RES_ITEM[a - M_PRE]])
        elif M_CONN <= a < M_LABAT: out.append('connect ' + E.ITEMS[E.RES_ITEM[a - M_CONN]])
        elif M_LABAT <= a < M_DLAB: out.append('lab@%s' % ('base', 'wood', 'iron')[a - M_LABAT])
        elif M_DLAB <= a < NM: out.append('del lab@%s' % ('base', 'wood', 'iron')[a - M_DLAB])
        else: out.append(str(a))
    return out


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('target')
    ap.add_argument('--max-nodes', type=int, default=200000)
    a = ap.parse_args()
    t, path = search(a.target, max_nodes=a.max_nodes)
    print(a.target, t, describe(path) if path else None)
