"""Overlap-free placement of modules as rectangles: skyline (bottom-left) packing of an ordering, with simulated
annealing over the ordering and over which modules are turned (transposed).  Every side of a module gets a
channel as wide as its belts need (ports on that side), so the router has room where the belts gather.
Cost = bounding square side, then belt length between connected ports."""
import math
import random
import time


def geometry(lay, ch, per_port, cap, extra=()):
    """per module: bounding box of its cells and the channel on each side (left, top, right, bottom)"""
    out = []
    ports = [[] for _ in range(lay.n)]
    for (s, d, gs, e, de, ge) in lay.nets:
        ports[gs].append(s)
        ports[ge].append(e)
    for g, t in extra:                                  # belts that come from outside the core
        ports[g].append(t)
    for g, cells in enumerate(lay.cells):
        xs = [c[0] for c in cells]
        ys = [c[1] for c in cells]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        cnt = [0, 0, 0, 0]
        for (x, y) in ports[g]:
            if x < x0:
                cnt[0] += 1
            elif x > x1:
                cnt[2] += 1
            elif y < y0:
                cnt[1] += 1
            elif y > y1:
                cnt[3] += 1
        mar = [min(cap, int(math.ceil(c * per_port))) for c in cnt]
        mar[2] += ch                                    # the channel itself only on the right and bottom:
        mar[3] += ch                                    # the neighbour on the other sides brings its own
        out.append((x0, y0, x1, y1, mar))
    return out


def skyline(order, W, H, width):
    sky = [0] * width
    pos = {}
    top = 0
    for g in order:
        w, h = W[g], H[g]
        best = None
        x = 0
        while x + w <= width:
            y = max(sky[x:x + w])
            if best is None or y < best[0]:
                best = (y, x)
            nx = x + 1
            while nx + w <= width and sky[nx - 1] == sky[nx] and sky[nx + w - 1] == sky[nx + w - 2]:
                nx += 1
            x = nx
        if best is None:
            return None, 10 ** 9
        y, x = best
        for i in range(x, x + w):
            sky[i] = y + h
        pos[g] = (x, y)
        top = max(top, y + h)
    return pos, top


def place(lay, ch=1, per_port=0.5, cap=4, width=100, seconds=60, seed=1, wl_w=0.02, turn=True, log=None, extra=()):
    """-> (offsets, turned flags, height, belt length); a turned module must be transposed before use"""
    rnd = random.Random(seed)
    G = geometry(lay, ch, per_port, cap, extra)
    n = lay.n
    nets = [(s, gs, e, ge) for (s, d, gs, e, de, ge) in lay.nets]

    def dims(g, t):
        x0, y0, x1, y1, m = G[g]
        w, h = x1 - x0 + 1 + m[0] + m[2], y1 - y0 + 1 + m[1] + m[3]
        return (h, w) if t else (w, h)

    def origin(g, t):
        """offset from the rectangle's corner to the module's own (0, 0)"""
        x0, y0, x1, y1, m = G[g]
        return (m[1] - y0, m[0] - x0) if t else (m[0] - x0, m[1] - y0)

    def cost(order, flip):
        W = [dims(g, flip[g])[0] for g in range(n)]
        H = [dims(g, flip[g])[1] for g in range(n)]
        pos, top = skyline(order, W, H, width)
        if pos is None:
            return 10 ** 9, None, 0, 0
        off = [None] * n
        for g in range(n):
            o = origin(g, flip[g])
            off[g] = (pos[g][0] + o[0], pos[g][1] + o[1])
        wl = 0
        for s, gs, e, ge in nets:
            a = (s[1], s[0]) if flip[gs] else s
            b = (e[1], e[0]) if flip[ge] else e
            wl += abs(off[gs][0] + a[0] - off[ge][0] - b[0]) + abs(off[gs][1] + a[1] - off[ge][1] - b[1])
        used = max(pos[g][0] + W[g] for g in range(n))
        return max(top, used) + 0.5 * top + wl_w * wl, off, top, wl

    order = sorted(range(n), key=lambda g: -dims(g, 0)[0] * dims(g, 0)[1])
    flip = [0] * n
    cur = cost(order, flip)[0]
    best = (cur, list(order), list(flip))
    t0 = time.time()
    T0, T1 = 3.0, 0.05
    it = 0
    while time.time() - t0 < seconds:
        T = T0 * (T1 / T0) ** ((time.time() - t0) / seconds)
        for _ in range(50):
            it += 1
            i, j = rnd.randrange(n), rnd.randrange(n)
            new, nf = list(order), flip
            r = rnd.random()
            if turn and r < 0.2:
                nf = list(flip)
                nf[i] ^= 1
            elif i == j:
                continue
            elif r < 0.6:
                new[i], new[j] = new[j], new[i]
            else:
                new.insert(j, new.pop(i))
            c = cost(new, nf)[0]
            if c <= cur or rnd.random() < math.exp((cur - c) / T):
                order, flip, cur = new, nf, c
                if cur < best[0]:
                    best = (cur, list(order), list(flip))
    c, off, top, wl = cost(best[1], best[2])
    if log:
        log('pack width %d: height %d, belt length %d, %d turned, %d orderings tried' % (width, top, wl, sum(best[2]), it))
    return off, best[2], top, wl
