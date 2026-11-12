"""The whole mega factory: john's ring (ring.py) as one fixed block, the core generated inside its hole and every
ring product routed from the ring's real exit belts to its consumers.

    python3 -u tools/mega/mega.py W H [seconds] [tries] [seed]     ring size, placement seconds per try
    python3 -u tools/mega/mega.py diag <pkl> [minutes] [warmup]    settled sim of a routed layout against the plan

Needs ~/data/ring_typed_WxH.json (python3 tools/mega/ring.py W H 30: the ring with its typed lanes and exits)."""
import collections
import json
import math
import os
import pickle
import random
import sys
import time

sys.path.insert(0, '.')
sys.path.insert(0, 'tools/mega')
import cluster as cl
import core
import jmod
from bmsolver.pieces import DIRS, step


def make_ring(W, H):
    T = json.load(open(os.path.expanduser('~/data/ring_typed_%dx%d.json' % (W, H))))
    rows = [list(r) for r in T['rows']]
    used = set(cl.Mod('ring', rows).cells)
    feeds = []
    for x, y, d, item in T['inputs']:                       # the map side: two belts in front of every lane, fed full
        dx, dy = DIRS[d]
        at = (x, y)
        for n in (1, 2):
            q = (x - n * dx, y - n * dy)
            if q in used or jmod.pours_into(T['rows'], q):
                break
            rows.append([3000, q[0], q[1], d, 0, -1, 0])
            used.add(q)
            at = q
        feeds.append((at, item, 480.0))
    ex = [list(e) for e in T['exits']]
    conc = [e for e in ex if e[3] == 'Concrete']
    if conc:                                                # typed with the spare frame exit draining freely, which starves
        min(conc, key=lambda e: e[4])[4] += max(0, 1080 - sum(e[4] for e in conc))   # two forges; behind the core it backs up
    T['exits'] = ex
    m = cl.Mod('ring', rows)
    m.feeds = feeds
    m.need, m.out_rates, m.ring_streams = {}, {}, []
    for k, (x, y, d, item, rate) in enumerate(T['exits']):
        if step((x, y), d) in m.cells:
            print('ring exit %s %s is blocked' % ((x, y), item))
            continue
        m.outs[('out', k)] = ((x, y), d)
        m.ring_streams.append((('out', k), item, rate))
    return m, T['hole']


def build(W, H):
    ring, hole = make_ring(W, H)
    core.RING = ring
    mods, nets, open_in, spare = core.build()
    R = len(mods) - 1
    assert mods[R] is ring
    return mods, nets, open_in, spare, hole


def core_part(mods, nets):
    """the netlist without the ring, for the placement: -> (nets between core modules, lanes fed from the ring)"""
    R = len(mods) - 1
    keep = [i for i, n in enumerate(nets) if n[0] != R and not (n[2] is None and nets[n[3]][0] == R)]
    idx = {i: k for k, i in enumerate(keep)}
    out = [nets[i] if nets[i][2] is not None else (nets[i][0], nets[i][1], None, idx[nets[i][3]]) for i in keep]
    fed = [(b, kb, mods[b].need.get(kb, 0)) for a, ka, b, kb in nets if a == R and b is not None]
    return out, fed


def main(W=230, H=224, seconds=60, tries=4, seed0=0):
    import frame
    W, H, tries, seed0 = int(W), int(H), int(tries), int(seed0)
    mods, nets, open_in, spare, hole = build(W, H)
    R = len(mods) - 1
    for i, key, need in open_in:
        print('  still open: %s %s %.1f' % (mods[i].name, key, need))
    cnets, fed = core_part(mods, nets)
    hx, hy, hw, hh = hole
    print('hole %dx%d at (%d, %d); %d core modules, %d belts (%d from the ring)' % (hw, hh, hx, hy, R, len(nets), len(nets) - len(cnets)), flush=True)
    out = os.path.expanduser('~/data/mega_%dx%d_s%d' % (W, H, seed0))
    rnd = random.Random(7 + seed0)
    t0 = time.time()
    for r in range(tries):
        per_port, cap, ch = rnd.choice([(0.5, 5, 3), (0.5, 4, 3), (0.34, 4, 3), (0.5, 6, 3), (0.5, 4, 2)])
        width = hw - rnd.randrange(8, 30)
        tm, off, top, wl = frame.place(mods[:R], cnets, fed, ch=ch, per_port=per_port, cap=cap, width=width, seconds=float(seconds), seed=seed0 + r + 1,
                                       log=lambda s: print('  ' + s, flush=True))
        if tm is None:
            print('try %d: width %d does not hold the frame modules' % (r + 1, width), flush=True)
            continue
        xs = [c[0] + o[0] for m, o in zip(tm, off) for c in m.cells]
        ys = [c[1] + o[1] for m, o in zip(tm, off) for c in m.cells]
        cw, chh = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
        tag = 'try %d (%.0f min): core %dx%d in hole %dx%d' % (r + 1, (time.time() - t0) / 60, cw, chh, hw, hh)
        if cw > hw - 4 or chh > hh - 4:
            print(tag + ': does not fit', flush=True)
            continue
        sx, sy = hx + (hw - cw) // 2 - min(xs), hy + (hh - chh) // 2 - min(ys)
        tm2 = list(tm) + [mods[R]]
        off2 = [(o[0] + sx, o[1] + sy) for o in off] + [(0, 0)]
        lay = cl.Layout(tm2, nets)
        lay.rounds = 300
        res = lay.route(off2, timeout=1800)
        if res is None:
            f = getattr(lay, 'fail', None)
            print(tag + ': did not route (%s)' % ('%d belts still clash' % len(f.get('nets', [])) if f and 'nets' in f else f.get('reason') if f else 'a port is blocked'), flush=True)
            if f and 'use' in f:
                cl.fail_picture(lay, off2, out + '_%d_jam.png' % (r + 1))
            if f and 'nets' in f:
                c = collections.Counter()
                for k in f['nets']:
                    a_, ka, b_, kb = nets[k]
                    c[(tm2[a_].name, 'join' if b_ is None else tm2[b_].name)] += 1
                for k, v in c.most_common(12):
                    print('      %d x %s -> %s' % (v, k[0], k[1]), flush=True)
            continue
        lay.off = off2
        bad = lay.check(res)
        PW, PH = cl.picture(lay, res, out + '_%d.png' % (r + 1))
        pickle.dump((tm2, nets, off2, res), open(out + '_%d.pkl' % (r + 1), 'wb'))
        json.dump({'rows': lay.game_rows(res)}, open(out + '_%d.json' % (r + 1), 'w'))
        print(tag + ': ROUTED %dx%d with %d route pieces, %d border problems' % (PW, PH, res['pieces'], len(bad)), flush=True)
        for b_ in bad[:20]:
            print('      check: ' + b_, flush=True)


def diag(path, minutes=60, warmup=120):
    tm, nets, off, res = pickle.load(open(os.path.expanduser(path), 'rb'))
    lay = cl.Layout(tm, nets)
    lay.off = off
    made, got = lay.sim(res, minutes=float(minutes), warmup=float(warmup))
    rep = lay.rep
    json.dump(rep, open(os.path.expanduser(path) + '.rep.json', 'w'))
    low = []
    for rn, (rec, crafts) in sorted(core.recipes().items()):
        if rec['id'] not in made:
            print('%-26s not built' % rn)
            continue
        got_ = made[rec['id']]
        want = crafts * rec['out'][0]['amount']
        n = sum(1 for mm in rep['machines'] if mm['recipe'] == rec['id'])
        print('%-26s %4d machines make %9.1f/min, plan %9.1f  %5.1f%%' % (rn, n, got_, want, 100 * got_ / want))
        if got_ < 0.997 * want:
            low.append('%s %.1f%%' % (rn, 100 * got_ / want))
    fed = collections.defaultdict(float)
    for f in rep['fedRates']:
        fed[f['kind']] += f['perMin']
    print('taken: ' + ', '.join('%s %.2f belts' % (k, v / 480) for k, v in sorted(fed.items())))
    for k, v in sorted(got.items()):
        print('sink %-34s %s' % (k, {a: round(b, 2) for a, b in v.items()}))
    print('VERDICT %s: %s' % (os.path.basename(path), 'every recipe at plan' if not low else 'under plan: ' + ', '.join(low)))


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'diag':
        diag(*sys.argv[2:5])
    else:
        main(*sys.argv[1:6])
