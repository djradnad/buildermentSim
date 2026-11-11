"""Constructive placement from scratch: take the module groups and the belts between them from a finished layout
(the real netlist), forget where everything was, and place the groups again one at a time, each where its belts
get shortest and the box grows least, with a small channel around it. Then every belt is routed at once.

    python tools/mega/place_core.py core.json out.json [channel] [box_weight]
"""
import json
import sys
import time

import numpy as np
from scipy.signal import fftconvolve

sys.path.insert(0, '.')
sys.path.insert(0, 'tools/mega')
import compact_core as cc
from bmsolver.pieces import step


def place(st, channel=1, box_w=2.0, size=420, log=print):
    n = st.n
    ports = [[] for _ in range(n)]                          # (cell, net, other group)
    for i, (s, d, gs, e, de, ge) in enumerate(st.nets):
        ports[gs].append((s, i, ge))
        ports[ge].append((e, i, gs))
    link = [dict() for _ in range(n)]
    for i, (s, d, gs, e, de, ge) in enumerate(st.nets):
        if gs != ge:
            link[gs][ge] = link[gs].get(ge, 0) + 1
            link[ge][gs] = link[ge].get(gs, 0) + 1
    occ = np.zeros((size, size), dtype=np.float32)          # [y, x], everything placed + its port tiles
    mid = size // 2
    off = [None] * n
    where = {}                                              # net -> placed port position (first end placed)
    order = []
    left = set(range(n))
    first = max(left, key=lambda g: len(st.cells[g]))
    box = None
    while left:
        if not order:
            g = first
        else:
            g = max(left, key=lambda g: (sum(v for h, v in link[g].items() if off[h] is not None), len(st.cells[g])))
        left.discard(g)
        order.append(g)
        cells = st.cells[g] + [c for c, i, h in ports[g]]
        xs = [c[0] for c in cells]
        ys = [c[1] for c in cells]
        x0, y0 = min(xs), min(ys)
        w, h = max(xs) - x0 + 1, max(ys) - y0 + 1
        ch = channel
        mask = np.zeros((h + 2 * ch, w + 2 * ch), dtype=np.float32)
        for c in cells:
            mask[c[1] - y0:c[1] - y0 + 2 * ch + 1, c[0] - x0:c[0] - x0 + 2 * ch + 1] = 1
        if box is None:
            ox, oy = mid - w // 2 - x0, mid - h // 2 - y0
        else:
            # overlap[y, x] = cells of the (widened) mask over occupied tiles when its corner is at (x, y)
            ov = fftconvolve(occ, mask[::-1, ::-1], mode='valid')
            ok = ov < 0.5
            H, W = ok.shape
            X = np.arange(W)[None, :] + ch                  # x of the group's own corner (x0) on the grid
            Y = np.arange(H)[:, None] + ch
            cost = np.zeros((H, W), dtype=np.float32)
            for c, i, hgrp in ports[g]:
                if i in where:
                    px, py = where[i]
                    cost += np.abs(X + (c[0] - x0) - px) + np.abs(Y + (c[1] - y0) - py)
            bx0, by0, bx1, by1 = box
            nw = np.maximum(bx1, X + w - 1) - np.minimum(bx0, X) + 1
            nh = np.maximum(by1, Y + h - 1) - np.minimum(by0, Y) + 1
            grow = np.maximum(nw, nh) * 2 + (nw + nh)       # most square first, then small
            cost = cost + box_w * (grow - grow.min())
            cost[~ok] = 1e9
            j = int(np.argmin(cost))
            yy, xx = divmod(j, W)
            assert cost[yy, xx] < 1e9, 'grid too small'
            ox, oy = xx + ch - x0, yy + ch - y0
        off[g] = (ox, oy)
        for c in cells:
            occ[c[1] + oy, c[0] + ox] = 1
        for c, i, hgrp in ports[g]:
            where.setdefault(i, (c[0] + ox, c[1] + oy))
        px = [c[0] + ox for c in st.cells[g]]
        py = [c[1] + oy for c in st.cells[g]]
        b = (min(px), min(py), max(px), max(py))
        box = b if box is None else (min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3]))
    return off, box


def main(path, out, channel=1, box_w=2.0):
    st = cc.State(path)
    t0 = time.time()
    off, box = place(st, int(channel), float(box_w))
    W, H = box[2] - box[0] + 1, box[3] - box[1] + 1
    print('placed %d groups in %.1f s: box %dx%d (channel %s, box weight %s)' % (st.n, time.time() - t0, W, H, channel, box_w), flush=True)
    t1 = time.time()
    res = st.route(off, timeout=300)
    if res is None:
        print('NOT routed (%.1f s)' % (time.time() - t1))
        return
    st.off = off
    print('routed and traced in %.1f s: %d route pieces (the original 134x120 layout routes with about 3360)' % (time.time() - t1, res['pieces']))
    st.save(res, out)


if __name__ == '__main__':
    main(*sys.argv[1:5])
