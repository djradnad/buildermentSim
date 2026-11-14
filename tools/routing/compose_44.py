import sys
sys.path.insert(0, '/home/claude/buildermentsim')
from bmsolver.colreach import *
from bmsolver import spec as S, bench
from bmsolver.pieces import mirror_code
def parse(rows):
    return [[r[i:i+2] if r[i:i+2] != '..' else None for i in range(0, len(r), 2)] for r in rows]
H01 = parse(['B1B1B1S1B1B1', 'O1B0I1B1B2B1', 'O1B0B1S1B1I1'])      # half swap rows 0,1; row 2 hops under
P = Problem(3, 3, 1, [0, 1, 2], [0, 1, 2], [(0, 1, 0), (1, 0, 0), (0, 0, 1)])
r, w, seen, f = search(P, 20, log=lambda s: None)
X01 = layout(seen, f)                                               # rows 0,1 cross; row 2 passes
print('cross', r, w); print('\n'.join(''.join(c or '..' for c in row) for row in X01))
def flip(g):                                                        # top <-> bottom
    return [[mirror_code(c, 'y') if c else None for c in row] for row in reversed(g)]
def place(g, top):                                                  # 3-row gadget on rows top..top+2 of 4, plain belt elsewhere
    w = len(g[0])
    return [g[y - top] if top <= y < top + 3 else ['O1'] + [None] * (w - 2) + ['I1'] for y in range(4)]
H12 = flip(H01)                                                     # half swap rows 1,2 of a 3-row block (row 0 under)
blocks = [place(H01, 0), place(H12, 1),                             # pairs (0,1) and (2,3)   [east first]
          place(H12, 0) if False else place(flip(H01), 0)]          # placeholder
seq_east_to_west = [place(H01, 0),            # half 0,1 (row 2 under)
                    place(flip(H01), 1),      # half 2,3: flip(H01) swaps rows 1,2 of block -> rows 2,3 of box, row 1 under
                    place(H12, 0),            # half 1,2 (row 0 under)
                    place(X01, 0),            # cross 0,1
                    place(flip(X01), 1),      # cross 2,3
                    place(H12, 0)]            # half 1,2
full = [sum((b[y] for b in reversed(seq_east_to_west)), []) for y in range(4)]
W = len(full[0])
grid = {(x, y): full[y][x] for y in range(4) for x in range(W) if full[y][x]}
win = S.Spec([S.Group('E', 4, at=0)], [S.Group('W', 4, at=0)]).window(W, 4)
print('4:4 at 4 tall %d wide verify=%s' % (W, bench.verify(grid, win, [0] * len(win.port_options) if win.port_options else None)))
print('\n'.join(''.join(c or '..' for c in row) for row in full))
Q = Problem(4, 4, 4, [0, 1, 2, 3], [0, 1, 2, 3], (1, 1, 1, 1))
fr = {Q.start()}
for x in range(W - 1, -1, -1):
    col = tuple(full[y][x] for y in range(4))
    nf = set()
    for s in fr:
        nf |= Q.fill(s, col)
    fr = nf
    cl = [s for s in fr if all(not t[2] and t[3] is None and t[1] in ('n', 'z') for t in s)]
    if not fr:
        print('dead at', x); break
    if cl:
        print(x, [tuple(t[0][1] if t[0] else None for t in s) for s in cl][:2])
