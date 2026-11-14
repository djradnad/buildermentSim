"""Gather the proven routing gadget results into one table for solvers: per case and height, the narrowest width
found (checked) and whether every narrower width at that height is proven impossible.

    python tools/routing/laws.py /mnt/project-files/builderment/routing/laws.json
Squeeze cases (sq<k>_<h>) are listed per stretch length L instead.
"""
import json
import os
import sys

OUT = os.path.expanduser('~/data/gadgets')


def load(name):
    st = {}
    for line in open(os.path.join(OUT, name + '.status')):
        k, v = line.split()[:2]
        st[k] = v                                     # last verdict wins
    return st


def no_ports(name, W, key):
    """the bundles don't even fit on the edges (the gadget tool skips such boxes without a status line)"""
    sys.path.insert(0, os.path.dirname(__file__))
    import gadgets
    if key.startswith('L'):
        return False
    H = int(key)
    mix = name.startswith(('mrgx', 'splx', 'trap', 'tap'))
    return (gadgets.window_mix(name, W, H) if mix else gadgets.window(name, W, H)) is None


def table():
    res = {}
    for f in sorted(os.listdir(OUT)):
        if not f.endswith('.status'):
            continue
        name = f[:-7]
        st = load(name)
        rows = {}
        for k, v in st.items():
            base, _, L = k.partition('L')
            W, H = map(int, base.split('x'))
            key = ('L%s' % L) if L else str(H)
            rows.setdefault(key, {})[W] = v
        out = {}
        for key, ws in rows.items():
            found = sorted(w for w, v in ws.items() if v == 'FOUND')
            if not found:
                below = [w for w, v in ws.items() if v == 'INFEASIBLE']
                out[key] = dict(min_W=None, impossible_up_to=max(below) if below else None)
                continue
            w0 = found[0]
            lo = 1 if not key.startswith('L') else int(key[1:]) + 2
            proven = all(ws.get(w) == 'INFEASIBLE' or no_ports(name, w, key) for w in range(lo, w0))
            out[key] = dict(min_W=w0, proven_min=proven)
        res[name] = out
    return res


if __name__ == '__main__':
    t = table()
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(OUT, 'laws.json')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(t, open(path, 'w'), indent=1, sort_keys=True)
    for name, rows in t.items():
        print(name, {k: (v['min_W'], v.get('proven_min', v.get('impossible_up_to'))) for k, v in sorted(rows.items())})
