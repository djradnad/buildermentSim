"""Polish every best run in best.json with local search, saving after each item. usage: python -m rl.polish_all <best.json> [rounds]"""
import json, sys
from .expert import polish

path = sys.argv[1]
rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 60
best = json.load(open(path))
for it in list(best):
    t, p, src = best[it]
    t2, p2 = polish(it, p, rounds=rounds)
    if t2 < t:
        best[it] = (t2, p2, src.split('+')[0] + '+polish')
        json.dump(best, open(path, 'w'))
    print('%-14s %7.1f -> %7.1f' % (it, t, min(t, t2)), flush=True)
