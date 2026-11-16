"""Build rl/data/tables.json: every number the RL environments use, from the game's own tables.

Sources (none hand-typed except the Gen 2 extractor rates):
  site/js/gamedata.js                  recipes, buildings (Steam build 16152482)
  factory/extract/D_BaseTechnology     tech tree: costs, prerequisites, unlocks, upgrades
  factory/extract/D_BaseBuildings      build costs, tier upgrade costs, build limits
  factory/extract/raw/D_BaseItems      SellValue (gold per item), parsed from the cooked table
  rates_<boost>.json                   exact ticks per craft (tools/recipe_rates.mjs, tick-exact)
  Gen 2 extractor rates                15/30/60/120/150 per min (iOS calculators; PC is 7.5/11.25/15/22.5/30)

usage: python rl/data/build_tables.py <extract dir> <rates dir>
"""
import json, os, re, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))


def sell_values(path, names):
    d = open(path, 'rb').read()
    out = {}
    for m in re.finditer(rb'cargo\.([a-z_]+)\x00', d):
        i = d.find(b'\x00\x05\x65\x00', m.end(), m.end() + 80)       # SellValue: [{ItemId 101 (gold), Amount}]
        out[m.group(1).decode()] = struct.unpack_from('<i', d, i + 4)[0] if i >= 0 else 0
    by_name = {}
    for gid, (key, name) in names.items():
        k = key.split('.', 1)[1]
        if key.startswith('cargo.'):
            by_name[name] = out.get('processed_uranium' if k == 'enriched_uranium' else k, 0)
    return by_name


def main(ext, rates_dir):
    s = open(os.path.join(ROOT, 'site/js/gamedata.js')).read()
    gd = json.loads(s[s.index('{'):s.rindex('}') + 1])
    names = json.load(open(os.path.join(ext, 'items.json')))
    item_name = {int(k): v[1] for k, v in names.items()}
    techs = json.load(open(os.path.join(ext, 'D_BaseTechnology.json')))
    blds = json.load(open(os.path.join(ext, 'D_BaseBuildings.json')))
    sell = sell_values(os.path.join(ext, 'raw/D_BaseItems.uasset'), names)

    row_of = {}
    for t in techs:                               # RequiredTechIds name rows "tech_<row>"; repeated rows = chain
        row_of.setdefault('tech_' + t['_row'], []).append(t['TechId'])
    tech_out = []
    for t in techs:
        req = []
        for r in t.get('RequiredTechIds') or []:
            ids = row_of[r]
            if len(ids) == 1:
                req.append(ids[0])
            else:                                 # upgrade chains share a row name: previous in the chain
                mine = [i for i in ids if i < t['TechId']] if t['_row'] == r[5:] else ids
                req.append(max(mine) if t['_row'] == r[5:] else ids[0])
        tech_out.append({
            'id': t['TechId'], 'row': t['_row'], 'name': t['DisplayName']['text'],
            'cost': {item_name[c['ItemId']]: c['Amount'] for c in t.get('UnlockCost', [])},
            'gems_ok': bool(t.get('bCanUnlockWithGems')), 'requires': req, 'modifiers': t.get('Modifiers', [])})

    b_out = {}
    for b in blds:
        if b['_row'].startswith('deco_') or b['_row'] in ('building_fence', 'building_concrete_wall', 'building_user_sign'):
            continue
        b_out[b['_row']] = {
            'id': b['BuildingId'], 'cost': {item_name[c['ItemId']]: c['Amount'] for c in b.get('BuildCost', [])},
            'limit': b.get('BuildLimit'),
            'upgrades': [{item_name[c['ItemId']]: c['Amount'] for c in u.get('UpgradeCost', [])}
                         for u in b.get('TierUpgrades', [])[1:]],
            'w': gd['buildings'].get(b['_row'], {}).get('w', b.get('Width', 1)),
            'h': gd['buildings'].get(b['_row'], {}).get('h', b.get('Length', 1))}

    ticks = {}
    for boost in ('0', '0.2', '0.4'):
        for r in json.load(open(os.path.join(rates_dir, 'rates_%s.json' % boost))):
            ticks.setdefault(boost, {}).setdefault(str(r['id']), {})[r['tier']] = r['period']
    recipes = {}
    for k, r in gd['recipes'].items():
        recipes[r['id']] = {'key': k[7:], 'building': r['building'], 'seconds': r['seconds'], 'alt': r['alt'],
                            'in': {i['item']: i['amount'] for i in r['in']},
                            'out': {i['item']: i['amount'] for i in r['out']},
                            'period': {b: ticks[b][str(r['id'])] for b in ticks}}
    out = {
        'items': [n for n in gd['items'] if n not in ('Gold', 'Gems')],
        'sell': sell, 'techs': tech_out, 'buildings': b_out, 'recipes': recipes,
        'extractor_per_min': {'gen2': [15, 30, 60, 120, 150], 'gen1': [7.5, 11.25, 15, 22.5, 30]},
        'uranium_per_min': {'gen2': 50, 'gen1': 10},
        'belt_per_min': 480, 'belt_tiles_per_s': 32 * 60 / 800,
        'resources': {'1': 'Wood Log', '2': 'Iron Ore', '4': 'Copper Ore', '8': 'Stone', '16': 'Coal',
                      '32': 'Wolframite', '64': 'Uranium Ore'},
    }
    json.dump(out, open(os.path.join(HERE, 'tables.json'), 'w'), indent=1)
    print('techs', len(tech_out), 'recipes', len(recipes), 'buildings', len(b_out))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
