"""Dense module for 2x2 machine-shop recipes (every one has two ingredients), after john's nano wire build kz88k4.

Lanes run north, two per machine column, hopping under two stacked machines at a time:

      N N   N N      exits = flow-through ends
      v v   v v      arm: takes from the exit above, drops into the machine below
      m m ^ m m
      m m ^ m m      left machines push east, right machines push west onto the shared collector (^ or v)
      m m ^ m m
      m m ^ m m
      ^ ^ ^ ^ ^      arm: takes from the entrance below, drops into the machine above
      n n . n n      entrances (the first ones are the module inputs)

    python tools/mega/shopmod.py 2025 3 4 [down]     # recipe, blocks (2 machines each) per column, columns -> sim check
"""
import json
import os
import sys

sys.path.insert(0, '.')
sys.path.insert(0, 'tools/mega')
import lanemod

B, I, O, ARM, SHOP = 3000, 3002, 3003, 3012, 3007


def build(recipe, blocks, cols, down=False, tier=4, arm_tier=4, serp=1):
    """`cols` machine columns (two share a collector), each with `blocks` x 2 machines.
    serp > 1: runs of that many columns are chained into one serpentine (up, U-turn, down, U-turn, ...), so a run
    needs each ingredient once; collectors then leave at the bottom.
    -> ports {('in' | 'thru', run, ingredient index): (x, y, d)} besides the usual lists"""
    out, inputs, outputs, through, ports = [], [], [], [], {}
    H = 8 * blocks                                   # rows 0 .. H-1: top exit row is 0, first entrance row is H-1
    if serp > 1:
        down = True
    colx = []
    for p in range((cols + 1) // 2):
        x0 = 5 * p
        for side, mx in ((0, x0), (1, x0 + 3))[:cols - 2 * p]:
            c = len(colx)
            colx.append(mx)
            up = (c % serp) % 2 == 0                 # direction of this column's lanes
            for lane in (0, 1):
                x = mx + lane
                for b in range(blocks):
                    yb = H - 1 - 8 * b                # bottom row of this block
                    out.append([I if up else O, x, yb, 0 if up else 2, 0, -1, 0])
                    out.append([ARM, x, yb - 1, 0, arm_tier - 1, -1, 0])
                    out.append([ARM, x, yb - 6, 2, arm_tier - 1, -1, 0])
                    out.append([O if up else I, x, yb - 7, 0 if up else 2, 0, -1, 0])
            for b in range(blocks):
                yb = H - 1 - 8 * b
                for my in (yb - 3, yb - 5):
                    out.append(lanemod.machine_row(SHOP, mx, my, 3 if side == 0 else 1, recipe, tier))
        cx = x0 + 2
        cl_ = 2 * p                                   # left column of the pair: joined to the right one at the bottom?
        pdown = down
        if serp > 1:
            pdown = not (cl_ + 1 < cols and (cl_ % serp) % 2 == 1 and cl_ % serp != serp - 1)
        for y in range(1, H - 1):
            out.append([B, cx, y, 2 if pdown else 0, 0, -1, 0])
        if pdown:
            out.append([B, cx, H - 1, 2, 0, -1, 0])
            outputs.append((cx, H - 1, 2))
        else:
            out.append([B, cx, 0, 0, 0, -1, 0])
            outputs.append((cx, 0, 0))
    ymin, ymax = 0, H - 1
    for c, mx in enumerate(colx):
        run, k = c // serp, c % serp
        up = k % 2 == 0
        first = k == 0
        last = k == serp - 1 or c == len(colx) - 1
        items = (0, 1) if (k % 2 == 0) else (1, 0)   # every U-turn flips which lane carries which ingredient
        for lane in (0, 1):
            x = mx + lane
            if first:
                ports[('in', run, items[lane])] = (x, H - 1, 0)
            if last:
                ports[('thru', run, items[lane])] = (x, 0, 0) if up else (x, H - 1, 2)
        if not last:                                  # nested U-turn into the next column
            nx = colx[c + 1]
            for lane in (0, 1):
                xa, xb = mx + lane, nx + 1 - lane
                depth = 2 - lane                      # the left lane takes the outer track
                if up:
                    yy = -depth
                    for y in range(-1, yy, -1):
                        out.append([B, xa, y, 0, 0, -1, 0])
                    out.append([B, xa, yy, 3, 0, -1, 0])
                    for x in range(xa + 1, xb):
                        out.append([B, x, yy, 3, 0, -1, 0])
                    for y in range(yy, 0):
                        out.append([B, xb, y, 2, 0, -1, 0])
                    ymin = min(ymin, yy)
                else:
                    yy = H - 1 + depth
                    for y in range(H, yy):
                        out.append([B, xa, y, 2, 0, -1, 0])
                    out.append([B, xa, yy, 3, 0, -1, 0])
                    for x in range(xa + 1, xb):
                        out.append([B, x, yy, 3, 0, -1, 0])
                    for y in range(yy, H - 1, -1):
                        out.append([B, xb, y, 0, 0, -1, 0])
                    ymax = max(ymax, yy)
    for (kind, run, it), t in sorted(ports.items()):
        (inputs if kind == 'in' else through).append(t)
    nruns = -(-len(colx) // serp)
    return dict(rows=out, inputs=inputs, outputs=outputs, through=through, w=5 * (cols // 2) + 3 * (cols % 2),
                h=ymax - ymin + 1, machines=2 * blocks * cols, groups=nruns, ports=ports,
                run_cols=[min(serp, len(colx) - r * serp) for r in range(nruns)])


if __name__ == '__main__':
    import modlib
    recipe, blocks, cols = map(int, sys.argv[1:4])
    down = len(sys.argv) > 4
    rec = {r['id']: r for r in modlib.gamedata()['recipes'].values()}[recipe]
    mod = build(recipe, blocks, cols, down)
    n = mod['machines']
    per = 60.0 / rec['seconds'] * 3
    col = 2 * blocks                                  # machines on one lane
    feeds = [(rec['in'][k % 2]['item'], rec['in'][k % 2]['amount'] * per * col) for k in range(len(mod['inputs']))]
    made, got, rep = lanemod.sim(mod, feeds)
    print('%s x%d: %dx%d = %d tiles (%.1f per machine), wanted %.1f/min, sim makes %.2f, lanes %s' % (
        rec['out'][0]['item'], n, mod['w'], mod['h'], mod['w'] * mod['h'], mod['w'] * mod['h'] / n,
        per * n * rec['out'][0]['amount'], made, [round(f[1]) for f in feeds[:2]]), flush=True)
    print(sorted(set(round(m['perMin'], 2) for m in rep['machines'])))
    json.dump({'rows': mod['rows']}, open(os.path.expanduser('~/data/shopmod_last.json'), 'w'))
