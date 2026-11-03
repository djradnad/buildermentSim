"""A layout of belt pieces on a grid, plus file formats (text grid, blueprint rows) and a steady-state flow check."""
import json
from fractions import Fraction

from .pieces import (UG_REACH, BUILDING_ID, KIND_OF_ID, step, push_dirs, accepts, mirror_code,
                     opposite)

ARROWS = '^<v>'


class Design:
    """tiles: {(x, y): code}. Coordinates are free; most functions work on the bounding box."""

    def __init__(self, tiles=None):
        self.tiles = dict(tiles or {})

    # ------------------------------------------------------------------ basics
    def __len__(self):
        return len(self.tiles)

    def __eq__(self, other):
        return isinstance(other, Design) and self.normalized().tiles == other.normalized().tiles

    def copy(self):
        return Design(self.tiles)

    def bbox(self):
        """(x0, y0, x1, y1), inclusive."""
        xs = [x for x, y in self.tiles]
        ys = [y for x, y in self.tiles]
        return min(xs), min(ys), max(xs), max(ys)

    @property
    def size(self):
        """(width, height) of the bounding box."""
        x0, y0, x1, y1 = self.bbox()
        return x1 - x0 + 1, y1 - y0 + 1

    def count(self, kind):
        return sum(1 for c in self.tiles.values() if c[0] == kind)

    def shifted(self, dx, dy):
        return Design({(x + dx, y + dy): c for (x, y), c in self.tiles.items()})

    def normalized(self):
        """Moved so the bounding box starts at (0, 0)."""
        x0, y0, _, _ = self.bbox()
        return self.shifted(-x0, -y0)

    def mirrored(self, axis='y'):
        """Mirror image inside the bounding box: axis 'y' flips top/bottom, 'x' flips left/right."""
        x0, y0, x1, y1 = self.bbox()
        out = {}
        for (x, y), c in self.tiles.items():
            p = (x, y0 + y1 - y) if axis == 'y' else (x0 + x1 - x, y)
            out[p] = mirror_code(c, axis)
        return Design(out)

    def rotated(self, k=1):
        """Turned k quarter turns counter-clockwise (direction d becomes d + k), normalized."""
        out = {}
        for (x, y), c in self.tiles.items():
            for _ in range(k % 4):
                x, y = y, -x
            out[(x, y)] = c[0] + str((int(c[1]) + k) % 4)
        return Design(out).normalized()

    def is_symmetric(self, axis='y', ignore_middle=False):
        """True when the design equals its own mirror image. ignore_middle: the middle row (axis 'y') or column
        (axis 'x') may differ, and for an even size the two middle ones (quasi-symmetry)."""
        m = self.mirrored(axis).tiles
        x0, y0, x1, y1 = self.bbox()
        lo, hi = (y0, y1) if axis == 'y' else (x0, x1)
        mid = {(lo + hi) // 2, (lo + hi + 1) // 2} if ignore_middle else set()
        cells = set(m) | set(self.tiles)
        return all(self.tiles.get(p) == m.get(p) for p in cells if (p[1] if axis == 'y' else p[0]) not in mid)

    # ------------------------------------------------------------------ text grids
    def to_text(self, compact=True):
        """Text picture. compact: one character per tile (belts as arrows, S/I/O upper case when facing east or
        west, lower case when facing north or south). Otherwise two characters per tile, the exact codes."""
        x0, y0, x1, y1 = self.bbox()
        lines = []
        for y in range(y0, y1 + 1):
            row = []
            for x in range(x0, x1 + 1):
                c = self.tiles.get((x, y))
                if not compact:
                    row.append(c or '..')
                elif c is None:
                    row.append('.')
                elif c[0] == 'B':
                    row.append(ARROWS[int(c[1])])
                else:
                    row.append(c[0] if c[1] in '13' else c[0].lower())
            lines.append(''.join(row))
        return '\n'.join(lines)

    @classmethod
    def from_text(cls, text):
        """Reads the two-character format of to_text(compact=False): 'B1S3..' etc., one line per row."""
        tiles = {}
        for y, line in enumerate(l for l in text.strip('\n').split('\n')):
            line = line.strip()
            for i in range(0, len(line), 2):
                c = line[i:i + 2]
                if c != '..':
                    if c[0] not in 'BSIO' or c[1] not in '0123':
                        raise ValueError('bad piece %r at row %d' % (c, y))
                    tiles[(i // 2, y)] = c
        return cls(tiles)

    # ------------------------------------------------------------------ blueprints
    def blueprint_rows(self):
        """Rows [building id, x, y, rotation, tier, recipe, filter] as in community blueprint json files.
        Belt pieces use recipe -1 like blueprints made in the game."""
        return [[BUILDING_ID[c[0]], x, y, int(c[1]), 0, -1, 0] for (x, y), c in sorted(self.tiles.items())]

    def save(self, path, name='layout'):
        """Blueprint json {"meta": ..., "rows": ...} (.json), or the two-character text grid (.txt)."""
        if str(path).endswith('.txt'):
            with open(path, 'w') as f:
                f.write(self.to_text(compact=False) + '\n')
            return
        rows = self.blueprint_rows()
        with open(path, 'w') as f:
            json.dump({'meta': {'name': name, 'count': len(rows)}, 'rows': rows}, f)

    @classmethod
    def load(cls, path):
        """Blueprint json with rows (other buildings are ignored), a json {"grid": [[x, y, code], ...]}, or a text
        grid (.txt)."""
        if str(path).endswith('.txt'):
            with open(path) as f:
                return cls.from_text(f.read())
        with open(path) as f:
            d = json.load(f)
        if 'rows' in d:
            return cls({(r[1], r[2]): KIND_OF_ID[r[0]] + str(r[3]) for r in d['rows'] if r[0] in KIND_OF_ID})
        if 'grid' in d:
            return cls({(x, y): c for x, y, c in d['grid']})
        raise ValueError('unknown design file format: %s' % path)

    # ------------------------------------------------------------------ connections
    def links(self):
        """Every connection (p, q, kind): p pushes into q ('push'), or underground p reaches exit q ('ug')."""
        out = []
        for p, c in self.tiles.items():
            for d in push_dirs(c):
                q = step(p, d)
                if q in self.tiles and accepts(self.tiles[q], d):
                    out.append((p, q, 'push'))
            if c[0] == 'I':
                d = int(c[1])
                for n in range(1, UG_REACH + 1):
                    q = step(p, d, n)
                    if self.tiles.get(q) == 'O%d' % d:
                        out.append((p, q, 'ug'))
                        break
        return out

    def open_inputs(self):
        """Tiles where items can enter from outside: (tile, direction items move in). A tile counts when nothing
        inside the design pushes into it from that side and the neighbour there is empty."""
        res = []
        fed = {(q, p) for p, q, k in self.links() if k == 'push'}
        for p, c in self.tiles.items():
            for d in range(4):
                back = step(p, opposite(d))
                if accepts(c, d) and back not in self.tiles and (p, back) not in fed:
                    res.append((p, d))
        return res

    def open_outputs(self):
        """(tile, direction) pairs where a piece pushes out into an empty neighbour."""
        res = []
        for p, c in self.tiles.items():
            for d in push_dirs(c):
                if step(p, d) not in self.tiles:
                    res.append((p, d))
        return res


# ---------------------------------------------------------------------- steady-state flows
def flows(design, sources, sinks):
    """Exact steady-state flows (fractions) when every output is drained and nothing backs up.

    sources: {tile: [amount of each commodity entering there]}, sinks: set of (tile, direction) that drain.
    Belts pass everything on; a splitter shares equally between its outputs that are connected (a link or a
    sink). Pieces with no way out keep what they get (reported as stuck). Loops are solved exactly.
    Returns (inflow per tile, {(tile, direction): amounts leaving through each sink})."""
    tiles = design.tiles
    ncom = len(next(iter(sources.values())))
    outs = {p: [] for p in tiles}
    for p, q, kind in design.links():
        outs[p].append(q)
    for p, d in sinks:
        outs[p].append(('sink', p, d))
    order = sorted(tiles)
    idx = {p: i for i, p in enumerate(order)}
    n = len(order)
    # inflow x satisfies x = s + A x, A[q][p] = share of p's flow sent to q; solve (I - A) x = s exactly
    share = {p: (Fraction(1, len(outs[p])) if tiles[p][0] == 'S' else Fraction(1)) if outs[p] else Fraction(0)
             for p in order}
    rows = [{i: Fraction(1)} for i in range(n)]
    for p in order:
        for q in outs[p]:
            if q in idx:
                r = rows[idx[q]]
                r[idx[p]] = r.get(idx[p], 0) - share[p]
    rhs = [[Fraction(v) for v in sources.get(p, [0] * ncom)] for p in order]
    x = _solve(rows, rhs)
    inflow = {p: x[idx[p]] for p in order}
    drained = {}
    for p, d in sinks:
        if ('sink', p, d) in outs[p]:
            drained[(p, d)] = [v * share[p] for v in inflow[p]]
    return inflow, drained


def _solve(rows, rhs):
    """Sparse Gauss-Jordan elimination with fractions. rows: list of {column: coefficient}, rhs: list of vectors.
    A singular system (items circling in a loop with no exit) raises ValueError."""
    n = len(rows)
    rows = [dict(r) for r in rows]
    rhs = [list(v) for v in rhs]
    cols = [set() for _ in range(n)]          # cols[c] = rows with a nonzero in column c
    for i, r in enumerate(rows):
        for c in r:
            cols[c].add(i)
    done = [False] * n
    pivot_row = [None] * n
    for c in range(n):
        cand = [i for i in cols[c] if not done[i] and rows[i].get(c, 0) != 0]
        if not cand:
            raise ValueError('flow has no steady state (items circle in a loop with no exit)')
        i = min(cand, key=lambda k: len(rows[k]))
        done[i] = True
        pivot_row[c] = i
        pv = rows[i][c]
        if pv != 1:
            rows[i] = {k: v / pv for k, v in rows[i].items()}
            rhs[i] = [v / pv for v in rhs[i]]
        for j in list(cols[c]):
            if j == i:
                continue
            f = rows[j].get(c, 0)
            if f == 0:
                continue
            for k, v in rows[i].items():
                nv = rows[j].get(k, 0) - f * v
                if nv == 0:
                    rows[j].pop(k, None)
                    cols[k].discard(j)
                else:
                    rows[j][k] = nv
                    cols[k].add(j)
            rhs[j] = [a - f * b for a, b in zip(rhs[j], rhs[i])]
    return [rhs[pivot_row[c]] for c in range(n)]
