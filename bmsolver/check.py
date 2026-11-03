"""Independent check of a finished layout with exact fractions (does not use the solver model)."""
from fractions import Fraction

from .design import Design, flows
from .pieces import opposite


def find_ports(design, in_sides=('E',), out_sides=('W',)):
    """Guess the inputs and outputs of a layout from its bounding box: inputs are pieces on an input side that
    accept items from outside and get nothing from inside, outputs are pieces on an output side that push out. Sorted along the side."""
    x0, y0, x1, y1 = design.bbox()
    edge = {'E': lambda p: p[0] == x1, 'W': lambda p: p[0] == x0, 'N': lambda p: p[1] == y0, 'S': lambda p: p[1] == y1}
    out_dir = {'N': 0, 'W': 1, 'S': 2, 'E': 3}
    fed = {q for p, q, k in design.links()}
    ins = [(p, d) for p, d in design.open_inputs()
           if p not in fed and any(edge[s](p) and opposite(d) == out_dir[s] for s in in_sides)]
    outs = [(p, d) for p, d in design.open_outputs() if any(edge[s](p) and d == out_dir[s] for s in out_sides)]
    key = lambda t: (t[1], t[0][1], t[0][0])
    return sorted(ins, key=key), sorted(outs, key=key)


def check(design, inputs, outputs, rates=None, mix=True):
    """inputs: [(tile, direction items move in)], outputs: [(tile, direction pushed out)].
    rates: belts per input (default: as full as the outputs allow). Returns a report dict:
      ok            every output gets an equal share of every input (mix) or of the total, nothing overflows,
                    nothing is stuck, every piece carries items
      shares        per output, the fraction of each input it receives
      overfull      tiles that would need more than one belt
      stuck         pieces that receive items but can't pass them on
      idle          pieces that carry nothing (can be removed)"""
    if not isinstance(design, Design):
        design = Design(design)
    n, m = len(inputs), len(outputs)
    if rates is None:
        rates = [min(Fraction(1), Fraction(m, n))] * n
    src = {}
    for i, (p, d) in enumerate(inputs):
        v = [Fraction(0)] * n
        v[i] = Fraction(rates[i])
        src[p] = [a + b for a, b in zip(src.get(p, [0] * n), v)]
    inflow, drained = flows(design, src, set(outputs))
    shares = []
    for o in outputs:
        got = drained.get(o, [Fraction(0)] * n)
        shares.append([g / Fraction(rates[i]) if rates[i] else Fraction(0) for i, g in enumerate(got)])
    want = Fraction(1, m)
    if mix:
        balanced = all(s == want for row in shares for s in row)
    else:
        totals = [sum(drained.get(o, [0] * n)) for o in outputs]
        balanced = len(set(totals)) == 1 and totals[0] > 0
    outs = {p: 0 for p in design.tiles}
    for p, q, k in design.links():
        outs[p] += 1
    sinks = {p for p, d in outputs}
    overfull = sorted(p for p, v in inflow.items() if sum(v) > 1)
    stuck = sorted(p for p, v in inflow.items() if sum(v) > 0 and outs[p] == 0 and p not in sinks)
    idle = sorted(p for p, v in inflow.items() if sum(v) == 0)
    return dict(ok=balanced and not overfull and not stuck and not idle, balanced=balanced, shares=shares,
                overfull=overfull, stuck=stuck, idle=idle, inputs=n, outputs=m)


def describe(report):
    lines = ['%d inputs -> %d outputs: %s' % (report['inputs'], report['outputs'], 'OK' if report['ok'] else 'NOT OK')]
    if not report['balanced']:
        for j, row in enumerate(report['shares']):
            lines.append('  output %d gets %s' % (j + 1, ' '.join(str(s) for s in row)))
    for k in ('overfull', 'stuck', 'idle'):
        if report[k]:
            lines.append('  %s: %s' % (k, report[k][:10]))
    return '\n'.join(lines)


def prune(design, inputs, outputs, rates=None, mix=True):
    """Remove pieces that carry nothing (left over from a solve that didn't minimise pieces). Safe: such pieces
    get nothing from any link, so taking them away can't change any flow."""
    d = design.copy() if isinstance(design, Design) else Design(design)
    while True:
        idle = check(d, inputs, outputs, rates, mix)['idle']
        if not idle:
            return d
        for p in idle:
            del d.tiles[p]
