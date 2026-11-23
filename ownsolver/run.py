"""Run bmown on a balancer / mixer window and re-check any find exactly.

  python3 ownsolver/run.py bal N M W H [--unmixed] [--timeout S]   free port positions (E inputs, W outputs)
  python3 ownsolver/run.py mix32 W [H]                              john's 3:2 pure-leftover mixer (pinned ports)
  python3 ownsolver/run.py spec FILE.json                           a spec file (pinned ports only)
Prints the solver's answer and, on FOUND, the exact re-check (tests/common.exact_check, design.flows engine)."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'tests'))
from common import S, balancer_window, mixer32_window, from_json, parse_grid, exact_check  # noqa: E402

BIN = os.path.join(HERE, 'bmown')


def win_json(win):
    js = dict(W=win.W, H=win.H, D=win.D, ncom=win.ncom,
              ports=[[p[0], p[1], d, k, list(v)] for (p, d), (k, v) in sorted(win.ports.items())],
              fixed=[[p[0], p[1], c] for p, c in sorted(win.fixed.items())],
              port_options=[[[[p[0], p[1], d, k, list(v)] for (p, d), (k, v) in sorted(opt.items())] for opt in grp]
                            for grp in win.port_options])
    return js


def run(win, timeout=60, extra=()):
    js = win_json(win)
    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
        json.dump(js, f)
        path = f.name
    out = subprocess.run([BIN, path, '--timeout', str(timeout)] + list(extra), capture_output=True, text=True).stdout
    os.unlink(path)
    lines = out.strip().split('\n')
    status = lines[0].split()[1]
    res = dict(status=status, stats=lines[-1])
    if status == 'FOUND':
        res['grid'] = '\n'.join(lines[1:1 + win.H])
        opts = [int(t) for t in lines[1 + win.H].split()[1:]]
        for grp, j in zip(win.port_options, opts):
            win.ports.update(grp[j])
        win.port_options = []
        res['check'] = exact_check(win, parse_grid(res['grid']))
    return res


def main(argv):
    timeout = 60
    if '--timeout' in argv:
        i = argv.index('--timeout'); timeout = float(argv[i + 1]); del argv[i:i + 2]
    unmixed = '--unmixed' in argv
    argv = [a for a in argv if a != '--unmixed']
    if argv[0] == 'bal':
        n, m, W, H = map(int, argv[1:5])
        win = S.balancer(n, m, mix=not unmixed).window(W, H)
        if win is None:
            print('ports do not fit'); return
    elif argv[0] == 'mix32':
        win = mixer32_window(int(argv[1]), int(argv[2]) if len(argv) > 2 else 4)
    else:
        win = from_json(json.load(open(argv[1])))
    r = run(win, timeout)
    print(r['status'], r['stats'])
    if 'grid' in r:
        print(r['grid'])
        print('exact check:', r['check'])


if __name__ == '__main__':
    main(sys.argv[1:])
