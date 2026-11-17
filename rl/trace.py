"""Record a best run step by step for the run viewer. usage: python -m rl.trace <best.json> <out.json> [items...]"""
import json, os, sys
import numpy as np
from . import econ as E
from .expert import make_env
from .search import describe


def econ_label(a):
    if a < E.A_BUILD: return None
    if a < E.A_EXTRACT: return 'build ' + E.RECIPES[a - E.A_BUILD]['key'].replace('_', ' ')
    if a < E.A_UPGRADE: return 'extractor + belt: ' + E.ITEMS[E.RES_ITEM[a - E.A_EXTRACT]]
    if a < E.A_LAB: return 'upgrade ' + E.CLASSES[a - E.A_UPGRADE]
    if a == E.A_LAB: return 'lab at site A'
    if a == E.A_VAULT: return 'gold vault'
    if E.A_REMOVE <= a < E.A_REMOVE_X: return 'delete ' + E.RECIPES[a - E.A_REMOVE]['key'].replace('_', ' ')
    if E.A_REMOVE_X <= a < E.A_PREX: return 'delete extractor ' + E.ITEMS[E.RES_ITEM[a - E.A_REMOVE_X]]
    if E.A_PREX <= a < E.A_CONN: return 'extractor, no belt yet: ' + E.ITEMS[E.RES_ITEM[a - E.A_PREX]]
    if E.A_CONN <= a < E.A_LABAT: return 'belt it to the lab: ' + E.ITEMS[E.RES_ITEM[a - E.A_CONN]]
    if E.A_LABAT <= a < E.A_DLAB: return 'lab at site ' + 'ABC'[a - E.A_LABAT]
    if E.A_DLAB <= a < E.NA: return 'delete lab at site ' + 'ABC'[a - E.A_DLAB]
    return str(a)


def node_order(e, nodes):
    """per resource: node indices nearest first for the standing labs (the env's 'k nearest nodes' model)"""
    mask = int(e._site_mask()[0]) or 1
    sel = [i for i in range(E.NS) if mask >> i & 1]
    out = []
    for f in E.RES_FLAGS[:6]:
        idx = np.where(nodes[:, 2] == f)[0]
        d = np.min([E.lab_belts(nodes[idx, 0], nodes[idx, 1], *e.sites[s]) for s in sel], 0)
        out.append(idx[np.argsort(d, kind='stable')].tolist())
    return out


def trace(item, path, minutes=60):
    env = make_env(1, [item], minutes)
    env.reset()
    e = env.e
    e.target[:] = E.IX[item]
    nodes = np.array(json.load(open(os.path.join(E.HERE, 'data', 'worlds.json')))[e.cfg['world']]['nodes']).reshape(-1, 3)
    frames, made = [], set()
    orig = e.step

    def rec(a):
        out = orig(a)
        a0 = int(np.asarray(a)[0])
        lab = econ_label(a0)
        new = [E.ITEMS[i] for i in range(E.NI) if np.isfinite(e.first[0, i]) and i not in made]
        made.update(i for i in range(E.NI) if np.isfinite(e.first[0, i]))
        if lab or new or not frames or e.t[0] - frames[-1]['t'] >= 5:
            order = node_order(e, nodes)
            used = []
            for x in range(6):
                k, f = int(e.n_ext[0, x]), int(e.n_free[0, x])
                used += [[n, 1] for n in order[x][:k]] + [[n, 0] for n in order[x][k:k + f]]
            frames.append(dict(t=float(e.t[0]), gold=round(float(e.gold[0]), 1), act=lab, new=new,
                               labs=e.lab_at[0].tolist(), used=used,
                               mach={E.RECIPES[j]['key']: int(e.n_rec[0, j]) for j in range(E.NR) if e.n_rec[0, j]},
                               buf=round(float(e.buf[0].sum() + e.bufc[0].sum()), 1),
                               rate=round(float(e.gold_rate[0]), 1)))
        return out
    e.step = rec
    pos, stall = 0, 0
    ti = E.IX[item]
    for _ in range(60 * (len(path) + 1)):
        if np.isfinite(e.first[0, ti]) or e.finished[0]:
            break
        want = path[pos] if pos < len(path) else 0
        ok = env.action_mask()[0, want]
        env.step(np.array([want if ok else 0]))
        stall = 0 if ok else stall + 1
        if pos < len(path) and (ok or stall > 40):
            pos += 1; stall = 0
    return dict(item=item, time=float(e.first[0, ti]), plan=describe(path), frames=frames,
                sites=[list(map(int, s)) for s in e.sites])


if __name__ == '__main__':
    best = json.load(open(sys.argv[1]))
    items = sys.argv[3:] or list(best)
    w = json.load(open(os.path.join(E.HERE, 'data', 'worlds.json')))['2CCJhI']['nodes']
    out = dict(nodes=w, resources=[E.ITEMS[E.RES_ITEM[x]] for x in range(6)], runs=[])
    for it in items:
        r = trace(it, best[it][1]); r['source'] = best[it][2]
        print(it, best[it][0], r['time'], len(r['frames']))
        out['runs'].append(r)
    json.dump(out, open(sys.argv[2], 'w'), separators=(',', ':'))
