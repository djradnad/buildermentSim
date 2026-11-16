"""N:N balancers in an N-tall box from proven 3-row pieces (2026-11-14): plan the arithmetic as a list of pair moves,
place each piece on its rows (the other rows run straight through underground, so no edge splitter can spill
into them), check the chain exactly, then squeeze (tools/data/shrink.py) and tidy (tools/routing/tidy.py).

Pieces (3 rows, inputs east, outputs west):
    H   half swap of a pair; the third row's belt hops under the whole piece          6 wide
    T   2/3 swap of a pair (each keeps 1/3); the third row passes                      9 wide
    X   the pair crosses (trade rows); the third row passes                            6 wide

    python tools/routing/nchain.py --n 6 --out ~/data/n66_chain.txt
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from bmsolver import spec as S, bench  # noqa: E402
from bmsolver.pieces import mirror_code  # noqa: E402


def parse(rows):
    return [[r[i:i + 2] if r[i:i + 2] != '..' else None for i in range(0, len(r), 2)] for r in rows]


def flip(g):
    return [[mirror_code(c, 'y') if c else None for c in row] for row in reversed(g)]


# pair = rows 0,1 of the piece, third row 2 ...
H_TOP = parse(['B1B1B1S1B1B1', 'O1B0I1B1B2B1', 'O1B0B1S1B1I1'])
X_TOP = parse(['B1B1..B2B1B1', 'O1B0I1B1B2B1', 'O1B0B1B1B1I1'])
# ... except T, whose pair is rows 1,2 (third row 0 on top)
T_BOT = parse(['O1B2B1B1B1B1B1I1B1', 'B1B1B2S1O1B0S1B1I1', 'B1B1B1B1B1B1B1B0B1'])
PIECES = {'H': (H_TOP, flip(H_TOP)), 'X': (X_TOP, flip(X_TOP)), 'T': (flip(T_BOT), T_BOT)}


def through(w):
    """one row passing a piece of width w underground (hops of at most 7)"""
    row = [None] * w
    x = w - 1
    while x > 0:
        row[x] = 'I1'
        o = max(0, x - 7)
        row[o] = 'O1'
        x = o - 1
    if x == 0:
        row[0] = 'B1'
    return row


def place(op, a, N):
    """the piece for pair (a, a+1) as N rows"""
    top_piece, bot_piece = PIECES[op]
    if a + 2 < N:
        g, top = top_piece, a          # pair on top, third row a+2
    else:
        g, top = bot_piece, a - 1      # pair at the bottom, third row a-1
    w = len(g[0])
    return [g[y - top] if top <= y < top + 3 else through(w) for y in range(N)]


PLANS = {
    3: [('H', 0), ('T', 1), ('H', 0)],
    4: [('H', 0), ('H', 2), ('H', 1), ('X', 0), ('X', 2), ('H', 1)],
    # 3:3 on each half, then a half swap between every row of the top half and one of the bottom half
    6: [('H', 0), ('T', 1), ('H', 0), ('H', 3), ('T', 4), ('H', 3),
        ('H', 2), ('X', 1), ('X', 3), ('H', 2), ('X', 0), ('X', 1), ('X', 4), ('X', 3), ('H', 2)],
}


def chain(N, plan):
    blocks = [place(op, a, N) for op, a in plan]          # east to west
    return [sum((b[y] for b in reversed(blocks)), []) for y in range(N)]


def check(full, N):
    W = len(full[0])
    grid = {(x, y): full[y][x] for y in range(N) for x in range(W) if full[y][x]}
    win = S.Spec([S.Group('E', N, at=0)], [S.Group('W', N, at=0)]).window(W, N)
    return bench.verify(grid, win, [0] * len(win.port_options) if win.port_options else None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, required=True)
    ap.add_argument('--out', default=None)
    a = ap.parse_args()
    full = chain(a.n, PLANS[a.n])
    ok = check(full, a.n)
    rows = [''.join(c or '..' for c in row) for row in full]
    print('%d:%d at %d tall, chain %d wide, exact check %s' % (a.n, a.n, a.n, len(full[0]), ok))
    print('\n'.join(rows))
    if a.out and ok:
        open(os.path.expanduser(a.out), 'w').write('\n'.join(rows) + '\n')


if __name__ == '__main__':
    main()
