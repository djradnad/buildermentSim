"""Module solver: recipe + rate (+ box) -> feeding plan -> sim-checked shape options.

Feeding plan, from exact tick rates (tier 4 machines run 3x, a tier 4 arm moves 90/min, a belt carries 480/min):
  machines            = rate / (machine rate), rounded up
  arms per ingredient = amount * machine rate / 90, rounded up
  lane group          = how many machines one belt of the heaviest ingredient can feed (each group has its own inputs)
  collector           = how many machines one output belt can take

Shapes are built from the pieces that proved themselves in the game sim: hop lanes under machine rows (one or two
arms per lane), paired rows facing one collector, folded rows with U-turns, and two-lane shop columns.

    python tools/mega/modsolve.py "Gyroscope" 308.3            # item (add _ALT for the alternative recipe), per minute
    python tools/mega/modsolve.py "Gyroscope" 308.3 30 30      # only shapes that fit a 30x30 box (either way round)
"""
import json
import math
import os
import sys

sys.path.insert(0, '.')
sys.path.insert(0, 'tools/mega')
import lanemod
import modlib
import shopmod

RECIPES = {r['id']: r for r in modlib.gamedata()['recipes'].values()}
BID = {'factory_manufacturer': 3010, 'factory_industrial': 3008, 'factory_machine_shop': 3007}
CACHE = 'data/modsolve_cache.json'


def find(name):
    alt = name.endswith('_ALT')
    item = name.replace('_ALT', '').replace('_', ' ')
    r = [r for r in RECIPES.values() if r['out'][0]['item'] == item and r['alt'] == alt]
    return r[0] if r else None


def feeding(rec, rate):
    rate1 = 60.0 / rec['seconds'] * 3
    outa = rec['out'][0]['amount']
    crafts = rate / outa
    n = math.ceil(crafts / rate1 - 1e-6)
    arms = [math.ceil(i['amount'] * rate1 / 90 - 1e-9) for i in rec['in']]
    heavy = max(i['amount'] for i in rec['in']) * rate1
    return dict(rate1=rate1, crafts=crafts, machines=n, arms=arms, per_arm=[i['amount'] * rate1 for i in rec['in']],
                lane_group=max(1, int(480 / heavy + 1e-9)), collector=max(1, int(480 / (rate1 * outa) + 1e-9)),
                size=lanemod.rc.SIZE[BID[rec['building']]][0] if rec['building'] in BID else None)


def split(n, k):
    return [n // k + (1 if i < n % k else 0) for i in range(k)]


def options(rec, rate):
    """-> list of dict(kind, args, w, h, machines, groups): every shape this solver can build for the job"""
    f = feeding(rec, rate)
    if f['size'] is None:
        return f, []
    bid, n, s, L = BID[rec['building']], f['machines'], f['size'], len(rec['in'])
    out = []
    lanes_needed = sum(math.ceil(a / 2) for a in f['arms'])
    if lanes_needed > s:
        return f, []                    # needs more lanes than the machine is wide: no pattern yet
    two = tuple(i for i, a in enumerate(f['arms']) if a == 2)
    if max(f['arms']) > 2:
        return f, []
    gmin = math.ceil(n / f['lane_group'])
    if bid == 3007 and not two:
        for blocks in (1, 2, 3):
            if 2 * blocks > f['lane_group'] or 4 * blocks > f['collector']:
                continue
            cols = math.ceil(n / (2 * blocks))
            m = shopmod.build(rec['id'], blocks, cols)
            out.append(dict(kind='shop columns', args=[blocks, cols], w=m['w'], h=m['h'], machines=m['machines'], groups=cols))
    for groups in range(gmin, min(n, gmin + 3) + 1):
        per = split(n, groups)
        if 2 * max(per) <= f['collector']:
            m = lanemod.paired(bid, rec['id'], per, L, left=two)
            out.append(dict(kind='paired rows', args=[per], w=m['w'], h=m['h'], machines=n, groups=groups))
        if not two:
            for rows in (1, 2, 3):
                if rows > min(per):
                    continue
                share = rows > 1 and 2 * math.ceil(max(per) / rows) <= f['collector']
                if math.ceil(max(per) / rows) > f['collector']:
                    continue
                m = lanemod.stack([lanemod.build(bid, rec['id'], p, rows, L, share=share) for p in per])
                out.append(dict(kind='folded rows', args=[per, rows, share], w=m['w'], h=m['h'], machines=n, groups=groups))
    seen, uniq = set(), []
    for o in sorted(out, key=lambda o: (o['w'] * o['h'], abs(o['w'] - o['h']))):
        if (o['w'], o['h']) not in seen:
            seen.add((o['w'], o['h']))
            uniq.append(o)
    return f, uniq


def build(rec, o):
    bid, L = BID[rec['building']], len(rec['in'])
    f = feeding(rec, 1)
    two = tuple(i for i, a in enumerate(f['arms']) if a == 2)
    if o['kind'] == 'shop columns':
        return shopmod.build(rec['id'], *o['args'])
    if o['kind'] == 'paired rows':
        return lanemod.paired(bid, rec['id'], o['args'][0], L, left=two)
    per, rows, share = o['args']
    return lanemod.stack([lanemod.build(bid, rec['id'], p, rows, L, share=share) for p in per])


def verify(rec, rate, o):
    """game sim at the wanted rate (belts carry exactly what the plan gives); -> items made per minute"""
    key = '%d|%.3f|%s|%s' % (rec['id'], rate, o['kind'], json.dumps(o['args']))
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    if key not in cache:
        mod = build(rec, o)
        f = feeding(rec, rate)
        if o['kind'] == 'shop columns':
            per = [o['args'][0] * 2] * o['args'][1]
        else:
            per = o['args'][0]
        tot = sum(per)
        feeds = [(i['item'], i['amount'] * f['crafts'] * p / tot) for p in per for i in rec['in']]
        made, got, rep = lanemod.sim(mod, feeds)
        cache[key] = made                                # the sim counts items made, not crafts
        json.dump(cache, open(CACHE, 'w'), indent=0)
    return cache[key]


def solve(name, rate, box=None, check=True, log=print):
    rec = find(name)
    f, opts = options(rec, rate)
    log('%s at %.1f/min: %d machines (%.2f needed), arms per machine %s, one lane feeds %d machines, one collector takes %d' % (
        name, rate, f['machines'], f['crafts'] / f['rate1'],
        ', '.join('%s %d (%.0f/min)' % (i['item'], a, p) for i, a, p in zip(rec['in'], f['arms'], f['per_arm'])),
        f['lane_group'], f['collector']))
    if not opts:
        log('  no pattern yet for this feeding (needs more lanes or arms than the patterns have)')
        return []
    keep = []
    for o in opts:
        if box and not ((o['w'] <= box[0] and o['h'] <= box[1]) or (o['h'] <= box[0] and o['w'] <= box[1])):
            continue
        o['made'] = verify(rec, rate, o) if check else None
        keep.append(o)
        log('  %-13s %3dx%-3d = %5d tiles, %4.1f per machine, %2d machines in %2d groups%s' % (
            o['kind'], o['w'], o['h'], o['w'] * o['h'], o['w'] * o['h'] / o['machines'], o['machines'], o['groups'],
            '' if o['made'] is None else ', sim %.1f/min%s' % (o['made'], '' if o['made'] >= rate * 0.995 else '  SHORT')))
    return keep


def catalogue(path):
    """every core recipe of the plan at its plan rate -> a table of shape options"""
    import core
    lines = []
    for rn, (rec, crafts) in sorted(core.recipes().items()):
        if rec['building'] in BID:
            solve(rn, crafts * rec['out'][0]['amount'], log=lambda s: (lines.append(s), print(s, flush=True)))
    open(path, 'w').write('\n'.join(lines) + '\n')


if __name__ == '__main__':
    a = sys.argv[1:]
    if a[0] == 'all':
        catalogue(a[1])
        sys.exit()
    solve(a[0], float(a[1]), (int(a[2]), int(a[3])) if len(a) > 3 else None)
