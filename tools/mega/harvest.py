"""Everything outside the mega factory on the map of the seed: an extractor on every used resource node, the
extractors grouped into belts (two boosted ones fill a belt: 2 x 240 = 480, sim-checked for any side-loading
arrangement), and every belt routed to the edge of the factory area john marked (data/mega_site.json).

    node tools/mega/world_nodes.mjs 2CCJhI > nodes.json ; node tools/mega/world_cover.mjs nodes.json > cover.json
    python3 -u tools/mega/harvest.py cover.json out_prefix [seconds]

Groups: per item, nodes are paired with their nearest free neighbour until a belt is full (480/min).  The first
extractor of a group starts the belt, the others side-load onto it (router `join`).  Where a belt enters the
factory edge is left to the router (all edge slots are interchangeable) until the ring of raw-material modules
exists; then the slots get items.  Node tiles and plants are blocked; hops pass under them.
Wolframite and uranium are in surplus on this seed: only the nodes nearest to the factory are used, up to the
plan's need (data/plan.json)."""
import json, math, os, pickle, sys, time, collections
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))
from bmsolver import negotiate
from bmsolver.pieces import step

HERE = os.path.dirname(os.path.abspath(__file__))
BELT = 480.0
SLOT_EVERY = 3                              # edge slots: two of every three tiles of the factory edge (the third stays closed)
RATE = {True: 240.0, False: 150.0}          # Gen 2 tier 5 extractor, boosted +60% or not
URANIUM = 50.0                              # Gen 2 uranium extractor
BASE = ('Wood Log', 'Stone', 'Iron Ore', 'Copper Ore', 'Coal')


def groups_for(nodes, per_belt_need=None):
    """[(item, [node...], rate)]: nodes = [(x, y, item, rate)] of one item, grouped greedily by nearness"""
    left = sorted(nodes)
    out = []
    while left:
        seed = max(left, key=lambda n: min([abs(n[0] - m[0]) + abs(n[1] - m[1]) for m in left if m is not n] or [0]))
        left.remove(seed)                   # the loneliest node first: it picks its nearest partners
        grp, tot = [seed], seed[3]
        while left:
            nx = min(left, key=lambda m: min(abs(g[0] - m[0]) + abs(g[1] - m[1]) for g in grp))
            if tot + nx[3] > BELT + 1e-6:
                break
            left.remove(nx); grp.append(nx); tot += nx[3]
        out.append((seed[2], grp, tot))
    return out


def plan_groups(cover, site, plan):
    L = site['loop']
    cx, cy = (L['x0'] + L['x1']) / 2.0, (L['y0'] + L['y1']) / 2.0
    far = lambda n: abs(n[0] - cx) + abs(n[1] - cy)
    by = collections.defaultdict(list)
    for x, y, t, b in cover['nodes']:
        by[t].append((x, y, t, URANIUM if t == 'Uranium Ore' else RATE[bool(b)]))
    groups = []
    for t, ns in by.items():
        if t not in BASE:                   # surplus items: the nearest nodes up to the need
            need = plan[t]['belts'] * BELT
            ns = sorted(ns, key=far)
            take, tot = [], 0.0
            for n in ns:
                if tot >= need:
                    break
                take.append(n); tot += n[3]
            ns = take
        groups += groups_for(ns)
    return groups


def build(cover, site, plan, margin=12, ban=()):
    nodes = cover['nodes']
    L = site['loop']
    xs = [n[0] for n in nodes] + [L['x0'], L['x1']]; ys = [n[1] for n in nodes] + [L['y0'], L['y1']]
    gx, gy = min(xs) - margin, min(ys) - margin
    W, H = max(xs) + margin - gx + 1, max(ys) + margin - gy + 1
    T = lambda x, y: (x - gx, y - gy)
    blocked = {T(n[0], n[1]) for n in nodes}
    for p in cover['plants']:
        b = p['box']                        # box = footprint grown by the radius 9 (edges at .4998)
        for x in range(int(math.ceil(b[0] + 9)), int(math.floor(b[2] - 9)) + 1):
            for y in range(int(math.ceil(b[1] + 9)), int(math.floor(b[3] - 9)) + 1):
                blocked.add(T(x, y))
    for x in range(L['x0'] + 1, L['x1']):   # the factory itself: nothing of the outside goes in
        for y in range(L['y0'] + 1, L['y1']):
            blocked.add(T(x, y))
    slots = []                              # (tile on the loop line, direction pushing inward)
    for x in [x for x in range(L['x0'] + 2, L['x1'] - 1) if (x - L['x0']) % SLOT_EVERY]:
        slots += [(T(x, L['y0']), 2), (T(x, L['y1']), 0)]
    for y in [y for y in range(L['y0'] + 2, L['y1'] - 1) if (y - L['y0']) % SLOT_EVERY]:
        slots += [(T(L['x0'], y), 3), (T(L['x1'], y), 1)]
    for x in range(L['x0'], L['x1'] + 1):   # the rest of the line is closed, so belts cannot run along the wall
        for y in (L['y0'], L['y1']):
            blocked.add(T(x, y))
    for y in range(L['y0'], L['y1'] + 1):
        for x in (L['x0'], L['x1']):
            blocked.add(T(x, y))
    for s, d in slots:
        blocked.discard(s)
    groups = plan_groups(cover, site, plan)
    cx, cy = T((L['x0'] + L['x1']) // 2, (L['y0'] + L['y1']) // 2)
    # Each extractor pushes into a free tile next to its node.  Two extractors of a group that can reach a common
    # tile share it (both push into the same belt start).  Otherwise the first starts the belt and the others
    # side-load onto it (router join).  Nodes with the fewest free sides choose first; `ban` holds (node tile,
    # facing) choices that left a belt without any route in an earlier attempt.
    member = {}
    for gi, (item, grp, tot) in enumerate(groups):
        for n in grp:
            member[T(n[0], n[1])] = (gi, n)
    taken = {}                              # out tile -> group using it
    facing = {}
    todo = set(member)
    slotset = {s_[0] for s_ in slots}
    opts_of = lambda p: [d for d in range(4) if step(p, d) not in blocked and step(p, d) not in slotset and (p, d) not in ban
                         and taken.get(step(p, d), member[p][0]) == member[p][0]]
    while todo:
        p = min(todo, key=lambda p: (len(opts_of(p)), p))
        opts = opts_of(p)
        if not opts:
            raise SystemExit('node %s has no free side' % (member[p][1],))
        gi = member[p][0]
        mates = [q for q in member if member[q][0] == gi and q != p]

        def score(d):
            q = step(p, d)
            shared = taken.get(q) == gi or any(abs(q[0] - m[0]) + abs(q[1] - m[1]) == 1 and m in todo
                                               and (m, [e for e in range(4) if step(m, e) == q][0]) not in ban for m in mates)
            steals = sum(step(o, e) == q for o in todo if member[o][0] != gi for e in range(4))
            room = sum(step(q, e) not in blocked and step(q, e) not in taken and step(q, e) not in member for e in range(4))
            return (room == 0, not shared, steals, -min(room, 2), abs(q[0] - cx) + abs(q[1] - cy))   # room: ways on from q
        d = min(opts, key=score)
        facing[p] = d
        taken[step(p, d)] = gi
        todo.discard(p)
    sources, joins, ext, info = [], {}, [], []
    for gi, (item, grp, tot) in enumerate(groups):
        tiles = {}                          # out tile -> net
        first = None
        for p in sorted((p for p in member if member[p][0] == gi), key=lambda p: abs(p[0] - cx) + abs(p[1] - cy)):
            d, n = facing[p], member[p][1]
            q = step(p, d)
            if q not in tiles:
                tiles[q] = len(sources)
                sources.append((q, d, tiles[q]))
                if first is None:
                    first = tiles[q]
                    info.append((first, item, tot, len(grp)))
                else:
                    joins[tiles[q]] = first
            ext.append((p, d, n[2], n[3], tiles[q]))
    local = {}
    return dict(W=W, H=H, off=(gx, gy), blocked=blocked, slots=slots, sources=sources, joins=joins, ext=ext, info=info, local=local)


def run(cover_path, out, seconds=600):
    cover = json.load(open(cover_path))
    site = json.load(open(os.path.join(HERE, '../../data/mega_site.json')))
    plan = json.load(open(os.path.join(HERE, '../../data/plan.json')))
    ban = set()
    for attempt in range(60):
        B = build(cover, site, plan, ban=ban)
        prim = [n for n, *_ in B['info']]
        if attempt == 0:
            per = collections.Counter(); rate = collections.Counter()
            for n, item, tot, k in B['info']:
                per[item] += 1; rate[item] += tot
            for item in per:
                print('%-12s %3d belts carrying %7.2f belts of items' % (item, per[item], rate[item] / BELT))
            print('%d extractors, %d belts, %d edge slots, grid %dx%d' % (len(B['ext']), len(prim), len(B['slots']), B['W'], B['H']))
        if len(prim) > len(B['slots']):
            raise SystemExit('more belts than edge slots')
        # every belt gets a slot of its own to aim at (nearest free, far belts first); the router may swap them
        free = list(B['slots'])
        src = {n: q for q, d, n in B['sources']}
        sinks = []
        L = site['loop']; gx, gy = B['off']
        ccx, ccy = (L['x0'] + L['x1']) / 2.0 - gx, (L['y0'] + L['y1']) / 2.0 - gy
        for n in sorted(prim, key=lambda n: -(abs(src[n][0] - ccx) + abs(src[n][1] - ccy))):
            s = min(free, key=lambda s: abs(s[0][0] - src[n][0]) + abs(s[0][1] - src[n][1]))
            free.remove(s)
            sinks.append((s[0], s[1], n))
        blocked = set(B['blocked']) | {p for p, d, *_ in B['ext']} | {s[0] for s in free}
        t0 = time.time()
        ok, res, dt = negotiate.route(B['W'], B['H'], len(B['sources']), blocked=blocked, sources=B['sources'], sinks=sinks,
                                      rounds=80, timeout=seconds, swap=[prim], join=B['joins'],
                                      log=lambda l: print('   ' + l) if 'onto' not in l else None)
        print('attempt %d: %s in %.0f s, %d joins' % (attempt + 1, 'routed' if ok else 'NOT routed: %s' % res.get('reason'), time.time() - t0, len(B['joins'])))
        pickle.dump((B, sinks, ok, res), open(out + '.pkl', 'wb'))
        if ok:
            break
        reason = res.get('reason', '')
        if 'has no route at all' not in reason:
            break
        bad = int(reason.split()[1])        # turn the extractors of that belt (then of the belt it joins) another way
        cands = [(p, d) for p, d, item, r, net in B['ext'] if net == bad] + \
                [(p, d) for p, d, item, r, net in B['ext'] if net == B['joins'].get(bad, -1)]
        new = [c for c in cands if c not in ban]
        if not new:
            break
        ban.add(new[0])
    if ok:
        codes = collections.Counter(c[0] for c in res['tiles'].values())
        print('%d belt pieces: %s' % (len(res['tiles']), dict(codes)))
        picture(B, res, cover, site, out + '.png')
    return ok


COL = {'Wood Log': (60, 200, 80), 'Stone': (200, 200, 200), 'Iron Ore': (120, 150, 255), 'Copper Ore': (255, 150, 60),
       'Coal': (120, 120, 120), 'Wolframite': (200, 80, 220), 'Uranium Ore': (230, 240, 60)}


def picture(B, res, cover, site, path, scale=3):
    from PIL import Image, ImageDraw
    im = Image.new('RGB', (B['W'] * scale, B['H'] * scale), (16, 20, 18))
    dr = ImageDraw.Draw(im)
    gx, gy = B['off']
    box = lambda x, y, c: dr.rectangle([x * scale, y * scale, x * scale + scale - 1, y * scale + scale - 1], fill=c)
    item_of = {}
    for net, item, tot, k in B['info']:
        item_of[net] = item
    for n, j in B['joins'].items():
        item_of[n] = item_of[j]
    for n, pieces in res['by_net'].items():
        c = tuple(int(v * 0.55) for v in COL[item_of[n]])
        for (x, y), code in pieces.items():
            box(x, y, c if code[0] == 'B' else (255, 255, 255))
    for (x, y), code in B['local'].items():
        box(x, y, (150, 150, 150))
    for p in cover['plants']:
        b = p['box']
        dr.rectangle([(math.ceil(b[0] + 9) - gx) * scale, (math.ceil(b[1] + 9) - gy) * scale,
                      (math.floor(b[2] - 9) - gx + 1) * scale - 1, (math.floor(b[3] - 9) - gy + 1) * scale - 1], fill=(255, 215, 0))
    for x, y, t, b in cover['nodes']:
        box(x - gx, y - gy, tuple(v // 3 for v in COL[t]))
    for p, d, item, r, net in B['ext']:
        box(p[0], p[1], COL[item])
    L = site['loop']
    dr.rectangle([(L['x0'] - gx) * scale, (L['y0'] - gy) * scale, (L['x1'] - gx + 1) * scale, (L['y1'] - gy + 1) * scale], outline=(255, 255, 255))
    im.save(path)


if __name__ == '__main__':
    run(sys.argv[1], sys.argv[2], *[float(a) for a in sys.argv[3:4]])
