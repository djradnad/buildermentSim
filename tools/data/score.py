"""Add john's scores to polished examples: footprint, pieces, edge splitters, gold, a tag ('clean', or 'edge' when a
splitter faces out of a non-port edge), and `front`: the layout wins at least one category among layouts of the same
ratio, box (W x H, both orientations kept apart), symmetry and tag. Writes a new JSONL.

    python tools/data/score.py ~/data/polished.jsonl ~/data/scored.jsonl
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from bmsolver import cleanup  # noqa: E402
sys.path.insert(0, os.path.dirname(__file__))
from polish import to_layout  # noqa: E402


def main(src, dst):
    recs = []
    for line in open(os.path.expanduser(src)):
        r = json.loads(line)
        lay = to_layout(r)
        d = lay.design
        r['footprint'] = cleanup.footprint(d)
        r['edge_splitters'] = cleanup.edge_splitters(lay)
        r['gold'] = cleanup.gold(d)
        r['pieces'] = len(d)
        r['tag'] = 'edge' if r['edge_splitters'] else 'clean'
        recs.append(r)
    uniq, seen = [], set()
    for r in recs:                       # polishing can turn several variants into the same layout
        k = (r['n'], r['W'], r['H'], r.get('sym'), tuple(r['grid']))
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    recs = uniq
    groups = {}
    for r in recs:
        groups.setdefault((r['n'], r['W'], r['H'], r.get('sym'), r['tag']), []).append(r)
    for g in groups.values():
        for r in g:
            mine = (r['footprint'], r['pieces'], r['edge_splitters'], r['gold'])
            # on the front unless another layout is at least as good everywhere and better somewhere
            r['front'] = not any(all(a <= b for a, b in zip(o, mine)) and o != mine for o in
                                 [(x['footprint'], x['pieces'], x['edge_splitters'], x['gold']) for x in g])
    with open(os.path.expanduser(dst), 'w') as fh:
        for r in recs:
            fh.write(json.dumps(r) + '\n')
    print(len(recs), 'examples,', sum(r['front'] for r in recs), 'on the front,',
          sum(r['tag'] == 'edge' for r in recs), 'tagged edge')


if __name__ == '__main__':
    main(*sys.argv[1:3])
