"""coverage map as a picture: one panel per ratio and mode, colored cells (python tools/data/coverage_png.py 2,3,4 out.png)"""
import sys
from PIL import Image, ImageDraw
sys.path.insert(0, __file__.rsplit('/', 1)[0])
import coverage as C

COL = {'F': (46, 160, 67), 'f': (150, 210, 160), 'P': (200, 50, 50), 'p': (235, 160, 160), 's': (240, 190, 60),
       '?': (120, 120, 220), '.': (235, 235, 235)}


def main(ns, out):
    box = C.load()
    cs, pad = 22, 34
    panels = []
    for n in ns:
        for sym in (False, True):
            d = box[n, sym]
            if not d:
                continue
            inf = C.infer(d, sym)
            Wm = max(W for W, H in d)
            Hm = max(H for W, H in d)
            im = Image.new('RGB', (pad + cs * Wm + 10, pad + cs * Hm + 10), 'white')
            dr = ImageDraw.Draw(im)
            dr.text((4, 4), '%d:%d %s  (x = width, y = height)' % (n, n, 'sym' if sym else 'asym'), fill='black')
            for W in range(1, Wm + 1):
                dr.text((pad + cs * (W - 1) + 5, 18), str(W), fill='black')
            for H in range(1, Hm + 1):
                dr.text((6, pad + cs * (H - 1) + 5), str(H), fill='black')
                for W in range(1, Wm + 1):
                    c = inf.get((W, H)) or C.mark(d.get((W, H), ()))
                    x, y = pad + cs * (W - 1), pad + cs * (H - 1)
                    dr.rectangle([x, y, x + cs - 2, y + cs - 2], fill=COL[c])
                    if c in 'Ps?':
                        dr.text((x + 7, y + 5), c, fill='black')
            panels.append(im)
    leg = Image.new('RGB', (420, 70), 'white')
    dr = ImageDraw.Draw(leg)
    items = [('F', 'found'), ('f', 'fits (contains a found box)'), ('P', 'proven impossible'), ('p', 'impossible (inside a P box)'),
             ('s', 'staged model only: not a proof'), ('?', 'undecided'), ('.', 'never tried')]
    for i, (c, t) in enumerate(items):
        x, y = 6 + (i % 2) * 210, 4 + (i // 2) * 16
        dr.rectangle([x, y, x + 12, y + 12], fill=COL[c])
        dr.text((x + 18, y), t, fill='black')
    panels.append(leg)
    cols = 2
    rows = [panels[i:i + cols] for i in range(0, len(panels), cols)]
    Wt = max(sum(p.width for p in r) + 10 * len(r) for r in rows)
    Ht = sum(max(p.height for p in r) + 10 for r in rows)
    big = Image.new('RGB', (Wt, Ht), 'white')
    y = 0
    for r in rows:
        x = 0
        for p in r:
            big.paste(p, (x, y))
            x += p.width + 10
        y += max(p.height for p in r) + 10
    big = big.resize((big.width * 2, big.height * 2), Image.NEAREST)
    big.save(out)


if __name__ == '__main__':
    main(list(map(int, sys.argv[1].split(','))), sys.argv[2])
