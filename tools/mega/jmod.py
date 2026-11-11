"""john's ring modules as typed, sim-checked blocks.

A decoded blueprint (data/bp/<code>.json) becomes a block with: every input lane and the item it must carry
(found by walking each lane to the machines it reaches and giving every machine all its ingredients), every
exit, and a game-sim run with all lanes fed that reports what each recipe makes against its machine capacity.

    python3 tools/mega/jmod.py srb44l 7b7vfm ...          # table per module
    python3 tools/mega/jmod.py 7b7vfm 2010=2011,2014=2012  # same, with recipes swapped (old id = new id)
"""
import collections
import json
import subprocess
import sys
import tempfile

sys.path.insert(0, '.')
sys.path.insert(0, 'tools/mega')
import modlib
import reroute_core as rc
from bmsolver.design import Design
from bmsolver.pieces import KIND_OF_ID, DIRS, step, opposite

B, ARM = 3000, 3012
OUTS = []
_REC = None


def recipes():
    global _REC
    if _REC is None:
        _REC = {r['id']: r for r in modlib.gamedata()['recipes'].values()}
    return _REC


def load(code, swap=None):
    rows = [list(r) for r in json.load(open('data/bp/%s.json' % code))['rows']]
    for r in rows:
        if swap and r[5] in swap:
            r[5] = swap[r[5]]
    return rows


def reach(rows):
    """-> (inputs [(tile, d, kind)], machines [row], {input index: set of machine indexes it feeds directly})"""
    rec = recipes()
    m = modlib.module('x', rows, rec)
    cells = set()
    for r in rows:
        cells.update([(r[1], r[2])] if r[0] in KIND_OF_ID else rc.footprint(r))
    x0, y0 = min(c[0] for c in cells), min(c[1] for c in cells)
    ins = [((x + x0, y + y0), d, k) for x, y, d, k in m['inputs']]
    global OUTS
    OUTS = [((x + x0, y + y0), d) for x, y, d in m['outputs']]
    belts = {(r[1], r[2]): KIND_OF_ID[r[0]] + str(r[3]) for r in rows if r[0] in KIND_OF_ID}
    des = Design(belts)
    nxt = collections.defaultdict(list)
    for p, q, k in des.links():
        nxt[p].append(q)
    machines = [r for r in rows if r[0] not in KIND_OF_ID and r[0] != ARM and r[5] in rec]
    owner = {}
    for i, r in enumerate(machines):
        for c in rc.footprint(r):
            owner[c] = i
    arms = collections.defaultdict(list)                    # tile an arm takes from -> machine it fills
    lays = set()                                            # tiles where an arm lays a machine's product
    for r in rows:
        if r[0] == ARM:
            src, dst = step((r[1], r[2]), opposite(r[3])), step((r[1], r[2]), r[3])
            if dst in owner:
                arms[src].append(owner[dst])
            if src in owner:
                lays.add(dst)
    got = {}
    for i, (p, d, k) in enumerate(ins):
        seen, todo, ms = {p}, [p], set()
        while todo:
            t = todo.pop()
            ms.update(arms.get(t, ()))
            c = belts[t]
            if c[0] in 'BO':
                q = step(t, int(c[1]))
                if q in owner:
                    ms.add(owner[q])
            elif c[0] == 'S':
                for dd in range(4):
                    q = step(t, dd)
                    if q in owner and dd != (int(c[1]) + 2) % 4:
                        ms.add(owner[q])
            for q in nxt[t]:
                if q not in seen and q not in lays:
                    seen.add(q)
                    todo.append(q)
        got[i] = ms
    return ins, machines, got


def assign(rows):
    """item for every input lane: every machine must find each outside ingredient on a lane that reaches it"""
    rec = recipes()
    ins, machines, got = reach(rows)
    made = {rec[r[5]]['out'][0]['item'] for r in machines}
    lanes_of = collections.defaultdict(set)                 # machine -> lanes
    for i, ms in got.items():
        for m in ms:
            lanes_of[m].add(i)
    item = {}
    # machines with the fewest candidate lanes first; a lane already typed keeps its item
    order = sorted(lanes_of, key=lambda m: len(lanes_of[m]))
    for m in order:
        need = [g['item'] for g in rec[machines[m][5]]['in'] if g['item'] not in made]
        lanes = sorted(lanes_of[m])
        have = {item[l] for l in lanes if l in item}
        free = [l for l in lanes if l not in item]
        for it in need:
            if it in have:
                continue
            if not free:
                break
            item[free.pop(0)] = it
            have.add(it)
    for i in range(len(ins)):
        if i not in item:                                   # extra lanes of one group: same item as a neighbour's need
            ms = got[i]
            need = [g['item'] for m in ms for g in rec[machines[m][5]]['in'] if g['item'] not in made]
            item[i] = collections.Counter(need).most_common(1)[0][0] if need else None
    return ins, machines, item


def sim(rows, ins, item, minutes=60, warmup=40, rate=480, closed=()):
    bs = [dict(id=r[0], x=r[1], y=r[2], o=r[3], tier=r[4] + 1, recipe=r[5] if r[5] > 0 else 0) for r in rows]
    used = set()
    for r in rows:
        used.update([(r[1], r[2])] if r[0] in KIND_OF_ID or r[0] == ARM else rc.footprint(r))
    feeds = []
    for i, (p, d, k) in enumerate(ins):
        if item.get(i) is None:
            continue
        dx, dy = DIRS[d]
        at = p
        for n in (1, 2):
            q = (p[0] - n * dx, p[1] - n * dy)
            if q in used:
                break
            bs.append(dict(id=B, x=q[0], y=q[1], o=d, tier=1, recipe=0))
            used.add(q)
            at = q
        feeds.append(dict(at=list(at), kind=item[i], perMin=rate, buffer=10))
    sinks = []
    for p, d in OUTS:                                       # every exit drains (two belts out, then a sink)
        if (p, d) in closed:
            continue
        dx, dy = DIRS[d]
        at = None
        for n in (1, 2):
            q = (p[0] + n * dx, p[1] + n * dy)
            if q in used:
                break
            bs.append(dict(id=B, x=q[0], y=q[1], o=d, tier=1, recipe=0))
            used.add(q)
            at = q
        if at:
            sinks.append(list(at))
    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
        json.dump(dict(buildings=bs, feeds=feeds, warmup=warmup, minutes=minutes, sinks=sinks), f)
    return json.loads(subprocess.run(['node', 'tools/simlayout.mjs', f.name], capture_output=True, text=True, check=True).stdout)


def report(code, swap=None, minutes=60, warmup=40):
    rec = recipes()
    rows = load(code, swap)
    ins, machines, item = assign(rows)
    rep = sim(rows, ins, item, 10, 10)
    raw = set(item.values())
    closed = set()                                          # exits that only let unused input through: dead ends
    for p, d in OUTS:
        dx, dy = DIRS[d]
        for n in (2, 1):
            got = rep['sinks'].get('%d,%d' % (p[0] + n * dx, p[1] + n * dy))
            if got is not None:
                if not got or all(k in raw for k in got):
                    closed.add((p, d))
                break
    rep = sim(rows, ins, item, minutes, warmup, closed=closed)
    by = collections.defaultdict(lambda: [0, 0.0, 0.0])
    for m in rep['machines']:
        r = rec.get(m['recipe'])
        if not r:
            continue
        name = r['out'][0]['item'] + (' ALT' if r.get('alt') else '')
        tier = next((x[4] for x in machines if (x[1], x[2]) == (m['x'], m['y'])), 3)
        capm = 60.0 / r['seconds'] * {0: 1, 1: 1.5, 2: 2, 3: 3, 4: 3}.get(tier, 3) * r['out'][0]['amount']
        by[name][0] += 1
        by[name][1] += m['perMin']
        by[name][2] += capm
    lanes = collections.Counter(item.values())
    fed = collections.defaultdict(float)
    for f in rep['fedRates']:
        fed[f['kind']] += f['perMin']
    out = collections.defaultdict(float)
    for s in rep['sinks'].values():
        for k, v in s.items():
            out[k] += v
    print('%s: lanes %s' % (code, ', '.join('%d %s (%.2f belts taken)' % (n, k, fed[k] / 480) for k, n in lanes.items())))
    for name, (n, got, capm) in by.items():
        print('   %-18s %4d machines make %8.1f/min = %5.1f%% of their capacity' % (name, n, got, 100 * got / capm))
    print('   leaves: ' + ', '.join('%s %.1f/min (%.2f belts)' % (k, v, v / 480) for k, v in sorted(out.items())))
    return rep


if __name__ == '__main__':
    swap = None
    for a in sys.argv[1:]:
        if '=' in a:
            swap = {int(p.split('=')[0]): int(p.split('=')[1]) for p in a.split(',')}
    for a in sys.argv[1:]:
        if '=' not in a:
            report(a, swap)
