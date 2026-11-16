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


def section(N, n, a, x_low=False, extra=8, timeout=600):
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
        # the x row (the belt joining the balanced group) must end (n-1)/n q + 1/n x
        q, x = (a + 1, a) if x_low else (a, a + 1)
        outs = [list(v) for v in ins]
        outs[x] = [0] * N; outs[x][q] = D - 3; outs[x][x] = 3
        outs[q] = [0] * N; outs[q][q] = 3; outs[q][x] = D - 3
        opts = [{((W - 1, i), EDGE['E']): ('out', v) for i, v in enumerate(outs)}]
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
    if os.environ.get('SPREAD', '1') == '1':     # after the loop only row rows[-2] is off: spread it
        return logical(rows[:-1]) + [('L', rows[-2], rows[-1], n), ('S', tuple(rows[:-1]), rows[-2])]
    return logical(rows[:-1]) + [('L', rows[-2], rows[-1], n)] + logical(rows[:-1])


def spread_plan(m, start, maxc=200):
    """cheapest adjacent moves that share one off row equally over m rows (every row gets 1/m of it): after a
    loop only that row differs from the balanced rest, so this replaces a second full balance (exact Dijkstra on
    the share vector)"""
    import heapq
    from fractions import Fraction as Fr
    cost = {'H': 6, 'T': 9, 'X': 6}

    def ap(s, op, i):
        a, b = s[i], s[i + 1]
        if op == 'H':
            na = nb = (a + b) / 2
        elif op == 'T':
            na, nb = a / 3 + 2 * b / 3, 2 * a / 3 + b / 3
        else:
            na, nb = b, a
        return s[:i] + (na, nb) + s[i + 2:]
    s0 = tuple(Fr(int(i == start)) for i in range(m))
    goal = tuple(Fr(1, m) for _ in range(m))
    pq, seen, k = [(0, 0, s0, ())], {s0: 0}, 0
    while pq:
        c, _, s, plan = heapq.heappop(pq)
        if s == goal:
            return list(plan)
        if c > maxc:
            return None
        for op in 'HTX':
            for i in range(m - 1):
                s2, c2 = ap(s, op, i), c + cost[op]
                if seen.get(s2, 1e9) <= c2:
                    continue
                seen[s2] = c2; k += 1
                heapq.heappush(pq, (c2, k, s2, plan + ((op, i),)))
    return None


def spread_logical(rows, start):
    """the spread plan as logical pair moves (crossings only reorder; route() places rows physically)"""
    pos = list(rows)
    out = []
    for op, i in spread_plan(len(rows), start):
        if op == 'X':
            pos[i], pos[i + 1] = pos[i + 1], pos[i]
        else:
            out.append((op, pos[i], pos[i + 1]))
    return out


def route(N, lmoves, at=None):
    """physical moves: keep track of which logical row sits where; bring a pair together by crossings and never
    cross back (balanced outputs are all alike, so the final order does not matter)"""
    at = list(range(N)) if at is None else at    # at[physical] = logical (shared with a caller)
    out = []
    for mv in lmoves:
        if mv[0] == 'S':                        # spread one off row over a group, planned on the physical order
            group, q = set(mv[1]), mv[2]
            ps = sorted(at.index(r) for r in group)
            if ps[-1] - ps[0] == len(ps) - 1:
                for op, i in spread_plan(len(ps), at.index(q) - ps[0]):
                    out.append((op, ps[0] + i))
                    if op == 'X':
                        at[ps[0] + i], at[ps[0] + i + 1] = at[ps[0] + i + 1], at[ps[0] + i]
                continue
            out += route(N, spread_logical(list(mv[1]), list(mv[1]).index(q)), at)
            continue
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
            out.append(('L', lo, mv[3], at[lo] == v))
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
            if (i, mv[2], mv[3]) not in cache:
                cache[i, mv[2], mv[3]] = section(N, mv[2], i, mv[3])
            if cache[i, mv[2], mv[3]] is None:
                raise SystemExit('no loop section found')
            blocks.append(cache[i, mv[2], mv[3]])
        else:
            blocks.append(nchain.place(op, i, N))
    if os.environ.get('LAYERS', '1') == '1':
        blocks = layered(N, moves, blocks)
        print('packed into %d layers' % len(blocks), flush=True)
    full = [sum((b[y] for b in reversed(blocks)), []) for y in range(N)]
    return full


def mix_matrix(block, N):
    """exact mix of an N-row block (inputs east, outputs west): M[out row][in row], or None if it jams"""
    from fractions import Fraction as F
    from bmsolver.design import Design, flows
    W = len(block[0])
    g = {(x, y): block[y][x] for y in range(N) for x in range(W) if block[y][x]}
    src = {(W - 1, y): [F(int(i == y)) for i in range(N)] for y in range(N)}
    try:
        inflow, dr = flows(Design(g), src, set(((0, y), 1) for y in range(N)))
    except ValueError:
        return None
    if any(sum(v) > 1 for v in inflow.values()):
        return None
    M = [list(dr.get(((0, y), 1), [F(0)] * N)) for y in range(N)]
    if any(sum(r) != 1 for r in M):
        return None
    return M


def matmul(A, B):
    """A after B (B is applied first)"""
    n = len(A)
    return [[sum(A[i][k] * B[k][j] for k in range(n)) for j in range(n)] for i in range(n)]


def piece_rows(op, i, N):
    if op == 'L':
        return set(range(N))
    return set(range(i, i + 3)) if i + 2 < N else set(range(i - 1, i + 2))


def layer_block(members, N):
    """members: [(rows, block)], disjoint rows; pad each piece on its east side with a hop run"""
    w = max(len(b[0]) for r, b in members)
    out = [nchain.through(w) for _ in range(N)]
    for rows, b in members:
        wp = len(b[0])
        for y in rows:
            pad = w - wp
            out[y] = list(b[y]) + (nchain.through(pad) if pad > 1 else ['B1'] * pad)
    return out


def layered(N, moves, blocks):
    """pack moves into layers: a move joins the earliest layer after the last one touching its rows, if the
    layer's exact mix still equals the moves applied one by one; otherwise it opens a new layer"""
    layers = []                                   # [{'rows': set, 'members': [(rows, block)], 'M': matrix}]
    last = {}
    for mv, b in zip(moves, blocks):
        R = piece_rows(mv[0], mv[1], N)
        start = max([last[r] + 1 for r in R if r in last] + [0])
        Mb = mix_matrix(b, N)
        placed = None
        for li in range(start, len(layers)):
            L = layers[li]
            if L['rows'] & R or any(r for r in R if L['rows'] & {r - 1, r + 1} and False):
                continue
            cand = layer_block(L['members'] + [(R, b)], N)
            Mc = mix_matrix(cand, N)
            if Mc is not None and Mc == matmul(Mb, L['M']):
                L['members'].append((R, b)); L['rows'] |= R; L['M'] = Mc
                placed = li
                break
        if placed is None:
            layers.append({'rows': set(R), 'members': [(R, b)], 'M': Mb})
            placed = len(layers) - 1
        for r in R:
            last[r] = placed
    return [layer_block(L['members'], N) for L in layers]


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
