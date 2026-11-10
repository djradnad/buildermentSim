"""Shrink a routed layout with big moves: take a module that touches the edge of the box on its longer side, lift
it out, and drop it into the free spot inside where its belts are shortest (channel kept); re-route everything;
keep the move when the layout still routes. Repeats until no edge module can be moved in.

    python tools/mega/relocate_core.py core.json out.json [seconds] [channel]
"""
import sys
import time

import numpy as np
from scipy.signal import fftconvolve

sys.path.insert(0, '.')
sys.path.insert(0, 'tools/mega')
import compact_core as cc


def main(path, out, seconds=900, channel=1):
    seconds, ch = float(seconds), int(channel)
    st = cc.State(path)
    n = st.n
    ports = [[] for _ in range(n)]
    for i, (s, d, gs, e, de, ge) in enumerate(st.nets):
        ports[gs].append((s, i, ge, e))
        ports[ge].append((e, i, gs, s))
    t0 = time.time()
    best = st.route(st.off)
    assert best is not None
    tabu = {}
    x0, y0, x1, y1 = st.box()
    print('start: box %dx%d, %d route pieces' % (x1 - x0 + 1, y1 - y0 + 1, best['pieces']), flush=True)
    step_no = 0
    while time.time() - t0 < seconds:
        step_no += 1
        x0, y0, x1, y1 = st.box()
        W, H = x1 - x0 + 1, y1 - y0 + 1
        # groups on the edges of the longer side (both edges), smallest first: cheapest to move
        ext = []
        for g in range(n):
            xs = [c[0] + st.off[g][0] for c in st.cells[g]]
            ys = [c[1] + st.off[g][1] for c in st.cells[g]]
            if W >= H and (min(xs) == x0 or max(xs) == x1) or W < H and (min(ys) == y0 or max(ys) == y1):
                ext.append(g)
        ext = [g for g in ext if tabu.get(g, 0) < step_no]
        if not ext:
            print('no edge module left to move')
            break
        # all modules on one edge have to move for the box to shrink; take the edge with the least tiles
        def edge_of(g):
            xs = [c[0] + st.off[g][0] for c in st.cells[g]]
            ys = [c[1] + st.off[g][1] for c in st.cells[g]]
            return (min(xs) == x0) if W >= H else (min(ys) == y0)
        lo = [g for g in ext if edge_of(g)]
        hi = [g for g in ext if not edge_of(g)]
        side = min([s for s in (lo, hi) if s], key=lambda s: sum(len(st.cells[g]) for g in s))
        off = list(st.off)
        moved = []
        ok_all = True
        for g in sorted(side, key=lambda g: -len(st.cells[g])):
            occ_d = {}
            for h in range(n):
                if h == g:
                    continue
                for c in st.cells[h]:
                    occ_d[(c[0] + off[h][0], c[1] + off[h][1])] = 1
                for c, i, hh, c2 in ports[h]:
                    occ_d[(c[0] + off[h][0], c[1] + off[h][1])] = 1
            # the target window: the current box shrunk by one on the side we are clearing
            bx0, by0, bx1, by1 = x0, y0, x1, y1
            if W >= H:
                if side is lo: bx0 += 1
                else: bx1 -= 1
            else:
                if side is lo: by0 += 1
                else: by1 -= 1
            gw, gh = bx1 - bx0 + 1, by1 - by0 + 1
            grid = np.zeros((gh, gw), dtype=np.float32)
            for (x, y) in occ_d:
                if bx0 <= x <= bx1 and by0 <= y <= by1:
                    grid[y - by0, x - bx0] = 1
            cells = st.cells[g] + [c for c, i, hh, c2 in ports[g]]
            xs = [c[0] for c in cells]
            ys = [c[1] for c in cells]
            cx0, cy0 = min(xs), min(ys)
            w, h = max(xs) - cx0 + 1, max(ys) - cy0 + 1
            if w + 2 * ch > gw or h + 2 * ch > gh:
                ok_all = False
                break
            mask = np.zeros((h + 2 * ch, w + 2 * ch), dtype=np.float32)
            for c in cells:
                mask[c[1] - cy0:c[1] - cy0 + 2 * ch + 1, c[0] - cx0:c[0] - cx0 + 2 * ch + 1] = 1
            # the channel may hang over the window edge: pad the grid with free tiles
            pad = np.pad(grid, ch)
            ov = fftconvolve(pad, mask[::-1, ::-1], mode='valid')
            ok = ov < 0.5
            Hh, Ww = ok.shape
            X = np.arange(Ww)[None, :] + bx0
            Y = np.arange(Hh)[:, None] + by0
            cost = np.zeros((Hh, Ww), dtype=np.float32)
            for c, i, hh, c2 in ports[g]:
                if hh == g:
                    continue
                px, py = c2[0] + off[hh][0], c2[1] + off[hh][1]
                cost += np.abs(X + (c[0] - cx0) - px) + np.abs(Y + (c[1] - cy0) - py)
            cost[~ok] = 1e9
            j = int(np.argmin(cost))
            yy, xx = divmod(j, Ww)
            if cost[yy, xx] >= 1e9:
                ok_all = False
                break
            off[g] = (xx + bx0 - cx0, yy + by0 - cy0)
            moved.append(g)
        if not ok_all:
            for g in side:
                tabu[g] = step_no + 6
            continue
        res = st.route(off, timeout=40)
        if res is None:
            for g in side:
                tabu[g] = step_no + 6
            print('%.0f s: moving %d edge modules in did not route' % (time.time() - t0, len(side)), flush=True)
            continue
        st.off, best = off, res
        x0, y0, x1, y1 = st.box()
        print('%.0f s: box %dx%d after moving %d edge modules in, %d route pieces' % (time.time() - t0, x1 - x0 + 1, y1 - y0 + 1, len(moved), best['pieces']), flush=True)
        st.save(best, out)
    x0, y0, x1, y1 = st.box()
    print('done: box %dx%d, %d route pieces' % (x1 - x0 + 1, y1 - y0 + 1, best['pieces']))


if __name__ == '__main__':
    main(*sys.argv[1:5])
