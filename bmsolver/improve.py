"""Make an existing layout smaller: re-solve narrow strips of it exactly, one column (or row) fewer each time.

A strip is a band of neighbouring columns over the full height of the layout. Everything outside the strip stays
as it is, and every flow crossing the strip's edges is kept exactly (same amounts, same mixtures), so the layout
still works whatever the solver puts inside. If the strip can be re-solved one column narrower, the layout loses a
column. Rows work the same way on the layout turned a quarter turn. When no strip shrinks any more, the pieces are
polished (fewest pieces, then fewest turns) window by window.

Mixtures are what keeps strips fast: lanes that carry the same mixture of inputs are one commodity inside a strip,
so a strip of a 9:9 balancer after its first mixing stage has only a few commodities.
"""
import math
import time
from fractions import Fraction

from . import model
from .check import check, prune
from .design import Design, flows
from .pieces import step, push_dirs, accepts, direction_between, opposite
from .search import Reporter


class Layout:
    """A design with its inputs and outputs: inputs [(tile, direction items move in)], outputs [(tile, dir out)]."""

    def __init__(self, design, inputs, outputs, rates=None, mix=True):
        self.design, self.inputs, self.outputs, self.mix = design, list(inputs), list(outputs), mix
        n, m = len(inputs), len(outputs)
        self.rates = list(rates) if rates else [min(Fraction(1), Fraction(m, n))] * n

    def check(self):
        return check(self.design, self.inputs, self.outputs, self.rates, self.mix)

    def size(self):
        return self.design.size

    def transformed(self, fn_tile, fn_dir, design):
        return Layout(design, [(fn_tile(p), fn_dir(d)) for p, d in self.inputs],
                      [(fn_tile(p), fn_dir(d)) for p, d in self.outputs], self.rates, self.mix)

    def rotated(self):
        """Quarter turn counter-clockwise (columns become rows), normalized."""
        g = self.design
        rot = Design({(y, -x): c[0] + str((int(c[1]) + 1) % 4) for (x, y), c in g.tiles.items()})
        x0, y0, _, _ = rot.bbox()
        f = lambda p: (p[1] - x0, -p[0] - y0)
        return self.transformed(f, lambda d: (d + 1) % 4, rot.shifted(-x0, -y0))

    def normalized(self):
        x0, y0, _, _ = self.design.bbox()
        return self.transformed(lambda p: (p[0] - x0, p[1] - y0), lambda d: d, self.design.shifted(-x0, -y0))


def link_flows(layout):
    """Every link (p, q) with the exact amount of each input it carries, plus the outputs' amounts."""
    g = layout.design
    n = len(layout.inputs)
    src = {}
    for i, (p, d) in enumerate(layout.inputs):
        v = [Fraction(0)] * n
        v[i] = Fraction(layout.rates[i])
        src[p] = [a + b for a, b in zip(src.get(p, [0] * n), v)]
    inflow, drained = flows(g, src, set(layout.outputs))
    links = g.links()
    outs = {p: [] for p in g.tiles}
    for p, q, k in links:
        outs[p].append((p, q, k))
    nouts = {p: len(outs[p]) + sum(1 for o in layout.outputs if o[0] == p) for p in g.tiles}
    lf = {}
    for p, q, k in links:
        share = Fraction(1, nouts[p]) if g.tiles[p][0] == 'S' else Fraction(1)
        lf[(p, q, k)] = [v * share for v in inflow[p]]
    return lf, inflow, drained, src


def cut_window(layout, x0, x1, y0=None, y1=None, shrink=0, cut=None):
    """The window over columns x0..x1 and rows y0..y1 (default: all rows) of a normalized layout, with every flow
    crossing its edges kept exactly. Returns (window, the window's current tiles as a Design).

    shrink (full-height windows only): the window is that many columns narrower and the columns east of it move
    west. Inputs and outputs on the layout's top or bottom edge inside the window keep their places, except that
    those east of column `cut` move west and one at `cut` may end up there or one to the west."""
    g = layout.design
    bx0, by0, bx1, by1 = g.bbox()
    y0 = by0 if y0 is None else y0
    y1 = by1 if y1 is None else y1
    if shrink and (y0, y1) != (by0, by1):
        raise ValueError('only full-height windows can shrink')
    H = y1 - y0 + 1
    W = x1 - x0 + 1 - shrink
    inside = lambda p: x0 <= p[0] <= x1 and y0 <= p[1] <= y1

    def local(p):
        """local coordinates; tiles east of a shrinking window move west"""
        return (p[0] - x0 - (shrink if p[0] > x1 else 0), p[1] - y0)

    lf, inflow, drained, src = link_flows(layout)
    # crossings: (in/out, where, vector of input amounts); where = ('port', tile, outward dir[, lane index]),
    # ('tin', entrance outside, dir, exit inside) or ('tout', exit outside, dir, entrance inside)
    cross = []
    for (p, q, k), v in lf.items():
        if inside(p) == inside(q):
            continue
        if k == 'push':
            if inside(p):
                cross.append(('out', ('port', p, direction_between(p, q)), v))
            else:
                cross.append(('in', ('port', q, direction_between(q, p)), v))
        else:
            d = int(g.tiles[p][1])
            if inside(p):
                cross.append(('out', ('tout', q, d, p), v))
            else:
                cross.append(('in', ('tin', p, d, q), v))
    for i, (p, d) in enumerate(layout.inputs):
        if inside(p):
            cross.append(('in', ('port', p, opposite(d), i), src[p]))
    for i, (p, d) in enumerate(layout.outputs):
        if inside(p):
            cross.append(('out', ('port', p, d, i), drained[(p, d)]))
    # commodities: the distinct mixtures entering the window; what leaves is traced back to them by a flow run on
    # the window alone (mixtures can be linearly dependent, so no guessing a decomposition)
    ins = [c for c in cross if c[0] == 'in' and sum(c[2]) > 0]
    comps = []

    def cid(v):
        tot = sum(v)
        u = [a / tot for a in v]
        if u not in comps:
            comps.append(u)
        return comps.index(u)
    colour = [cid(c[2]) for c in ins]
    nc, nin = len(comps), len(ins)
    sub = Design({p: c for p, c in g.tiles.items() if inside(p)})
    srcw = {}
    for j, (_, where, v) in enumerate(ins):
        at = where[1] if where[0] == 'port' else where[3]          # a tunnel delivers at its exit
        e = [Fraction(0)] * nin
        e[j] = sum(v)
        srcw[at] = [a + b for a, b in zip(srcw.get(at, [0] * nin), e)]
    sinks = {(w[1], w[2]) for kind, w, v in cross if kind == 'out' and w[0] == 'port'}
    inw, drw = flows(sub, srcw, sinks) if srcw else ({p: [0] * nin for p in sub.tiles}, {})
    agg = lambda vec: [sum(vec[j] for j in range(nin) if colour[j] == k) for k in range(nc)]
    amounts = [x for v in inw.values() for x in agg(v)] + [sum(c[2]) for c in ins]
    win = model.Window(W, H, nc, 1)
    win.io = {}                 # (in/out, layout lane index) -> port key, or option group index
    raw = []
    for j, (_, where, v) in enumerate(ins):
        vec = [Fraction(0)] * nc
        vec[colour[j]] = sum(v)
        raw.append(('in', where, vec))
    for kind, where, v in cross:
        if kind != 'out':
            continue
        w = agg(drw.get((where[1], where[2]), [0] * nin)) if where[0] == 'port' else agg(inw[where[3]])
        if sum(w) != sum(v):
            raise RuntimeError('window flow mismatch at %s (please report)' % (where,))
        raw.append(('out', where, w))
        amounts += w
    D = win.D = math.lcm(12, *[Fraction(a).denominator for a in amounts])
    units = lambda vec: [int(a * D) for a in vec]
    for kind, where, vec in raw:
        if where[0] == 'tin':
            win.tun_in[local(where[1]), where[2]] = units(vec)
            continue
        if where[0] == 'tout':
            win.tun_out[local(where[1]), where[2]] = units(vec)
            continue
        p, d = where[1], where[2]
        lp = (W - 1 if d == 3 else p[0] - x0, p[1] - y0)          # the east edge moves with a shrink
        if shrink and d in (0, 2):                                 # layout input/output on the top or bottom
            lx = p[0] - x0 - (shrink if cut is not None and p[0] > cut else 0)
            if cut is not None and p[0] == cut and lx > 0:
                win.port_options.append([{((x, lp[1]), d): (kind, units(vec))} for x in (lx, lx - 1)])
                win.io[kind, where[3]] = len(win.port_options) - 1
                continue
            lp = (min(lx, W - 1), lp[1])
        win.ports[lp, d] = (kind, units(vec))
        if len(where) > 3:
            win.io[kind, where[3]] = (lp, d)
    # edges where nothing crosses now: deny what an outside neighbour could start doing
    edges = [((x, y), d) for y in range(y0, y1 + 1) for x, d in ((x0, 1), (x1, 3))] + \
            [((x, y), d) for x in range(x0, x1 + 1) for y, d in ((y0, 0), (y1, 2))]
    for p, d in edges:
        key = ((W - 1 if d == 3 else p[0] - x0, p[1] - y0), d)
        if key in win.ports:
            continue
        cu = g.tiles.get(step(p, d))
        if cu is None:
            continue
        if opposite(d) in push_dirs(cu):
            win.ports[key] = 'deny_in'
        elif accepts(cu, d):
            win.ports[key] = 'deny_out'
    # tunnels passing over the window: no new ends of that direction under them
    for (p, q, k) in lf:
        if k == 'ug' and not inside(p) and not inside(q):
            d = int(g.tiles[p][1])
            n = max(abs(q[0] - p[0]), abs(q[1] - p[1]))
            for j in range(1, n):
                t = step(p, d, j)
                if inside(t):
                    if shrink:                  # positions shift: block the whole row
                        win.no_ug |= {((xx, t[1] - y0), d) for xx in range(W)}
                    else:
                        win.no_ug.add((local(t), d))
    return win, sub


def _splice(layout, win, res, x0, x1, y0=None, y1=None, shrink=0):
    """Replace the window's tiles by the solved ones; with a shrink, columns east of the window move west."""
    g = layout.design.tiles
    _, by0, _, by1 = layout.design.bbox()
    y0 = by0 if y0 is None else y0
    y1 = by1 if y1 is None else y1
    inside = lambda p: x0 <= p[0] <= x1 and y0 <= p[1] <= y1
    move = lambda p: (p[0] - shrink, p[1]) if p[0] > x1 else p
    out = {move(p): c for p, c in g.items() if not inside(p)}
    for (x, y), c in res['grid'].items():
        out[(x + x0, y + y0)] = c

    def lane(kind, i, p, d):
        where = win.io.get((kind, i))
        if where is None:
            return (move(p), d)
        if isinstance(where, int):              # one of two places: read which one the solver used
            ((lp, _),) = win.port_options[where][res['options'][where]].keys()
        else:
            lp = where[0]
        return ((lp[0] + x0, lp[1] + y0), d)
    ins = [lane('in', i, p, d) for i, (p, d) in enumerate(layout.inputs)]
    outs = [lane('out', i, p, d) for i, (p, d) in enumerate(layout.outputs)]
    return Layout(Design(out), ins, outs, layout.rates, layout.mix)


def straight(design, c):
    """Can column c be deleted as it is? Only belts moving west fed from the east (no side inputs) or empty tiles
    that would not join their neighbours."""
    g = design.tiles
    _, y0, _, y1 = design.bbox()
    for y in range(y0, y1 + 1):
        t, e, w = g.get((c, y)), g.get((c + 1, y)), g.get((c - 1, y))
        if t is None:
            if e and w and 1 in push_dirs(e) and accepts(w, 1):
                return False
            continue
        if t != 'B1':
            return False
        for d in (0, 2):
            n = g.get(step((c, y), d))
            if n and opposite(d) in push_dirs(n):
                return False
    return True


def delete_column(layout, c):
    move = lambda p: (p[0] - 1, p[1]) if p[0] > c else p
    d = Design({move(p): k for p, k in layout.design.tiles.items() if p[0] != c})
    return Layout(d, [(move(p), dd) for p, dd in layout.inputs], [(move(p), dd) for p, dd in layout.outputs],
                  layout.rates, layout.mix)


def clear_column(layout, c, band=6, half=3, timeout=10, workers=4):
    """Make column c deletable band by band (small windows around it), then delete it. Returns the new layout or
    None. Inputs/outputs in column c can't be moved this way."""
    lay = layout
    x0b, y0b, x1b, y1b = lay.design.bbox()
    a, b = max(x0b, c - half), min(x1b, c + half)
    if a == c or b == c:
        return None
    if any(p[0] == c for p, d in lay.inputs + lay.outputs):
        return None
    for y in range(y0b, y1b + 1, band):
        ye = min(y1b, y + band - 1)
        if all(lay.design.tiles.get((c, yy)) in (None, 'B1') for yy in range(y, ye + 1)) and \
                straight(Design({p: k for p, k in lay.design.tiles.items() if y - 1 <= p[1] <= ye + 1}), c):
            continue
        win, sub = cut_window(lay, a, b, y, ye)
        win.clear_col = c - a
        win.hint = {(p[0] - a, p[1] - y): k for p, k in sub.tiles.items()}
        r = model.solve(win, timeout=timeout, workers=workers)
        if 'grid' not in r:
            return None
        lay = _splice(lay, win, r, a, b, y, ye)
    if not straight(lay.design, c):
        return None
    new = delete_column(lay, c)
    new.design = prune(new.design, new.inputs, new.outputs, new.rates, new.mix)
    return new if new.check()['ok'] else None


def hint_without_column(sub, x0, x1, c):
    return {((x - x0) - (1 if x > c else 0), y): k for (x, y), k in sub.tiles.items() if x != c}


def strip_width(layout, cells):
    W, H = layout.design.size
    return max(3, min(W, cells // H))


def shrink_columns(layout, cells=120, timeout=20, workers=4, reporter=None, symmetry=None, view=None):
    """One pass over every strip position (strips of about `cells` tiles); returns the improved layout and how
    many columns were removed. view(layout) turns the layout back for reporting (row passes work turned)."""
    rep = reporter or Reporter()
    view = view or (lambda l: l)
    lay = layout.normalized()
    removed = 0
    x = lay.design.size[0] - 1
    while x >= 0 and lay.design.size[0] > 1:
        W0 = lay.design.size[0]
        width = strip_width(lay, cells)
        a = max(0, min(x - width // 2, W0 - width))
        b = min(W0 - 1, a + width - 1)
        win, sub = cut_window(lay, a, b, shrink=1, cut=x)
        win.symmetry = symmetry
        _, y0, _, _ = lay.design.bbox()
        win.hint = {(p[0], p[1] - y0): k for p, k in hint_without_column(sub, a, b, x).items()}
        r = model.solve(win, timeout=timeout, workers=workers)
        if 'grid' in r:
            new = _splice(lay, win, r, a, b, shrink=1)
            new.design = prune(new.design, new.inputs, new.outputs, new.rates, new.mix)
            rep_ = new.check()
            if rep_['ok']:
                lay = new.normalized()
                removed += 1
                v = view(lay).design
                rep.solution(v, *v.size, 'smaller')
                x = min(x, lay.design.size[0] - 1)
                continue
            rep.message('strip %d..%d: re-solved strip failed the full check, skipped (please report): %s'
                        % (a, b, {k: v for k, v in rep_.items() if k != 'shares'}))
        x -= 1
    return lay, removed


def clear_columns(layout, cells=36, timeout=10, workers=4, reporter=None, view=None):
    """One pass of band clearing over every column (for layouts too tall for full-height strips)."""
    rep = reporter or Reporter()
    view = view or (lambda l: l)
    lay = layout.normalized()
    half = 3
    band = max(3, cells // (2 * half + 1))
    removed = 0
    c = lay.design.size[0] - 2
    while c >= 1:
        new = clear_column(lay, c, band, half, timeout, workers)
        if new is not None:
            lay = new.normalized()
            removed += 1
            v = view(lay).design
            rep.solution(v, *v.size, 'smaller')
            c = min(c, lay.design.size[0] - 2)
            continue
        c -= 1
    return lay, removed


def shrink_pass(layout, cells, timeout, workers, rep, symmetry, view):
    """Full-height strips when they can be at least 5 wide, otherwise band clearing."""
    if strip_width(layout, cells) >= 5 or symmetry:
        return shrink_columns(layout, cells, timeout, workers, rep, symmetry, view)
    return clear_columns(layout, cells, timeout, workers, rep, view)


def polish(layout, cells=160, timeout=30, workers=4, reporter=None, symmetry=None):
    """Fewest pieces, then fewest turns, strip by strip (same size or smaller)."""
    rep = reporter or Reporter()
    lay = layout.normalized()
    W0 = lay.design.size[0]
    width = strip_width(lay, cells)
    starts = sorted(set(list(range(0, max(1, W0 - width + 1), max(1, width // 2))) + [max(0, W0 - width)]))
    for a in starts:
        if a >= lay.design.size[0]:
            break
        b = min(lay.design.size[0] - 1, a + width - 1)
        win, sub = cut_window(lay, a, b)
        win.symmetry = symmetry
        _, y0, _, _ = lay.design.bbox()
        win.hint = {(p[0] - a, p[1] - y0): k for p, k in sub.tiles.items()}
        r = model.solve(win, timeout=timeout, workers=workers, objective=True, turn_weight=1)
        if 'grid' in r and r['pieces'] <= len(sub):
            new = _splice(lay, win, r, a, b)
            if new.check()['ok'] and len(new.design) <= len(lay.design):
                if len(new.design) < len(lay.design):
                    rep.solution(new.design, *new.design.size, 'polished')
                lay = new.normalized()
    return lay


def improve(layout, cells=(36, 64, 120), timeout=10, rows=True, polish_timeout=30, workers=4, reporter=None,
            symmetry=None, time_limit=None):
    """Shrink columns and rows, polish, and repeat while anything gets smaller. Returns the new Layout.
    cells: strip sizes to use, smallest first; bigger strips (slower, see further) are tried only when the
    smaller ones find nothing, and each bigger one gets twice the time per try.
    symmetry 'y' keeps a top/bottom mirrored layout mirrored (then only columns are removed)."""
    rep = reporter or Reporter()
    t0 = time.time()
    lay = layout.normalized()
    lay.design = prune(lay.design, lay.inputs, lay.outputs, lay.rates, lay.mix)
    if not lay.check()['ok']:
        raise ValueError('the starting layout does not work: %s' % lay.check())
    if symmetry and not lay.design.is_symmetric(symmetry):
        raise ValueError('the starting layout is not symmetric')
    rep.solution(lay.design, *lay.design.size, 'start')
    turn_back = lambda l: l.rotated().rotated().rotated()
    cells = [cells] if isinstance(cells, int) else list(cells)
    level = 0
    while level < len(cells):
        c, to = cells[level], timeout * 2 ** level
        before = (lay.design.size[0] * lay.design.size[1], len(lay.design))
        lay, n = shrink_pass(lay, c, to, workers, rep, symmetry, None)
        m = 0
        if rows and not symmetry:           # a row strip of a mirrored layout has no mirror partner inside it
            r, m = shrink_pass(lay.rotated(), c, to, workers, rep, None, turn_back)
            lay = turn_back(r)
        lay = polish(lay, max(c, 64), polish_timeout, workers, rep, symmetry)
        after = (lay.design.size[0] * lay.design.size[1], len(lay.design))
        rep.message('strips of ~%d tiles: %d columns and %d rows removed, now %dx%d with %d pieces'
                    % ((c, n, m) + lay.design.size + (len(lay.design),)))
        if time_limit and time.time() - t0 > time_limit:
            break
        level = 0 if after < before and level > 0 else level + (after >= before)
    rep.solution(lay.design, *lay.design.size, 'best')
    return lay
