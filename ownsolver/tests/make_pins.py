"""Write pinned regression specs (every tile fixed) for every known design to specs/pin_*.json.
Each design is first verified with the exact check; ones that fail it are listed and skipped.
Sources: tests/designs/*.txt, /root/data/box33_*.txt, site/catalogue.json (W*H <= MAXAREA)."""
import glob
import json
import os
import sys

from common import ROOT, Design, window_of_design, pin, save, exact_check, check_py

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'specs')
MAXAREA = int(sys.argv[1]) if len(sys.argv) > 1 else 10 ** 9


def emit(name, design, ins, outs, mix, rows):
    design_n = design
    try:
        win, sp = window_of_design(design_n, ins, outs, mix)
    except Exception as e:
        rows.append((name, 'SKIP ports: %s' % e))
        return
    pin(win, design_n)
    ok, why = exact_check(win, design_n.tiles)
    if not ok and mix:
        return emit(name, design, ins, outs, False, rows)
    if not ok:
        rows.append((name, 'SKIP exact check fails: %s' % why))
        return
    if win.ncom > 1 or True:
        ok2, _ = check_py(win, design_n.tiles)
        if not ok2:
            rows.append((name, 'NOTE check.py disagrees'))
    save(win, os.path.join(OUT, 'pin_%s.json' % name), name='pin ' + name)
    rows.append((name, 'ok %dx%d ncom=%d D=%d' % (win.W, win.H, win.ncom, win.D)))


def main():
    from bmsolver.check import find_ports
    rows = []
    files = sorted(glob.glob(os.path.join(ROOT, 'tests', 'designs', '*.txt'))) + sorted(glob.glob('/root/data/box33_*.txt'))
    for f in files:
        d = Design.load(f).normalized()
        if d.size[0] * d.size[1] > MAXAREA:
            continue
        ins, outs = find_ports(d)
        emit(os.path.basename(f)[:-4], d, ins, outs, len(ins) > 1, rows)
    cat = json.load(open(os.path.join(ROOT, 'site', 'catalogue.json')))['designs']
    for c in cat:
        if c['W'] * c['H'] > MAXAREA:
            continue
        tiles = {}
        for y, line in enumerate(c['grid']):
            for i in range(0, len(line), 2):
                if line[i:i + 2] != '..':
                    tiles[(i // 2, y)] = line[i:i + 2]
        d = Design(tiles)
        if d.bbox()[:2] != (0, 0) or d.size != (c['W'], c['H']):
            # keep the catalogue frame (empty edge rows/cols matter for port positions)
            pass
        ins = [((x, y), dd) for x, y, dd in c['ins']]
        outs = [((x, y), dd) for x, y, dd in c['outs']]
        emit('cat_%s_%s' % (c['id'], '%d_%d_%dx%d' % (c['n'], c['m'], c['W'], c['H'])), d, ins, outs, c['n'] > 1, rows)
    for r in rows:
        print('%-40s %s' % r)
    print(sum(1 for r in rows if r[1].startswith('ok')), 'specs written of', len(rows))


if __name__ == '__main__':
    main()
