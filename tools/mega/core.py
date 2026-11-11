"""The whole core generated from the ET plan (data/plan.json): one tailored module per recipe made in machine
shops, industrial factories and manufacturers, joined by flow-through belts, placed and routed.

Items made outside the core (ring items: ingots, plates, wire, glass, ...) are left as open module inputs for now.
Four recipes need more than one arm per ingredient (Atomic Locator, Electromagnet, Gyroscope, Matter Duplicator +
Particle Glue) and are stand-in blocks of the size of john's builds until they get their own generator.

    python tools/mega/core.py [seconds]
"""
import json
import math
import os
import pickle
import sys

sys.path.insert(0, '.')
sys.path.insert(0, 'tools/mega')
import anneal_core
import cluster as cl
from bmsolver.pieces import step

B = 3000
CORE = ('factory_manufacturer', 'factory_industrial', 'factory_machine_shop')
STANDIN = {2035: (33, 8), 2041: (14, 13)}     # recipe -> (w, h)
GLUE_IN_DUPLICATOR = True       # john's duplicator build makes its own particle glue from matter compressors


def recipes():
    """plan name -> (recipe, crafts per minute)"""
    plan = json.load(open('data/plan.json'))
    recs = list(cl.RECIPES.values())
    out = {}
    for item, v in plan.items():
        for rn, belts in v['use'].items():
            alt = rn.endswith('_ALT')
            name = rn.replace('_ALT', '').replace('_', ' ')
            r = [r for r in recs if r['out'][0]['item'] == name and r['alt'] == alt]
            if not r:
                continue
            amt = [i['amount'] for i in r[0]['in'] if i['item'] == item][0]
            out[rn] = (r[0], belts * 480 / amt)
    return out


def standin(name, rec, crafts):
    w, h = STANDIN[rec['id']]
    m = cl.Mod(name, [])
    m.cells = {(x, y) for x in range(w) for y in range(h)}
    m.rec, m.need, m.standin = rec, {}, True
    y = 0
    for ing in rec['in']:
        if rec['id'] == 2041 and ing['item'] == 'Particle Glue':
            continue
        tot = ing['amount'] * crafts
        k = max(1, math.ceil(tot / 480 - 1e-9))
        for g in range(k):
            m.ins[(g, ing['item'])] = ((-1, y), 3)
            m.need[(g, ing['item'])] = tot / k
            y += 1
    if rec['id'] == 2041:
        m.ins[(0, 'Matter Compressor')] = ((-1, y), 3)
        glue = [i['amount'] for i in rec['in'] if i['item'] == 'Particle Glue'][0]
        m.need[(0, 'Matter Compressor')] = crafts * glue / 10.0         # 1 compressor -> 10 glue
    tot = rec['out'][0]['amount'] * crafts
    k = max(1, math.ceil(tot / 480 - 1e-9))
    m.out_rates = {}
    for g in range(k):
        m.outs[('out', g)] = ((w - 1, g), 3)
        m.out_rates[('out', g)] = tot / k
    m.out_item = rec['out'][0]['item']
    return m


def make(rn, rec, crafts, far=False):
    rate1 = 60.0 / rec['seconds'] * 3
    n = math.ceil(crafts / rate1 - 1e-6)
    outa = rec['out'][0]['amount']
    heavy = max(i['amount'] for i in rec['in']) * rate1
    if rec['id'] in STANDIN:
        return standin(rn, rec, crafts)
    if rec['building'] == 'factory_machine_shop' and heavy <= 90:
        blocks = max(1, min(3, int(480 / (2 * heavy)), int(480 / (4 * rate1 * outa))))
        while blocks > 1 and 2 * blocks * math.ceil(n / (2 * blocks)) - n >= 2:
            blocks -= 1                                   # fewer spare machines
        m = cl.shop_mod(rn, rec['id'], n, blocks)
        m.out_rates = {}
        cols = m.groups
        for k in range((cols + 1) // 2):
            m.out_rates[('out', k)] = min(2, cols - 2 * k) * 2 * blocks * rate1 * outa
        return m
    mg = max(1, int(480 / heavy + 1e-9))
    groups = math.ceil(n / mg)
    arms = [math.ceil(i['amount'] * rate1 / 90 - 1e-9) for i in rec['in']]
    if max(arms) == 2:                                    # a second arm on those lanes, taking from the entrance
        per = [n // groups + (1 if k < n % groups else 0) for k in range(groups)]
        m = cl.paired_mod(rn, rec['id'], per, left=tuple(i for i, a in enumerate(arms) if a == 2), far=far)
        m.out_rates = {('out', k): c * rate1 * outa for k, c in enumerate(m.outm)}
        return m
    per = [n // groups + (1 if k < n % groups else 0) for k in range(groups)]
    rows = 2 if max(per) >= 8 and not far else 1
    share = rows == 2 and max(per) * rate1 * outa <= 480
    m = cl.recipe_mod(rn, rec['id'], n, groups, rows, share=share, far=far)
    m.out_rates = {('out', k): c * rate1 * outa for k, c in enumerate(m.outm)}
    return m


def primary_index(nets):
    """index of the last real (non-join) net"""
    return max(i for i, n in enumerate(nets) if n[2] is not None)


def build():
    rs = recipes()
    mods, nets, names = [], [], []
    made_in_core = {rec['out'][0]['item'] for rec, c in rs.values() if rec['building'] in CORE}
    for rn, (rec, crafts) in sorted(rs.items()):
        if rec['building'] in CORE:
            m = make(rn, rec, crafts, far=not any(i['item'] in made_in_core for i in rec['in']))
            if not getattr(m, 'standin', False):           # plan rates: the machines are rounded up, the belts are not
                i0 = rec['in'][0]
                f = i0['amount'] * crafts / sum(v for (g, it), v in m.need.items() if it == i0['item'])
                full = sum(m.out_rates.values())
                want = crafts * rec['out'][0]['amount']
                m.need = {k: v * f for k, v in m.need.items()}
                m.out_rates = {k: v * want / full for k, v in m.out_rates.items()}
            mods.append(m)
            names.append(rn)
    streams = {}                # item -> list of [module, out key, rate]
    for i, m in enumerate(mods):
        for key, rate in m.out_rates.items():
            streams.setdefault(m.out_item, []).append([i, key, rate])
    open_in = []                # inputs fed from outside the core
    lanes = [(need, i, key) for i, m in enumerate(mods) for key, need in m.need.items()]
    for need, i, (g, item) in sorted(lanes, key=lambda t: -t[0]):
        pool = streams.get(item)
        if pool is not None and getattr(mods[i], 'standin', False):
            if g:
                continue
            m = mods[i]
            tot = sum(v for (gg, it), v in m.need.items() if it == item)
            ports = [m.ins[(gg, it)] for (gg, it) in sorted(m.need) if it == item]
            y = max(t[1] for t, d in m.ins.values()) + 1
            k = 0
            pool.sort(key=lambda s: s[2])
            while tot > 0.5 and pool:
                pick = next((s for s in pool if s[2] >= tot - 1e-6), None) or pool[-1]
                pool.remove(pick)
                if k >= len(ports):
                    ports.append(((-1, y), 3))
                    y += 1
                m.ins[('p', k, item)] = ports[k]
                nets.append((pick[0], pick[1], i, ('p', k, item)))
                tot -= pick[2]
                k += 1
            if tot > 0.5:
                print('  short: %s for %s by %.1f' % (item, m.name, tot))
            if tot < -0.5:
                print('  note: %s gets %.1f/min more %s than it uses' % (m.name, -tot, item))
            continue
        if pool is None:
            open_in.append((i, (g, item), need))
            continue
        pool.sort(key=lambda s: s[2])
        pick = next((s for s in pool if s[2] >= need - 1e-6), None)
        if pick is None and not pool:
            print('  nothing left: %s for %s wants %.1f' % (item, mods[i].name, need))
            open_in.append((i, (g, item), need))
            continue
        if pick is None:        # join belts until there is enough
            pick = list(pool.pop()) + [[]]
            while pick[2] < need - 1e-6 and pool:
                other = pool.pop()
                pick[3].append((other[0], other[1]))                # joins this belt on the way (router's job)
                pick[2] += other[2]
            if pick[2] < need - 1e-6:
                print('  short: %s for %s wants %.1f, has %.1f' % (item, mods[i].name, need, pick[2]))
        else:
            pool.remove(pick)
        nets.append((pick[0], pick[1], i, (g, item)))
        for (jm, jk) in (pick[3] if len(pick) > 3 else []):
            nets.append((jm, jk, None, len(nets) - 1 - 0 * len(nets) if False else primary_index(nets)))
        left = pick[2] - need
        thru = ('thru', g, item)
        if thru in mods[i].outs and left > 0.5:
            pool.append([i, thru, left])
        mods[i].fed = getattr(mods[i], 'fed', set()) | {thru}
    used = {(a, ka) for a, ka, b, kb in nets}
    spare = {}
    for i, m in enumerate(mods):
        for key in [k for k in m.outs if (i, k) not in used]:
            if key[0] == 'thru' and not getattr(m, 'standin', False):
                cl.cap(m, key)
            elif key[0] == 'out' or key == 'out':
                spare.setdefault(getattr(m, 'out_item', 'merge'), []).append((i, key))
    return mods, nets, open_in, spare


def main(seconds=120, rounds=1, ring=0):
    """keeps trying packings (width, channel rule, seed) and routing them; every routed one that beats the best
    so far is saved to ~/data/core_gen.{pkl,png,json}"""
    import pack
    import random
    import time
    mods, nets, open_in, spare = build()
    area = sum(len(m.cells) for m in mods)
    side = math.ceil(area ** 0.5)
    print('%d modules, %d tiles (a %dx%d square before any routing), %d belts inside, %d open inputs' % (
        len(mods), area, side, side, len(nets), len(open_in)), flush=True)
    if seconds <= 0:
        for m in sorted(mods, key=lambda m: -len(m.cells)):
            if len(m.cells) > 4:
                xs = [c[0] for c in m.cells]
                ys = [c[1] for c in m.cells]
                print('  %-26s %3dx%-3d %5d tiles%s' % (m.name, max(xs) - min(xs) + 1, max(ys) - min(ys) + 1, len(m.cells),
                                                      '  (stand-in)' if getattr(m, 'standin', False) else ''))
        return
    base = cl.Layout(mods, nets)
    out = os.path.expanduser('~/data/core_gen')
    best = None
    rnd = random.Random(1)
    t0 = time.time()
    for r in range(int(rounds)):
        per_port, cap, ch = rnd.choice([(0.34, 4, 3), (0.5, 5, 3), (0.5, 6, 2), (0.34, 4, 4), (0.5, 8, 3)])
        width = side + rnd.randrange(8, 34)
        extra = [(i, mods[i].ins[key][0]) for (i, key, need) in open_in] if ring else ()
        off, flip, top, wl = pack.place(base, ch=ch, per_port=per_port, cap=cap, width=width, seconds=seconds, seed=r + 1, wl_w=rnd.choice([0.02, 0.04, 0.06]), extra=extra)
        tm = [cl.transpose(m) if f else m for m, f in zip(mods, flip)]
        nets_r = nets
        if ring:
            tm, nets_r, off = add_ring(tm, nets, open_in, off)
        lay = cl.Layout(tm, nets_r)
        xs = [c[0] + o[0] for cells, o in zip(lay.cells, off) for c in cells]
        ys = [c[1] + o[1] for cells, o in zip(lay.cells, off) for c in cells]
        W, H = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
        tag = 'try %d (%.0f min): channel %d + %.2f per port (max %d), %dx%d, belt length %d, %d turned' % (
            r + 1, (time.time() - t0) / 60, ch, per_port, cap, W, H, wl, sum(flip))
        if best and max(W, H) >= best and not ring:
            print(tag + ': not smaller than the best, skipped', flush=True)
            continue
        res = lay.route(off, timeout=900 if ring else 150)
        if res is None:
            print(tag + ': did not route', flush=True)
            continue
        lay.off = off
        bad = lay.check(res)
        for line in bad:
            print('  CHECK:', line, flush=True)
        best = max(W, H)
        pickle.dump((off, flip, res), open(out + '.pkl', 'wb'))
        json.dump({'rows': lay.game_rows(res)}, open(out + '.json', 'w'))
        PW, PH = cl.picture(lay, res, out + '.png')
        print(tag + ': ROUTED %dx%d with %d route pieces%s' % (PW, PH, res['pieces'], '' if not bad else ' (check failed)'), flush=True)
        if ring:
            made, got = lay.sim(res, minutes=40, warmup=60)
            want = {rec['id']: crafts * rec['out'][0]['amount'] for rn, (rec, crafts) in recipes().items()}
            for rid, v in sorted(made.items(), key=lambda t: t[1] / want[t[0]]):
                print('    %-24s sim %8.1f/min, plan %8.1f (%.0f%%)' % (cl.RECIPES[rid]['out'][0]['item'], v, want[rid], 100 * v / want[rid]), flush=True)
    print('done, best side %s' % best, flush=True)




def anneal_main(seconds=600, rounds=6):
    """pack (legal start, turned modules) -> free annealing on belt length and box -> route; keeps the smallest"""
    import pack
    import random
    import time
    mods, nets, open_in, spare = build()
    base = cl.Layout(mods, nets)
    side = math.ceil(sum(len(m.cells) for m in mods) ** 0.5)
    out = os.path.expanduser('~/data/core_ann')
    rnd = random.Random(7)
    best = None
    t0 = time.time()
    for r in range(int(rounds)):
        off, flip, top, wl = pack.place(base, ch=1, per_port=0.25, cap=3, width=side + 30, seconds=30, seed=r + 1, wl_w=0.05)
        tm = [cl.transpose(m) if f else m for m, f in zip(mods, flip)]
        lay = cl.Layout(tm, nets)
        lay.off = off
        ch = rnd.choice([2, 2, 3])
        an = anneal_core.Anneal(lay, ch=ch, box_w=rnd.choice([10.0, 30.0]), seed=r + 1)
        off2 = an.run(float(seconds), log=lambda s: None)
        x0, y0, x1, y1 = an.box()
        W, H = x1 - x0 + 1, y1 - y0 + 1
        tag = 'anneal %d (%.0f min): channel %d, %dx%d, belt length %d, overlap %d' % (r + 1, (time.time() - t0) / 60, ch, W, H, an.wl, an.overlap)
        if an.overlap or (best and max(W, H) >= best):
            print(tag + ': skipped', flush=True)
            continue
        res = lay.route(off2, timeout=900)
        if res is None:
            print(tag + ': did not route (%d belts still clash)' % len(getattr(lay, 'fail', {}).get('nets', [])), flush=True)
            continue
        lay.off = off2
        bad = lay.check(res)
        best = max(W, H)
        pickle.dump((off2, flip, res), open(out + '.pkl', 'wb'))
        json.dump({'rows': lay.game_rows(res)}, open(out + '.json', 'w'))
        PW, PH = cl.picture(lay, res, out + '.png')
        print(tag + ': ROUTED %dx%d with %d route pieces%s' % (PW, PH, res['pieces'], '' if not bad else ' (check failed: %s)' % bad[0]), flush=True)
    print('done, best side %s' % best, flush=True)


def border_source(item, rate, d):
    """two belts pointing d (into the core) with a sim feed on the first"""
    dx, dy = cl.DIRS[d]
    m = cl.Mod('ring ' + item, [[B, 0, 0, d, 0, -1, 0], [B, dx, dy, d, 0, -1, 0]])
    m.outs['out'] = ((dx, dy), d)
    m.feed = ((0, 0), item, rate)
    return m


def add_ring(tm, nets, open_in, off):
    """every input that is fed from outside the core gets a source on the nearest side of the core's box
    -> (modules, nets, offsets) with the sources appended"""
    xs = [c[0] + o[0] for m, o in zip(tm, off) for c in m.cells]
    ys = [c[1] + o[1] for m, o in zip(tm, off) for c in m.cells]
    x0, y0, x1, y1 = min(xs) - 3, min(ys) - 3, max(xs) + 3, max(ys) + 3
    mods2, nets2, off2 = list(tm), list(nets), list(off)
    taken = set()
    for (i, key, need) in open_in:
        (t, d) = tm[i].ins[key]
        px, py = t[0] + off[i][0], t[1] + off[i][1]
        side = min((px - x0, 3), (x1 - px, 1), (py - y0, 2), (y1 - py, 0))[1]     # direction the source pushes
        k = 0
        while True:
            for sgn in (1, -1):
                c = (px if side in (0, 2) else py) + sgn * k
                if (side, c) not in taken:
                    break
            else:
                k += 1
                continue
            break
        taken.add((side, c))
        pos = {3: (x0 - 2, c), 1: (x1 + 2, c), 2: (c, y0 - 2), 0: (c, y1 + 2)}[side]
        mods2.append(border_source(key[-1], need, side))
        off2.append(pos)
        nets2.append((len(mods2) - 1, 'out', i, key))
    return mods2, nets2, off2


def frame_main(seconds=90, rounds=10):
    """frame placement (ring-fed modules on the top and bottom edges) -> ring sources -> route -> game sim"""
    import frame
    import random
    import time
    mods, nets, open_in, spare = build()
    side = math.ceil(sum(len(m.cells) for m in mods) ** 0.5)
    out = os.path.expanduser('~/data/core_frame')
    rnd = random.Random(3)
    best = None
    t0 = time.time()
    want = {rec['id']: crafts * rec['out'][0]['amount'] for rn, (rec, crafts) in recipes().items()}
    for r in range(int(rounds)):
        per_port, cap, ch = rnd.choice([(0.34, 3, 2), (0.34, 4, 3), (0.5, 4, 2), (0.25, 2, 2), (0.5, 5, 3)])
        width = side + rnd.randrange(12, 40)
        tm, off, top, wl = frame.place(mods, nets, open_in, ch=ch, per_port=per_port, cap=cap, width=width, seconds=seconds, seed=r + 1,
                                       log=lambda s: print('  ' + s, flush=True))
        if tm is None:
            print('try %d: width %d does not hold the frame modules' % (r + 1, width), flush=True)
            continue
        tm2, nets2, off2 = add_ring(tm, nets, open_in, off)
        lay = cl.Layout(tm2, nets2)
        tag = 'try %d (%.0f min): channel %d + %.2f per port (max %d), %dx%d, belt length %d' % (
            r + 1, (time.time() - t0) / 60, ch, per_port, cap, width, top, wl)
        res = lay.route(off2, timeout=600)
        if res is None:
            f = getattr(lay, 'fail', None)
            print(tag + ': did not route (%s)' % ('%d belts still clash' % len(f.get('nets', [])) if f else 'a port is blocked'), flush=True)
            if f:
                import collections
                c = collections.Counter()
                for k in f.get('nets', []):
                    a_, ka, b_, kb = nets2[k]
                    c[(tm2[a_].name, 'join' if b_ is None else tm2[b_].name)] += 1
                for k, v in c.most_common(14):
                    print('      %d x %s -> %s' % (v, k[0], k[1]), flush=True)
            continue
        lay.off = off2
        bad = lay.check(res)
        PW, PH = cl.picture(lay, res, out + '_%d.png' % (r + 1))
        print(tag + ': ROUTED %dx%d with %d route pieces%s' % (PW, PH, res['pieces'], '' if not bad else ' (check: %s)' % bad[0]), flush=True)
        made, got = lay.sim(res, minutes=40, warmup=60)
        for rid, v in sorted(made.items(), key=lambda t: t[1] / want[t[0]]):
            print('    %-24s sim %8.1f/min, plan %8.1f (%.0f%%)' % (cl.RECIPES[rid]['out'][0]['item'], v, want[rid], 100 * v / want[rid]), flush=True)
        if best is None or max(PW, PH) < best:
            best = max(PW, PH)
            pickle.dump((off2, res, r + 1), open(out + '.pkl', 'wb'))
            json.dump({'rows': lay.game_rows(res)}, open(out + '.json', 'w'))
            cl.picture(lay, res, out + '.png')
    print('done, best side %s' % best, flush=True)


def squeeze_main(minutes=100, src='~/data/core_gen.pkl'):
    """pull the modules of a routed core toward the middle one at a time, re-routing everything after each move;
    a move stays when the core still routes.  Saves ~/data/core_sq.{pkl,json,png} after every gain."""
    import time
    mods, nets, open_in, spare = build()
    off, flip, res = pickle.load(open(os.path.expanduser(src), 'rb'))
    tm = [cl.transpose(m) if f else m for m, f in zip(mods, flip)]
    lay = cl.Layout(tm, nets)
    lay.off = list(off)
    out = os.path.expanduser('~/data/core_sq')
    t0 = time.time()

    def box(o, r=None):
        xs = [c[0] + oo[0] for cells, oo in zip(lay.cells, o) for c in cells]
        ys = [c[1] + oo[1] for cells, oo in zip(lay.cells, o) for c in cells]
        if r:
            xs += [x + r['origin'][0] for (x, y) in r['tiles']]
            ys += [y + r['origin'][1] for (x, y) in r['tiles']]
        return min(xs), min(ys), max(xs), max(ys)

    def overlaps(o, g):
        mine = {(c[0] + o[g][0], c[1] + o[g][1]) for c in lay.cells[g]}
        for h in range(lay.n):
            if h != g and any((c[0] + o[h][0], c[1] + o[h][1]) in mine for c in lay.cells[h]):
                return True
        return False
    b = box(lay.off, res)
    print('start %dx%d' % (b[2] - b[0] + 1, b[3] - b[1] + 1), flush=True)
    step_sizes = [8, 4, 2, 1]
    while time.time() - t0 < minutes * 60:
        gained = False
        b = box(lay.off)
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        W, H = b[2] - b[0], b[3] - b[1]

        def far(g):
            xs = [c[0] + lay.off[g][0] for c in lay.cells[g]]
            ys = [c[1] + lay.off[g][1] for c in lay.cells[g]]
            mx, my = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
            return max(abs(mx - cx) / max(W, 1), abs(my - cy) / max(H, 1)), mx, my
        for g in sorted(range(lay.n), key=lambda g: -far(g)[0]):
            if time.time() - t0 > minutes * 60:
                break
            _, mx, my = far(g)
            dirs = []
            if abs(my - cy) / max(H, 1) >= abs(mx - cx) / max(W, 1):
                dirs = [(0, 1 if my < cy else -1), (1 if mx < cx else -1, 0)]
            else:
                dirs = [(1 if mx < cx else -1, 0), (0, 1 if my < cy else -1)]
            done = False
            for dx, dy in dirs:
                for k in step_sizes:
                    o2 = list(lay.off)
                    o2[g] = (lay.off[g][0] + dx * k, lay.off[g][1] + dy * k)
                    if overlaps(o2, g):
                        continue
                    r2 = lay.route(o2, timeout=90)
                    if r2 is None:
                        continue
                    lay.off, res = o2, r2
                    bb = box(lay.off, res)
                    print('%.0f min: %s moved %d,%d -> %dx%d, %d route pieces' % (
                        (time.time() - t0) / 60, tm[g].name, dx * k, dy * k, bb[2] - bb[0] + 1, bb[3] - bb[1] + 1, res['pieces']), flush=True)
                    pickle.dump((lay.off, flip, res), open(out + '.pkl', 'wb'))
                    gained = done = True
                    break
                if done:
                    break
        if not gained:
            break
    json.dump({'rows': lay.game_rows(res)}, open(out + '.json', 'w'))
    PW, PH = cl.picture(lay, res, out + '.png')
    print('done %dx%d, check %s' % (PW, PH, lay.check(res) or 'ok'), flush=True)


if __name__ == '__main__':
    if sys.argv[1] == 'frame':
        frame_main(*[float(a) for a in sys.argv[2:4]])
        sys.exit()
    if sys.argv[1] == 'squeeze':
        squeeze_main(*[float(a) for a in sys.argv[2:3]])
        sys.exit()
    if sys.argv[1] == 'anneal':
        anneal_main(*[float(a) for a in sys.argv[2:4]])
    else:
        main(*[float(a) for a in sys.argv[1:4]])
