"""Text grid of blueprint rows: machines as a letter per product, belts ^<v>, hop entrances ∩⊂∪⊃, hop exits NWSE,
splitters 0-3 (their direction), arms as arrows.   python3 tools/mega/txt.py <code or rows.json>"""
import sys
sys.path.insert(0, '.'); sys.path.insert(0, 'tools/mega')
import jmod, reroute_core as rc
from bmsolver.pieces import KIND_OF_ID

def grid(rows):
    rec = jmod.recipes(); g = {}; names = {}
    for r in rows:
        if r[0] in KIND_OF_ID:
            g[(r[1], r[2])] = {'B': '^<v>', 'I': '∩⊂∪⊃', 'O': 'NWSE', 'S': '0123'}[KIND_OF_ID[r[0]]][r[3]]
        elif r[0] == 3012:
            g[(r[1], r[2])] = '↑←↓→'[r[3]]
        else:
            n = rec[r[5]]['out'][0]['item'] if r[5] in rec else '?'
            ch = names.setdefault(n, 'abcdefghijklmnopqrstuwxyz'[len(names) % 25])
            for c in rc.footprint(r):
                g[c] = ch
    xs = [p[0] for p in g]; ys = [p[1] for p in g]
    out = [''.join(g.get((x, y), ' ') for x in range(min(xs), max(xs) + 1)) for y in range(min(ys), max(ys) + 1)]
    return '\n'.join(out) + '\n' + ', '.join('%s=%s' % (v, k) for k, v in names.items())

if __name__ == '__main__':
    print(grid(jmod.load(sys.argv[1])))
