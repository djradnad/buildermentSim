"""Run with: python -m pytest tests   (or python tests/test_all.py)

The pin test is the standing completeness check: every known working layout must be accepted by the exact model
with all of its tiles fixed. If a change to the model rejects one of them, the change rules out real layouts.
"""
import glob
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from bmsolver import spec as S, search, model, compose
from bmsolver.check import find_ports
from bmsolver.design import Design
from bmsolver.improve import Layout, improve, cut_window
from bmsolver.sim import simulate

HERE = os.path.dirname(__file__)
DESIGNS = sorted(glob.glob(os.path.join(HERE, 'designs', '*.txt')))


def layout_of(path):
    d = Design.load(path).normalized()
    ins, outs = find_ports(d)
    return Layout(d, ins, outs, mix=len(ins) > 1)


def test_known_designs_pass_exact_check():
    for f in DESIGNS:
        lay = layout_of(f)
        assert lay.check()['ok'], f


def test_known_designs_pinned_in_model():
    for f in DESIGNS:
        lay = layout_of(f)
        if len(lay.design) > 400:
            continue                     # the 27:27 is too big for a quick whole-layout model
        W, H = lay.design.size
        win, sub = cut_window(lay, 0, W - 1)
        win.fixed = {(x, y): lay.design.tiles.get((x, y)) for x in range(W) for y in range(H)}
        r = model.solve(win, timeout=60)
        assert r['status'] in ('OPTIMAL', 'FEASIBLE'), (f, r['status'])


def test_sim_matches_check_on_splitters():
    for f in DESIGNS:
        lay = layout_of(f)
        if len(lay.inputs) != 1:
            continue
        r = simulate(lay.design, lay.inputs, lay.outputs, minutes=2, warmup=1)
        n = len(lay.outputs)
        # outputs differ by at most one item over the 2 measured minutes (480/n may not be whole)
        assert all(abs(x - 480 / n) <= 0.5 + 1e-9 for x in r['out_per_min']), (f, r['out_per_min'])


def test_sim_balancer_single_input():
    lay = layout_of(os.path.join(HERE, 'designs', 'bal_4_4_9x6.txt'))
    for i in range(4):
        r = simulate(lay.design, lay.inputs, lay.outputs, minutes=2, warmup=1, feed=[i])
        assert r['out_per_min'] == [120.0] * 4


def test_search_small():
    r = search.find(S.splitter(5), timeout=30)
    assert (r['W'], r['H'], r['proven']) == (3, 5, True)
    r = search.find(S.splitter(6), symmetry='y', timeout=30)
    assert r is None                     # 1 input and 6 outputs can't both sit on the mirror axis
    r = search.find(S.splitter(6), symmetry='y', quasi=True, timeout=30)
    assert (r['W'], r['H'], r['proven']) == (2, 6, True)
    assert r['design'].is_symmetric('y', ignore_middle=True)
    r = search.find(S.merger(3), timeout=30)
    assert (r['W'], r['H']) == (1, 3)


def test_custom_sides():
    # 2 inputs from the top, outputs to the left: every output gets half of each input
    sp = S.Spec([S.Group('N', 2)], [S.Group('W', 2)], mix=True)
    r = search.find(sp, timeout=60)
    assert r is not None and r['report']['ok']


def test_improve_shrinks():
    sp = S.splitter(12)
    win = sp.window(5, 14)
    r = model.solve(win, timeout=30)
    ins, outs = sp.ports_used(win, r['options'])
    lay = Layout(Design(r['grid']), ins, outs, sp.rates, mix=False)
    new = improve(lay, timeout=10)
    W, H = new.design.size
    assert new.check()['ok'] and W * H < 5 * 14


def test_compose_4():
    lay = compose.compose(4, log=lambda m: None)
    assert lay.check()['ok']


def test_satnet_accepts_known_layouts():
    """The direct-CNF kissat path (bmsolver/satnet.py) must accept every known layout with all tiles fixed: test
    designs, the 3:3 boxes in /root/data (when present) and a sample of the catalogue. Checked once (2026-11-23)
    also for box66_28x6_fixed.txt (FEASIBLE, ~90 s and ~8 GB, so not repeated here) and all catalogue designs."""
    import json
    from bmsolver import satnet
    if not os.path.exists(satnet.KISSAT):
        print('skip: no kissat')
        return
    texts = [(f, open(f).read(), None) for f in DESIGNS] + \
        [(f, open(f).read(), None) for f in sorted(glob.glob('/root/data/box33_*.txt'))]
    cat = json.load(open(os.path.join(HERE, '..', 'site', 'catalogue.json')))['designs']
    for d in [d for d in cat if d['kind'] == 'balance' and d['W'] * d['H'] <= 40][:12] + \
            [d for d in cat if d['kind'] == 'split' and d['W'] * d['H'] <= 30][:6]:
        texts.append((d['id'], '\n'.join(d['grid']), ([((x, y), q) for x, y, q in d['ins']],
                                                     [((x, y), q) for x, y, q in d['outs']])))
    for name, txt, ports in texts:
        dsg = Design.from_text(txt).normalized()
        ins, outs = ports or find_ports(dsg)
        lay = Layout(dsg, ins, outs, mix=True)
        if len(lay.design) > 400:
            continue
        W, H = lay.design.size
        win, sub = cut_window(lay, 0, W - 1)
        grid = {(x, y): lay.design.tiles.get((x, y)) for x in range(W) for y in range(H)}
        assert satnet.pin(grid, win) == 'FEASIBLE', name


if __name__ == '__main__':
    for name, fn in list(globals().items()):
        if name.startswith('test_'):
            fn()
            print('ok', name, flush=True)


def test_staged_model_accepts_known_balancers():
    """radix.py (staged networks) must accept the known staged layouts with every tile fixed."""
    from bmsolver import radix
    for name, arity in (('bal_2_2_sym_3x8.txt', (2,)), ('bal_4_4_9x6.txt', (2, 2)), ('bal_4_4_11x5.txt', (2, 2))):
        lay = layout_of(os.path.join(HERE, 'designs', name))
        W, H = lay.design.size
        win, sub = cut_window(lay, 0, W - 1)
        win.fixed = {(x, y): lay.design.tiles.get((x, y)) for x in range(W) for y in range(H)}
        r = radix.solve(win, arity, 'cpsat', 60)
        assert r['status'] in ('OPTIMAL', 'FEASIBLE'), (name, r['status'])


def test_handrules_keep_known_layouts():
    """Every optional hand rule (bmsolver/handrules.py, BM_RULES) must accept the known layouts with every tile
    fixed, in the netplace models (edge labels and mix classes) and in the flow model."""
    from ortools.sat.python import cp_model
    from bmsolver import netplace as NP, handrules as HR
    from bmsolver.design import Design
    g18 = ('B1B1B1S1O1B2B1B1B1B1B1I1B1B1B1S1B1B1\n'
           'O1B0I1B1S1B1B2S1O1B0S1B1..B0I1B1B2B1\n'
           'O1B0B1B1B1I1B1B1B1B1B1B0O1B0B1S1B1I1\n')
    cases = []
    for f in DESIGNS:
        lay = layout_of(f)
        if len(lay.design) <= 400:
            cases.append(lay)
    d = Design.from_text(g18).normalized()
    ins, outs = find_ports(d)
    cases.append(Layout(d, ins, outs, mix=True))
    old = os.environ.get('BM_RULES')
    try:
        for lay in cases:
            W, H = lay.design.size
            win, sub = cut_window(lay, 0, W - 1)
            grid = {(x, y): lay.design.tiles.get((x, y)) for x in range(W) for y in range(H)}
            os.environ['BM_RULES'] = ''
            net = NP.extract(grid, win)
            win.fixed = dict(grid)
            os.environ['BM_RULES'] = 'f1,p1,static,adj3,hop3'
            assert HR.prefilter(net, win) is None
            for mod in (NP.build(net, win), NP.build_cls(net, win), model.Model(win, flows=True)):
                sv = cp_model.CpSolver()
                sv.parameters.max_time_in_seconds = 60
                sv.parameters.num_workers = 2
                st = sv.StatusName(sv.Solve(mod.m))
                assert st in ('OPTIMAL', 'FEASIBLE'), (W, H, st)
    finally:
        if old is None:
            os.environ.pop('BM_RULES', None)
        else:
            os.environ['BM_RULES'] = old
