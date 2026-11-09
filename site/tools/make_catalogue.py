"""Builds site/catalogue.json: the best known splitters, mergers and balancers, each checked with exact flows and
sorted into symmetric / quasi-symmetric / asymmetric.

    python site/tools/make_catalogue.py SOURCE... > site/catalogue.json

To add designs to the current catalogue, pass it as the first source:

    python site/tools/make_catalogue.py site/catalogue.json site/tools/catalogue_extra.jsonl > new.json

A SOURCE is an earlier catalogue.json, a .txt grid, a blueprint .json ({"rows"} or {"grid"}), or a .jsonl file with one design per line:
{"name", "source", "id" (blueprint id, optional), "rows" | "grid" | "text" | "b"}. "b" is [[x, y, building id,
rotation], ...] as in wiki dumps. File names like bal44_9x6.txt or split_1_12_2x12.txt give no ratio: it is read
from the design itself (its open inputs and outputs).

Every design is turned so items flow east to west (inputs on the east side, outputs on the west side). Rules:
  sym    the design equals its top/bottom mirror image
  quasi  the same, except for one mirror pair of tiles in the middle row(s) that hold the same piece facing
         another way (like the wiki splitters with one turned splitter in the middle)
  asym   anything else
"proven_for" (from solver logs): the solver proved no smaller area exists under that rule, so also under any
stricter rule the design meets.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from bmsolver.design import Design                         # noqa: E402
from bmsolver.check import check, find_ports                # noqa: E402
from bmsolver.pieces import KIND_OF_ID, mirror_code         # noqa: E402
from bmsolver import cleanup                               # noqa: E402
from bmsolver.improve import Layout                        # noqa: E402

SIDES = 'NWSE'


def load_any(path):
    out = []
    if path.endswith('.jsonl'):
        for line in open(path):
            if line.strip():
                out.append(json.loads(line))
        return out
    if path.endswith('.txt'):
        return [dict(name=os.path.basename(path)[:-4], text=open(path).read(), source='ours')]
    d = json.load(open(path))
    if 'designs' in d:                       # an earlier catalogue.json: re-check its designs and keep their ids
        return [dict(name=e['name'], source=e['source'], id=e.get('id'), text='\n'.join(e['grid']), n=e['n'], m=e['m'],
                     **({'proven_for': e['proven_for']} if e.get('proven_for') else {})) for e in d['designs']]
    d.setdefault('name', os.path.basename(path)[:-5])
    return [d]


def to_design(e):
    if 'text' in e:
        return Design.from_text(e['text'])
    if 'rows' in e:
        return Design({(r[1], r[2]): KIND_OF_ID[r[0]] + str(r[3]) for r in e['rows'] if r[0] in KIND_OF_ID})
    if 'grid' in e:
        return Design({(x, y): c for x, y, c in e['grid']})
    if 'b' in e:
        return Design({(x, y): KIND_OF_ID[i] + str(o) for x, y, i, o in e['b'] if i in KIND_OF_ID})
    raise ValueError('no layout in ' + e.get('name', '?'))


def loose_ports(design):
    """Like find_ports(E, W), but an input may also be a tile that something inside feeds (side-loading
    mergers: a column of belts each taking one lane from the east)."""
    x0, y0, x1, y1 = design.bbox()
    ins = [(p, dd) for p, dd in design.open_inputs() if p[0] == x1 and dd == 1]
    outs = [(p, dd) for p, dd in design.open_outputs() if p[0] == x0 and dd == 1]
    key = lambda t: (t[0][1], t[0][0])
    return sorted(ins, key=key), sorted(outs, key=key)


def orient(d, want=None):
    """want: (n, m) when the entry says which ratio it is (catalogue entries, solver results), so a layout whose
    east-edge lanes are side-fed (many-to-few solver balancers) is read with the inputs it was made for."""
    r = _orient(d, find_ports)
    r2 = _orient(d, lambda x, a, b: loose_ports(x))      # side-loaded lanes (mergers, solver many-to-few balancers)
    if want:
        for c in (r, r2):
            if c is not None and (len(c[1]), len(c[2])) == tuple(want) and contiguous(c[1]) and contiguous(c[2]):
                return c
        # more open lanes than ports: take the side-by-side run of n inputs and m outputs that checks out
        n, m = want
        runs = lambda ps, k: [ps[i:i + k] for i in range(len(ps) - k + 1) if contiguous(ps[i:i + k])]
        for k in range(4):
            rr = d.rotated(k)
            ins, outs = loose_ports(rr)
            for i_ in runs(ins, n):
                for o_ in runs(outs, m):
                    rep = check(rr, i_, o_, mix=len(i_) > 1)
                    if rep['balanced'] and not rep['overfull'] and not rep['stuck']:
                        rep['stray_out'] = len(outs) > m
                        rep['mixed'] = True
                        return rr, i_, o_, rep
    if r is not None and len(r[2]) > 1:
        return r
    if r2 is not None and len(r2[2]) == 1 and (r is None or (len(r[2]) == 1 and len(r2[1]) > len(r[1]))):
        return r2
    return r


def _orient(d, ports):
    """Turn so the inputs are on the east side and the outputs on the west side, trying every quarter turn.
    Returns (design, inputs, outputs, report) for the reading with the most outputs that checks out."""
    best = None
    for k in range(4):
        r = d.rotated(k)
        ins, outs = ports(r, ('E',), ('W',))
        if not ins or not outs:
            continue
        # a splitter at an end of the output side may face the edge without being an output (in game it only
        # pushes where something takes items), so also try without the first or last open end
        for outs_ in ((outs, outs[1:], outs[:-1]) if len(d) < 400 else (outs,)):
            if not outs_:
                continue
            done = False
            for mix in (True, False):
                rep = check(r, ins, outs_, mix=mix and len(ins) > 1)
                if rep['balanced'] and not rep['overfull'] and not rep['stuck']:
                    rep['stray_out'] = outs_ is not outs
                    cand = (len(ins) + len(outs_), mix, k, r, ins, outs_, rep)
                    if best is None or cand[:2] > best[:2]:
                        best = cand
                    done = True
                    break
            if done:
                break
    if best is None:
        return None
    _, mix, k, r, ins, outs, rep = best
    rep['mixed'] = mix or len(ins) == 1
    return r, ins, outs, rep


def symmetry(d):
    """'sym', 'quasi' (one mirror pair in the middle row or rows differs: turned, or splitter vs belt) or 'asym'."""
    x0, y0, x1, y1 = d.bbox()
    mir = lambda p: (p[0], y0 + y1 - p[1])
    mids = {(y0 + y1) // 2, (y0 + y1 + 1) // 2}
    diff = []
    for p in set(d.tiles) | {mir(q) for q in d.tiles}:
        q = mir(p)
        if q < p:
            continue
        a, b = d.tiles.get(p), d.tiles.get(q)
        want = mirror_code(b, 'y') if b else None
        if a != want:
            diff.append((p, a, b))
    if not diff:
        return 'sym'
    if len(diff) == 1:
        p, a, b = diff[0]
        # one pair in the middle row(s): the same piece turned, or a splitter whose mirror image is a belt
        if p[1] in mids and a and b and (a[0] == b[0] or {a[0], b[0]} == {'S', 'B'}):
            return 'quasi'
    return 'asym'


def contiguous(ports):
    """The ports (all on one side) sit next to each other with no tile between them."""
    ys = sorted(p[1] for p, d in ports)
    return ys == list(range(ys[0], ys[0] + len(ys))) if ys else True


LOOSE = {'sym': 0, 'quasi': 1, 'asym': 2}


def scores(e):
    """The independent ways one layout can beat another of the same ratio (smaller is better): width, height (so the
    best layout of every width and every height smaller than the most square one stays), the symmetry rule it meets,
    full mixing, and the clean-up scores taken in john's order (footprint, pieces, splitter faces out of the edge,
    gold, clean edge)."""
    return (e['W'], e['H'], LOOSE[e['mode']], 0 if e['mixed'] else 1,
            (e['footprint'], e['pieces'], e['edge_splitters'], e['gold'], 1 if e['stray_out'] else 0))


def drop_obsolete(ents):
    """Leave out layouts that another layout of the same ratio matches or beats on every score (john 2026-11-09).
    Of layouts that tie on everything, the community one (or one with a blueprint code) stays."""
    pref = lambda e: (e['source'] == 'ours', not e.get('id'))
    keep = []
    for e in ents:
        se = scores(e)
        by = None
        for f in ents:
            if f is e or (f['n'], f['m']) != (e['n'], e['m']):
                continue
            sf = scores(f)
            if all(a <= b for a, b in zip(sf, se)) and (sf != se or pref(f) < pref(e) or
                                                        (pref(f) == pref(e) and ents.index(f) < ents.index(e))):
                by = f
                break
        if by is None:
            keep.append(e)
            continue
        if e.get('proven_for') and by['W'] * by['H'] == e['W'] * e['H']:
            by['proven_for'] = max([x for x in (by.get('proven_for'), e['proven_for']) if x], key=LOOSE.get)
        print('obsolete: %d:%d %dx%d %s (beaten by %s)' % (e['n'], e['m'], e['W'], e['H'], e.get('name'), by.get('name')),
              file=sys.stderr)
    return keep


def main(paths):
    seen = {}
    for path in paths:
        for e in load_any(path):
            try:
                d = to_design(e).normalized()
            except Exception as ex:
                print('skip', e.get('name'), ex, file=sys.stderr)
                continue
            o = orient(d, (e['n'], e['m']) if e.get('n') and e.get('m') else None)
            if o is None:
                print('not balanced:', e.get('name'), file=sys.stderr)
                continue
            r, ins, outs, rep = o
            # stray pieces that carry nothing are removed (they never change any flow)
            for p in rep['idle']:
                del r.tiles[p]
            lay = Layout(r, ins, outs, mix=rep['mixed']).normalized()      # ports move with the trimmed layout
            r = lay.design
            gap = [side for side, ps in (('inputs', lay.inputs), ('outputs', lay.outputs)) if not contiguous(ps)]
            if gap:
                # players put balancers on a bus: the ports must sit side by side, so the box shows the real space
                print('ports not together (%s):' % ' and '.join(gap), e.get('name'), file=sys.stderr)
                continue
            n, m = len(ins), len(outs)
            W, H = r.size
            kind = 'split' if n == 1 else 'merge' if m == 1 else 'balance'
            key = r.to_text(compact=False)
            ent = dict(kind=kind, n=n, m=m, W=W, H=H, pieces=len(r), mode=symmetry(r), mixed=rep['mixed'],
                       name=e.get('name'), source=e.get('source', 'ours'), id=e.get('id'),
                       trimmed=len(rep['idle']), stray_out=rep.get('stray_out', False), splitters=r.count('S'), ug=r.count('I') + r.count('O'), grid=key.split('\n'))
            LOOSE = {'sym': 0, 'quasi': 1, 'asym': 2}
            # scores for layouts of the same box (john's order): footprint, pieces, edge splitters, gold
            ent['outs'] = [[q[0], q[1], dd] for q, dd in lay.outputs]     # the real outputs (tile, direction)
            ent['ins'] = [[q[0], q[1], dd] for q, dd in lay.inputs]       # the real inputs (tile, direction items move)
            ent.update(footprint=cleanup.footprint(r), edge_splitters=cleanup.edge_splitters(lay), gold=cleanup.gold(r))
            if e.get('proven_for'):
                ent['proven_for'] = e['proven_for']      # smallest area under this rule (and every stricter one)
            sk = (n, m, key)          # one layout can be two ratios (a 2:2 that also balances 4 side-fed lanes)
            if sk in seen:
                old = seen[sk]
                pf = max([x for x in (old.get('proven_for'), ent.get('proven_for')) if x], key=LOOSE.get, default=None)
                if old['source'] == 'ours' and ent['source'] != 'ours':
                    seen[sk] = old = ent        # credit the community design when ours is the same
                if pf:
                    old['proven_for'] = pf
                if not old.get('id') and ent.get('id'):
                    old['id'] = ent['id']
                continue
            seen[sk] = ent
            print('%-8s %-7s %3dx%-3d %4d pcs %-5s %s' % (kind, '%d:%d' % (n, m), W, H, len(r), ent['mode'],
                                                          e.get('name')), file=sys.stderr)
    ids = {tuple(e['grid']): e['id'] for e in seen.values() if e.get('id')}
    for e in seen.values():             # the same layout under another ratio: same game blueprint
        if not e.get('id') and tuple(e['grid']) in ids:
            e['id'] = ids[tuple(e['grid'])]
    ents = sorted(drop_obsolete(list(seen.values())),
                  key=lambda e: (e['n'], e['m'], e['W'] * e['H'], e['footprint'], e['pieces']))
    json.dump({'version': 1, 'designs': ents}, sys.stdout, separators=(',', ':'))


if __name__ == '__main__':
    main(sys.argv[1:])
