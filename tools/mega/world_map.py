"""Picture and numbers of the mega factory site on the map of seed 2CCJhI:
    node tools/mega/world_nodes.mjs 2CCJhI > nodes.json ; node tools/mega/world_cover.mjs nodes.json > cover.json
    python3 tools/mega/world_map.py cover.json out.png
Nodes by item (bright = boosted by a plant of data/mega_site.json, dark = not), the plants and the belt loop john
drew for the factory.  Prints belts per item: a tier 5 Gen 2 extractor gives 150/min, 240 boosted; a belt 480."""
import json, os, sys, collections
from PIL import Image, ImageDraw

COL = {'Wood Log': (60, 200, 80), 'Stone': (190, 190, 190), 'Iron Ore': (120, 150, 255), 'Copper Ore': (255, 150, 60),
       'Coal': (90, 90, 90), 'Wolframite': (200, 80, 220), 'Uranium Ore': (230, 240, 60)}


def main(cover, out, scale=2):
    d = json.load(open(cover))
    site = json.load(open(os.path.join(os.path.dirname(__file__), '../../data/mega_site.json')))
    nodes = d['nodes']
    xs = [n[0] for n in nodes]; ys = [n[1] for n in nodes]
    x0, y0 = min(xs) - 15, min(ys) - 15
    W, H = max(xs) + 15 - x0, max(ys) + 15 - y0
    im = Image.new('RGB', (W * scale, H * scale), (18, 24, 20))
    dr = ImageDraw.Draw(im)
    for p in d['plants']:
        b = p['box']
        dr.rectangle([(b[0] - x0) * scale, (b[1] - y0) * scale, (b[2] - x0) * scale, (b[3] - y0) * scale], outline=(70, 60, 20), fill=(40, 36, 16))
    for p in d['plants']:
        b = p['box']
        dr.rectangle([(b[0] + 9 - x0) * scale, (b[1] + 9 - y0) * scale, (b[2] - 9 - x0) * scale, (b[3] - 9 - y0) * scale], fill=(255, 220, 0))
    L = site['loop']
    dr.rectangle([(L['x0'] - x0) * scale, (L['y0'] - y0) * scale, (L['x1'] - x0) * scale, (L['y1'] - y0) * scale], outline=(255, 255, 255), width=2)
    rate = collections.Counter(); cnt = collections.Counter(); dark = collections.Counter()
    for x, y, t, b in nodes:
        c = COL[t] if b else tuple(v // 2 for v in COL[t])
        dr.rectangle([(x - x0) * scale - 1, (y - y0) * scale - 1, (x - x0) * scale + scale, (y - y0) * scale + scale], fill=c)
        rate[t] += 240 if b else 150; cnt[t] += 1; dark[t] += not b
    im.save(out)
    for t in COL:
        print('%-12s %3d nodes, %3d not boosted, %6.2f belts' % (t, cnt[t], dark[t], rate[t] / 480.0))


if __name__ == '__main__':
    main(*sys.argv[1:3])
