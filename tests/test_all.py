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


if __name__ == '__main__':
    for name, fn in list(globals().items()):
        if name.startswith('test_'):
            fn()
            print('ok', name, flush=True)
