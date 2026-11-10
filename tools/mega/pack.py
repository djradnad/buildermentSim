"""Overlap-free placement of modules as rectangles: skyline (bottom-left) packing of an ordering, with simulated
annealing over the ordering.  Cost = bounding square side, then belt length between connected ports."""
import math
import random
import time


def rects(lay, ch):
    """per module: (x0, y0, w, h) of its cells and port tiles, plus a channel of ch on the right and bottom"""
    ext = [set(c) for c in lay.cells]
    for (s, d, gs, e, de, ge) in lay.nets:
        ext[gs].add(s)
        ext[ge].add(e)
    out = []
    for cells in ext:
        xs = [c[0] for c in cells]
        ys = [c[1] for c in cells]
        out.append((min(xs), min(ys), max(xs) - min(xs) + 1 + ch, max(ys) - min(ys) + 1 + ch))
    return out


def skyline(order, R, width):
    """-> positions {module: (x, y)} of each rectangle's top-left, and the height used"""
    sky = [0] * width
    pos = {}
    top = 0
    for g in order:
        w, h = R[g][2], R[g][3]
        best = None
        x = 0
        while x + w <= width:
            y = max(sky[x:x + w])
            if best is None or y < best[0]:
                best = (y, x)
            # jump to the next place where the skyline changes
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


def place(lay, ch=2, width=100, seconds=60, seed=1, wl_w=0.02, log=None):
    rnd = random.Random(seed)
    R = rects(lay, ch)
    n = lay.n

    def cost(order):
        pos, top = skyline(order, R, width)
        if pos is None:
            return 10 ** 9, None, 0, 0
        wl = 0
        for (s, d, gs, e, de, ge) in lay.nets:
            a, b = pos[gs], pos[ge]
            wl += abs(a[0] - R[gs][0] + s[0] - (b[0] - R[ge][0] + e[0])) + abs(a[1] - R[gs][1] + s[1] - (b[1] - R[ge][1] + e[1]))
        used = max(pos[g][0] + R[g][2] for g in order)
        return max(top, used) + 0.2 * top + wl_w * wl, pos, top, wl

    order = sorted(range(n), key=lambda g: -R[g][2] * R[g][3])
    cur, pos, top, wl = cost(order)
    best = (cur, list(order))
    t0 = time.time()
    T0, T1 = 3.0, 0.05
    it = 0
    while True:
        el = time.time() - t0
        if el > seconds:
            break
        T = T0 * (T1 / T0) ** (el / seconds)
        for _ in range(50):
            it += 1
            i, j = rnd.randrange(n), rnd.randrange(n)
            if i == j:
                continue
            new = list(order)
            if rnd.random() < 0.5:
                new[i], new[j] = new[j], new[i]
            else:
                new.insert(j, new.pop(i))
            c = cost(new)[0]
            if c <= cur or rnd.random() < math.exp((cur - c) / T):
                order, cur = new, c
                if cur < best[0]:
                    best = (cur, list(order))
    c, pos, top, wl = cost(best[1])
    if log:
        log('pack width %d: %dx%d, belt length %d, %d orderings tried' % (width, max(pos[g][0] + R[g][2] for g in pos) - ch, top - ch, wl, it))
    return [(pos[g][0] - R[g][0], pos[g][1] - R[g][1]) for g in range(n)], top - ch, wl
