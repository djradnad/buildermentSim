"""Placement with a frame: modules fed only from outside the core sit on the top or bottom edge with their input
side facing out (their ring belts then run straight in), every other module is packed between them.

Skyline packing of an ordering (top-edge modules first, bottom-edge modules last), annealing over the ordering,
which edge each frame module takes and how the inner modules are turned."""
import math
import random
import time

import cluster as cl
from bmsolver.pieces import step


def info(mod, ports, ring_ports, ch, per_port, cap, outward=None):
    """bounding box, channel per side (left, top, right, bottom), from the belts on each side"""
    xs = [c[0] for c in mod.cells]
    ys = [c[1] for c in mod.cells]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    cnt = [0, 0, 0, 0]
    for (x, y) in list(ports) + list(ring_ports):
        if x < x0:
            cnt[0] += 1
        elif x > x1:
            cnt[2] += 1
        elif y < y0:
            cnt[1] += 1
        elif y > y1:
            cnt[3] += 1
    mar = [min(cap, int(math.ceil(c * per_port))) for c in cnt]
    mar[2] += ch
    mar[3] += ch
    if outward is not None:
        mar[outward] = 0 if outward == 1 else 1
    return (x0, y0, x1, y1, mar)


def ring_side(mod, keys):
    """which side all of the module's outside-fed inputs are on: 1 top, 3 bottom, else None"""
    xs = [c[0] for c in mod.cells]
    ys = [c[1] for c in mod.cells]
    ts = [mod.ins[k][0] for k in keys]
    if all(t[1] < min(ys) for t in ts):
        return 1
    if all(t[1] > max(ys) for t in ts):
        return 3
    return None


def place(mods, nets, open_in, ch=2, per_port=0.34, cap=3, width=110, seconds=60, seed=1, wl_w=0.03, log=None):
    """-> (chosen modules, offsets, height, belt length)"""
    rnd = random.Random(seed)
    n = len(mods)
    ring_keys = [[] for _ in range(n)]
    for (i, key, need) in open_in:
        ring_keys[i].append(key)
    fed_inside = {b for a, ka, b, kb in nets if b is not None}
    frame = [g for g in range(n) if ring_keys[g] and g not in fed_inside and not getattr(mods[g], 'standin', False)]
    var = []                                   # per module: list of (Mod, outward side or None)
    for g in range(n):
        if g in frame:
            f = cl.facings(mods[g])
            top = next((m for m in f if ring_side(m, ring_keys[g]) == 1), None)
            bot = next((m for m in f if ring_side(m, ring_keys[g]) == 3), None)
            if top and bot:
                var.append([(top, 1), (bot, 3)])
                continue
            frame.remove(g)
        var.append([(mods[g], None), (cl.transpose(mods[g]), None)])
    fs = set(frame)

    def pt(g, v, spec):
        m = var[g][v][0]
        if spec[0] == 'i':
            return m.ins[spec[1]][0]
        t, d = m.outs[spec[1]]
        return step(t, d, spec[2])
    ends = []
    for i, (a, ka, b, kb) in enumerate(nets):
        if b is None:
            a2, ka2 = nets[kb][0], nets[kb][1]
            ends.append(((a, ('o', ka, 1)), (a2, ('o', ka2, 2))))
        else:
            ends.append(((a, ('o', ka, 1)), (b, ('i', kb))))
    G = []
    for g in range(n):
        row = []
        for v, (m, outward) in enumerate(var[g]):
            ports = [pt(g, v, sp) for e in ends for (gg, sp) in e if gg == g]
            rp = [] if g in fs else [m.ins[k][0] for k in ring_keys[g]]
            row.append(info(m, ports, rp, ch, per_port, cap, outward))
        G.append(row)

    def cost(order, v):
        sky = [0] * width
        pos = {}
        top = 0
        pen = 0
        seq = [g for g in order if g in fs and v[g] == 0] + [g for g in order if g not in fs] + [g for g in order if g in fs and v[g] == 1]
        for g in seq:
            x0, y0, x1, y1, m = G[g][v[g]]
            w, h = x1 - x0 + 1 + m[0] + m[2], y1 - y0 + 1 + m[1] + m[3]
            best = None
            x = 0
            while x + w <= width:
                y = max(sky[x:x + w])
                if best is None or y < best[0]:
                    best = (y, x)
                x += 1
            if best is None or best[0] >= 10 ** 5:
                return 10 ** 9, None, 0, 0
            y, x = best
            if g in fs and v[g] == 0 and y > 0:
                pen += 60
            for i in range(x, x + w):
                sky[i] = 10 ** 6 if (g in fs and v[g] == 1) else y + h
            pos[g] = (x + m[0] - x0, y + m[1] - y0)
            top = max(top, y + h)
        wl = 0
        for (ga, sa), (gb, sb) in ends:
            a, b = pt(ga, v[ga], sa), pt(gb, v[gb], sb)
            wl += abs(pos[ga][0] + a[0] - pos[gb][0] - b[0]) + abs(pos[ga][1] + a[1] - pos[gb][1] - b[1])
        return max(top, width) + 0.5 * top + wl_w * wl + pen, pos, top, wl

    order = sorted(range(n), key=lambda g: -len(mods[g].cells))
    v = [0] * n
    for k, g in enumerate(frame):
        v[g] = k % 2
    cur = cost(order, v)[0]
    best = (cur, list(order), list(v))
    t0 = time.time()
    T0, T1 = 3.0, 0.05
    it = 0
    while time.time() - t0 < seconds:
        T = T0 * (T1 / T0) ** ((time.time() - t0) / seconds)
        for _ in range(20):
            it += 1
            i, j = rnd.randrange(n), rnd.randrange(n)
            new, nv = list(order), v
            r = rnd.random()
            if r < 0.25:
                nv = list(v)
                nv[i] ^= 1
            elif i == j:
                continue
            elif r < 0.65:
                new[i], new[j] = new[j], new[i]
            else:
                new.insert(j, new.pop(i))
            c = cost(new, nv)[0]
            if c <= cur or rnd.random() < math.exp((cur - c) / T):
                order, v, cur = new, nv, c
                if cur < best[0]:
                    best = (cur, list(order), list(v))
    c, pos, top, wl = cost(best[1], best[2])
    if pos is None:
        return None, None, 0, 0
    if log:
        log('frame width %d: height %d, belt length %d, %d on the frame, %d orderings' % (width, top, wl, len(frame), it))
    return [var[g][best[2][g]][0] for g in range(n)], [pos[g] for g in range(n)], top, wl
