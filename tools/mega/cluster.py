"""End-to-end cluster builder: recipe modules (hop-lane families) + item chains with flow-through + placement
(annealing) + routing (negotiated) + a game-sim run of the whole thing.

    python tools/mega/cluster.py mc [seconds]      # matter compressor with turbocharger, motor, tank, frame
"""
import json
import math
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, '.')
sys.path.insert(0, 'tools/mega')
import anneal_core
import compact_core as cc
import lanemod
import modlib
import reroute_core as rc
from bmsolver.pieces import DIRS, KIND_OF_ID, BUILDING_ID, step

B, I, O = 3000, 3002, 3003
RECIPES = {r['id']: r for r in modlib.gamedata()['recipes'].values()}


class Mod:
    def __init__(self, name, rows):
        self.name, self.rows = name, rows
        self.cells = set()
        for r in rows:
            self.cells.update([(r[1], r[2])] if r[0] in KIND_OF_ID else rc.footprint(r))
        self.ins = {}          # key -> (tile the feeding belt sits on, direction it pushes)
        self.outs = {}         # key -> (tile where the product appears, direction it arrives)
        self.feed = None       # (tile, item, per minute) for the sim
        self.sink = None       # tile for the sim


def recipe_mod(name, recipe, n, groups=1, rows=1, share=False):
    rec = RECIPES[recipe]
    bid = {'factory_manufacturer': 3010, 'factory_industrial': 3008, 'factory_machine_shop': 3007}[rec['building']]
    L = len(rec['in'])
    per = [n // groups + (1 if k < n % groups else 0) for k in range(groups)]
    fam = lanemod.stack([lanemod.build(bid, recipe, m, rows, L, share=share) for m in per])
    mod = Mod(name, [list(r) for r in fam['rows']])
    mod.rec, mod.groups, mod.per = rec, groups, per
    rate1 = 60.0 / rec['seconds'] * 3
    for g in range(groups):
        for i, ing in enumerate(rec['in']):
            x, y, d = fam['inputs'][g * L + i]
            mod.ins[(g, ing['item'])] = (step((x, y), (d + 2) % 4), d)
            j = i if rows % 2 == 1 else L - 1 - i
            x, y, d = fam['through'][g * L + j]
            mod.outs[('thru', g, ing['item'])] = ((x, y), d)
        mod.need = {(g, ing['item']): ing['amount'] * rate1 * per[g] for g in range(groups) for ing in rec['in']}
    mod.outm = fam['outm']
    for k, (x, y, d) in enumerate(fam['outputs']):
        mod.outs[('out', k)] = ((x, y), d)
    mod.out_item = rec['out'][0]['item']
    mod.out_rate = rate1 * n
    return mod


def shop_mod(name, recipe, n, blocks):
    """machine-shop recipe in the dense two-lane column form; n is rounded up to whole columns"""
    import shopmod
    rec = RECIPES[recipe]
    cols = -(-n // (2 * blocks))
    fam = shopmod.build(recipe, blocks, cols)
    mod = Mod(name, [list(r) for r in fam['rows']])
    mod.rec, mod.groups, mod.per = rec, cols, [2 * blocks] * cols
    rate1 = 60.0 / rec['seconds'] * 3
    mod.need = {}
    for g in range(cols):
        for i, ing in enumerate(rec['in']):
            x, y, d = fam['inputs'][g * 2 + i]
            mod.ins[(g, ing['item'])] = (step((x, y), (d + 2) % 4), d)
            x, y, d = fam['through'][g * 2 + i]
            mod.outs[('thru', g, ing['item'])] = ((x, y), d)
            mod.need[(g, ing['item'])] = ing['amount'] * rate1 * 2 * blocks
    for k, (x, y, d) in enumerate(fam['outputs']):
        mod.outs[('out', k)] = ((x, y), d)
    mod.out_item = rec['out'][0]['item']
    mod.out_rate = rate1 * 2 * blocks * cols * rec['out'][0]['amount']
    return mod


def cap(mod, key):
    """dead-end an unused flow-through exit with a short belt so the last arm can still take from it"""
    (x, y), d = mod.outs.pop(key)
    for k in (1, 2):
        t = step((x, y), d, k)
        mod.rows.append([B, t[0], t[1], d, 0, -1, 0])
        mod.cells.add(t)
    mod.cells.add(step((x, y), d, 3))                  # keep the tile past the dead end empty


def source_mod(item, rate):
    m = Mod('src ' + item, [[B, 0, 0, 3, 0, -1, 0], [B, 1, 0, 3, 0, -1, 0]])
    m.outs['out'] = ((1, 0), 3)
    m.feed = ((0, 0), item, rate)
    return m


def sink_mod(name):
    m = Mod('sink ' + name, [[B, 0, 0, 3, 0, -1, 0], [B, 1, 0, 3, 0, -1, 0]])
    m.ins['in'] = ((-1, 0), 3)
    m.sink = (1, 0)
    m.cells.add((2, 0))                                # nothing may sit where the sink pushes
    return m


def merge_mod():
    m = Mod('merge', [[B, 0, 0, 3, 0, -1, 0]])
    m.ins['a'] = ((-1, 0), 3)
    m.ins['b'] = ((0, -1), 2)
    m.outs['out'] = ((0, 0), 3)
    return m


class Layout(cc.State):
    def __init__(self, mods, nets):
        """nets: (module index, out key, module index, in key)"""
        self.mods = mods
        self.n = len(mods)
        self.cells = [sorted(m.cells) for m in mods]
        self.off = [(0, 0)] * self.n
        self.nets = []
        for a, ka, b, kb in nets:
            (t, d) = mods[a].outs[ka]
            (e, de) = mods[b].ins[kb]
            self.nets.append((step(t, d), d, a, e, de, b))
        self.ug = []
        self.group = {}
        for g, m in enumerate(mods):
            for r in m.rows:
                if r[0] in (I, O):
                    p = (r[1], r[2], g)
                    self.ug.append((p, KIND_OF_ID[r[0]] + str(r[3])))
                    self.group[p] = g

    def pos(self, c, g, off=None):
        o = (off or self.off)[g]
        return (c[0] + o[0], c[1] + o[1])

    def game_rows(self, res):
        x0, y0 = res['origin']
        rows = []
        for g, m in enumerate(self.mods):
            o = self.off[g]
            rows += [[r[0], r[1] + o[0], r[2] + o[1]] + list(r[3:]) for r in m.rows]
        rows += [[BUILDING_ID[c[0]], x + x0, y + y0, int(c[1]), 0, -1, 0] for (x, y), c in sorted(res['tiles'].items())]
        return rows

    def check(self, res):
        """every push across a module border must be one of the planned belts; -> list of problems"""
        x0, y0 = res['origin']
        owner, piece = {}, {}
        for g, m in enumerate(self.mods):
            o = self.off[g]
            for c in m.cells:
                owner[(c[0] + o[0], c[1] + o[1])] = g
            for r in m.rows:
                if r[0] in (B, I, O):
                    piece[(r[1] + o[0], r[2] + o[1])] = (r[0], r[3])
        for (x, y), c in res['tiles'].items():
            p = (x + x0, y + y0)
            if p in owner:
                return ['route piece on module tile %s' % (p,)]
            owner[p] = -1
            piece[p] = (BUILDING_ID[c[0]], int(c[1]))
        ok = set()
        for (s, d, gs, e, de, ge) in self.nets:
            a, b = self.pos(s, gs), self.pos(e, ge)
            ok.add((step(a, (d + 2) % 4), a))
            ok.add((b, step(b, de)))
        bad = []
        for p, (kind, d) in piece.items():
            if kind == I:
                continue
            q = step(p, d)
            if q in owner and owner[q] != owner[p] and (p, q) not in ok:
                bad.append('%s (%s) pushes into %s (%s)' % (p, self.name(owner[p]), q, self.name(owner[q])))
        return bad

    def name(self, g):
        return 'route' if g < 0 else self.mods[g].name

    def sim(self, res, minutes=60, warmup=40, save=None):
        rows = self.game_rows(res)
        bs = [dict(id=r[0], x=r[1], y=r[2], o=r[3], tier=r[4] + 1, recipe=r[5] if r[5] > 0 else 0) for r in rows]
        feeds, sinks, names = [], [], {}
        for g, m in enumerate(self.mods):
            o = self.off[g]
            if m.feed:
                t, item, rate = m.feed
                feeds.append(dict(at=[t[0] + o[0], t[1] + o[1]], kind=item, perMin=rate, buffer=10))
            if m.sink:
                sinks.append([m.sink[0] + o[0], m.sink[1] + o[1]])
                names['%d,%d' % (m.sink[0] + o[0], m.sink[1] + o[1])] = m.name
        if save:
            json.dump(dict(buildings=bs, feeds=feeds, warmup=warmup, minutes=minutes, sinks=sinks), open(save, 'w'))
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
            json.dump(dict(buildings=bs, feeds=feeds, warmup=warmup, minutes=minutes, sinks=sinks), f)
        try:
            rep = json.loads(subprocess.run(['node', 'tools/simlayout.mjs', f.name], capture_output=True, text=True, check=True).stdout)
        finally:
            os.unlink(f.name)
        self.rep = rep
        made = {}
        for mm in rep['machines']:
            made[mm['recipe']] = made.get(mm['recipe'], 0) + mm['perMin']
        got = {names.get(k, k): v for k, v in rep['sinks'].items()}
        return made, got


# the UG list of cc.State.route keys tiles by position; give Layout its own version of the two lookups it needs
def _route(self, off, timeout=60):
    from bmsolver import negotiate, place
    from bmsolver.pieces import UG_REACH
    occ = {}
    for g in range(self.n):
        for c in self.cells[g]:
            occ[self.pos(c, g, off)] = g
    xs = [p[0] for p in occ]
    ys = [p[1] for p in occ]
    x0, y0, x1, y1 = min(xs) - 2, min(ys) - 2, max(xs) + 2, max(ys) + 2
    W, H = x1 - x0 + 1, y1 - y0 + 1
    sh = lambda p: (p[0] - x0, p[1] - y0)
    blocked = {sh(p) for p in occ}
    pos_ug = {}
    for (x, y, g), c in self.ug:
        pos_ug[self.pos((x, y), g, off)] = c
    fixed_ug, no_ug = set(), set()
    for p, c in pos_ug.items():
        fixed_ug.add((sh(p), int(c[1])))
        if c[0] == 'I':
            d = int(c[1])
            for k in range(1, UG_REACH + 1):
                q = step(p, d, k)
                if pos_ug.get(q) == 'O%d' % d:
                    break
                no_ug.add((sh(q), d))
    sources, sinks = [], []
    owner = {}
    for i, (s, d, gs, e, de, ge) in enumerate(self.nets):
        a, b = sh(self.pos(s, gs, off)), sh(self.pos(e, ge, off))
        if a in blocked or b in blocked:
            return None
        for t in (a, b):
            if owner.setdefault(t, i) != i:
                return None
        sources.append((a, d, i))
        sinks.append((b, de, i))
    ok, res, secs = negotiate.route(W, H, len(self.nets), blocked, sources, sinks, fixed_ug=fixed_ug, no_ug=no_ug, log=getattr(self, "rlog", None),
                                    open_cost=0.2, timeout=timeout, rounds=40)
    if not ok:
        return None
    try:
        place.trace(dict(W=W, H=H, blocked=blocked, sources=sources, sinks=sinks, nets=len(self.nets), reach=UG_REACH), res)
    except AssertionError as ex:
        print('trace failed:', ex)
        return None
    res['origin'] = (x0, y0)
    return res


Layout.route = _route


def mc_cluster():
    """matter compressor (plan 77/min) with its four suppliers at plan size"""
    mods, nets = [], []

    def add(m):
        mods.append(m)
        return len(mods) - 1
    mc = add(recipe_mod('Matter Compressor', 2039, 14, 1, 2))
    tank = add(recipe_mod('Tank', 2038, 5, 1, 1))
    turbo = add(recipe_mod('Turbocharger', 2054, 12, 2, 1))
    motor = add(recipe_mod('Electric Motor', 2052, 25, 3, 1))
    frame = add(recipe_mod('Industrial Frame', 2053, 27, 6, 1))

    def external(item, rate, m, g):
        s = add(source_mod(item, rate))
        nets.append((s, 'out', m, (g, item)))

    def supply(m):
        """every input lane of every group of m gets its own outside source, and unused lane ends are capped"""
        mod = mods[m]
        for (g, item), need in mod.need.items():
            if (m, (g, item)) not in fed:
                external(item, need, m, g)
        for key in [k for k in mod.outs if k[0] == 'thru' and (m, k) not in used]:
            cap(mod, key)
    fed, used = set(), set()

    def merged(m):
        """one port carrying all of m's product: its collectors joined by merge tiles"""
        keys = [k for k in mods[m].outs if k[0] == 'out']
        cur = (m, keys[0])
        for k in keys[1:]:
            j = add(merge_mod())
            nets.append((cur[0], cur[1], j, 'a'))
            nets.append((m, k, j, 'b'))
            cur = (j, 'out')
        return cur
    # product chains: producer -> matter compressor lane -> flow-through end -> leftover sink
    for prod, name in ((tank, 'Tank'), (turbo, 'Turbocharger'), (motor, 'Electric Motor'), (frame, 'Industrial Frame')):
        a, ka = merged(prod)
        nets.append((a, ka, mc, (0, name)))
        fed.add((mc, (0, name)))
        left = mods[prod].out_rate - mods[mc].need[(0, name)]
        if left > 1:
            s = add(sink_mod(name + ' leftover'))
            nets.append((mc, ('thru', 0, name), s, 'in'))
            used.add((mc, ('thru', 0, name)))
    out = add(sink_mod('Matter Compressor'))
    a, ka = merged(mc)
    nets.append((a, ka, out, 'in'))
    for m in (mc, tank, turbo, motor, frame):
        supply(m)
    return mods, nets


def picture(lay, res, path):
    from PIL import Image, ImageDraw
    import colorsys
    rows = lay.game_rows(res)
    xs, ys = [], []
    cells = {}
    for g, m in enumerate(lay.mods):
        for c in m.cells:
            cells[lay.pos(c, g)] = g
    x0, y0 = res['origin']
    for (x, y) in res['tiles']:
        cells[(x + x0, y + y0)] = -1
    xs = [p[0] for p in cells]
    ys = [p[1] for p in cells]
    mx, my = min(xs), min(ys)
    W, H = max(xs) - mx + 1, max(ys) - my + 1
    Z = max(4, min(12, 1400 // max(W, H)))
    im = Image.new('RGB', (W * Z, H * Z), (20, 22, 26))
    d = ImageDraw.Draw(im)
    big = [g for g, m in enumerate(lay.mods) if len(m.cells) > 4]
    for (x, y), g in cells.items():
        if g == -1:
            col = (235, 235, 235)
        elif g in big:
            col = tuple(int(255 * v) for v in colorsys.hsv_to_rgb(big.index(g) / len(big), 0.55, 0.8))
        else:
            col = (255, 80, 80)
        d.rectangle([(x - mx) * Z, (y - my) * Z, (x - mx) * Z + Z - 2, (y - my) * Z + Z - 2], fill=col)
    im.save(path)
    return W, H


def main(which='mc', seconds=120):
    mods, nets = mc_cluster()
    lay = Layout(mods, nets)
    area = sum(len(m.cells) for m in mods)
    print('%d modules (%d tiles), %d belts to route' % (len(mods), area, len(nets)), flush=True)
    # start: spread the modules on a row so nothing overlaps, then anneal
    x = y = rowh = 0
    off = []
    for m in sorted(range(len(mods)), key=lambda i: -len(mods[i].cells)):
        xs = [c[0] for c in mods[m].cells]
        ys = [c[1] for c in mods[m].cells]
        w, h = max(xs) - min(xs) + 5, max(ys) - min(ys) + 5
        if x + w > 110:
            x, y, rowh = 0, y + rowh, 0
        off.append((m, (x - min(xs), y - min(ys))))
        x += w
        rowh = max(rowh, h)
    off = [o for m, o in sorted(off)]
    lay.off = off
    best = None
    for attempt in range(4):
        an = anneal_core.Anneal(lay, ch=2 + attempt // 2, box_w=30.0, seed=attempt + 1)
        off2 = an.run(float(seconds), log=lambda s: None)
        x0, y0, x1, y1 = an.box()
        print('attempt %d: box %dx%d, belt length %d, overlap %d' % (attempt + 1, x1 - x0 + 1, y1 - y0 + 1, an.wl, an.overlap), flush=True)
        if an.overlap:
            continue
        res = lay.route(off2, timeout=120)
        if res is None:
            print('  did not route', flush=True)
            continue
        lay.off = off2
        best = res
        break
    if best is None:
        print('no routed placement')
        return
    out = os.path.expanduser('~/data/cluster_%s' % which)
    import pickle
    pickle.dump((lay.off, best), open(out + '.pkl', 'wb'))
    for line in lay.check(best):
        print('  CHECK:', line, flush=True)
    json.dump({'rows': lay.game_rows(best)}, open(out + '.json', 'w'))
    W, H = picture(lay, best, out + '.png')
    print('routed: %dx%d, %d route pieces; running the game sim...' % (W, H, best['pieces']), flush=True)
    made, got = lay.sim(best, save=out + '_sim.json')
    for rid, v in sorted(made.items()):
        print('  machines make %-20s %.1f/min' % (RECIPES[rid]['out'][0]['item'], v))
    for k, v in got.items():
        print('  %-30s %s' % (k, {a: round(b, 1) for a, b in v.items()}))


if __name__ == '__main__':
    main(*sys.argv[1:3])
