"""Plan search (2026-11-15): the cheapest sequence of pair moves on ADJACENT rows that turns N inputs into N equal
mixes, by exact arithmetic. Moves (3-row pieces, widths as built): H half swap, T 2/3 swap, X crossing, L loop
(1/n - (n-1)/n, only when the prime n is needed). Beam search on the exact mixing matrix, cost = summed piece
width; the chain is then built by planner.py from the plan.

    python tools/routing/plansearch.py --n 7 --beam 4000
"""
import argparse
from fractions import Fraction as F

COST = {'H': 6, 'T': 9, 'X': 6, 'L': 18, 'J': 9}   # J: john's 0d31bh 3:3 on rows i..i+2 (needs rows i-1, i+3)


def apply(M, op, i, n=None):
    if op == 'J':
        c = tuple((x + y + z) / 3 for x, y, z in zip(M[i], M[i + 1], M[i + 2]))
        return M[:i] + (c, c, c) + M[i + 3:]
    a, b = M[i], M[i + 1]
    if op == 'H':
        c = tuple((x + y) / 2 for x, y in zip(a, b)); na, nb = c, c
    elif op == 'T':
        na = tuple(x / 3 + 2 * y / 3 for x, y in zip(a, b)); nb = tuple(2 * x / 3 + y / 3 for x, y in zip(a, b))
    elif op == 'X':
        na, nb = b, a
    else:
        na = tuple(x / n + (n - 1) * y / n for x, y in zip(a, b))
        nb = tuple((n - 1) * x / n + y / n for x, y in zip(a, b))
    return M[:i] + (na, nb) + M[i + 2:]


import os
JOHN = os.environ.get('JPIECE', '0') == '1'


def dev(M, N):
    u = F(1, N)
    return float(sum((x - u) ** 2 for r in M for x in r))


def search(N, beam=3000, maxd=40, alpha=None, loops=()):
    start = tuple(tuple(F(int(i == j)) for j in range(N)) for i in range(N))
    goal = tuple(tuple(F(1, N) for _ in range(N)) for _ in range(N))
    alpha = alpha if alpha is not None else 6.0 * N
    front = {start: (0, [])}
    best = None
    seen = {start: 0}
    for d in range(maxd):
        nxt = {}
        for M, (c, plan) in front.items():
            ops = [('H', None), ('T', None), ('X', None)] + [('L', n) for n in loops] + \
                ([('J', None)] if JOHN else [])
            for op, n in ops:
                for i in (range(1, N - 3) if op == 'J' else range(N - 1)):
                    if op == 'X' and plan and plan[-1][:2] == ('X', i):
                        continue
                    M2 = apply(M, op, i, n)
                    if M2 == M:
                        continue
                    c2 = c + COST[op]
                    if best and c2 >= best[0]:
                        continue
                    if seen.get(M2, 1e9) <= c2:
                        continue
                    seen[M2] = c2
                    p2 = plan + [(op, i) if n is None else (op, i, n)]
                    if M2 == goal:
                        if not best or c2 < best[0]:
                            best = (c2, p2)
                            print('found cost %d, %d moves: %s' % (c2, len(p2), ' '.join(
                                '%s%d' % m[:2] for m in p2)), flush=True)
                        continue
                    nxt[M2] = (c2, p2)
        if not nxt:
            break
        front = dict(sorted(nxt.items(), key=lambda kv: kv[1][0] + alpha * dev(kv[0], N))[:beam])
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, required=True)
    ap.add_argument('--beam', type=int, default=3000)
    ap.add_argument('--alpha', type=float, default=None)
    ap.add_argument('--maxd', type=int, default=40)
    a = ap.parse_args()
    loops = []
    m = a.n
    for p in (2, 3):
        while m % p == 0:
            m //= p
    if m > 1:
        loops = [p for p in range(5, a.n + 1) if a.n % p == 0 and all(p % q for q in range(2, p))]
    print(search(a.n, a.beam, a.maxd, a.alpha, loops))


if __name__ == '__main__':
    main()
