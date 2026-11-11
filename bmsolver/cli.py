"""Command line: python -m bmsolver <command> ... (python -m bmsolver -h for help)."""
import argparse
import json
import os
import sys

from . import search, spec as S
from .check import check, find_ports, describe
from .design import Design

EXAMPLES = """examples:
  python -m bmsolver split 12                    smallest 1:12 splitter (inputs east, outputs west)
  python -m bmsolver split 7 --quasi --live      quasi-symmetric 1:7 (one turned middle tile), watch it in the browser
  python -m bmsolver balance 3 --goal both       smallest-area and most-square 3:3 balancers
  python -m bmsolver balance 8                   8:8 built from stages, then shrunk
  python -m bmsolver merge 4 --in N --out W      4:1 merger, inputs on top, output on the left
  python -m bmsolver custom examples/two_sides.json
  python -m bmsolver improve my_layout.json      shrink an existing blueprint
  python -m bmsolver check my_layout.json        is it balanced?
"""


def main(argv=None):
    ap = argparse.ArgumentParser(prog='bmsolver', description='Layout solver for Builderment belts, splitters and '
                                 'balancers.', epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    def solve_opts(p):
        p.add_argument('--in', dest='in_side', default='E', help='side of the inputs: N, E, S or W (default E)')
        p.add_argument('--out', dest='out_side', default='W', help='side of the outputs (default W)')
        p.add_argument('--goal', choices=['area', 'square', 'both'], default='area',
                       help='smallest area, most square (shortest longest side), or both')
        p.add_argument('--sym', action='store_true', help='mirror symmetric')
        p.add_argument('--quasi', nargs='?', type=int, const=1, default=0, metavar='N',
                       help='mirror symmetric except N tile pairs (default 1) in the middle row(s): turned another way, '
                       'or a splitter facing a plain belt like the input splitter of the wiki 1:even splitters')
        p.add_argument('--axis', choices=['y', 'x'], default=None,
                       help='mirror axis: y flips top/bottom (default for east/west ports), x flips left/right')
        p.add_argument('--timeout', type=float, default=60, help='seconds per window size (default 60)')
        p.add_argument('--time-limit', type=float, default=None, help='stop after this many seconds in total')
        p.add_argument('--workers', type=int, default=os.cpu_count() or 4, help='CPU threads for the solver')
        p.add_argument('--live', action='store_true', help='show progress on a local web page')
        p.add_argument('--port', type=int, default=8765, help='port for --live (default 8765)')
        p.add_argument('-o', '--out-dir', default='out', help='where to save results (default ./out)')

    p = sub.add_parser('split', help='1:N splitter')
    p.add_argument('n', type=int)
    solve_opts(p)
    p = sub.add_parser('merge', help='N:1 merger')
    p.add_argument('n', type=int)
    solve_opts(p)
    p = sub.add_parser('balance', help='N:M balancer (every output gets an equal share of every input)')
    p.add_argument('n', type=int)
    p.add_argument('m', type=int, nargs='?')
    p.add_argument('--no-mix', action='store_true', help='only equal totals per output (faster; fine when every '
                   'input carries the same item)')
    p.add_argument('--exact', action='store_true', help='always use the exact size search, even for big N:N')
    solve_opts(p)
    p = sub.add_parser('custom', help='inputs and outputs from a json file (see examples/)')
    p.add_argument('file')
    solve_opts(p)
    p = sub.add_parser('improve', help='shrink an existing layout (blueprint json or text grid)')
    p.add_argument('file')
    solve_opts(p)
    p.add_argument('--no-mix', action='store_true')
    p = sub.add_parser('check', help='check a layout with exact fractions')
    p.add_argument('file')
    p.add_argument('--in', dest='in_side', default='E')
    p.add_argument('--out', dest='out_side', default='W')
    p.add_argument('--no-mix', action='store_true')
    p = sub.add_parser('show', help='print a layout as text')
    p.add_argument('file')
    a = ap.parse_args(argv)

    if a.cmd == 'show':
        d = Design.load(a.file)
        print('%dx%d, %d pieces\n%s' % (d.size + (len(d), d.to_text())))
        return 0
    if a.cmd == 'check':
        d = Design.load(a.file)
        ins, outs = find_ports(d, (a.in_side.upper(),), (a.out_side.upper(),))
        rep = check(d, ins, outs, mix=not a.no_mix)
        print(describe(rep))
        return 0 if rep['ok'] else 1

    reporter = _reporter(a)
    os.makedirs(a.out_dir, exist_ok=True)
    symmetry = None
    if a.sym or a.quasi:
        symmetry = a.axis or ('y' if a.in_side.upper() in 'EW' else 'x')

    if a.cmd == 'improve':
        from .improve import Layout, improve
        d = Design.load(a.file)
        ins, outs = find_ports(d, (a.in_side.upper(),), (a.out_side.upper(),))
        lay = Layout(d, ins, outs, mix=not a.no_mix)
        new = improve(lay, timeout=min(a.timeout, 30), workers=a.workers, reporter=reporter, symmetry=symmetry,
                      time_limit=a.time_limit)
        _save(new.design, os.path.splitext(os.path.basename(a.file))[0] + '_improved', a.out_dir)
        return _finish(reporter)

    if a.cmd == 'split':
        sp = S.splitter(a.n, a.in_side, a.out_side)
    elif a.cmd == 'merge':
        sp = S.merger(a.n, a.in_side, a.out_side)
    elif a.cmd == 'balance':
        sp = S.balancer(a.n, a.m, a.in_side, a.out_side, mix=not a.no_mix)
        if (a.m in (None, a.n)) and a.n >= 4 and not a.exact and not a.no_mix:
            return _big_balancer(a, sp, reporter, symmetry)
    else:
        sp = load_spec(a.file)
        if symmetry is None and getattr(sp, 'symmetry', None):
            symmetry = sp.symmetry
            a.quasi = a.quasi or getattr(sp, 'quasi', False)
    goals = ['area', 'square'] if a.goal == 'both' else [a.goal]
    known = {}
    for goal in goals:
        reporter.message('%s %s, %s%s' % (sp.name, goal, 'quasi-symmetric ' if a.quasi else
                                          'symmetric ' if symmetry else '', 'searching'))
        r = search.find(sp, goal=goal, symmetry=symmetry, quasi=a.quasi, timeout=a.timeout,
                        time_limit=a.time_limit, workers=a.workers, reporter=reporter, known=known)
        if r is None:
            reporter.message('nothing found')
            continue
        known = {k: v for k, v in r['status_by_size'].items() if v != 'FOUND'}
        tag = '%s_%s%s' % (sp.name.replace(':', 'to'), 'quasi_' if a.quasi else 'sym_' if symmetry else '', goal)
        path = _save(r['design'], tag, a.out_dir)
        reporter.message('%s: %dx%d, %d pieces, %s -> %s' % (goal, r['W'], r['H'], len(r['design']),
                         'proven smallest' if r['proven'] else 'not proven smallest', path))
    return _finish(reporter)


def _big_balancer(a, sp, reporter, symmetry):
    from .compose import compose, factors
    from .improve import improve
    if factors(a.n) is None:
        reporter.message('%d:%d has no 2x/3x stage plan; using the exact search (can take long)' % (a.n, a.n))
        r = search.find(sp, goal=a.goal if a.goal != 'both' else 'area', timeout=a.timeout,
                        time_limit=a.time_limit, workers=a.workers, reporter=reporter)
        if r:
            _save(r['design'], sp.name.replace(':', 'to'), a.out_dir)
        return _finish(reporter)
    if symmetry:
        reporter.message('note: the stage builder makes plain (not mirrored) balancers; --sym is ignored')
    reporter.message('%s: building from mixing stages' % sp.name)
    lay = compose(a.n, timeout=a.timeout, log=reporter.message)
    reporter.solution(lay.design, *lay.design.size, 'built from stages')
    new = improve(lay, timeout=min(a.timeout, 30), workers=a.workers, reporter=reporter, time_limit=a.time_limit)
    path = _save(new.design, sp.name.replace(':', 'to'), a.out_dir)
    reporter.message('%dx%d, %d pieces -> %s' % (new.design.size + (len(new.design), path)))
    return _finish(reporter)


def load_spec(path):
    """Custom spec json: {"name": ..., "inputs": [{"side": "N", "count": 2, "at": 0, "rate": 1}, ...],
    "outputs": [...], "mix": true, "symmetry": "y", "quasi": false}"""
    with open(path) as f:
        d = json.load(f)
    grp = lambda g: S.Group(g['side'], g['count'], g.get('at'), g.get('positions'), g.get('rate'))
    sp = S.Spec([grp(g) for g in d['inputs']], [grp(g) for g in d['outputs']], mix=d.get('mix', True),
                name=d.get('name'))
    sp.symmetry, sp.quasi = d.get('symmetry'), d.get('quasi', False)
    return sp


def _reporter(a):
    if getattr(a, 'live', False):
        from .live import LiveView
        return LiveView(port=a.port)
    return search.PrintReporter()


def _save(design, tag, out_dir):
    d = design.normalized()
    tag = ''.join(c if c.isalnum() or c in '-_' else '_' for c in tag)
    base = os.path.join(out_dir, '%s_%dx%d' % ((tag,) + d.size))
    d.save(base + '.json', name=tag)
    d.save(base + '.txt')
    return base + '.json'


def _finish(reporter):
    if hasattr(reporter, 'finish'):
        reporter.finish()
    return 0


if __name__ == '__main__':
    sys.exit(main())
