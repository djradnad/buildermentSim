"""Independent check of a repeating channel pattern: follow every belt piece by piece and count how many
separate belts go round the cylinder eastward. Rows use the lanes.py letters (> < ^ v belts, e/E n/N s/S w/W
underground entrance/exit by direction)."""
import sys

D = {'^': 0, '<': 1, 'v': 2, '>': 3}
ENT = {'n': 0, 'w': 1, 's': 2, 'e': 3}
EXI = {'N': 0, 'W': 1, 'S': 2, 'E': 3}
DIRS = {0: (0, -1), 1: (-1, 0), 2: (0, 1), 3: (1, 0)}


def trace(rows, wrap=True, reach=7):
    h, P = len(rows), len(rows[0])
    g = {(x, y): rows[y][x] for y in range(h) for x in range(P) if rows[y][x] != '.'}
    nxt, wind = {}, {}
    for (x, y), ch in g.items():
        if ch in D or ch in EXI:
            d = D.get(ch, EXI.get(ch))
            qx, qy = x + DIRS[d][0], y + DIRS[d][1]
            w = (qx // P) if wrap else 0
            q = (qx % P, qy)
            assert 0 <= qy < h and q in g, ('pushes into nothing', x, y)
            t = g[q]
            ok = (t in D and D[t] != (d + 2) % 4) or (t in ENT and ENT[t] == d)
            assert ok, ('not accepted', x, y, q)
            nxt[x, y], wind[x, y] = q, w
        else:
            d = ENT[ch]
            for n in range(1, reach + 1):
                qx, qy = x + n * DIRS[d][0], y + n * DIRS[d][1]
                q = (qx % P, qy)
                assert 0 <= qy < h
                t = g.get(q)
                assert not (t in ENT and ENT[t] == d), ('entrance inside a tunnel', x, y)
                if t in EXI and EXI[t] == d:
                    nxt[x, y], wind[x, y] = q, qx // P
                    break
            else:
                raise AssertionError(('no exit', x, y))
    assert len(set(nxt.values())) == len(nxt) == len(g), 'a piece is fed twice or not at all'
    seen, belts = set(), 0
    for p in g:
        if p in seen:
            continue
        w, q = 0, p
        while q not in seen:
            seen.add(q)
            w += wind[q]
            q = nxt[q]
        belts += w
    return belts


if __name__ == '__main__':
    rows = [l.strip() for l in open(sys.argv[1]) if l.strip()]
    print(len(rows), 'rows, period', len(rows[0]), '->', trace(rows), 'belts eastward')
