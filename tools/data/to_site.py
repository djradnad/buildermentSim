"""Pick polished layouts worth sending to the site: drop any that a smaller box with the same width or the same height
already beats (catalogue or this batch), and any that a catalogue entry of the same box beats on every one of
footprint, pieces, edge splitters and gold. Writes the site inbox format.

    python tools/data/to_site.py ~/data/scored.jsonl catalogue.json sent1.jsonl,sent2.jsonl out.jsonl
"""
import json
import sys


def main(scored, catalogue, sent, out):
    cat = [d for d in json.load(open(catalogue))['designs'] if d.get('kind') != 'split' and d['n'] == d['m']]
    already = set()
    for p in sent.split(','):
        for line in open(p):
            already.add(json.loads(line)['text'])
    pool = [dict(n=d['n'], W=d['W'], H=d['H'], sym=d.get('mode') == 'sym', fp=d.get('footprint', 1e9),
                 pcs=d.get('pieces', 1e9), edge=d.get('edge_splitters', 1e9), gold=d.get('gold', 1e9)) for d in cat]
    cands = []
    for line in open(scored):
        r = json.loads(line)
        t = '\n'.join(r['grid'])
        if not r.get('front') or t in already:
            continue
        already.add(t)
        cands.append(dict(n=r['n'], W=r['W'], H=r['H'], sym=bool(r.get('sym')), fp=r['footprint'], pcs=r['pieces'],
                          edge=r['edge_splitters'], gold=r['gold'], tag=r.get('tag', ''), text=t))
    allp = pool + cands

    def beaten(c):
        for o in allp:
            if o is c or o['n'] != c['n'] or (c['sym'] and not o['sym']):
                continue
            if (o['W'] == c['W'] and o['H'] < c['H']) or (o['H'] == c['H'] and o['W'] < c['W']):
                return True
            if (o['W'], o['H']) == (c['W'], c['H']) and o in pool and all(
                    o[k] <= c[k] for k in ('fp', 'pcs', 'edge', 'gold')):
                return True
        return False
    keep = [c for c in cands if not beaten(c)]
    with open(out, 'w') as fh:
        for c in keep:
            fh.write(json.dumps(dict(n=c['n'], m=c['n'], source='ours', text=c['text'],
                                     name='aisat %d:%d %dx%d%s %s' % (c['n'], c['n'], c['W'], c['H'],
                                                                     ' sym' if c['sym'] else '', c['tag']))) + '\n')
    print(len(cands), 'new front layouts,', len(keep), 'kept')


if __name__ == '__main__':
    main(*sys.argv[1:])
