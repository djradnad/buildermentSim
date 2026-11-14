"""Proven-smallest belt routing gadgets: how many tiles a bundle of full belts needs to cross, turn or reorder.

Every belt is its own item (commodity), there are no splitters, and each belt must arrive at its own exit, so
the solver has to build real crossings (underground hops) and turns. A box size is FOUND (checked by tracing every
belt from its entrance to its exit) or INFEASIBLE (proven). Boxes are walked by width for a few heights.

Cases (n, a, b = numbers of belts):
    rev<n>        n parallel belts west -> east, leaving in reversed order
    rot<n>        n parallel belts, the top belt leaves at the bottom, the others move up one
    swap<a>_<b>   a bundle of a belts and a bundle of b belts below it trade places (west -> east)
    cross<a>_<b>  a belts west -> east cross b belts north -> south
    turn<n>       n belts enter on the west and leave on the south (a bundle turning a corner)
    turnx<a>_<b>  a belts west -> south (turning) while b belts go north -> south beside... crossing them

    python tools/routing/gadgets.py rev3,rev4,swap2_2 600       # cases, seconds per box
    python tools/routing/gadgets.py table
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from bmsolver import model  # noqa: E402
from bmsolver.model import Window  # noqa: E402

OUT = os.path.expanduser('~/data/gadgets')


def spec(name):
    """-> (n belts, [(entry edge, slot)], [(exit edge, slot)]) with edges 'W','N','E','S' and slots counted from
    the start of the bundle on that edge; belt i enters at ins[i] and must leave at outs[i]"""
    import re
    kind, *nums = re.match(r'([a-z]+)(\d+)(?:_(\d+))?', name).groups()
    nums = [int(v) for v in nums if v]
    if kind == 'rev':
        n = nums[0]
        return n, [('W', i) for i in range(n)], [('E', n - 1 - i) for i in range(n)]
    if kind == 'rot':
        n = nums[0]
        return n, [('W', i) for i in range(n)], [('E', (i - 1) % n) for i in range(n)]
    if kind == 'swap':
        a, b = nums
        return a + b, [('W', i) for i in range(a + b)], [('E', b + i) for i in range(a)] + [('E', i) for i in range(b)]
    if kind == 'cross':
        a, b = nums
        return a + b, [('W', i) for i in range(a)] + [('N', i) for i in range(b)], \
            [('E', i) for i in range(a)] + [('S', i) for i in range(b)]
    if kind == 'turn':
        n = nums[0]
        return n, [('W', i) for i in range(n)], [('S', i) for i in range(n)]
    if kind == 'turnx':
        a, b = nums
        return a + b, [('W', i) for i in range(a)] + [('N', i) for i in range(b)], \
            [('S', i) for i in range(a + b)]
    if kind == 'riffle':                      # a1..ak b1..bk -> a1 b1 a2 b2 ... (perfect shuffle of two bundles)
        k = nums[0]
        return 2 * k, [('W', i) for i in range(2 * k)], [('E', 2 * i) for i in range(k)] + [('E', 2 * i + 1) for i in range(k)]
    if kind == 'spread':                      # k adjacent belts leave on every other row (a gap between each)
        k = nums[0]
        return k, [('W', i) for i in range(k)], [('E', 2 * i) for i in range(k)]
    if kind == 'mrgx':                        # 2k half belts; belts i and i+k merge into output i (pairs apart)
        k = nums[0]
        return 2 * k, [('W', i) for i in range(2 * k)], [('E', i) for i in range(k)]
    if kind == 'splx':                        # k full belts; belt i splits, its halves leave at i and i+k
        k = nums[0]
        return k, [('W', i) for i in range(k)], [('E', i) for i in range(2 * k)]
    if kind == 'tap':
        k = nums[0]
        return k, [('W', i) for i in range(k)], [('E', i) for i in range(k)]
    if kind in ('trapt', 'trapb'):
        k = nums[0]
        return k + 2, [('W', i) for i in range(k + 2)], [('E', i) for i in range(k + 1)]
    if kind == 'sq':
        k = nums[0]
        return k, [('W', i) for i in range(k)], [('E', i) for i in range(k)]
    raise ValueError(name)


EDGE = {'W': 1, 'N': 0, 'E': 3, 'S': 2}


def tile(edge, k, W, H):
    return {'W': (0, k), 'E': (W - 1, k), 'N': (k, 0), 'S': (k, H - 1)}[edge]


def window(name, W, H, L=0):
    """bundles keep their belts side by side and in order on an edge, but may sit anywhere along it.
    L > 0 (squeeze cases sq<k>_<h>): rows h.. are blocked over L middle columns (built over: hops may pass under)"""
    n, ins, outs = spec(name)
    w = Window(W, H, n, 1)
    if L:
        h = int(name.split('_')[1])
        x0 = (W - L) // 2
        w.blocked = {(x, y) for x in range(x0, x0 + L) for y in range(h, H)}
        w.walls = set(w.blocked)            # a real narrow corridor: nothing passes under the closed rows
    w.max_splitters = 0
    groups = {}
    for i, (e, k) in enumerate(ins):
        groups.setdefault(('in', e), []).append((i, k))
    for i, (e, k) in enumerate(outs):
        groups.setdefault(('out', e), []).append((i, k))
    w.port_options = []
    for (kind, e), members in groups.items():
        span = max(k for i, k in members) + 1
        L = H if e in 'WE' else W
        opts = []
        for s in range(L - span + 1):
            opt = {}
            for i, k in members:
                vec = [0] * n
                vec[i] = 1
                opt[(tile(e, s + k, W, H), EDGE[e])] = (kind, vec)
            opts.append(opt)
        if not opts:
            return None
        w.port_options.append(opts)
    return w


def window_mix(name, W, H):
    """merge / split cases: amounts in halves (D = 2); every bundle sits anywhere along its edge"""
    if name.startswith('tap'):
        # bus tap: k full belts west -> east; half of the top belt leaves through the bottom edge, crossing the
        # other k-1 belts; the rest of the bus goes on in order
        k = int(name[3:])
        w = Window(W, H, k, 2)
        ins = [[2 if j == i else 0 for j in range(k)] for i in range(k)]
        outs = [[(1 if i == 0 else 2) if j == i else 0 for j in range(k)] for i in range(k)]
        tap = [1] + [0] * (k - 1)
        w.port_options = []
        for kind, e, vecs in (('in', 'W', ins), ('out', 'E', outs), ('out', 'S', [tap])):
            L_ = H if e in 'WE' else W
            opts = [{(tile(e, s_ + i, W, H), EDGE[e]): (kind, v) for i, v in enumerate(vecs)}
                    for s_ in range(L_ - len(vecs) + 1)]
            if not opts:
                return None
            w.port_options.append(opts)
        return w
    if name.startswith('trap'):
        # john's trapped belt: a half belt, k full belts, a half belt; the halves merge into one full belt that
        # leaves above (trapt) or below (trapb) the k full belts, which keep their order
        k = int(name[5:])
        n = k + 2
        w = Window(W, H, n, 2)
        w.max_splitters = 0
        ins = [[1 if j == i else 0 for j in range(n)] for i in range(n)]
        for i in range(1, k + 1):
            ins[i][i] = 2
        full = [[2 if j == i else 0 for j in range(n)] for i in range(1, k + 1)]
        merged = [[1 if j in (0, n - 1) else 0 for j in range(n)]]
        outs = merged + full if name[4] == 't' else full + merged
        w.port_options = []
        for kind, e, vecs in (('in', 'W', ins), ('out', 'E', outs)):
            opts = [{(tile(e, s + i, W, H), EDGE[e]): (kind, v) for i, v in enumerate(vecs)}
                    for s in range(H - len(vecs) + 1)]
            if not opts:
                return None
            w.port_options.append(opts)
        return w
    k = int(name[4:])
    mrg = name.startswith('mrgx')
    n = 2 * k if mrg else k
    w = Window(W, H, n, 2)
    if mrg:
        w.max_splitters = 0
    ins, outs = [], []
    for i in range(2 * k if mrg else k):
        v = [0] * n
        v[i] = 1 if mrg else 2
        ins.append(v)
    for j in range(k if mrg else 2 * k):
        v = [0] * n
        if mrg:
            v[j] = v[j + k] = 1
        else:
            v[j % k] = 1
        outs.append(v)
    w.port_options = []
    for kind, e, vecs in (('in', 'W', ins), ('out', 'E', outs)):
        opts = [{(tile(e, s + i, W, H), EDGE[e]): (kind, v) for i, v in enumerate(vecs)} for s in range(H - len(vecs) + 1)]
        if not opts:
            return None
        w.port_options.append(opts)
    return w


def verify_mix(grid, win, options):
    """exact flow check: every output port gets exactly its planned amounts, nothing overfull or stuck"""
    from fractions import Fraction
    from bmsolver.check import check, Design
    from bmsolver.pieces import accepts, push_dirs
    ins, outs, rates, want = [], [], [], []
    for g, j in zip(win.port_options, options):
        for (t, d), (kind, vec) in sorted(g[j].items()):
            c = grid.get(t)
            if kind == 'in':
                if not c or not accepts(c, (d + 2) % 4):
                    return False
                ins.append((t, (d + 2) % 4))
                rates.append(Fraction(sum(vec), win.D))
            else:
                if not c or d not in push_dirs(c):
                    return False
                outs.append((t, d))
                want.append(vec)
    r = check(Design(grid), ins, outs, rates=rates, mix=True)
    if r['overfull'] or r['stuck']:
        return False
    # shares[o][i] = fraction of input i reaching output o; inputs are listed in commodity order
    for o, vec in enumerate(want):
        for i in range(len(ins)):
            got = r['shares'][o][i] * rates[i] * win.D
            if got != vec[i] if len(vec) == len(ins) else None:
                return False
    return True


def trace(grid, win, options):
    """follow every belt from its entrance: -> True when each one ends at its own exit"""
    from bmsolver.check import Design
    nxt = {}
    for p, q, k in Design(grid).links():
        nxt.setdefault(p, []).append(q)
    ins, outs = {}, {}
    for g, j in zip(win.port_options, options):
        for (t, d), (kind, vec) in g[j].items():
            (ins if kind == 'in' else outs)[vec.index(1)] = (t, d)
    from bmsolver.pieces import push_dirs
    for i, (t, d) in ins.items():
        p, seen = t, set()
        while p not in seen:
            seen.add(p)
            if p not in grid or not grid[p]:
                return False
            if p == outs[i][0] and outs[i][1] in push_dirs(grid[p]):
                break
            if len(nxt.get(p, [])) != 1:
                return False
            p = nxt[p][0]
        else:
            return False
    return True


def status(name):
    p = os.path.join(OUT, '%s.status' % name)
    return {l.split()[0]: l.split()[1:] for l in open(p)} if os.path.exists(p) else {}


def box(name, W, H, timeout, L=0):
    done = status(name)
    key = '%dx%d' % (W, H) + ('L%d' % L if L else '')
    if key in done and (done[key][0] != 'UNKNOWN' or float(done[key][1]) >= timeout):
        return done[key][0]
    mix = name.startswith(('mrgx', 'splx', 'trap', 'tap'))
    win = window_mix(name, W, H) if mix else window(name, W, H, L)
    if win is None:
        return 'INFEASIBLE'
    t0 = time.time()
    r = model.solve(win, timeout=timeout, workers=int(os.environ.get('GADGET_WORKERS', 4)))
    ok = False
    if 'grid' in r:
        if mix:
            ok = verify_mix(r['grid'], win, r['options'])
        else:
            ok = trace(r['grid'], win, r['options'])
        with open(os.path.join(OUT, '%s.jsonl' % name), 'a') as fh:
            fh.write(json.dumps(dict(case=name, W=W, H=H, L=L, traced=ok, pieces=r.get('pieces'), options=r['options'],
                                     grid={'%d,%d' % p: c for p, c in r['grid'].items() if c})) + '\n')
    st = r['status'] if r['status'] in ('INFEASIBLE', 'UNKNOWN') else ('FOUND' if ok else 'BADTRACE')
    with open(os.path.join(OUT, '%s.status' % name), 'a') as fh:
        fh.write('%s %s %.0f\n' % (key, st, time.time() - t0 if st != 'UNKNOWN' else timeout))
    print('%s %s %s %.0fs pieces=%s' % (name, key, st, time.time() - t0, r.get('pieces')), flush=True)
    if 'grid' in r:
        print(show(r['grid'], W, H), flush=True)
    return st


def show(grid, W, H):
    return '\n'.join(' '.join(grid.get((x, y)) or '..' for x in range(W)) for y in range(H))


def run(name, timeout):
    """for each height the narrowest width; stop once a taller box no longer gets narrower"""
    n, ins, outs = spec(name)
    best = None
    for H in range(1, 2 * n + 3):
        for W in range(1, (best or 4 * n + 6)):
            st = box(name, W, H, timeout)
            if st in ('FOUND', 'BADTRACE'):
                break
            if st == 'UNKNOWN':
                break
        else:
            if best is not None:
                return                                 # this height did not beat the best width: taller won't either
            continue
        if st == 'FOUND':
            best = W


def run_sq(name, timeout, Lmax=12, extra=8):
    """squeeze: k belts straight through, with only h rows open over a middle stretch of L columns. For each L the
    narrowest box (width W = L + ends); a long stretch with no box up to L + extra wide is reported as such."""
    k = spec(name)[0]
    for L in range(1, Lmax + 1):
        for W in range(L + 2, L + extra + 1):
            st = box(name, W, k, timeout, L)
            if st in ('FOUND', 'BADTRACE', 'UNKNOWN'):
                break
        else:
            print('%s L=%d: nothing up to width %d' % (name, L, L + extra), flush=True)


def table():
    for f in sorted(os.listdir(OUT)):
        if f.endswith('.status'):
            st = status(f[:-7])
            got = sorted((k for k, v in st.items() if v[0] == 'FOUND'), key=lambda k: tuple(map(int, k.split('L')[0].split('x')))[::-1])
            un = [k for k, v in st.items() if v[0] == 'UNKNOWN']
            print('%-12s found: %-40s undecided: %s' % (f[:-7], ', '.join(got), ', '.join(un)))


if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    if sys.argv[1] == 'table':
        table()
    else:
        for nm in sys.argv[1].split(','):
            (run_sq if nm.startswith('sq') else run)(nm, float(sys.argv[2]))
