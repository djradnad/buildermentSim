"""Unit modules for the raw-material ring: every recipe made in workshops, furnaces and forges.

Each unit takes at most one full belt per ingredient and is checked in the game sim.

  tree    john's dense 4-wide splitter tree (from rj9v5h): one belt split evenly over 4 one-tile machines.
          For machines that take more than an arm can carry (90/min). A chain of splitters in series throttles
          the belt; this tree passes all 480.
              . v S S <- feed          root S (right) halves the belt, each half is halved again
              v S S S
              M M M M
              < < < <   collector
  split2  the same idea for 2 machines per belt (Tungsten Ore takes 360/min).
  arms    one-tile machines taking up to 90/min: feed belt, arm, machine, collector (4 rows, any length).
          The feed passes every arm, the leftover flows on (dead end = the last consumer).
  forge   john's hop lanes (lanemod): one lane per ingredient under every forge, two arms per lane where one
          arm (90/min) is too slow.
  pour    forges whose heavy ingredient is over two arms (Concrete_ALT, 300 stone): the belt is split in two
          and poured straight into the forges, the light ingredient comes on a hop lane with arms.

    python3 tools/mega/rawmod.py            # sim check of one unit of every raw recipe
"""
import json
import math
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, '.')
sys.path.insert(0, 'tools/mega')
import lanemod
import reroute_core as rc
from bmsolver.pieces import DIRS

B, SP, UI, UO, ARM = 3000, 3001, 3002, 3003, 3012
BID = {'factory_workshop': 3005, 'factory_furnace': 3006, 'factory_forge': 3009}
ARM_RATE = 90.0
BELT = 480.0


def cap(rec):
    """crafts per minute of one tier 5 machine"""
    return 60.0 / rec['seconds'] * 3


def _unit(rows, inputs, outputs, through, machines, kind):
    cells = set()
    for r in rows:
        cells.update(rc.footprint(r) if r[0] not in (B, SP, UI, UO) else [(r[1], r[2])])
    xs = [c[0] for c in cells]
    ys = [c[1] for c in cells]
    return dict(rows=rows, inputs=inputs, outputs=outputs, through=through, machines=machines, kind=kind,
                w=max(xs) - min(xs) + 1, h=max(ys) - min(ys) + 1, tiles=len(cells))


def tree(bid, recipe, double=False, tier=4):
    """4 machines (8 when double: a second tree mirrored under the collector, with its own feed)"""
    rows, inputs = [], []
    for s in ((1, -1) if double else (1,)):
        d = 2 if s == 1 else 0                               # toward the collector at y = 0 (machines above it at y = -1)
        for x, y, i, o in ((1, 3, B, d), (2, 3, SP, 1), (3, 3, SP, 1), (0, 2, B, d), (1, 2, SP, d), (2, 2, SP, 1), (3, 2, SP, d)):
            rows.append([i, x, -s * y, o, 0, -1, 0])
        rows += [[bid, x, -s, d, tier - 1, recipe, 0] for x in range(4)]
        inputs.append((3, -s * 3, 1))
    rows += [[B, x, 0, 1, 0, -1, 0] for x in range(4)]
    return _unit(rows, inputs, [(0, 0, 1)], [], 8 if double else 4, 'tree')


def split2(bid, recipe, tier=4):
    """2 machines, half a belt each:   feed -> S -> M      S's side output drops into the second machine's belt
                                              v -> M"""
    rows = [[SP, 2, 0, 1, 0, -1, 0], [B, 2, 1, 1, 0, -1, 0],
            [bid, 1, 0, 1, tier - 1, recipe, 0], [bid, 1, 1, 1, tier - 1, recipe, 0],
            [B, 0, 0, 2, 0, -1, 0], [B, 0, 1, 2, 0, -1, 0]]
    return _unit(rows, [(2, 0, 1)], [(0, 1, 2)], [], 2, 'split2')


def arms(bid, recipe, n, tier=4, arm_tier=4):
    """n one-tile machines in a row; feed travels east on row 0, collector travels west on row 3"""
    rows = []
    for x in range(n):
        rows += [[B, x, 0, 3, 0, -1, 0], [ARM, x, 1, 2, arm_tier - 1, -1, 0], [bid, x, 2, 2, tier - 1, recipe, 0],
                 [B, x, 3, 1, 0, -1, 0]]
    return _unit(rows, [(0, 0, 3)], [(0, 3, 1)], [(n - 1, 0, 3)], n, 'arms')


def forge(recipe, n, rec, tier=4):
    left = tuple(i for i, ing in enumerate(rec['in']) if ing['amount'] * cap(rec) > ARM_RATE)
    m = lanemod.build(3009, recipe, n, 1, len(rec['in']), tier=tier, left=left)
    u = _unit(m['rows'], m['inputs'], m['outputs'], m['through'], n, 'forge')
    return u


def pour(recipe, rec, tier=4):
    """2 forges; ingredient 0 poured in from a split belt on the row above, ingredient 1 on a hop lane with arms"""
    m = lanemod.build(3009, recipe, 2, 1, 1, tier=tier)     # lane on row 0, forges at x 1-2 and 6-7, rows 0-1
    rows = m['rows'] + [[B, 1, -1, 3, 0, -1, 0], [SP, 2, -1, 3, 0, -1, 0], [B, 3, -1, 3, 0, -1, 0], [B, 4, -1, 3, 0, -1, 0],
                        [B, 5, -1, 3, 0, -1, 0], [B, 6, -1, 2, 0, -1, 0]]
    return _unit(rows, [(1, -1, 3)] + m['inputs'], m['outputs'], m['through'], 2, 'pour')


def unit(name, rec):
    """the standard unit of a raw recipe -> (unit, feed per input [(item, per minute)], product per minute)"""
    bid = BID[rec['building']]
    c = cap(rec)
    take = [ing['amount'] * c for ing in rec['in']]
    out = rec['out'][0]['amount']
    if bid == 3009:
        if max(take) > 2 * ARM_RATE:
            u = pour(rec['id'], rec)
            n = 2
        else:
            n = max(1, min(8, int(math.ceil(BELT / max(take) - 1e-9))))    # 3.56 forges per belt: 4 run at 89%
            u = forge(rec['id'], n, rec)
    elif max(take) > BELT / 2 - 1e-9 and max(take) > ARM_RATE:
        u, n = split2(bid, rec['id']), 2
    elif max(take) > ARM_RATE:
        u, n = tree(bid, rec['id']), 4
    else:
        n = int(math.ceil(BELT / take[0] - 1e-9))
        n = min(n, 12, max(1, int(BELT / (c * out) + 1e-9)))      # the collector carries one belt
        u = arms(bid, rec['id'], n)
    feeds = [(ing['item'], min(BELT, t * n)) for ing, t in zip(rec['in'], take)]
    made = min(f[1] / t for f, t in zip(feeds, take)) * c * out
    return u, feeds, made


def sim(u, feeds, minutes=60, warmup=30):
    made, got, rep = lanemod.sim(u, feeds, minutes=minutes, warmup=warmup)
    return made, got, rep


def main():
    import core
    R = core.recipes()
    tiles = 0
    bad = []
    for name, (rec, crafts) in R.items():
        if rec['building'] not in BID:
            continue
        u, feeds, want = unit(name, rec)
        made, got, rep = sim(u, feeds)
        need = crafts / cap(rec)                             # machines the plan needs at 100%
        per = want / rec['out'][0]['amount'] / cap(rec)      # machines-worth one unit delivers
        units = int(math.ceil(need / per - 1e-6))
        tiles += units * u['tiles']
        ok = abs(got - want) < 0.005 * want
        if not ok:
            bad.append(name)
        print('%-22s %-6s %2d machines %2dx%-2d %3d tiles | in %s | want %.1f sim %.1f %s | plan %.1f machines = %d units, %d tiles' % (
            name, u['kind'], u['machines'], u['w'], u['h'], u['tiles'],
            ', '.join('%s %.0f' % f for f in feeds), want, got, 'ok' if ok else 'UNDER', need, units, units * u['tiles']), flush=True)
    print('all units: %d tiles' % tiles)
    print('VERDICT rawmod: ' + ('every unit at plan' if not bad else 'under plan: ' + ', '.join(bad)))


if __name__ == '__main__':
    main()
