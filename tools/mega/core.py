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
    if rec['building'] == 'factory_machine_shop' and heavy <= 90 and not any((rn, i['item']) in TWO_ARM for i in rec['in']):
        blocks = max(1, min(3, int(480 / (2 * heavy)), int(480 / (4 * rate1 * outa))))
        while blocks > 1 and 2 * blocks * math.ceil(n / (2 * blocks)) - n >= 2:
            blocks -= 1                                   # fewer spare machines
        serp = max(1, int(480 / (2 * blocks * heavy) + 1e-9))
        m = cl.shop_mod(rn, rec['id'], n, blocks, serp=serp)
        m.out_rates = {}
        cols = len([k for k in m.outs if k[0] == 'out']) * 2 - (math.ceil(n / (2 * blocks)) % 2)   # machine columns (m.groups counts serpentine runs)
        for k in range((cols + 1) // 2):
            m.out_rates[('out', k)] = min(2, cols - 2 * k) * 2 * blocks * rate1 * outa
        return m
    mg = max(1, int(480 / heavy + 1e-9))
    groups = math.ceil(n / mg)
    arms = [max(math.ceil(i['amount'] * rate1 / 90 - 1e-9), 2 if (rn, i['item']) in TWO_ARM else 1) for i in rec['in']]
    if max(arms) == 2:                                    # a second arm on those lanes, taking from the entrance
        per = [n // groups + (1 if k < n % groups else 0) for k in range(groups)]
        light = arms.index(1) if len(arms) == 2 and 1 in arms else None
        if light is not None and 2 * max(per) * rec['in'][light]['amount'] * rate1 > 480:
            light = None
        m = cl.paired_mod(rn, rec['id'], per, left=tuple(i for i, a in enumerate(arms) if a == 2), far=far, chain=None if far else light)
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


def gather(first, rest, need, cap_join=None):
    """first + joiners from rest (largest that fit one belt) -> (streams, total); the joiners together stay under cap_join"""
    chosen, tot, rest, joined = [first], first[2], list(rest), 0.0
    while tot < need - 1e-6 and rest:
        fit = [s_ for s_ in rest if tot + s_[2] <= 480 + 1e-6 and (cap_join is None or joined + s_[2] < cap_join)]
        if not fit:
            break
        other = next((s_ for s_ in fit if tot + s_[2] >= need - 1e-6), fit[-1])
        rest.remove(other)
        chosen.append(other)
        tot += other[2]
        joined += other[2]
    return chosen, tot


def feed_options(pool, need):
    """clean ways to feed a lane from the streams of its item: one stream, a thin leftover as the main stream with
    smaller joiners, or collectors / thick leftovers joined"""
    pool = sorted(pool, key=lambda s_: s_[2])
    opts, seen = [], set()

    def add(c, t):
        k = tuple(sorted((round(s_[2], 2), s_[3]) for s_ in c))
        if t >= need - 0.01 and k not in seen:
            seen.add(k)
            opts.append(c)
    for s_ in pool:
        if s_[2] >= need - 1e-6:
            add([s_], s_[2])
    strong = [s_ for s_ in pool if not s_[3]]
    for w in sorted((s_ for s_ in pool if s_[3]), key=lambda s_: -s_[2]):
        add(*gather(w, strong, need, cap_join=w[2]))
    for k in range(len(strong) - 1, -1, -1):
        add(*gather(strong[k], strong[:k] + strong[k + 1:], need))
    return opts


def plan_item(lanes, pool, leftover, limit=200000):
    """order the lanes of one item and choose each one's streams so that every lane is fed cleanly (search).
    lanes: [(need, module, key)]; leftover(lane, total) -> the stream that flows on, or None; -> [(lane, streams)] or None"""
    count = [0]

    def dfs(pool, rest, acc):
        if not rest:
            return acc
        count[0] += 1
        if count[0] > limit:
            return None
        tried = set()
        for k, lane in enumerate(rest):
            sig = (round(lane[0], 3), lane[1])
            if sig in tried:
                continue
            tried.add(sig)
            for ch in feed_options(pool, lane[0]):
                tot = sum(s_[2] for s_ in ch)
                new = [s_ for s_ in pool if not any(s_ is c for c in ch)]
                lf = leftover(lane, tot)
                if lf is not None:
                    new.append(lf)
                elif tot - lane[0] > 0.5:
                    continue                                 # more than the lane uses and no way on: lost supply
                r = dfs(new, rest[:k] + rest[k + 1:], acc + [(lane, ch)])
                if r is not None:
                    return r
        return None
    return dfs(list(pool), sorted(lanes, key=lambda t: -t[0]), [])


FLOW = {}                       # (module, out key) -> planned items per minute on that belt, filled by build()


TWO_ARM = set()                 # (recipe name, item): lanes that get a second arm per machine so a thin leftover can pass on
LEAKY = set()


def build():
    """the core's modules and belts; lanes whose thin leftover cannot be wired cleanly get a second arm, then again"""
    for k in range(6):
        LEAKY.clear()
        out = _build()
        new = LEAKY - TWO_ARM
        if not new:
            return out
        print('  second arm for: %s' % ', '.join('%s in %s' % (it, rn) for rn, it in sorted(new)))
        TWO_ARM.update(new)
    return out


def _build():
    FLOW.clear()
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
            streams.setdefault(m.out_item, []).append([i, key, rate, False])
    open_in = []                # inputs fed from outside the core
    lanes = [(need, i, key) for i, m in enumerate(mods) for key, need in m.need.items()]
    def flows_on(lane, tot):
        """the stream that leaves a lane fed with tot: [module, thru key, rate, thin?] or None"""
        need, i, (g, item) = lane
        left = tot - need
        thru = ('thru', g, item)
        if thru not in mods[i].outs and ('thru', g + 1, item) in mods[i].outs and (g + 1, item) not in mods[i].need:
            thru = ('thru', g + 1, item)                    # a chained pair: the lane ends on the lower group
        if thru not in mods[i].outs or left <= 0.5:
            return None
        rec_i = rs[names[i]][0]
        amt = [q['amount'] for q in rec_i['in'] if q['item'] == item][0] * 60.0 / rec_i['seconds'] * 3
        per_arm = amt / max(math.ceil(amt / 90 - 1e-9), 2 if (names[i], item) in TWO_ARM else 1)   # what the last arm on this lane must catch per minute
        # a tier 4 arm is busy 1/90 min per item: from a moving belt it catches 1 / (1/passing + 1/90)
        enough = float('inf') if per_arm >= 89.9 else 1 / (1 / per_arm - 1 / 90.0) - per_arm
        return [i, thru, left, left < enough]
    forced = {}                 # lane -> its streams, for the items where a clean wiring was found by search
    order = []
    standin_items = {key[-1] for m in mods if getattr(m, 'standin', False) for key in m.need}
    for item in sorted(streams):
        if item in standin_items:
            continue
        mine = [t for t in lanes if t[2][1] == item]
        plan = plan_item(mine, streams[item], flows_on) if mine else None
        if plan is None:
            if mine:
                print('  no clean wiring found for %s, falling back to the greedy one' % item)
            continue
        for lane, ch in plan:
            forced[(lane[1], lane[2])] = [(c[0], c[1]) for c in ch]
            order.append(lane)
    todo = order + [t for t in sorted(lanes, key=lambda t: -t[0]) if (t[1], t[2]) not in forced]
    waited = 0                  # lanes put back in a row (a full round of them = no clean way left)
    while todo:
        need, i, (g, item) = todo.pop(0)
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
                if k >= len(ports):                           # more belts than lanes on the west side: come in from the top
                    xt = 1 + 2 * len([1 for t_, d_ in m.ins.values() if d_ == 2])
                    ports.append(((xt, -1), 2))
                m.ins[('p', k, item)] = ports[k]
                nets.append((pick[0], pick[1], i, ('p', k, item)))
                FLOW[(pick[0], pick[1])] = pick[2]
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
        want_ids = forced.get((i, (g, item)))
        if want_ids:
            chosen = [next(s_ for s_ in pool if (s_[0], s_[1]) == w_) for w_ in want_ids]
            for c in chosen:
                pool.remove(c)
            pick = list(chosen[0][:2]) + [sum(c[2] for c in chosen), [(c[0], c[1]) for c in chosen[1:]]]
        elif pick is None and not pool:
            print('  nothing left: %s for %s wants %.1f' % (item, mods[i].name, need))
            open_in.append((i, (g, item), need))
            continue
        elif pick is None:      # join belts until there is enough; a belt carries 480, so the joined total must fit
            strong = [s_ for s_ in pool if not s_[3]]
            best = None
            for w in sorted((s_ for s_ in pool if s_[3]), key=lambda s_: -s_[2]):
                # a thin leftover (its last arm upstream only keeps up when this lane backs up into it) must be the
                # main stream here: merges alternate, so the smaller side never queues
                c, t = gather(w, strong, need, cap_join=w[2])
                if t >= need - 0.01:
                    best = (c, t)
                    break
            if best is None and strong:
                c, t = gather(strong[-1], strong[:-1], need)
                if t >= need - 0.01:
                    best = (c, t)
            if best is None and waited < len(todo):
                todo.append((need, i, (g, item)))                   # later lanes leave leftovers that may fit
                waited += 1
                continue
            if best is None:                                         # no clean way left: say what is wrong with it
                best = gather(pool[-1], pool[:-1], need)
                chosen, tot = best
                rest = [s_ for s_ in pool if s_ not in chosen]
                while tot < need - 1e-6 and rest:
                    other = rest.pop()
                    chosen.append(other)
                    tot += other[2]
                best = (chosen, tot)
                if any(s_[3] for s_ in chosen):
                    print('  LEAKY: %s for %s joins a thin leftover as a side stream' % (item, mods[i].name))
                    LEAKY.update((names[s_[0]], item) for s_ in chosen if s_[3])
            chosen, tot = best
            if tot > 480.5:
                print('  OVERFULL: %s for %s joins %.1f/min on one belt' % (item, mods[i].name, tot))
            if tot < need - 0.01:
                print('  short: %s for %s wants %.1f, has %.1f' % (item, mods[i].name, need, tot))
            for c in chosen:
                pool.remove(c)
            pick = list(chosen[0][:2]) + [tot, [(c[0], c[1]) for c in chosen[1:]]]
        else:
            pool.remove(pick)
            pick = list(pick[:3]) + [[]]
        waited = 0
        nets.append((pick[0], pick[1], i, (g, item)))
        FLOW[(pick[0], pick[1])] = pick[2]              # what this belt carries on arrival (its joins included)
        for (jm, jk) in pick[3]:
            nets.append((jm, jk, None, len(nets) - 1 - 0 * len(nets) if False else primary_index(nets)))
        lf = flows_on((need, i, (g, item)), pick[2])
        if lf is not None:
            pool.append(lf)
        thru = ('thru', g, item)
        if thru not in mods[i].outs and ('thru', g + 1, item) in mods[i].outs and (g + 1, item) not in mods[i].need:
            thru = ('thru', g + 1, item)
        mods[i].fed = getattr(mods[i], 'fed', set()) | {thru}
    used = {(a, ka) for a, ka, b, kb in nets}
    spare = {}
    for i, m in enumerate(mods):
        if not getattr(m, 'standin', False):
            cl.cap_all(m, [k for k in m.outs if (i, k) not in used and k[0] == 'thru'])
        for key in [k for k in m.outs if (i, k) not in used]:
            if key[0] == 'thru' and not getattr(m, 'standin', False):
                pass
            elif key[0] == 'out' or key == 'out':
                spare.setdefault(getattr(m, 'out_item', 'merge'), []).append((i, key))
                t, d = m.outs[key]                                  # leaves the core: a sink for the sim, nothing next to it
                if not getattr(m, 'standin', False):
                    m.sinks.append(t)
                    m.cells.add(step(t, d))
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
        off, flip, top, wl = pack.place(base, ch=ch, per_port=per_port, cap=cap, width=width, seconds=seconds, seed=seed0 + r + 1, wl_w=rnd.choice([0.02, 0.04, 0.06]), extra=extra)
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
    def near(e):
        (t, d) = tm[e[0]].ins[e[1]]
        px, py = t[0] + off[e[0]][0], t[1] + off[e[0]][1]
        dist, side = min((px - x0, 3), (x1 - px, 1), (py - y0, 2), (y1 - py, 0))
        return (d != side, dist)
    for (i, key, need) in sorted(open_in, key=near):    # inputs that face the border first: they get the slot in line
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


def widen(tm, off, fail, by=2):
    """open the layout where a failed routing jammed: the tiles wanted by several belts mark the spot; every module
    that starts past it moves `by` tiles away (right for a jam in an upright channel, down for a flat one).
    -> new offsets for the modules of tm, or None when there is no jam to read"""
    x0, y0 = fail['origin']
    red = sorted((x + x0, y + y0) for (x, y), k in fail.get('use', {}).items() if k > 1)
    if not red:
        return None
    xs = sorted(p[0] for p in red)
    ys = sorted(p[1] for p in red)
    mid = lambda v: v[len(v) // 2]
    iqr = lambda v: v[(3 * len(v)) // 4] - v[len(v) // 4]
    upright = iqr(ys) >= iqr(xs)                             # the jam runs north-south: make the channel wider
    old = [tuple(o) for o in off]
    for up in (upright, not upright):                        # the other direction when this one moves nothing
        k = 0 if up else 1
        for c in ((mid(xs) if up else mid(ys)) + dc for dc in (0, -2, 2, -5, 5, -9, 9)):
            new = []
            for m, o in zip(tm, off):
                lo = min(cc[k] for cc in m.cells) + o[k]
                new.append(((o[0] + by, o[1]) if up else (o[0], o[1] + by)) if lo > c else tuple(o))
            if new == old or len(set(new)) < 2 and False:
                continue
            if all(n_ != o_ for n_, o_ in zip(new, old)):
                continue                                     # everything moved: nothing opened
            seen, clash = set(), False
            for m, o in zip(tm, new):
                for cc in m.cells:
                    t = (cc[0] + o[0], cc[1] + o[1])
                    if t in seen:
                        clash = True
                        break
                    seen.add(t)
                if clash:
                    break
            if not clash:
                return new
    return None


def frame_main(seconds=90, rounds=10, seed0=0):
    """frame placement (ring-fed modules on the top and bottom edges) -> ring sources -> route -> game sim"""
    import frame
    import random
    import time
    mods, nets, open_in, spare = build()
    side = math.ceil(sum(len(m.cells) for m in mods) ** 0.5)
    seed0 = int(seed0)
    out = os.path.expanduser('~/data/core_frame%s' % ('_s%d' % seed0 if seed0 else ''))
    rnd = random.Random(3 + seed0)
    best = None
    t0 = time.time()
    want = {rec['id']: crafts * rec['out'][0]['amount'] for rn, (rec, crafts) in recipes().items()}
    for r in range(int(rounds)):
        per_port, cap, ch = rnd.choice([(0.5, 5, 3), (0.5, 5, 3), (0.5, 4, 3), (0.34, 4, 3), (0.5, 6, 3), (0.5, 4, 2)])
        width = side + rnd.randrange(10, 34)
        tm, off, top, wl = frame.place(mods, nets, open_in, ch=ch, per_port=per_port, cap=cap, width=width, seconds=seconds, seed=seed0 + r + 1,
                                       log=lambda s: print('  ' + s, flush=True))
        if tm is None:
            print('try %d: width %d does not hold the frame modules' % (r + 1, width), flush=True)
            continue
        tag = 'try %d (%.0f min): channel %d + %.2f per port (max %d), %dx%d, belt length %d' % (
            r + 1, (time.time() - t0) / 60, ch, per_port, cap, width, top, wl)
        for fix in range(13):                                # route; where it jams, open the layout there and go again
            tm2, nets2, off2 = add_ring(tm, nets, open_in, off)
            lay = cl.Layout(tm2, nets2)
            lay.rounds = 300
            res = lay.route(off2, timeout=600)
            f = getattr(lay, 'fail', None)
            if res is not None or not f or 'use' not in f or fix == 12:
                break
            cl.fail_picture(lay, off2, out + '_%d_jam.png' % (r + 1))
            off_new = widen(tm, off, f)
            if off_new is None:
                print('    no way to open the layout at this jam', flush=True)
                break
            print('    %d belts clash, opening the layout at the jam (%d)' % (len(f.get('nets', [])), fix + 1), flush=True)
            off = off_new
        if fix:
            tag += ', opened %d times' % fix
        if res is None:
            f = getattr(lay, 'fail', None)
            print(tag + ': did not route (%s)' % ('%d belts still clash' % len(f.get('nets', [])) if f else 'a port is blocked'), flush=True)
            if f and 'nets' not in f:
                import re
                k = int(re.search(r'net (\d+)', f.get('reason', 'net 0')).group(1))
                a_, ka, b_, kb = nets2[k]
                print('      %s: %s %s -> %s %s' % (f.get('reason'), tm2[a_].name, ka, 'join' if b_ is None else tm2[b_].name, kb), flush=True)
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
        pickle.dump((tm2, nets2, off2, res), open(out + '_%d.pkl' % (r + 1), 'wb'))
        print(tag + ': ROUTED %dx%d with %d route pieces, %d border problems' % (PW, PH, res['pieces'], len(bad)), flush=True)
        for b_ in bad[:20]:
            print('      check: ' + b_, flush=True)
        frame_diag(out + '_%d.pkl' % (r + 1))
        if best is None or max(PW, PH) < best:
            best = max(PW, PH)
            pickle.dump((off2, res, r + 1), open(out + '.pkl', 'wb'))
            json.dump({'rows': lay.game_rows(res)}, open(out + '.json', 'w'))
            cl.picture(lay, res, out + '.png')
    print('done, best side %s' % best, flush=True)


def frame_diag(path, minutes=40, warmup=60):
    """re-check and re-sim a routed frame layout (pickle of frame_main): border problems, each module against the plan,
    and every source that could not push its full rate"""
    tm, nets, off, res = pickle.load(open(os.path.expanduser(path), 'rb'))
    if not FLOW:
        build()
    lay = cl.Layout(tm, nets)
    lay.off = off
    for b_ in lay.check(res):
        print('check: ' + b_)
    ends = [lay.pos(e, ge) for (s_, d, gs, e, de, ge) in lay.nets]
    made, got = lay.sim(res, minutes=minutes, warmup=warmup, probes=ends)
    rep = lay.rep
    json.dump(rep, open(os.path.expanduser(path) + '.rep.json', 'w'))
    for i, (a_, ka, b_, kb) in enumerate(nets):             # what really arrives on every belt between modules
        if b_ is None or tm[a_].feed or getattr(tm[b_], 'standin', False):
            continue
        need = FLOW.get((a_, ka), tm[b_].need.get(kb, 0))     # planned: this lane's share plus what flows on
        flow = rep['probes'][i]
        if flow is not None and flow < 0.985 * need:
            print('belt %-22s %-14s -> %-22s %-26s carries %6.1f of %6.1f' % (tm[a_].name, ka, tm[b_].name, kb, flow, need))
    for k, v in sorted(got.items()):
        print('sink %-34s %s' % (k, {a: round(b, 1) for a, b in v.items()}))
    owner = {}
    for g, m in enumerate(tm):
        for c in m.cells:
            owner[(c[0] + off[g][0], c[1] + off[g][1])] = g
    per = {}
    for mm in rep['machines']:
        g = owner.get((mm['x'], mm['y']))
        rec = cl.RECIPES[mm['recipe']]
        nominal = 60.0 / rec['seconds'] * 3 * rec['out'][0]['amount']
        t = per.setdefault(g, [0, 0, 0, {}])
        t[0] += mm['perMin']
        t[1] += nominal
        t[2] += 1
        if mm['perMin'] < 0.9 * nominal:                    # why slow, from the state at the end
            why = 'output backed up' if mm['out'] >= 2 * rec['out'][0]['amount'] else 'waits for ' + ' + '.join(mm['lack']) if mm['lack'] else 'busy'
            t[3][why] = t[3].get(why, 0) + 1
    want = {rn.replace(' ', '_'): crafts * rec['out'][0]['amount'] for rn, (rec, crafts) in recipes().items()}
    for g, (made, nominal, n, why) in sorted(per.items(), key=lambda t: t[1][0] / t[1][1]):
        print('%-28s %3d machines make %8.1f/min, plan %8.1f, full speed %8.1f  %s' % (
            tm[g].name, n, made, want.get(tm[g].name, 0), nominal, ', '.join('%d %s' % (v, k) for k, v in sorted(why.items(), key=lambda t: -t[1]))))
    at = {}
    for g, m in enumerate(tm):
        if m.feed:
            at[(m.feed[0][0] + off[g][0], m.feed[0][1] + off[g][1])] = g
    print('%d of %d sources attached' % (rep['feeds'], rep['wanted']))
    for f in rep['fedRates']:
        g = at.get(tuple(f['at']))
        if g is None:
            continue
        if f['perMin'] < f['want'] * 0.98:
            dest = [tm[b_].name if b_ is not None else 'join' for a_, ka, b_, kb in nets if a_ == g]
            print('source %-22s gives %6.1f of %6.1f -> %s' % (f['kind'], f['perMin'], f['want'], dest))


def modcheck(minutes=60, warmup=30):
    """every core module alone in the game sim, each input lane fed exactly its plan share, used flow-through exits
    and collectors open: does it make its plan rate?  The check that a module (with its dead-end caps) is sound."""
    import subprocess
    import tempfile
    mods, nets, open_in, spare = build()
    want = {rn: crafts * rec['out'][0]['amount'] for rn, (rec, crafts) in recipes().items()}
    bad = 0
    for m in mods:
        if getattr(m, 'standin', False):
            continue
        bs = [dict(id=r[0], x=r[1], y=r[2], o=r[3], tier=r[4] + 1, recipe=r[5] if r[5] > 0 else 0) for r in m.rows]
        feeds, sinks = [], []
        for key, (t, d) in m.ins.items():
            if key not in m.need:
                continue
            a = step(t, (d + 2) % 4)
            bs += [dict(id=B, x=a[0], y=a[1], o=d, tier=4, recipe=0), dict(id=B, x=t[0], y=t[1], o=d, tier=4, recipe=0)]
            feeds.append(dict(at=list(a), kind=key[-1], perMin=m.need[key], buffer=10))
        for key, (t, d) in m.outs.items():
            a, b_ = step(t, d), step(t, d, 2)
            bs.append(dict(id=B, x=a[0], y=a[1], o=d, tier=4, recipe=0))
            if key[0] == 'thru':
                continue                                     # the lane carries only this module's share here: a dead end
            bs.append(dict(id=B, x=b_[0], y=b_[1], o=d, tier=4, recipe=0))
            sinks.append(list(b_))
        sinks += [list(t) for t in m.sinks]
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
            json.dump(dict(buildings=bs, feeds=feeds, warmup=warmup, minutes=minutes, sinks=sinks), f)
        rep = json.loads(subprocess.run(['node', 'tools/simlayout.mjs', f.name], capture_output=True, text=True, check=True).stdout)
        os.unlink(f.name)
        made = sum(mm['perMin'] for mm in rep['machines'])
        ok = made >= want[m.name] * 0.999
        bad += not ok
        print('%-26s %3d machines make %8.2f/min, plan %8.2f  %s' % (m.name, len(rep['machines']), made, want[m.name], 'ok' if ok else 'SHORT %.1f%%' % (100 * made / want[m.name])), flush=True)
    print('%d modules short' % bad)


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
        frame_main(*[float(a) for a in sys.argv[2:5]])
        sys.exit()
    if sys.argv[1] == 'modcheck':
        modcheck(*[float(a) for a in sys.argv[2:4]])
        sys.exit()
    if sys.argv[1] == 'diag':
        frame_diag(sys.argv[2], *[float(a) for a in sys.argv[3:5]])
        sys.exit()
    if sys.argv[1] == 'squeeze':
        squeeze_main(*[float(a) for a in sys.argv[2:3]])
        sys.exit()
    if sys.argv[1] == 'anneal':
        anneal_main(*[float(a) for a in sys.argv[2:4]])
    else:
        main(*[float(a) for a in sys.argv[1:4]])
