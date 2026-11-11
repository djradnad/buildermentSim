"""Staircase strip generator in john's style (vc04aj / mbl5k9): every stage of 6-wide groups takes one raw lane
per group, splits it over 6 furnaces (+6 workshops), merges the stage's products in a collector row into full belts
that dive under the stages below. Stages shift one column left, so raw lanes and products pass on underground hops.
Stage k rows (y = 6k + r): r0 hop-in, r1 splitters, r2 furnaces, r3 workshops, r4 collector, r5 hop-out.
lanes: {abs x: product} at the top (raw lanes enter going south at y=-1)."""
import json
# product: group width, raw item, building row 2, building row 3 (None = plain belts), output/min per full lane
P = {'wire':  (6, 'Copper_Ore', (3006, 2002), (3005, 2003), 320, 'Copper_Wire'),
     'hs':    (6, 'Copper_Ore', (3006, 2002), (3005, 2013), 96, 'Heat_Sink'),
     'plate': (6, 'Iron_Ore', (3006, 2004), (3005, 2014), 240, 'Iron_Plating'),
     'cu':    (6, 'Copper_Ore', (3006, 2002), None, 480, 'Copper_Ingot'),
     'fe':    (6, 'Iron_Ore', (3006, 2004), None, 480, 'Iron_Ingot'),
     'glass': (4, 'Stone', (3005, 2006), (3006, 2008), 120, 'Glass'),
     'sand':  (4, 'Stone', (3005, 2006), None, 480, 'Sand'),
     'tung':  (4, 'Wolframite', (3006, 2009), None, 96, 'Tungsten_Ore')}
RAW = {k: v[1] for k, v in P.items()}
OUT = {k: v[4] for k, v in P.items()}
PROD = {k: v[5] for k, v in P.items()}
N, W, S, E = 0, 1, 2, 3
BT, MT = 0, 3


def B(x, y, o): return [3000, x, y, o, BT, -1, 0]
def SP(x, y, o): return [3001, x, y, o, BT, -1, 0]
def UI(x, y): return [3002, x, y, S, BT, -1, 0]
def UO(x, y): return [3003, x, y, S, BT, -1, 0]


def build(lanes, ox0=0, K=None, tail=2):
    G = P[next(iter(lanes.values()))][0]
    assert all(P[p][0] == G for p in lanes.values())
    C2, C3 = G // 2 - 1, G // 2                         # consumed column, split-partner column
    K = K or G - 1
    rows = []
    flow = {x: ('raw', p) for x, p in lanes.items()}       # what arrives at r0 of the current stage, by column
    tunnel = {}                                           # products that dove at the previous stage's r5
    for x in lanes:
        rows += [B(x, -2, S), B(x, -1, S)]
        assert (x - ox0) % G != C3, ('lane on a split-partner column', x)
    outs = []
    for k in range(K):
        y0, ox = 6 * k, ox0 - k
        last = k == K - 1
        nflow, ntunnel = {}, {}
        groups = {}                                       # group col0 -> product
        for x, (kind, p) in flow.items():
            c = (x - ox) % G
            if c == C2:
                assert kind == 'raw', ('product hits a splitter column', x, k)
                groups[x - C2] = p
            else:
                assert c != C3, ('flow on a split-partner column', x, k)
                rows += [UI(x, y0), UO(x, y0 + 5)]       # hop under this stage
                nflow[x] = (kind, p)
        for x, p in tunnel.items():
            assert (x - ox) % G == C3, ('tunnel exit not on the partner column', x, k)
            rows.append(UO(x, y0 + 5)); nflow[x] = ('prod', p)
        for g0, p in groups.items():
            if G == 6:
                rows += [SP(g0 + 2, y0, S), B(g0 + 3, y0, S),
                         B(g0, y0 + 1, S), SP(g0 + 1, y0 + 1, W), SP(g0 + 2, y0 + 1, S), SP(g0 + 3, y0 + 1, S),
                         SP(g0 + 4, y0 + 1, E), B(g0 + 5, y0 + 1, S)]
            else:
                rows += [SP(g0 + 1, y0, S), B(g0 + 2, y0, S),
                         B(g0, y0 + 1, S), SP(g0 + 1, y0 + 1, S), SP(g0 + 2, y0 + 1, S), B(g0 + 3, y0 + 1, S)]
            b2, b3 = P[p][2], P[p][3]
            if p == 'tung':                               # 1 lane = 1.33 furnaces: build only the middle two
                rows = [r for r in rows if not (r[0] == 3001 and r[2] == y0 + 1 and r[1] in (g0 + 1, g0 + 2))]
                rows = [r for r in rows if not (r[0] == 3000 and r[2] == y0 + 1 and r[1] in (g0, g0 + 3))]
                rows += [B(g0 + 1, y0 + 1, S), B(g0 + 2, y0 + 1, S)]
                for i in (1, 2):
                    rows += [[b2[0], g0 + i, y0 + 2, S, MT, b2[1], 0], B(g0 + i, y0 + 3, S)]
                continue
            for i in range(G):
                rows.append([b2[0], g0 + i, y0 + 2, S, MT, b2[1], 0])
                rows.append([b3[0], g0 + i, y0 + 3, S, MT, b3[1], 0] if b3 else B(g0 + i, y0 + 3, S))
        # collectors: runs of neighbouring groups with the same product, cut into spans of <= 480/min
        for g0 in sorted(groups):
            p = groups[g0]
            if groups.get(g0 - G) == p:
                continue
            run = []
            g = g0
            while groups.get(g) == p:
                run.append(g); g += G
            cols = [x for g in run for x in range(g, g + G)]
            per = OUT[p] / float(G)
            isc2 = [(x - ox) % G == C2 for x in cols]
            n = len(cols)
            best = {n: (0, [])}                           # fewest spans, each <= 480/min with a dive column
            for i in range(n - 1, -1, -1):
                cand = None
                for j in range(i + 1, n + 1):
                    if (j - i) * per > 480 + 1e-9:
                        break
                    if any(isc2[i:j]) and j in best:
                        c = (best[j][0] + 1, [cols[i:j]] + best[j][1])
                        if cand is None or c[0] < cand[0]:
                            cand = c
                if cand:
                    best[i] = cand
            fixed = best[0][1]
            for sp in fixed:
                c2 = [x for x in sp if (x - ox) % G == C2]
                o = min(c2, key=lambda x: abs(x - (sp[0] + sp[-1]) / 2))
                if p == 'tung':                           # no dead collector ends beside the empty columns
                    while sp[0] != o and (sp[0] - ox) % G in (0, G - 1): sp = sp[1:]
                    while sp[-1] != o and (sp[-1] - ox) % G in (0, G - 1): sp = sp[:-1]
                for x in sp:
                    rows.append(B(x, y0 + 4, E if x < o else W if x > o else S))
                if last:
                    rows.append(B(o, y0 + 5, S)); nflow[o] = ('prod', p)
                else:
                    rows.append(UI(o, y0 + 5)); ntunnel[o] = p
                outs.append((k, o, p, len(sp) * per))
        flow, tunnel = nflow, ntunnel
    assert not tunnel
    yb = 6 * K
    for x, (kind, p) in flow.items():
        assert kind == 'prod', ('raw lane left unused', x, p)
        rows += [B(x, yb + t, S) for t in range(tail)]
    return rows, {x: p for x, (kind, p) in flow.items()}, outs


if __name__ == '__main__':
    lanes = {x: 'wire' for x in range(0, 18) if x % 6 != 3}
    rows, prods, outs = build(lanes)
    json.dump({'meta': {}, 'rows': rows}, open('t_wire.json', 'w'))
    print(len(rows), prods, outs)
