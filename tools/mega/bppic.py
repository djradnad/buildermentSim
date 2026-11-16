"""Picture + table of a blueprint rows file: machines coloured by what they make, belts grey, hops blue,
splitters orange, arms black. The table lists every recipe with its machine count and bounding box.

    python3 tools/mega/bppic.py data/bp/3rlwj5.json out.png [scale]
"""
import collections
import colorsys
import json
import sys

sys.path.insert(0, '.')
sys.path.insert(0, 'tools/mega')
import modlib
import reroute_core as rc
from bmsolver.pieces import KIND_OF_ID


def table(rows, rec):
    box = collections.defaultdict(list)
    for r in rows:
        if r[0] in KIND_OF_ID or r[0] == 3012:
            continue
        name = rec[r[5]]['out'][0]['item'] + (' ALT' if rec[r[5]].get('alt') else '') if r[5] in rec else 'building %d' % r[0]
        box[name] += rc.footprint(r)
    out = {}
    for name, cells in box.items():
        xs = [c[0] for c in cells]
        ys = [c[1] for c in cells]
        n = sum(1 for r in rows if r[0] not in KIND_OF_ID and r[0] != 3012 and (
            (rec[r[5]]['out'][0]['item'] + (' ALT' if rec[r[5]].get('alt') else '')) if r[5] in rec else 'building %d' % r[0]) == name)
        out[name] = (n, min(xs), max(xs), min(ys), max(ys))
    return out


def picture(rows, rec, path, scale=4):
    from PIL import Image, ImageDraw
    cells = {}
    names = sorted({rec[r[5]]['out'][0]['item'] + (' ALT' if rec[r[5]].get('alt') else '') for r in rows if r[5] in rec})
    col = {n: tuple(int(255 * c) for c in colorsys.hsv_to_rgb(i / max(1, len(names)), 0.75 if i % 2 else 0.5, 0.95 if i % 3 else 0.7))
           for i, n in enumerate(names)}
    for r in rows:
        if r[0] in KIND_OF_ID:
            cells[(r[1], r[2])] = {'B': (170, 170, 170), 'S': (255, 140, 0), 'I': (60, 110, 255), 'O': (60, 110, 255)}[KIND_OF_ID[r[0]]]
        elif r[0] == 3012:
            cells[(r[1], r[2])] = (0, 0, 0)
        else:
            c = col.get(rec[r[5]]['out'][0]['item'] + (' ALT' if rec[r[5]].get('alt') else ''), (90, 90, 90)) if r[5] in rec else (90, 90, 90)
            for p in rc.footprint(r):
                cells[p] = c
    xs = [p[0] for p in cells]
    ys = [p[1] for p in cells]
    x0, y0 = min(xs), min(ys)
    W, H = max(xs) - x0 + 1, max(ys) - y0 + 1
    legend = 170
    im = Image.new('RGB', (W * scale + legend, max(H * scale, 14 * len(names) + 10)), (255, 255, 255))
    d = ImageDraw.Draw(im)
    for (x, y), c in cells.items():
        d.rectangle([(x - x0) * scale, (y - y0) * scale, (x - x0 + 1) * scale - 1, (y - y0 + 1) * scale - 1], fill=c)
    for i, n in enumerate(names):
        d.rectangle([W * scale + 6, 6 + 14 * i, W * scale + 16, 16 + 14 * i], fill=col[n])
        d.text((W * scale + 20, 5 + 14 * i), n, fill=(0, 0, 0))
    im.save(path)
    return x0, y0, W, H


def main():
    rows = json.load(open(sys.argv[1]))['rows']
    rec = {r['id']: r for r in modlib.gamedata()['recipes'].values()}
    t = table(rows, rec)
    for name, (n, xa, xb, ya, yb) in sorted(t.items(), key=lambda kv: (kv[1][1], kv[1][3])):
        print('%-24s %4d machines  x %4d..%-4d y %4d..%-4d' % (name, n, xa, xb, ya, yb))
    if len(sys.argv) > 2:
        print('picture: x0 %d y0 %d, %dx%d' % picture(rows, rec, sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 4))


if __name__ == '__main__':
    main()
