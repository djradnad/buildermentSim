"""N:N balancer at N tall by arithmetic (2026-11-15): plan the mixing as pair moves plus loop sections, build it
from proven pieces, check it exactly, then squeeze (tools/data/shrink.py --unit-mult) and tidy.

Plan, recursively:
  * N = f * m with f in (2, 3): balance each block of m rows, then balance every f rows {k, k+m, ...} as an f:f
    (half swap for 2; half, 2/3, half for 3). Non-adjacent pairs are brought together by crossings and sent back.
  * any other N (a prime p > 3 in it): balance rows 0..N-2 as (N-1):(N-1), then a loop section mixes rows N-2 and
    N-1 as 1/N - (N-1)/N (the last row ends (N-1)/N q + 1/N x), then balance rows 0..N-2 again.
Loop sections: a proven 2-belt loop piece (gadgets mixs1_<N> / mixf1) pinned on the bottom 3 rows; the solver
routes the other N-3 belts past it (squeeze law: N-3 open rows carry them).

    python tools/routing/planner.py --n 7 --out ~/data/n77_chain.txt
"""
import argparse
import json
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
import nchain  # noqa: E402
from bmsolver.model import Window  # noqa: E402
from bmsolver import model  # noqa: E402
from bmsolver.pieces import mirror_code  # noqa: E402
from bmsolver import rules  # noqa: E402

PIECE_LOG = os.path.expanduser('~/data/gadgets15')
EDGE = {'W': 1, 'N': 0, 'E': 3, 'S': 2}


def pair(op, i, j):
    """moves for an op on rows i < j: cross j up next to i, op, cross back"""
    down = [('X', k) for k in range(j - 1, i, -1)]
    return down + [(op, i)] + list(reversed(down))


def fmix(rows):
    """balance a group of 2 or 3 rows (sorted)"""
    if len(rows) == 2:
        return pair('H', *rows)
    a, b, c = rows
    return pair('H', a, b) + pair('T', b, c) + pair('H', a, b)


def plan(rows):
    """moves that balance the given contiguous rows"""
    n = len(rows)
    if n == 1:
        return []
    for f in (2, 3):
        if n % f == 0 and n > f:
            m = n // f
            out = []
            for g in range(f):
                out += plan(rows[g * m:(g + 1) * m])
            for k in range(m):
                out += fmix([rows[k + g * m] for g in range(f)])
            return out
    if n in (2, 3):
        return fmix(rows)
    return plan(rows[:-1]) + [('L', rows[-2], n)] + plan(rows[:-1])


def piece(n):
    """best traced 2-belt loop piece for 1/n with 3 rows: (W, grid dict)"""
    name = 'mixf1' if n == 5 else 'mixs1_%d' % n
    best = None
    for l in open(os.path.join(PIECE_LOG, name + '.jsonl')):
        r = json.loads(l)
        if r['traced'] and r['H'] == 3 and (best is None or r['W'] < best['W']):
            best = r
    if best is None:
        raise SystemExit('no 3-row loop piece for 1/%d yet (gadgets.py %s)' % (n, name))
    return best


def section(N, n, a, extra=8, timeout=600):
    """loop section, N rows: rows a, a+1 mix 1/n - (n-1)/n through the pinned piece (rows a-1..a+1), the rest
    pass. Returns rows (inputs east, outputs west) or None"""
    r = piece(n)
    top = a - 1
    assert rules.section_fits(N, 3, N - 2), 'not enough rows to pass the other belts'
    D = 3 * n
    for W in range(r['W'] + 4, r['W'] + extra + 1):
        ox = (W - r['W']) // 2
        w = Window(W, N, N, D)
        w.fixed = {}
        for k_, c in r['grid'].items():
            x, y = map(int, k_.split(','))
            w.fixed[(x + ox, y + top)] = c
        for x in range(r['W']):
            for y in range(3):
                w.fixed.setdefault((x + ox, y + top), None)
        ins = [[D if j == i else 0 for j in range(N)] for i in range(N)]
        opts = []
        for kk in (1, n - 1):
            outs = [list(v) for v in ins]
            outs[a] = [0] * N; outs[a][a] = kk * 3; outs[a][a + 1] = D - kk * 3
            outs[a + 1] = [0] * N; outs[a + 1][a] = D - kk * 3; outs[a + 1][a + 1] = kk * 3
            opts.append({((W - 1, i), EDGE['E']): ('out', v) for i, v in enumerate(outs)})
        w.port_options = [[{((0, i), EDGE['W']): ('in', v) for i, v in enumerate(ins)}], opts]
        res = model.solve(w, timeout=timeout, workers=int(os.environ.get('WORKERS', 8)))
        print('section %d rows, loop 1/%d at rows %d,%d, %d wide: %s' % (N, n, a, a + 1, W, res['status']), flush=True)
        if 'grid' in res:
            g = res['grid']
            return [[mirror_code(g[(W - 1 - x, y)], 'x') if g.get((W - 1 - x, y)) else None for x in range(W)]
                    for y in range(N)]
    return None


def logical(rows):
    """the plan on logical rows: ('H'|'T', u, v), ('L', u, v, n)"""
    n = len(rows)
    if n == 1:
        return []
    def fm(rs):
        if len(rs) == 2:
            return [('H', rs[0], rs[1])]
        a, b, c = rs
        return [('H', a, b), ('T', b, c), ('H', a, b)]
    for f in (2, 3):
        if n % f == 0 and n > f:
            m = n // f
            out = []
            for g in range(f):
                out += logical(rows[g * m:(g + 1) * m])
            for k in range(m):
                out += fm([rows[k + g * m] for g in range(f)])
            return out
    if n in (2, 3):
        return fm(rows)
    return logical(rows[:-1]) + [('L', rows[-2], rows[-1], n)] + logical(rows[:-1])


def route(N, lmoves):
    """physical moves: keep track of which logical row sits where; bring a pair together by crossings and never
    cross back (balanced outputs are all alike, so the final order does not matter)"""
    at = list(range(N))                         # at[physical] = logical
    out = []
    for mv in lmoves:
        u, v = mv[1], mv[2]
        while True:
            pu, pv = at.index(u), at.index(v)
            lo, hi = min(pu, pv), max(pu, pv)
            if hi - lo == 1:
                break
            # move the outer one a step inward, preferring the move that keeps rows away from the edges
            k = hi - 1 if hi - 1 > lo else lo
            out.append(('X', k))
            at[k], at[k + 1] = at[k + 1], at[k]
        pu, pv = at.index(u), at.index(v)
        lo = min(pu, pv)
        if mv[0] == 'L':
            if lo == 0:                         # the pinned piece needs a row above the pair
                out.append(('X', 1)); at[1], at[2] = at[2], at[1]
                out.append(('X', 0)); at[0], at[1] = at[1], at[0]
                out.append(('X', 1)); at[1], at[2] = at[2], at[1]
                lo = 1
            out.append(('L', lo, mv[3]))
        elif mv[0] == 'T' and pu > pv:
            out.append(('T', lo))               # 2/3 swap is symmetric in its pair
        else:
            out.append((mv[0], lo))
    return out


def build(N):
    moves = route(N, logical(list(range(N))))
    print('%d:%d plan: %d moves' % (N, N, len(moves)), ' '.join('%s%d' % m[:2] for m in moves), flush=True)
    blocks, cache = [], {}
    for mv in moves:
        op, i = mv[0], mv[1]
        if op == 'L':
            if i not in cache:
                cache[i] = section(N, mv[2], i)
            if cache[i] is None:
                raise SystemExit('no loop section found')
            blocks.append(cache[i])
        else:
            blocks.append(nchain.place(op, i, N))
    full = [sum((b[y] for b in reversed(blocks)), []) for y in range(N)]
    return full


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, required=True)
    ap.add_argument('--out', default=None)
    a = ap.parse_args()
    full = build(a.n)
    ok = nchain.check(full, a.n)
    rows = [''.join(c or '..' for c in row) for row in full]
    print('%d:%d at %d tall, chain %d wide, exact check %s' % (a.n, a.n, a.n, len(full[0]), ok))
    print('\n'.join(rows))
    if a.out and ok:
        open(os.path.expanduser(a.out), 'w').write('\n'.join(rows) + '\n')


if __name__ == '__main__':
    main()
