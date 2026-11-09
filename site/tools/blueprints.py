"""Game blueprints for every catalogue design, so each one can be copied into the game.

    python site/tools/blueprints.py verify [--candidates]   # check every blueprint id in catalogue.json against its
                                                            # grid (--candidates: also try candidate_ids.json)
    python site/tools/blueprints.py upload [--dry]    # make, upload and check a blueprint for every design without one
                                                      # (and for the mirror image of every non-symmetric design)
    python site/tools/blueprints.py file NAME OUT     # write the game file for one design (to test by hand)

Blueprints go through Human-Crow's blueprint service (the same one the site reads codes with):
  /readBlp    game file -> decoded form (u32 metadata length, JSON metadata, aligned columns)
  /downloadBlp code -> game file
  /uploadBlp  game file -> {"id": code}
The file is written the way the game writes it (a flatbuffer: root = (link, name, buildings), building = (id, x, y,
rotation, tier, recipe/filter)); belts, splitters and undergrounds carry recipe -1 like in-game blueprints.

A design is only given an id after the uploaded blueprint has been downloaded again and decoded to exactly the
design's pieces (up to a shift). Ids that do not match are dropped (verify) and made again (upload).
"""
import gzip
import json
import os
import struct
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOGUE = os.path.join(HERE, '..', 'catalogue.json')
WORKER = 'https://builderment.hcrow.workers.dev'
CODE_ID = {'B': 3000, 'S': 3001, 'I': 3002, 'O': 3003}


# ---- the game file -------------------------------------------------------------------------------------------

def build(name, rows, link=''):
    """rows: [id, x, y, rotation, tier (0 = tier 1), recipe/filter, colour] in the game file's rotation."""
    out = bytearray()

    def pad4():
        while len(out) % 4:
            out.append(0)
    out += b'\0' * 4                                   # root offset, patched below
    rvt = len(out); out += struct.pack('<5H', 10, 16, 12, 8, 4); pad4()
    bvt = len(out); out += struct.pack('<8H', 16, 20, 18, 12, 8, 7, 6, 4); pad4()
    root = len(out); out += b'\0' * 16
    struct.pack_into('<I', out, 0, root)
    struct.pack_into('<i', out, root, root - rvt)
    vec = len(out); out += struct.pack('<I', len(rows)); out += b'\0' * (4 * len(rows))
    struct.pack_into('<I', out, root + 4, vec - (root + 4))
    for k, (bid, x, y, o, tier, rec, _colour) in enumerate(rows):
        pad4(); t = len(out)
        struct.pack_into('<I', out, vec + 4 + 4 * k, t - (vec + 4 + 4 * k))
        tb = bytearray(20)
        struct.pack_into('<i', tb, 0, t - bvt)
        struct.pack_into('<h', tb, 4, rec)
        struct.pack_into('<B', tb, 6, tier)
        struct.pack_into('<B', tb, 7, o)
        struct.pack_into('<i', tb, 8, y)
        struct.pack_into('<i', tb, 12, x)
        struct.pack_into('<H', tb, 18, bid)
        out += tb
    for fo, s in ((8, name), (12, link)):
        pad4(); p = len(out); b = s.encode()
        out += struct.pack('<I', len(b)) + b + b'\0'
        struct.pack_into('<I', out, root + fo, p - (root + fo))
    pad4()
    return bytes(out)


def game_rows(rows):
    """Decoded (site / Human-Crow) rows -> game file rows: every non-belt building is stored with rotation + 2;
    belts, splitters and undergrounds as they are. Tier is only kept for machines."""
    return [[r[0], r[1], r[2], r[3] if r[0] <= 3003 else (r[3] + 2) & 3, r[4] if r[0] > 3003 else 0, r[5], 0]
            for r in rows]


def grid_rows(grid):
    rows = []
    for y, line in enumerate(grid):
        for i in range(0, len(line), 2):
            t = line[i:i + 2]
            if t != '..':
                rows.append([CODE_ID[t[0]], i // 2, y, int(t[1]), 0, -1, 0])
    return rows


# ---- the blueprint service -----------------------------------------------------------------------------------

def _post(path, body):
    # the worker refuses Python's default User-Agent
    req = urllib.request.Request(WORKER + path, data=body, method='POST',
                                 headers={'Content-Type': 'text/plain', 'User-Agent': 'Mozilla/5.0 (buildermentsim catalogue tool)'})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    return gzip.decompress(data) if data[:2] == b'\x1f\x8b' else data


def decode(buf):
    ml = struct.unpack_from('<I', buf, 0)[0]
    meta = json.loads(buf[4:4 + ml].decode())
    n = meta.get('count', 0) if meta.get('buildings') else 0
    off, cols = 4 + ml, []
    for fmt, size in (('<H', 2), ('<i', 4), ('<i', 4), ('<B', 1), ('<B', 1), ('<h', 2), ('<I', 4)):
        off = (off + 3) & ~3
        cols.append([struct.unpack_from(fmt, buf, off + i * size)[0] for i in range(n)])
        off += n * size
    return meta, [list(r) for r in zip(*cols)] if n else []


def download(code):
    return decode(_post('/readBlp', _post('/downloadBlp', code.encode())))[1]


def upload(data):
    """game file bytes -> blueprint code (sent as is; the service wants gzip only above 1.5 MB)"""
    res = json.loads(_post('/uploadBlp', data).decode())
    return res.get('id') or res.get('blpId') or res.get('code')


def same_layout(a, b):
    """Same pieces up to a shift (belt-family rows only: id, x, y, rotation)."""
    if len(a) != len(b) or not a:
        return False
    norm = lambda rs: sorted((r[0], r[1] - min(q[1] for q in rs), r[2] - min(q[2] for q in rs), r[3] & 3) for r in rs)
    return norm(a) == norm(b)


def rotations(rows):
    """The layout under every quarter turn (catalogue designs are stored turned east to west; an older upload may
    be in its original turn). Belt-family rotation 0 N, 1 W, 2 S, 3 E turns with the tiles."""
    out, cur = [], [list(r) for r in rows]
    for _ in range(4):
        out.append(cur)
        cur = [[r[0], r[2], -r[1], (r[3] + 1) & 3] + r[4:] for r in cur]   # a quarter turn counter-clockwise
    return out


# ---- commands ------------------------------------------------------------------------------------------------

def design_name(d):
    return '%d:%d %s %dx%d' % (d['n'], d['m'], d['mode'], d['W'], d['H'])


def cmd_verify(cat, save, candidates=False):
    bad = 0
    if candidates:
        cand = json.load(open(os.path.join(HERE, 'candidate_ids.json')))
        for d in cat['designs']:
            if not d.get('id') and d.get('name') in cand:
                d['id'] = cand[d['name']]
    for d in cat['designs']:
        if not d.get('id') or d['source'] == 'wiki':     # wiki ids come from the wiki itself (may hold unused pieces)
            continue
        rows = grid_rows(d['grid'])
        try:
            got = download(d['id'])
        except Exception as ex:
            print('?? %-6s %-22s download failed: %s' % (d['id'], design_name(d), ex))
            continue
        ok = any(same_layout(got, r) for r in rotations(rows))
        if not ok:
            bad += 1
            print('XX %-6s %-22s does not match (%d pieces there, %d here): dropped' % (d['id'], design_name(d), len(got), len(rows)))
            d['id'] = None
        else:
            print('ok %-6s %s' % (d['id'], design_name(d)))
    print(bad, 'ids dropped')
    save()


def mirror_grid(grid):
    """Top <-> bottom mirror image of a catalogue grid (north and south swap)."""
    flip = {'0': '2', '2': '0'}
    return [''.join(r[i] + flip.get(r[i + 1], r[i + 1]) if r[i] != '.' else '..' for i in range(0, len(r), 2))
            for r in reversed(grid)]


def cmd_upload(cat, save, dry):
    todo = [(d, 'id', d['grid'], design_name(d)) for d in cat['designs'] if not d.get('id')]
    # a blueprint for the mirror image of every layout that is not its own mirror image (the designer's Mirror)
    todo += [(d, 'mirror_id', mirror_grid(d['grid']), design_name(d) + ' mirrored')
             for d in cat['designs'] if d['mode'] != 'sym' and not d.get('mirror_id')]
    print(len(todo), 'blueprints to make')
    for d, field, grid, name in todo:
        rows = grid_rows(grid)
        data = build(name, game_rows(rows))
        _, back = decode(_post('/readBlp', data))
        if not same_layout(back, rows):
            print('XX %-22s the service decodes the file differently: skipped' % name)
            continue
        if dry:
            print('ok %-22s file checks out (%d pieces)' % (name, len(rows)))
            continue
        code = upload(data)
        got = download(code)
        if same_layout(got, rows):
            d[field] = code
            print('up %-6s %s' % (code, name))
            save()                                       # keep progress if a later upload fails
        else:
            print('XX %-6s %-22s re-download differs: not used' % (code, name))


def main(argv):
    cat = json.load(open(CATALOGUE))

    def save():
        with open(CATALOGUE, 'w') as f:
            json.dump(cat, f, separators=(',', ':'))
    if argv[:1] == ['verify']:
        cmd_verify(cat, save, '--candidates' in argv)
    elif argv[:1] == ['upload']:
        cmd_upload(cat, save, '--dry' in argv)
    elif argv[:1] == ['file']:
        d = next(d for d in cat['designs'] if design_name(d) == argv[1] or d.get('name') == argv[1])
        open(argv[2], 'wb').write(build(design_name(d), game_rows(grid_rows(d['grid']))))
    else:
        print(__doc__)


if __name__ == '__main__':
    main(sys.argv[1:])
