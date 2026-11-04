"""Check a solved layout (belts, splitters, tunnels and machines) in the game-exact simulator (site/js/gamesim.js).

    rep = gsim.run(win, grid, machines, items=['Copper Ore', 'Copper Ingot'], per_unit=15, recipes=[2002])

Every input port gets two feeder belts outside the window fed at its rate; every output port gets two belts outside
whose open end takes everything. Returns the sim's settled machine rates and what each output receives per minute.
"""
import json
import os
import subprocess
import tempfile

from .pieces import BUILDING_ID, DIRS

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, '..', 'tools', 'simlayout.mjs')


def buildings(win, grid, machines, recipes, tiers=None):
    bs = [dict(id=BUILDING_ID[c[0]], x=x, y=y, o=int(c[1])) for (x, y), c in grid.items()]
    for ti, (ax, ay), o in machines:
        mt = win.machines[ti]
        bs.append(dict(id=mt['bid'], x=ax, y=ay, o=o, recipe=recipes[ti], tier=(tiers or {}).get(ti, mt.get('tier', 1))))
    return bs


def run(win, grid, machines, items, per_unit, recipes, options=None, warmup=10, minutes=30, buffer=10):
    """per_unit: items per minute in one flow unit (480 / win.D for a full belt of D units). buffer: items a feed
    may hold back while its belt is blocked (10 = a machine's output store, as when upstream production feeds it)."""
    bs = buildings(win, grid, machines, recipes)
    keys = dict(win.ports)
    for i, g in enumerate(win.port_options):
        for j, opt in enumerate(g):
            if options is None or options[i] == j:
                keys.update(opt)
    feeds, outs = [], []
    for (t, d), spec in keys.items():
        kind = spec[0] if isinstance(spec, tuple) else spec
        if kind not in ('in', 'out'):
            continue
        dx, dy = DIRS[d]
        p1, p2 = (t[0] + dx, t[1] + dy), (t[0] + 2 * dx, t[1] + 2 * dy)
        if kind == 'in':
            o = (d + 2) % 4
            bs += [dict(id=3000, x=p[0], y=p[1], o=o) for p in (p1, p2)]
            for c, v in enumerate(spec[1]):
                if v:
                    feeds.append(dict(at=list(p2), kind=items[c], perMin=v * per_unit, buffer=buffer))
        else:
            bs += [dict(id=3000, x=p[0], y=p[1], o=d) for p in (p1, p2)]
            outs.append('%d,%d' % p2)
    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
        json.dump(dict(buildings=bs, feeds=feeds, warmup=warmup, minutes=minutes), f)
        path = f.name
    try:
        out = subprocess.run(['node', SIM, path], capture_output=True, text=True, check=True).stdout
    finally:
        os.unlink(path)
    rep = json.loads(out)
    rep['outputs'] = {k: rep['sinks'].get(k, {}) for k in outs}
    return rep
