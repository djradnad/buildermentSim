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


def make(rn, rec, crafts):
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
        m = cl.paired_mod(rn, rec['id'], per, left=tuple(i for i, a in enumerate(arms) if a == 2))
        m.out_rates = {('out', k): c * rate1 * outa for k, c in enumerate(m.outm)}
        return m
    per = [n // groups + (1 if k < n % groups else 0) for k in range(groups)]
    rows = 2 if max(per) >= 8 else 1
    share = rows == 2 and max(per) * rate1 * outa <= 480
    m = cl.recipe_mod(rn, rec['id'], n, groups, rows, share=share)
    m.out_rates = {('out', k): c * rate1 * outa for k, c in enumerate(m.outm)}
    return m


def build():
    rs = recipes()
    mods, nets, names = [], [], []
    for rn, (rec, crafts) in sorted(rs.items()):
        if rec['building'] in CORE:
            m = make(rn, rec, crafts)
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
            pick = pool.pop()
            while pick[2] < need - 1e-6 and pool:
                other = pool.pop()
                j = len(mods)
                mods.append(cl.merge_mod())
                nets.append((pick[0], pick[1], j, 'a'))
                nets.append((other[0], other[1], j, 'b'))
                pick = [j, 'out', pick[2] + other[2]]
            if pick[2] < need - 1e-6:
                print('  short: %s for %s wants %.1f, has %.1f' % (item, mods[i].name, need, pick[2]))
        else:
            pool.remove(pick)
        nets.append((pick[0], pick[1], i, (g, item)))
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


def main(seconds=120):
    mods, nets, open_in, spare = build()
    area = sum(len(m.cells) for m in mods)
    print('%d modules, %d tiles (a %dx%d square before any routing), %d belts inside, %d open inputs' % (
        len(mods), area, math.ceil(area ** 0.5), math.ceil(area ** 0.5), len(nets), len(open_in)), flush=True)
    for m in sorted(mods, key=lambda m: -len(m.cells)):
        if len(m.cells) > 4:
            xs = [c[0] for c in m.cells]
            ys = [c[1] for c in m.cells]
            print('  %-26s %3dx%-3d %5d tiles%s' % (m.name, max(xs) - min(xs) + 1, max(ys) - min(ys) + 1, len(m.cells),
                                                  '  (stand-in)' if getattr(m, 'standin', False) else ''))
    print('  spare product ends:', {k: len(v) for k, v in spare.items()}, flush=True)
    if seconds <= 0:
        return
    lay = cl.Layout(mods, nets)
    x = y = rowh = 0
    off = []
    for i in sorted(range(len(mods)), key=lambda i: -len(mods[i].cells)):
        xs = [c[0] for c in mods[i].cells]
        ys = [c[1] for c in mods[i].cells]
        w, h = max(xs) - min(xs) + 4, max(ys) - min(ys) + 4
        if x + w > 105:
            x, y, rowh = 0, y + rowh, 0
        off.append((i, (x - min(xs), y - min(ys))))
        x += w
        rowh = max(rowh, h)
    lay.off = [o for i, o in sorted(off)]
    out = os.path.expanduser('~/data/core_gen')
    import pack
    tries = []
    for width in (96, 100, 104, 108):
        off2, top, wl = pack.place(lay, ch=2, width=width, seconds=seconds / 4, seed=width, log=lambda s: print('  ' + s, flush=True))
        tries.append((max(width, top) + 0.02 * wl, width, off2))
    for score, width, off2 in sorted(tries):
        print('routing the width %d packing...' % width, flush=True)
        res = lay.route(off2, timeout=300)
        if res is None:
            print('  did not route', flush=True)
            continue
        lay.off = off2
        pickle.dump((lay.off, res), open(out + '.pkl', 'wb'))
        for line in lay.check(res):
            print('  CHECK:', line, flush=True)
        W, H = cl.picture(lay, res, out + '.png')
        print('routed: %dx%d, %d route pieces' % (W, H, res['pieces']), flush=True)
        return
    print('no routed placement')


if __name__ == '__main__':
    main(*[float(a) for a in sys.argv[1:2]])
