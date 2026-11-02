"""Build N:N balancers that are too big to solve in one go, then let improve.py shrink them.

N is written as a product of 2s and 3s (4 = 2x2, 6 = 2x3, 8 = 2x2x2, 9 = 3x3, 12 = 2x2x3, ...). Lane i has one
digit per factor. Stage s mixes, in r:r blocks, the lanes whose numbers differ only in digit s; after every stage
each output carries an equal share of every input. The blocks of a stage are stacked in one column; between two
stages a shuffle strip brings the right lanes together. Lanes that carry the same mixture are interchangeable,
so a shuffle only has to deliver one lane of the right mixture to each block input, which keeps it quick to solve.

Mixer blocks come from blocks.json (solved once with `python -m bmsolver.compose`), shuffles are solved here.
"""
import itertools
import json
import os

from . import model
from .check import check
from .design import Design
from .improve import Layout
from .pieces import push_dirs, accepts, opposite
from .spec import balancer

BLOCKS_FILE = os.path.join(os.path.dirname(__file__), 'blocks.json')


def factors(n):
    out = []
    for r in (3, 2):
        while n % r == 0:
            out.append(r)
            n //= r
    return sorted(out) if n == 1 else None


# ---------------------------------------------------------------------- mixer blocks
def solve_block(r, h, timeout=120, max_w=14, log=print):
    """Smallest r:r balancer exactly h rows high with closed edges (safe to stack), polished."""
    sp = balancer(r)
    for W in range(1, max_w + 1):
        win = sp.window(W, h)
        if win is None:
            continue
        win.closed_edges = True
        res = model.solve(win, timeout=timeout)
        log('  %d:%d block %dx%d %s %.0fs' % (r, r, W, h, res['status'], res['time']))
        if 'grid' not in res:
            continue
        win.hint = res['grid']
        pol = model.solve(win, timeout=timeout, objective=True, turn_weight=1)
        if 'grid' in pol:
            res = pol
        ins, outs = sp.ports_used(win, res['options'])
        rep = check(Design(res['grid']), ins, outs)
        assert rep['ok'], rep
        return dict(r=r, W=W, H=h, grid=[[x, y, c] for (x, y), c in res['grid'].items()],
                    ins=[p[1] for p, d in ins], outs=[p[1] for p, d in outs])
    return None


def build_block_library(path=BLOCKS_FILE, specs=((2, 3), (2, 4), (3, 5), (3, 6)), timeout=300):
    lib = load_blocks(path) if os.path.exists(path) else []
    have = {(b['r'], b['H']) for b in lib}
    for r, h in specs:
        if (r, h) in have:
            continue
        b = solve_block(r, h, timeout)
        if b:
            lib.append(b)
            with open(path, 'w') as f:
                json.dump(lib, f, indent=0)
    return lib


def load_blocks(path=BLOCKS_FILE):
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------------- shuffle strips
def solve_shuffle(H, in_rows, out_rows, deny, timeout=60, max_w=20, log=print):
    """Route lanes between two stages. in_rows: {row: class} entering the east edge, out_rows: {row: class}
    leaving the west edge (each class as often on both sides). deny: {(row, side): 'deny_in'|'deny_out'} from the
    neighbouring blocks. No splitters; lanes of one class are interchangeable. Returns (W, grid) or None."""
    classes = sorted(set(in_rows.values()))
    nc = len(classes)
    vec = lambda c: [1 if k == c else 0 for k in classes]
    for W in range(1, max_w + 1):
        win = model.Window(W, H, nc, 1)
        win.max_splitters = 0
        win.closed_edges = True
        for y, c in in_rows.items():
            win.ports[(W - 1, y), 3] = ('in', vec(c))
        for y, c in out_rows.items():
            win.ports[(0, y), 1] = ('out', vec(c))
        for (y, side), kind in deny.items():
            key = ((W - 1, y), 3) if side == 'E' else ((0, y), 1)
            win.ports.setdefault(key, kind)
        res = model.solve(win, timeout=timeout)
        log('  shuffle %dx%d %s %.0fs' % (W, H, res['status'], res['time']))
        if 'grid' in res:
            win.hint = res['grid']
            pol = model.solve(win, timeout=timeout, objective=True, turn_weight=1)
            return W, (pol if 'grid' in pol else res)['grid']
    return None


def edge_denials(design, x_out, rows, side):
    """For a strip whose edge on `side` touches column x_out of design: which rows must refuse what."""
    deny = {}
    d = 3 if side == 'E' else 1                 # direction pointing out of the strip
    for y in rows:
        c = design.tiles.get((x_out, y))
        if c is None:
            continue
        if opposite(d) in push_dirs(c):
            deny[y, side] = 'deny_in'
        elif accepts(c, d):
            deny[y, side] = 'deny_out'
    return deny


# ---------------------------------------------------------------------- composition
def compose(n, blocks=None, timeout=60, goal='area', log=print):
    """A working n:n balancer (not yet small), trying every choice of mixer blocks and keeping the smallest.
    Returns a Layout with inputs east and outputs west."""
    rs = factors(n)
    if rs is None:
        raise ValueError('%d has factors other than 2 and 3; solve it directly instead' % n)
    lib = blocks or load_blocks()
    cands = {r: [b for b in lib if b['r'] == r] for r in set(rs)}
    for r, c in cands.items():
        if not c:
            raise ValueError('no %d:%d mixer block in the library' % (r, r))
    best = None
    for combo in itertools.product(*[cands[r] for r in sorted(cands)]):
        lay = _compose(n, rs, dict(zip(sorted(cands), combo)), timeout, log)
        W, H = lay.design.size
        key = (W * H, max(W, H)) if goal == 'area' else (max(W, H), W * H)
        if best is None or key < best[0]:
            best = (key, lay)
    return best[1]


def _compose(n, rs, pick, timeout, log):
    H = max((n // r) * pick[r]['H'] for r in rs)
    # digit of lane i for stage s
    strides = [1]
    for r in rs:
        strides.append(strides[-1] * r)
    digit = lambda i, s: (i // strides[s]) % rs[s]
    # stage s: block for each group of lanes differing only in digit s; block j takes lanes in digit order
    stages = []
    for s, r in enumerate(rs):
        groups = {}
        for i in range(n):
            key = tuple(digit(i, t) for t in range(len(rs)) if t != s)
            groups.setdefault(key, []).append(i)
        stages.append([sorted(g, key=lambda i: digit(i, s)) for _, g in sorted(groups.items())])
    tiles, x = {}, 0                            # build from the outputs (west, x = 0) towards the inputs
    order = list(range(len(rs)))[::-1]          # last stage at the west edge
    placed = []
    for k, s in enumerate(order):
        b = pick[rs[s]]
        for j, grp in enumerate(stages[s]):
            y0 = j * b['H']
            for bx, by, c in b['grid']:
                tiles[(x + bx, y0 + by)] = c
        placed.append((s, x, b))
        x += b['W']                             # shuffle strips go in afterwards (they need both neighbours)
    design = Design(tiles)
    # lane classes: before stage s, a lane's mixture is fixed by its digits t >= s (digits < s are mixed away)
    # insert shuffles from west to east; each shift moves the eastern part
    for k in range(len(order) - 1):
        s_w, xw, bw = placed[k]                 # western stage (later in the flow)
        s_e, xe, be = placed[k + 1]             # eastern stage (earlier)
        cls = lambda i, s: tuple(digit(i, t) for t in range(s, len(rs)))
        in_rows, out_rows = {}, {}
        for j, grp in enumerate(stages[s_e]):   # outputs of the eastern stage: mixed over digit s_e
            for q, i in enumerate(grp):
                in_rows[j * be['H'] + be['outs'][q]] = cls(i, s_e + 1)
        for j, grp in enumerate(stages[s_w]):   # inputs of the western stage
            for q, i in enumerate(grp):
                out_rows[j * bw['H'] + bw['ins'][q]] = cls(i, s_w)
        deny = {}
        deny.update(edge_denials(design, xe, range(H), 'E'))
        deny.update(edge_denials(design, xw + bw['W'] - 1, range(H), 'W'))
        res = solve_shuffle(H, in_rows, out_rows, deny, timeout, log=log)
        if res is None:
            raise RuntimeError('no shuffle found between stages %d and %d' % (s_e, s_w))
        w, grid = res
        x_ins = xw + bw['W']
        moved = {((p[0] + w, p[1]) if p[0] >= x_ins else p): c for p, c in design.tiles.items()}
        for (gx, gy), c in grid.items():
            moved[(x_ins + gx, gy)] = c
        design = Design(moved)
        placed = [(s, (xx + w if xx >= x_ins else xx), b) for s, xx, b in placed]
    s0, x0, b0 = placed[-1]
    inputs = []
    for j, grp in enumerate(stages[s0]):
        for q, i in enumerate(grp):
            inputs.append((i, (x0 + b0['W'] - 1, j * b0['H'] + b0['ins'][q])))
    inputs = [(p, 1) for i, p in sorted(inputs)]
    sl, xl, bl = placed[0]
    outputs = [((0, j * bl['H'] + bl['outs'][q]), 1) for j, grp in enumerate(stages[sl]) for q in range(len(grp))]
    lay = Layout(design, inputs, sorted(outputs, key=lambda t: t[0][1]))
    rep = lay.check()
    if not rep['ok']:
        raise RuntimeError('composed balancer failed the check (please report): %s' % rep)
    return lay


if __name__ == '__main__':
    build_block_library()
