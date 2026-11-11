"""Exact machine counts per recipe: how many machines use whole input belts, whole output belts, or both.

Rates are tick-exact (rates_0.json: crafts per minute per tier, from tools/recipe_rates.mjs). A belt carries 480
items/min. For each recipe and tier, every count k = 1..kmax is checked: input item i needs k * crafts * amount_i
per minute; it is "full" when that is a whole number of belts. The table lists the smallest k where all inputs are
whole belts, the smallest where the output is, and the smallest where both are.

    python tools/modules/ratios.py GAMEDATA.js rates_0.json > ratios.md
"""
import json
import re
import sys
from fractions import Fraction as F

BELT = 480


def main(gd_path, rates_path, kmax=200):
    txt = open(gd_path).read()
    gd = json.loads(txt[txt.index('{'):txt.rindex('}') + 1])
    rates = json.load(open(rates_path))
    rate = {}
    for r in rates:
        num, den = r['exact'].split('/')
        # exact = items/min for the output; crafts/min = items/min / amount
        rate[r['id'], r['tier']] = F(int(num), int(den)) / r['amount'] if '/' in r['exact'] else None
    print('| Recipe | Tier | per machine (crafts/min) | all inputs full | output full | both | belts at "both" (in / out) |')
    print('|---|---|---|---|---|---|---|')
    for name, rc in gd['recipes'].items():
        for tier in (1, 2, 3, 4):
            c = rate.get((rc['id'], tier))
            if c is None:
                continue
            def belts(k, amt):
                return k * c * amt / BELT
            ins = [(i['item'], i['amount']) for i in rc['in']]
            out = rc['out'][0]['amount']
            k_in = next((k for k in range(1, kmax + 1) if all(belts(k, a).denominator == 1 for _, a in ins)), None)
            k_out = next((k for k in range(1, kmax + 1) if belts(k, out).denominator == 1), None)
            k_both = next((k for k in range(1, kmax + 1) if all(belts(k, a).denominator == 1 for _, a in ins)
                           and belts(k, out).denominator == 1), None)
            desc = ''
            if k_both:
                desc = ', '.join('%s %s' % (belts(k_both, a), it) for it, a in ins) + ' / %s' % belts(k_both, out)
            print('| %s | T%d | %s | %s | %s | %s | %s |' % (name.replace('recipe_', ''), tier, c, k_in or '-',
                                                       k_out or '-', k_both or '-', desc))


if __name__ == '__main__':
    main(*sys.argv[1:3])
