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


FIXES = 14
# channel per belt on a module side, its cap, base channel: roomy first, the shrink step tightens it
ROOM = [tuple(float(v) if '.' in v else int(v) for v in os.environ['BM_ROOM'].split(','))] if os.environ.get('BM_ROOM') else [(0.8, 8, 5), (0.7, 7, 6), (1.0, 8, 4)]


def dead_ends(T):
    """the tile in front of every ring belt end that is not a typed exit (lane ends, leftover raw): kept free of
    route pieces, the belt simply ends there and backs up"""
    jmod.reach([list(r) for r in T['rows']])
    typed = {(e[0], e[1], e[2]) for e in T['exits']}
    return {step(p, d) for p, d in jmod.OUTS if (p[0], p[1], d) not in typed}


def reroute(path, W=230, H=224):
    """route a saved layout again (same module places) with the current ring rules"""
    tm, nets, off, res = pickle.load(open(os.path.expanduser(path), 'rb'))
    T = json.load(open(os.path.expanduser('~/data/ring_typed_%dx%d.json' % (int(W), int(H)))))
    tm[-1].cells.update(dead_ends(T))
    R = len(tm) - 1
    hx, hy, hw, hh = T['hole']
    for fix in range(FIXES + 1):
        lay = cl.Layout(tm, nets)
        lay.rounds = int(os.environ.get("BM_ROUNDS", 400))
        res = lay.route(off, timeout=float(os.environ.get('BM_RTIME', 1200)))
        f = getattr(lay, 'fail', None)
        if res is not None or not f or 'use' not in f or fix == FIXES:
            break
        new = core.widen(tm[:R], off[:R], f)
        if new is None:
            break
        dx = max(n[0] - o[0] for n, o in zip(new, off[:R]))
        dy = max(n[1] - o[1] for n, o in zip(new, off[:R]))
        new = [(n[0] - dx, n[1] - dy) for n in new]
        if min(c[0] + o[0] for m, o in zip(tm[:R], new) for c in m.cells) < hx + 1 or min(c[1] + o[1] for m, o in zip(tm[:R], new) for c in m.cells) < hy + 1:
            print('the hole is full', flush=True)
            break
        print('%d belts clash, opened the core by %d,%d (%d)' % (len(f.get('nets', [])), dx, dy, fix + 1), flush=True)
        off = new + [(0, 0)]
    if res is None:
        print('did not route')
        return
    lay.off = off
    bad = lay.check(res)
    out = os.path.expanduser(path)[:-4] + 'r'
    PW, PH = cl.picture(lay, res, out + '.png')
    pickle.dump((tm, nets, off, res), open(out + '.pkl', 'wb'))
    json.dump({'rows': lay.game_rows(res)}, open(out + '.json', 'w'))
    print('ROUTED %dx%d with %d route pieces, %d border problems -> %s.pkl' % (PW, PH, res['pieces'], len(bad)))
    for b_ in bad[:20]:
        print('   check: ' + b_)


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
    m.cells.update(dead_ends(T))
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
    fixed = []
    for a, ka, b, kb in nets:
        if a == R and b is not None:
            t, d = mods[R].outs[ka]
            fixed.append((b, kb, (t[0] - hx, t[1] - hy)))
    t0 = time.time()
    for r in range(tries):
        per_port, cap, ch = rnd.choice(ROOM)
        width = hw - rnd.randrange(8, 30)
        tm, off, top, wl = frame.place(mods[:R], cnets, fed, ch=ch, per_port=per_port, cap=cap, width=width, seconds=float(seconds), seed=seed0 + r + 1, fixed=fixed, hole=(hw, hh),
                                       log=lambda s: print('  ' + s, flush=True))
        if tm is None:
            print('try %d: width %d does not hold the frame modules' % (r + 1, width), flush=True)
            continue
        xs = [c[0] + o[0] for m, o in zip(tm, off) for c in m.cells]
        ys = [c[1] + o[1] for m, o in zip(tm, off) for c in m.cells]
        cw, chh = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
        tag = 'try %d (%.0f min): core %dx%d in hole %dx%d' % (r + 1, (time.time() - t0) / 60, cw, chh, hw, hh)
        if cw > hw - 6 or chh > hh - 6:
            print(tag + ': does not fit', flush=True)
            continue
        # bottom right corner of the hole: most ring belts come from the stack on top and the steel column on the left
        sx, sy = hx + hw - cw - 3 - min(xs), hy + hh - chh - 3 - min(ys)
        tm2 = list(tm) + [mods[R]]
        off2 = [(o[0] + sx, o[1] + sy) for o in off] + [(0, 0)]
        for fix in range(FIXES + 1):                          # route; where it jams, open the core there and go again
            lay = cl.Layout(tm2, nets)
            lay.rounds = int(os.environ.get("BM_ROUNDS", 300))
            res = lay.route(off2, timeout=float(os.environ.get('BM_RTIME', 900)))
            f = getattr(lay, 'fail', None)
            if res is not None or not f or 'use' not in f or fix == FIXES:
                break
            cl.fail_picture(lay, off2, out + '_%d_jam.png' % (r + 1))
            new = core.widen(tm2[:R], off2[:R], f)
            if new is None:
                print('    no way to open the core at this jam', flush=True)
                break
            dx = max(n[0] - o[0] for n, o in zip(new, off2[:R]))
            dy = max(n[1] - o[1] for n, o in zip(new, off2[:R]))
            new = [(n[0] - dx, n[1] - dy) for n in new]      # the core sits in the bottom right corner: grow up and left
            lx = min(c[0] + o[0] for m, o in zip(tm2[:R], new) for c in m.cells)
            ly = min(c[1] + o[1] for m, o in zip(tm2[:R], new) for c in m.cells)
            if lx < hx + 1 or ly < hy + 1:
                print('    the hole is full, cannot open the core further', flush=True)
                break
            print('    %d belts clash, opened the core by %d,%d at the jam (%d)' % (len(f.get('nets', [])), dx, dy, fix + 1), flush=True)
            off2 = new + [(0, 0)]
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
    if len(sys.argv) > 1 and sys.argv[1] == 'reroute':
        reroute(*sys.argv[2:5])
    elif len(sys.argv) > 1 and sys.argv[1] == 'diag':
        diag(*sys.argv[2:5])
    else:
        main(*sys.argv[1:6])
