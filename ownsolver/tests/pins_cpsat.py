"""Validate the pinned specs themselves: CP-SAT (model.py, 1 worker) must accept each pin (W*H <= MAXAREA)."""
import glob, json, os, sys
from common import from_json
from bmsolver import model
MAXAREA = int(sys.argv[1]) if len(sys.argv) > 1 else 200
for p in sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'specs', 'pin_*.json'))):
    js = json.load(open(p))
    if js['W'] * js['H'] > MAXAREA:
        continue
    r = model.solve(from_json(js), timeout=60, workers=1)
    print('%-50s %-10s %.1fs' % (os.path.basename(p), r['status'], r['time']), flush=True)
