"""Clean up a working layout without changing its box, in this order:

1. footprint: empty tiles on the outside of the layout (corners and edges), so the real footprint (the orthogonal
   hull of its pieces) gets smaller, e.g. a 7x7 that only covers 48 tiles;
2. pieces: fewest pieces;
3. edge splitters: no splitter facing out of the layout's edge where there is no output (in game it pushes nothing
   there, but it looks like one more output);
4. gold: the lowest build cost (belt 4, splitter 10, each underground end 10), which also prefers belts over
   splitters and undergrounds that do nothing.

Each step re-solves small windows exactly (every flow crossing a window's edges is kept, see improve.cut_window),
with the tiles outside the footprint kept empty, and keeps a change only when the whole layout still checks out.

    python -m bmsolver.cleanup layout.txt          # a text grid, inputs east, outputs west
"""
import sys
import time

from . import model
from .check import prune
from .design import Design
from .improve import Layout, cut_window, _splice
from .pieces import step, push_dirs
from .search import Reporter

GOLD = {'B': 4, 'S': 10, 'I': 10, 'O': 10}          # game data: gold to build each piece
WEIGHTS = {k: 100 + v for k, v in GOLD.items()}      # solver objective: fewest pieces, then least gold


def hull(cells):
    """Orthogonal hull: the cells plus every cell between two hull cells in the same row or column."""
    h = set(cells)
    while True:
        rows, cols = {}, {}
        for x, y in h:
            rows.setdefault(y, []).append(x)
            cols.setdefault(x, []).append(y)
        add = {(x, y) for y, xs in rows.items() for x in range(min(xs), max(xs) + 1)} | \
              {(x, y) for x, ys in cols.items() for y in range(min(ys), max(ys) + 1)}
        if add <= h:
            return h
        h |= add


def footprint(design):
    return len(hull(design.tiles))


def port_edges(layout):
    return {(p, d) for p, d in layout.outputs} | {(p, (d + 2) % 4) for p, d in layout.inputs}


def edge_splitters(layout):
    """Splitter faces that point out of the footprint where there is no output."""
    g, h, pe = layout.design.tiles, hull(layout.design.tiles), port_edges(layout)
    n = 0
    for p, c in g.items():
        if c[0] != 'S':
            continue
        for d in push_dirs(c):
            if step(p, d) not in h and (p, d) not in pe:
                n += 1
    return n


def gold(design):
    return sum(GOLD[c[0]] for c in design.tiles.values())


def score(layout):
    """Same box assumed: footprint, then pieces, then edge splitters, then gold (smaller is better)."""
    return (footprint(layout.design), len(layout.design), edge_splitters(layout), gold(layout.design))


def _window(lay, x0, x1, y0, y1, extra_blocked=(), closed=True):
    """cut_window plus: tiles outside the footprint (and extra_blocked) stay empty, and with closed=True no
    splitter faces out of the footprint where the layout has no port."""
    win, sub = cut_window(lay, x0, x1, y0, y1)
    h = hull(lay.design.tiles) - set(extra_blocked)
    W, H = x1 - x0 + 1, y1 - y0 + 1
    loc = lambda p: (p[0] - x0, p[1] - y0)
    win.blocked = {loc((x, y)) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1) if (x, y) not in h}
    if closed:
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                if (x, y) not in h:
                    continue
                for d in range(4):
                    q = step((x, y), d)
                    if q in h:
                        continue            # a neighbour inside the footprint: spilling there is allowed
                    lp = loc((x, y))
                    inside_win = x0 <= q[0] <= x1 and y0 <= q[1] <= y1
                    if not inside_win and not (lp[0] in (0, W - 1) or lp[1] in (0, H - 1)):
                        continue
                    key = (lp, d)
                    if key not in win.ports:
                        win.ports[key] = 'deny_out'
    win.hint = {loc(p): k for p, k in sub.tiles.items()}
    return win, sub


def _try(lay, box, extra_blocked, timeout, workers, closed, objective=True):
    x0, x1, y0, y1 = box
    try:
        win, sub = _window(lay, x0, x1, y0, y1, extra_blocked, closed)
    except RuntimeError:
        return None
    r = model.solve(win, timeout=timeout, workers=workers, objective=objective, weights=WEIGHTS)
    if 'grid' not in r:
        return None
    new = _splice(lay, win, r, x0, x1, y0, y1)
    new.design = prune(new.design, new.inputs, new.outputs, new.rates, new.mix)
    if not new.check()['ok']:
        return None
    return new


def _boxes(W, H, size, stride):
    xs = sorted(set(list(range(0, max(1, W - size + 1), stride)) + [max(0, W - size)]))
    ys = sorted(set(list(range(0, max(1, H - size + 1), stride)) + [max(0, H - size)]))
    return [(x, min(W - 1, x + size - 1), y, min(H - 1, y + size - 1)) for y in ys for x in xs]


def cleanup(layout, size=5, timeout=10, workers=4, reporter=None, time_limit=600):
    rep = reporter or Reporter()
    t0 = time.time()
    lay = layout.normalized()
    lay.design = prune(lay.design, lay.inputs, lay.outputs, lay.rates, lay.mix)
    W, H = lay.design.size
    rep.message('start %dx%d: footprint %d, %d pieces, %d edge splitters, %d gold' % ((W, H) + score(lay)))
    port_tiles = {p for p, d in lay.inputs} | {p for p, d in lay.outputs}

    # 1. footprint: try to empty each outer tile of the hull, corners first
    better = True
    while better and time.time() - t0 < time_limit:
        better = False
        h = hull(lay.design.tiles)
        outer = [p for p in h if p not in port_tiles and any(step(p, d) not in h for d in range(4))]
        corner = lambda p: min(abs(p[0] - cx) + abs(p[1] - cy) for cx in (0, W - 1) for cy in (0, H - 1))
        for p in sorted(outer, key=lambda p: (corner(p), p)):
            for k in (size, size + 2, size + 4):          # bigger windows only when a small one can't do it
                if time.time() - t0 > time_limit:
                    break
                bx = max(0, min(p[0] - k // 2, W - k))
                by = max(0, min(p[1] - k // 2, H - k))
                b = (bx, min(W - 1, bx + k - 1), by, min(H - 1, by + k - 1))
                new = _try(lay, b, [p], timeout * (k - size + 1), workers, closed=False, objective=False)
                if new and new.design.size == (W, H) and footprint(new.design) < footprint(lay.design):
                    lay = new
                    rep.solution(lay.design, W, H, 'footprint %d' % footprint(lay.design))
                    better = True
                    break
            if better:
                break

    # 2. pieces, 3. edge splitters: windows all over, keeping the footprint; a window may also close its edges
    for closed in (False, True):
        better = True
        while better and time.time() - t0 < time_limit:
            better = False
            for b in _boxes(W, H, size, max(1, size // 2)):
                if time.time() - t0 > time_limit:
                    break
                new = _try(lay, b, (), timeout, workers, closed)
                if new and new.design.size == (W, H) and score(new) < score(lay):
                    lay = new
                    rep.solution(lay.design, W, H, 'pieces %d, edge splitters %d, gold %d' % score(lay)[1:])
                    better = True
    rep.message('done %dx%d: footprint %d, %d pieces, %d edge splitters, %d gold' % ((W, H) + score(lay)))
    return lay


def main(argv):
    from .check import find_ports
    d = Design.from_text(open(argv[0]).read()).normalized()
    ins, outs = find_ports(d, ('E',), ('W',))
    lay = cleanup(Layout(d, ins, outs))
    print(lay.design.to_text(compact=False))


if __name__ == '__main__':
    main(sys.argv[1:])
