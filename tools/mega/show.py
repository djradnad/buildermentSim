"""Text picture of a blueprint rows file: machines as letters (upper = anchor), arms a/A by direction digits,
belts as arrows, entrances n w s e, exits N W S E.   python tools/mega/show.py data/bp/qzq10z.json [x0 x1]"""
import json, sys
sys.path.insert(0, '.'); sys.path.insert(0, 'tools/mega')
import reroute_core as rc
from bmsolver.pieces import KIND_OF_ID

def show(rows, xa=None, xb=None):
    g = {}
    k = 0
    for r in rows:
        if r[0] in KIND_OF_ID:
            g[(r[1], r[2])] = {'B': '^<v>', 'I': 'nwse', 'O': 'NWSE', 'S': '0123'}[KIND_OF_ID[r[0]]][r[3]]
        elif r[0] == 3012:
            g[(r[1], r[2])] = 'urdl'[0:0] or {0: '↑', 1: '←', 2: '↓', 3: '→'}[r[3]]
        else:
            ch = chr(ord('a') + k % 26); k += 1
            for c in rc.footprint(r):
                g[c] = ch
            g[(r[1], r[2])] = ch.upper()
    xs = [p[0] for p in g]; ys = [p[1] for p in g]
    x0, x1 = min(xs), max(xs)
    if xa is not None: x0, x1 = x0 + int(xa), x0 + int(xb)
    for y in range(min(ys), max(ys) + 1):
        print(''.join(g.get((x, y), '.') for x in range(x0, x1 + 1)))

if __name__ == '__main__':
    rows = json.load(open(sys.argv[1]))['rows']
    show(rows, *sys.argv[2:4])
    import collections
    print(collections.Counter((r[0], r[3], r[4], r[5]) for r in rows if r[0] not in KIND_OF_ID).most_common(8))
