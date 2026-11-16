"""Real layouts that show a capacity: belts funnel into a narrow gap and out again, surface ports only.

    python tools/routing/witness.py H h F L east west [secs]
H rows outside, h rows open over the L middle columns, F funnel columns each side; `east` belts go west->east,
`west` belts east->west. Prints the 2-char grid and the traced belt count per direction."""
import sys
sys.path.insert(0, __import__('os').path.dirname(__file__))
from region import Region, surface_only, codes, DIRS  # noqa: E402


def trace_any(rows, reach=7):
    """follow every belt from the piece nothing feeds to where it leaves the grid -> {(from edge, to edge): count}"""
    H, W = len(rows), len(rows[0]) // 2
    g = {(x, y): rows[y][2 * x:2 * x + 2] for y in range(H) for x in range(W) if rows[y][2 * x:2 * x + 2] != '..'}

    def nxt(p):
        k, d = g[p][0], int(g[p][1])
        if k == 'I':
            for n in range(1, reach + 1):
                q = (p[0] + n * DIRS[d][0], p[1] + n * DIRS[d][1])
                if g.get(q) == 'O%d' % d:
                    return q
                assert g.get(q) != 'I%d' % d, ('entrance inside tunnel', p)
            raise AssertionError(('no exit', p))
        q = (p[0] + DIRS[d][0], p[1] + DIRS[d][1])
        if not (0 <= q[0] < W and 0 <= q[1] < H):
            return 'NWSE'[d]
        assert q in g, ('pushes into nothing', p)
        kk, dd = g[q][0], int(g[q][1])
        assert (kk == 'B' and dd != (d + 2) % 4) or (kk == 'I' and dd == d), ('not accepted', p, q)
        return q
    succ = {p: nxt(p) for p in g}
    fed = {}
    for p, q in succ.items():
        if isinstance(q, tuple):
            assert q not in fed, ('fed twice', q)
            fed[q] = p
    out, used = {}, set()
    for p in g:
        if p in fed:
            continue
        k, d = g[p][0], int(g[p][1])
        assert k != 'O', ('exit without entrance', p)
        back = (p[0] - DIRS[d][0], p[1] - DIRS[d][1]) if k == 'I' else None
        src = 'W' if p[0] == 0 else 'E' if p[0] == W - 1 else 'N' if p[1] == 0 else 'S'
        q = p
        while isinstance(q, tuple):
            used.add(q)
            q = succ[q]
        out[src, q] = out.get((src, q), 0) + 1
    assert used == set(g), ('pieces on a closed loop or unused', sorted(set(g) - used)[:5])
    return out


if __name__ == '__main__':
    H, h, F, L, east, west = [int(v) for v in sys.argv[1:7]]
    secs = float(sys.argv[7]) if len(sys.argv) > 7 else 600
    top = (H - h) // 2
    cells = {(x, y) for x in range(F) for y in range(H)} | {(x, y) for x in range(F, F + L) for y in range(top, top + h)} \
        | {(x, y) for x in range(F + L, 2 * F + L) for y in range(H)}
    mouths = {'W': {(-1, y) for y in range(H)}, 'E': {(2 * F + L, y) for y in range(H)}}
    R = Region(cells, mouths, ncls=2 if west else 1)
    surface_only(R)
    R.m.Add(R.flux_in('W', 0) == east)
    if west:
        R.m.Add(R.flux_in('E', 1) == west)
    R.m.Minimize(sum(R.X.values()))
    st, rows, _, _ = R.solve(secs)
    print('witness H %d h %d F %d L %d east %d west %d: %s' % (H, h, F, L, east, west, st), flush=True)
    if st == 'FOUND':
        cr = codes(R)
        print('traced:', trace_any(cr))
        print('\n'.join(cr))
