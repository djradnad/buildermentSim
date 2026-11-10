"""Module library from john's blueprints (data/bp/<code>.json, saved by the decode workflow).

For every blueprint: its exact tile set, the recipes inside, every INPUT lane (a belt / entrance nothing feeds),
every OUTPUT (a belt / exit pushing into nothing), and the items the recipes need from outside and make for
outside. Input lanes are interchangeable unless proven otherwise (no arm filters): the router chooses.

    python tools/mega/modlib.py            # table of all modules -> data/modlib.json
"""
import glob
import json
import os
import sys

sys.path.insert(0, '.')
sys.path.insert(0, 'tools/mega')
import reroute_core as rc
from bmsolver.design import Design
from bmsolver.pieces import KIND_OF_ID, step, opposite


def gamedata():
    s = open('site/js/gamedata.js').read()
    i = s.index('export const GAMEDATA = ') + len('export const GAMEDATA = ')
    return json.JSONDecoder().raw_decode(s[i:])[0]


def module(code, rows, recipes):
    belts = {(r[1], r[2]): KIND_OF_ID[r[0]] + str(r[3]) for r in rows if r[0] in KIND_OF_ID}
    solid = set()
    for r in rows:
        if r[0] not in KIND_OF_ID:
            solid.update(rc.footprint(r))
    des = Design(belts)
    fed = {q for p, q, k in des.links()}
    drop, pick, prod = set(), set(), set()
    machine_cells = set()
    for r in rows:
        if r[0] not in KIND_OF_ID and r[0] != 3012:
            machine_cells.update(rc.footprint(r))
    for r in rows:
        if r[0] == 3012:                                    # arm: takes behind, drops ahead
            d = r[3]                                        # checked on qsq1kb: lane exit -> arm -> machine
            src = step((r[1], r[2]), opposite(d))
            if src in machine_cells:
                prod.add(step((r[1], r[2]), d))             # an arm lays a machine's product here
            drop.add(step((r[1], r[2]), d))
            pick.add(src)
    nxt = {}
    for p, q, k in des.links():
        nxt.setdefault(p, q)

    def is_input(p):
        """walking down the belt from p: something takes from it (arm, machine) before any product lands on it"""
        for _ in range(200):
            if p in prod:
                return False
            if p in pick:
                return True
            q = nxt.get(p)
            if q is None:
                c = belts[p]
                return c[0] in 'BO' and step(p, int(c[1])) in machine_cells
            p = q
        return False
    paired = {q for p, q, k in des.links() if k == 'ug'}
    ins = []
    for p, c in belts.items():
        d = int(c[1])
        if c[0] == 'O':
            if p not in paired and is_input(p):             # john leaves out the first entrance of a hop lane
                ins.append((p, d, 'O'))
            continue
        if p in fed or p in drop or step(p, opposite(d)) in solid:
            continue                                        # fed by a belt, an arm, or the machine behind it
        if is_input(p):
            ins.append((p, d, c[0]))
    outs = [(p, d) for p, d in des.open_outputs() if step(p, d) not in solid]
    cells = set(belts) | solid
    xs = [p[0] for p in cells]
    ys = [p[1] for p in cells]
    x0, y0 = min(xs), min(ys)
    made, need, count = {}, {}, {}
    for r in rows:
        rec = recipes.get(r[5])
        if rec and r[0] not in KIND_OF_ID and r[0] != 3012:
            count[r[5]] = count.get(r[5], 0) + 1
            for o in rec['out']:
                made[o['item']] = 1
            for i in rec['in']:
                need[i['item']] = 1
    ext_in = sorted(set(need) - set(made))
    ext_out = sorted(set(made) - set(need))
    sh = lambda p: [p[0] - x0, p[1] - y0]
    return dict(code=code, w=max(xs) - x0 + 1, h=max(ys) - y0 + 1, tiles=len(cells),
                recipes={recipes[k]['out'][0]['item']: v for k, v in count.items() if k in recipes},
                needs=ext_in, makes=ext_out,
                inputs=[sh(p) + [d, k] for p, d, k in sorted(ins)], outputs=[sh(p) + [d] for p, d in sorted(outs)],
                cells=sorted(sh(p) for p in cells))


def main():
    gd = gamedata()
    recipes = {r['id']: r for r in gd['recipes'].values()}
    lib = {}
    for f in sorted(glob.glob('data/bp/*.json')):
        code = os.path.basename(f)[:-5]
        m = module(code, json.load(open(f))['rows'], recipes)
        lib[code] = m
        print('%s %dx%d fill %d%% | %s | needs %d: %s | lanes in %d out %d' % (
            code, m['w'], m['h'], 100 * m['tiles'] // (m['w'] * m['h']),
            ', '.join('%d %s' % (v, k) for k, v in m['recipes'].items()), len(m['needs']), ', '.join(m['needs']),
            len(m['inputs']), len(m['outputs'])))
    json.dump(lib, open('data/modlib.json', 'w'))


if __name__ == '__main__':
    main()
