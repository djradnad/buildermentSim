"""Placement search by simulated annealing on the real netlist of a finished layout.

Modules (rigid groups) move freely; overlap is allowed during the search but priced, and the price rises until
none is left. Cost = belt length (Manhattan, per run) + box (most square, then small) + overlap. Every module
keeps a channel of free tiles around it. The fast router is the judge at the end.

    python tools/mega/anneal_core.py core.json out.json [seconds] [channel] [box_weight] [seed]
"""
import math
import random
import sys
import time

import numpy as np

sys.path.insert(0, '.')
sys.path.insert(0, 'tools/mega')
import compact_core as cc

SIZE = 400


class Anneal:
    def __init__(self, st, ch=1, box_w=30.0, seed=1):
        self.st, self.ch, self.box_w = st, ch, box_w
        self.rnd = random.Random(seed)
        n = st.n
        self.ports = [[] for _ in range(n)]
        for i, (s, d, gs, e, de, ge) in enumerate(st.nets):
            self.ports[gs].append((s, i))
            self.ports[ge].append((e, i))
        self.netpos = [[None, None] for _ in st.nets]       # absolute positions of both ends
        x0, y0, x1, y1 = st.box()
        self.shift = (SIZE // 2 - (x0 + x1) // 2, SIZE // 2 - (y0 + y1) // 2)
        self.off = [(o[0] + self.shift[0], o[1] + self.shift[1]) for o in st.off]
        self.idx, self.bb = [], []
        for g in range(n):
            cells = set(st.cells[g]) | {c for c, i in self.ports[g]}
            body = set()
            c1 = ch
            for (x, y) in cells:
                for dx in range(0, c1 + 1):                 # channel on two sides only: neighbours add theirs
                    for dy in range(0, c1 + 1):
                        body.add((x + dx, y + dy))
            a = np.array(sorted(body), dtype=np.int32)
            self.idx.append((a[:, 1].copy(), a[:, 0].copy()))
            xs = [c[0] for c in st.cells[g]]
            ys = [c[1] for c in st.cells[g]]
            self.bb.append((min(xs), min(ys), max(xs), max(ys)))
        self.count = np.zeros((SIZE, SIZE), dtype=np.int16)
        for g in range(n):
            ys, xs = self.idx[g]
            self.count[ys + self.off[g][1], xs + self.off[g][0]] += 1
        self.overlap = int(np.maximum(self.count - 1, 0).sum())
        for i, (s, d, gs, e, de, ge) in enumerate(st.nets):
            self.netpos[i] = [self.abs(s, gs), self.abs(e, ge)]
        self.wl = sum(self.net_len(i) for i in range(len(st.nets)))
        self.gnets = [sorted({i for c, i in self.ports[g]}) for g in range(n)]

    def abs(self, c, g, off=None):
        o = off or self.off[g]
        return (c[0] + o[0], c[1] + o[1])

    def net_len(self, i):
        (ax, ay), (bx, by) = self.netpos[i]
        return abs(ax - bx) + abs(ay - by)

    def box(self):
        x0 = min(b[0] + o[0] for b, o in zip(self.bb, self.off))
        y0 = min(b[1] + o[1] for b, o in zip(self.bb, self.off))
        x1 = max(b[2] + o[0] for b, o in zip(self.bb, self.off))
        y1 = max(b[3] + o[1] for b, o in zip(self.bb, self.off))
        return x0, y0, x1, y1

    def box_cost(self):
        x0, y0, x1, y1 = self.box()
        w, h = x1 - x0 + 1, y1 - y0 + 1
        return 2 * max(w, h) + w + h

    def apply(self, g, new, lam):
        """move module g (always); -> cost change, or None when it would leave the grid (nothing moved)"""
        st = self.st
        ys, xs = self.idx[g]
        old = self.off[g]
        oy, ox = ys + old[1], xs + old[0]
        ny, nx = ys + new[1], xs + new[0]
        if nx.min() < 2 or ny.min() < 2 or nx.max() >= SIZE - 2 or ny.max() >= SIZE - 2:
            return None
        d_ov = -int((self.count[oy, ox] >= 2).sum())
        self.count[oy, ox] -= 1
        d_ov += int((self.count[ny, nx] >= 1).sum())
        self.count[ny, nx] += 1
        d_wl = 0
        for i in self.gnets[g]:
            s, d, gs, e, de, ge = st.nets[i]
            before = self.net_len(i)
            if gs == g:
                self.netpos[i][0] = self.abs(s, g, new)
            if ge == g:
                self.netpos[i][1] = self.abs(e, g, new)
            d_wl += self.net_len(i) - before
        b0 = self.box_cost()
        self.off[g] = new
        d_box = self.box_cost() - b0
        self.overlap += d_ov
        self.wl += d_wl
        return d_wl + lam * d_ov + self.box_w * d_box

    def try_move(self, g, new, T, lam):
        old = self.off[g]
        delta = self.apply(g, new, lam)
        if delta is None:
            return False
        if delta <= 0 or self.rnd.random() < math.exp(-delta / T):
            return True
        self.apply(g, old, lam)
        return False

    def try_swap(self, g, h, T, lam):
        """exchange the places of two modules (centres swapped)"""
        (ax0, ay0, ax1, ay1), (bx0, by0, bx1, by1) = self.bb[g], self.bb[h]
        og, oh = self.off[g], self.off[h]
        cg = (og[0] + (ax0 + ax1) // 2, og[1] + (ay0 + ay1) // 2)
        chh = (oh[0] + (bx0 + bx1) // 2, oh[1] + (by0 + by1) // 2)
        ng = (chh[0] - (ax0 + ax1) // 2, chh[1] - (ay0 + ay1) // 2)
        nh = (cg[0] - (bx0 + bx1) // 2, cg[1] - (by0 + by1) // 2)
        d1 = self.apply(g, ng, lam)
        if d1 is None:
            return False
        d2 = self.apply(h, nh, lam)
        if d2 is None:
            self.apply(g, og, lam)
            return False
        delta = d1 + d2
        if delta <= 0 or self.rnd.random() < math.exp(-delta / T):
            return True
        self.apply(h, oh, lam)
        self.apply(g, og, lam)
        return False

    def run(self, seconds, log=print):
        t0 = time.time()
        n = self.st.n
        it = acc = 0
        T0, T1 = 6.0, 0.3
        while True:
            el = time.time() - t0
            if el > seconds:
                break
            f = el / seconds
            T = T0 * (T1 / T0) ** f
            lam = 2.0 + 400.0 * f ** 3                      # overlap gets expensive toward the end
            reach = max(1, int(12 * (1 - f) + 1))
            for _ in range(500):
                g = self.rnd.randrange(n)
                o = self.off[g]
                if self.rnd.random() < 0.1:
                    h = self.rnd.randrange(n)
                    if h != g:
                        it += 1
                        acc += self.try_swap(g, h, T, lam)
                    continue
                if self.rnd.random() < 0.15 and self.gnets[g]:     # jump toward the middle of its partners
                    tx = ty = k = 0
                    for i in self.gnets[g]:
                        s, d, gs, e, de, ge = self.st.nets[i]
                        a, b = self.netpos[i]
                        mine, other = (a, b) if gs == g else (b, a)
                        tx += other[0] - mine[0]
                        ty += other[1] - mine[1]
                        k += 1
                    new = (o[0] + round(tx / k) + self.rnd.randint(-2, 2), o[1] + round(ty / k) + self.rnd.randint(-2, 2))
                else:
                    new = (o[0] + self.rnd.randint(-reach, reach), o[1] + self.rnd.randint(-reach, reach))
                if new != o:
                    it += 1
                    acc += self.try_move(g, new, T, lam)
            if int(el) // 20 != int(time.time() - t0) // 20:
                x0, y0, x1, y1 = self.box()
                log('%.0f s: T %.2f, box %dx%d, belt length %d, overlap %d, %d moves (%d kept)' % (
                    el, T, x1 - x0 + 1, y1 - y0 + 1, self.wl, self.overlap, it, acc))
        # squeeze out the last overlaps: only moves that do not add overlap
        t1 = time.time()
        while self.overlap > 0 and time.time() - t1 < 120:
            for _ in range(500):
                g = self.rnd.randrange(n)
                o = self.off[g]
                r = 3 if len(self.st.cells[g]) > 3 else 8
                self.try_move(g, (o[0] + self.rnd.randint(-r, r), o[1] + self.rnd.randint(-r, r)), 0.3, 500.0)
        return [(o[0] - self.shift[0], o[1] - self.shift[1]) for o in self.off]


def main(path, out, seconds=300, ch=1, box_w=30.0, seed=1):
    st = cc.State(path)
    an = Anneal(st, int(ch), float(box_w), int(seed))
    x0, y0, x1, y1 = an.box()
    print('start: box %dx%d, belt length %d, overlap %d (channels of the old layout count as overlap)' % (
        x1 - x0 + 1, y1 - y0 + 1, an.wl, an.overlap), flush=True)
    off = an.run(float(seconds), log=lambda s: print(s, flush=True))
    x0, y0, x1, y1 = an.box()
    print('annealed: box %dx%d, belt length %d, overlap %d' % (x1 - x0 + 1, y1 - y0 + 1, an.wl, an.overlap), flush=True)
    t0 = time.time()
    import json
    json.dump(off, open(out + '.off', 'w'))
    from bmsolver import negotiate
    orig = negotiate.route

    def wrap(*a, **k):
        k['log'] = lambda s: print('  ' + s, flush=True)
        r = orig(*a, **k)
        if not r[0]:
            print('  ' + str(r[1])[:300], flush=True)
        return r
    negotiate.route = wrap
    res = st.route(off, timeout=240)
    if res is None:
        print('NOT routed (%.0f s)' % (time.time() - t0))
        return
    st.off = off
    print('routed and traced in %.1f s: %d route pieces' % (time.time() - t0, res['pieces']))
    st.save(res, out)


if __name__ == '__main__':
    main(*sys.argv[1:7])
