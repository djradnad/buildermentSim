import sys
sys.path.insert(0, '/home/claude/buildermentsim')
from bmsolver.colreach import *
from bmsolver import spec as S, bench
g2 = Problem(3, 3, 3, [0, 1, 2], [0, 1, 2], [(3, 0, 0), (0, 1, 2), (0, 2, 1)])
r, w, seen, f = search(g2, 40, log=lambda s: print(' ', s, flush=True), procs=3)
print('exchange 2/3 rows 1,2:', r, w, flush=True)
G2 = layout(seen, f)
G1 = [r.split() for r in []]
half = ['B1B1B1S1B1B1', 'O1B0I1B1B2B1', 'O1B0B1S1B1I1']
G1 = [[row[i:i + 2] if row[i:i + 2] != '..' else None for i in range(0, len(row), 2)] for row in half]
# west to east: half | exchange | half  (inputs enter the east half first)
full = [G1[y] + G2[y] + G1[y] for y in range(3)]
W = len(full[0])
grid = {(x, y): full[y][x] for y in range(3) for x in range(W) if full[y][x]}
win = S.Spec([S.Group('E', 3, at=0)], [S.Group('W', 3, at=0)]).window(W, 3)
opts = [0] * len(win.port_options) if win.port_options else None
print('3:3 at 3 tall, %d wide, verify=%s' % (W, bench.verify(grid, win, opts)))
print('\n'.join(''.join(c or '..' for c in row) for row in full))
