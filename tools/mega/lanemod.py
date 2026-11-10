"""Generator for john's hop-lane modules in any fold: n machines in `rows` serpentine rows.

One row (lanes travel east):   e MMMM < E e MMMM < E ...      e/E = underground entrance / exit of every lane,
                               e MMMM < E e MMMM < E ...      <   = arm taking from the exit, dropping in the machine
                               v collector row (machines push their product down onto it)
Every lane passes under every machine; the machine's arms take what the recipe needs, the rest flows on (the far
end of the last row is a flow-through exit). Rows alternate direction and are joined by nested U-turns.

    python tools/mega/lanemod.py 3010 2039 13 2 4      # building, recipe, machines, rows, lanes -> sim check
"""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, '.')
sys.path.insert(0, 'tools/mega')
import reroute_core as rc

B, S, I, O, ARM = 3000, 3001, 3002, 3003, 3012


def machine_row(bid, x, y, o, recipe, tier):
    """row for a machine whose top-left tile is (x, y), output side o"""
    fp = rc.footprint([bid, 0, 0, o])
    mx, my = min(c[0] for c in fp), min(c[1] for c in fp)
    return [bid, x - mx, y - my, o, tier - 1, recipe, 0]


def build(bid, recipe, n, rows, lanes, tier=4, arm_tier=4):
    """-> dict(rows, inputs [(x, y, d)], outputs [(x, y, d)], through [(x, y, d)], w, h)"""
    s = rc.SIZE[bid][0]
    assert lanes <= s
    P = s + 3
    per = [n // rows + (1 if k < n % rows else 0) for k in range(rows)]
    width = max(per) * P
    out, inputs, outputs, through = [], [], [], []
    prev_ends = None
    for k in range(rows):
        y0 = k * (s + 1)
        east = k % 2 == 0
        m = per[k]
        fx = (lambda x: x) if east else (lambda x: width - 1 - x)
        d = 3 if east else 1
        for j in range(m):
            base = j * P
            for i in range(lanes):
                out.append([I, fx(base), y0 + i, d, 0, -1, 0])
                out.append([ARM, fx(base + s + 1), y0 + i, 1 if east else 3, arm_tier - 1, -1, 0])
                out.append([O, fx(base + s + 2), y0 + i, d, 0, -1, 0])
            mx = base + 1 if east else width - 1 - (base + s)
            out.append(machine_row(bid, mx, y0, 2, recipe, tier))
        # collector under the machines, flowing back toward the row's start
        cd = 1 if east else 3
        xs = sorted(fx(x) for x in range(1, m * P - 2))
        for x in xs:
            out.append([B, x, y0 + s, cd, 0, -1, 0])
        outputs.append((xs[0] if east else xs[-1], y0 + s, cd))
        starts = [(fx(0), y0 + i) for i in range(lanes)]
        ends = [(fx(m * P - 1), y0 + i) for i in range(lanes)]
        if k == 0:
            inputs = [(x, y, d) for x, y in starts]
        else:
            # nested U-turn from the previous row's ends (outermost = its top lane) to this row's starts
            pd = 3 if not east else 1                       # direction the previous row travels
            for i, (ex, ey) in enumerate(prev_ends):
                col = (width + (lanes - 1 - i)) if pd == 3 else (-1 - (lanes - 1 - i))
                ty = y0 + (lanes - 1 - i)
                x = ex
                while x != col:
                    x += 1 if pd == 3 else -1
                    out.append([B, x, ey, pd if x != col else 2, 0, -1, 0])
                for y in range(ey + 1, ty + 1):
                    out.append([B, col, y, 2 if y != ty else d, 0, -1, 0])
                sx = starts[lanes - 1 - i][0]
                x = col
                while True:
                    x += 1 if d == 3 else -1
                    if x == sx:
                        break
                    out.append([B, x, ty, d, 0, -1, 0])
        prev_ends = ends
        last_d = d
    through = [(x, y, last_d) for x, y in prev_ends]
    xs = [r[1] for r in out]
    ys = [r[2] for r in out]
    cells = set()
    for r in out:
        cells.update(rc.footprint(r) if r[0] not in (B, S, I, O) else [(r[1], r[2])])
    cx = [c[0] for c in cells]
    cy = [c[1] for c in cells]
    return dict(rows=out, inputs=inputs, outputs=outputs, through=through,
                w=max(cx) - min(cx) + 1, h=max(cy) - min(cy) + 1, machines=n)


def stack(mods):
    """independent modules one under the other (each keeps its own inputs: a lane carries at most one belt, so
    a recipe whose heaviest ingredient needs more than 480/min is split into groups)"""
    out = dict(rows=[], inputs=[], outputs=[], through=[], machines=0, groups=len(mods))
    y = 0
    w = 0
    for m in mods:
        x0 = min(c for r in m['rows'] for c in ([r[1]] if r[0] in (B, S, I, O, ARM) else [p[0] for p in rc.footprint(r)]))
        sh = lambda t: (t[0] - x0, t[1] + y, t[2])
        out['rows'] += [[r[0], r[1] - x0, r[2] + y] + r[3:] for r in m['rows']]
        for k in ('inputs', 'outputs', 'through'):
            out[k] += [sh(t) for t in m[k]]
        out['machines'] += m['machines']
        y += m['h']
        w = max(w, m['w'])
    out['w'], out['h'] = w, y
    return out


def family(bid, recipe, n, groups, rows):
    per = [n // groups + (1 if k < n % groups else 0) for k in range(groups)]
    s = rc.SIZE[bid][0]
    return stack([build(bid, recipe, m, rows, s) for m in per])


def sim(mod, feeds, minutes=40, warmup=20):
    """feeds: one (item, per minute) per input lane. Flow-through ends are dead-ended (the last consumer)."""
    bs = [dict(id=r[0], x=r[1], y=r[2], o=r[3], tier=r[4] + 1, recipe=r[5] if r[5] > 0 else 0) for r in mod['rows']]
    fl, sinks = [], []
    from bmsolver.pieces import DIRS
    for (x, y, d), (item, rate) in zip(mod['inputs'], feeds):
        dx, dy = DIRS[d]
        for k in (1, 2):
            bs.append(dict(id=B, x=x - k * dx, y=y - k * dy, o=d))
        fl.append(dict(at=[x - 2 * dx, y - 2 * dy], kind=item, perMin=rate, buffer=10))
    for (x, y, d) in mod['outputs']:
        dx, dy = DIRS[d]
        for k in (1, 2):
            bs.append(dict(id=B, x=x + k * dx, y=y + k * dy, o=d))
        sinks.append([x + 2 * dx, y + 2 * dy])
    for (x, y, d) in mod['through']:                        # leftovers back up on a short dead-end belt
        dx, dy = DIRS[d]
        for k in (1, 2, 3):
            bs.append(dict(id=B, x=x + k * dx, y=y + k * dy, o=d))
    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
        json.dump(dict(buildings=bs, feeds=fl, warmup=warmup, minutes=minutes, sinks=sinks), f)
    try:
        rep = json.loads(subprocess.run(['node', 'tools/simlayout.mjs', f.name], capture_output=True, text=True, check=True).stdout)
    finally:
        os.unlink(f.name)
    made = sum(m['perMin'] for m in rep['machines'])
    got = sum(v for s in rep['sinks'].values() for v in s.values())
    return made, got, rep


if __name__ == '__main__':
    import modlib
    bid, recipe, n, groups, rows = map(int, sys.argv[1:6])
    rec = {r['id']: r for r in modlib.gamedata()['recipes'].values()}[recipe]
    mod = family(bid, recipe, n, groups, rows)
    per = 60.0 / rec['seconds'] * 3 * n                     # tier 4 machines run 3x
    feeds = [(i['item'], i['amount'] * per / groups) for g in range(groups) for i in rec['in']]
    made, got, rep = sim(mod, feeds)
    heavy = max(i['amount'] for i in rec['in']) * per / groups
    print('%s x%d, %d group(s) of %d row(s): %dx%d = %d tiles, wanted %.1f/min, sim makes %.2f (heaviest lane %.0f/min)' % (
        rec['out'][0]['item'], n, groups, rows, mod['w'], mod['h'], mod['w'] * mod['h'], per * rec['out'][0]['amount'], made, heavy), flush=True)
    json.dump({'rows': mod['rows']}, open(os.path.expanduser('~/data/lanemod_last.json'), 'w'))
