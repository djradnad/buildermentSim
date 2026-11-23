"""Upload the grids in grids.json as game blueprints, download each again and check it matches. Prints name -> code."""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'site', 'tools'))
import blueprints as B
for name, g in json.load(open(os.path.join(os.path.dirname(__file__), 'grids.json'))):
    rows = B.grid_rows([r.strip() for r in g.split('/')])
    code = B.upload(B.build(name, B.game_rows(rows)))
    back = B.download(code)
    ok = any(B.same_layout(r, rows) for r in B.rotations(back))
    print('BP %s | %s | %s' % (name, code, 'verified' if ok else 'MISMATCH'), flush=True)
