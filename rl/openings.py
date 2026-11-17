"""Runner-style openings (john 2026-11-17): pre-build extractors before the factory is affordable (their 10-item
buffers fill), pick the lab site, move the lab when iron is needed (delete, rebuild near iron, later a 2nd lab
back at the old site). Enumerates these variants of each best plan, keeps the fastest, then polishes it.
usage: python -m rl.openings <best.json> [polish rounds]"""
import itertools, json, sys
import numpy as np
from . import econ as E
from .expert import polish, run_paths
from .macro import M_DLAB, M_LAB, M_LABAT, M_PRE, M_X1, M_X4

LABS = [M_LAB, M_LABAT + 1, M_LABAT + 2]


def variants(path, rng, n=1500):
    base = [a for a in path if a != M_LAB and not (M_LABAT <= a < M_DLAB + E.NS)]
    need = {}
    for a in base:
        if M_X1 <= a < M_X4: need[a - M_X1] = need.get(a - M_X1, 0) + 1
        if M_X4 <= a < M_X4 + E.NX: need[a - M_X4] = need.get(a - M_X4, 0) + 4
    res = sorted(need)
    out = set()
    for _ in range(n):
        s0 = int(rng.integers(3))
        pre = []
        for x in res:
            k = int(rng.choice([0, 0, 1, 2, 4, need[x], need[x] + 2]))
            pre += [M_PRE + x] * k
        rng.shuffle(pre)
        cut = int(rng.integers(len(pre) + 1))           # some extractors before the lab, some right after
        p = pre[:cut] + [LABS[s0]] + pre[cut:] + list(base)
        if rng.random() < 0.6:                          # move the lab: delete it, rebuild elsewhere, maybe a 2nd later
            i = int(rng.integers(cut + 1, len(p) + 1))
            s1 = int(rng.choice([s for s in range(3) if s != s0]))
            mv = [M_DLAB + s0, LABS[s1]]
            p = p[:i] + mv + p[i:]
            if rng.random() < 0.6:
                j = int(rng.integers(i + 2, len(p) + 1))
                p = p[:j] + [LABS[s0]] + p[j:]
        out.add(tuple(p))
    return [list(p) for p in out]


def main(db, rounds=20):
    best = json.load(open(db))
    rng = np.random.default_rng(1)
    for it in list(best):
        t, p, src = best[it]
        cands = variants(p, rng)
        ts = []
        for i in range(0, len(cands), 256):
            ts += list(run_paths(it, cands[i:i + 256], cutoff=t + 1))
        k = int(np.argmin(ts))
        t1, p1 = float(ts[k]), cands[k]
        if t1 < t:
            t2, p2 = polish(it, p1, rounds=rounds)
            if t2 < t1: t1, p1 = t2, p2
        if t1 < t:
            best[it] = (t1, p1, src.split('+')[0] + '+opening')
            json.dump(best, open(db, 'w'))
        print('%-14s %7.1f -> %7.1f' % (it, t, min(t, t1)), flush=True)


if __name__ == '__main__':
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 20)
