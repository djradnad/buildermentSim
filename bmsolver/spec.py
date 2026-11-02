"""What to build: input and output lanes on the sides of a rectangle, and what every output must receive.

A Spec has groups of inputs and groups of outputs. A group is a run of `count` neighbouring lanes on one side
('N', 'E', 'S', 'W'). Its position along the side is free (the solver picks it) unless `at` gives the first lane's
offset, or `positions` lists every lane's offset. Offsets count from the top for E/W sides and from the left for
N/S sides.

Every output gets an equal share of every input (mix=True, a balancer), or only an equal share of the total
(mix=False: faster, enough when all inputs carry the same item). A splitter is 1 input, a merger is 1 output.
Input rates are in belts (1 = a full belt). By default inputs run as full as the outputs allow.
"""
import math
from fractions import Fraction

from .model import Window
from .pieces import SIDE_NAMES

EDGE_DIR = {'N': 0, 'W': 1, 'S': 2, 'E': 3}       # direction pointing out of the window across that side


class Group:
    def __init__(self, side, count, at=None, positions=None, rate=None):
        side = side.upper()
        if side not in SIDE_NAMES:
            raise ValueError('side must be one of N, E, S, W')
        self.side, self.count, self.at, self.rate = side, count, at, rate
        self.positions = list(positions) if positions is not None else None
        if self.positions is not None and len(self.positions) != count:
            raise ValueError('positions must list %d offsets' % count)

    def __repr__(self):
        where = 'free' if self.at is None and self.positions is None else (self.positions or 'at %d' % self.at)
        return '%d on %s (%s)' % (self.count, self.side, where)

    def offsets(self, length):
        """Every allowed list of lane offsets along a side of this length."""
        if self.positions is not None:
            return [self.positions] if max(self.positions) < length else []
        if self.at is not None:
            return [list(range(self.at, self.at + self.count))] if self.at + self.count <= length else []
        return [list(range(a, a + self.count)) for a in range(length - self.count + 1)]


def edge_tile(side, offset, W, H):
    return {'E': (W - 1, offset), 'W': (0, offset), 'N': (offset, 0), 'S': (offset, H - 1)}[side]


class Spec:
    def __init__(self, inputs, outputs, mix=True, unit_mult=1, name=None):
        self.inputs, self.outputs, self.mix = list(inputs), list(outputs), mix
        self.unit_mult = unit_mult
        n_in = sum(g.count for g in self.inputs)
        n_out = sum(g.count for g in self.outputs)
        self.n_in, self.n_out = n_in, n_out
        self.name = name or '%d:%d' % (n_in, n_out)
        default = min(Fraction(1), Fraction(n_out, n_in))
        self.rates = []                 # one rate per input lane
        for g in self.inputs:
            self.rates += [Fraction(g.rate) if g.rate is not None else default] * g.count
        total = sum(self.rates)
        if total > n_out:
            raise ValueError('inputs carry %s belts but %d outputs hold at most %d' % (total, n_out, n_out))
        need = [12] + [Fraction(r, n_out).denominator for r in self.rates] + [r.denominator for r in self.rates]
        if not mix:
            need.append(Fraction(total, n_out).denominator)
        self.D = math.lcm(*need) * unit_mult      # units per full belt
        self.ncom = n_in if mix and n_in > 1 else 1

    # ------------------------------------------------------------------ amounts
    def input_vecs(self):
        D, out = self.D, []
        for i, r in enumerate(self.rates):
            v = [0] * self.ncom
            v[i if self.ncom > 1 else 0] = int(r * D)
            out.append(v)
        return out

    def output_vec(self):
        D, M = self.D, self.n_out
        if self.ncom > 1:
            return [int(r * D / M) for r in self.rates]
        return [int(sum(self.rates) * D / M)]

    # ------------------------------------------------------------------ geometry
    def sides(self):
        return {g.side for g in self.inputs + self.outputs}

    def min_size(self):
        """Smallest (W, H) the ports alone allow."""
        need = {s: 0 for s in 'NESW'}
        for g in self.inputs + self.outputs:
            need[g.side] += g.count
            if g.positions is not None:
                need[g.side] = max(need[g.side], max(g.positions) + 1)
            elif g.at is not None:
                need[g.side] = max(need[g.side], g.at + g.count)
        return max(1, need['N'], need['S']), max(1, need['E'], need['W'])

    def monotone(self):
        """True when a layout that fits W x H also fits any larger window (so size proofs carry over): all ports
        on two opposite sides with free positions, no symmetry. Grow by a straight column at the input side or
        an empty row at the far end."""
        s = self.sides()
        free = all(g.at is None and g.positions is None for g in self.inputs + self.outputs)
        ins = {g.side for g in self.inputs}
        return free and (s <= {'E', 'W'} or s <= {'N', 'S'}) and len(ins) == 1 and len(s) == 2

    def window(self, W, H, symmetry=None, quasi=False):
        """The model window for a W x H rectangle, or None if the ports don't fit."""
        win = Window(W, H, self.ncom, self.D)
        win.symmetry, win.quasi = symmetry, quasi
        vin, vout = self.input_vecs(), self.output_vec()
        groups = [(g, 'in', vin[sum(x.count for x in self.inputs[:k]):][:g.count])
                  for k, g in enumerate(self.inputs)] + [(g, 'out', [vout] * g.count) for g in self.outputs]
        per_side = {}
        for g, kind, vecs in groups:
            per_side[g.side] = per_side.get(g.side, 0) + 1
        self_mirrored = {'y': 'EW', 'x': 'NS'}.get(symmetry, '')
        for g, kind, vecs in groups:
            length = H if g.side in 'EW' else W
            opts = g.offsets(length)
            if g.side in self_mirrored and per_side[g.side] == 1:
                opts = [o for o in opts if mirror_ok(o, length, quasi)]
            if not opts:
                return None
            d = EDGE_DIR[g.side]
            options = [{(edge_tile(g.side, o, W, H), d): (kind, vecs[i]) for i, o in enumerate(offs)} for offs in opts]
            if len(options) == 1:
                for key, val in options[0].items():
                    if key in win.ports:
                        return None
                    win.ports[key] = val
            else:
                win.port_options.append(options)
        return win

    def ports_used(self, win, options):
        """(inputs, outputs) of a solved window in lane order: inputs as (tile, direction items move in), outputs as
        (tile, direction pushed out). options: the chosen option of each free group (model result)."""
        ports = dict(win.ports)
        for group, j in zip(win.port_options, options):
            ports.update(group[j])
        ins = sorted([(p, (d + 2) % 4, v) for (p, d), (k, v) in ports.items() if k == 'in'], key=_lane_key)
        outs = sorted([(p, d, v) for (p, d), (k, v) in ports.items() if k == 'out'], key=_lane_key)
        return [(p, d) for p, d, v in ins], [(p, d) for p, d, v in outs]


def mirror_ok(offsets, length, quasi=False):
    """Are these lane offsets their own mirror image (quasi: apart from the middle one or two)?"""
    s = set(offsets)
    diff = s ^ {length - 1 - o for o in s}
    return not diff or (quasi and diff <= {(length - 1) // 2, length // 2})


def _lane_key(t):
    p, d, v = t
    return (v.index(max(v)) if len(v) > 1 else 0, d, p[1], p[0])


# ---------------------------------------------------------------------- common shapes
def splitter(n, in_side='E', out_side='W'):
    """1 input split evenly into n outputs (inputs east, outputs west by default)."""
    return Spec([Group(in_side, 1)], [Group(out_side, n)], mix=False, name='1:%d' % n)


def merger(n, in_side='E', out_side='W'):
    """n inputs merged evenly into 1 output (each input at 1/n belt)."""
    return Spec([Group(in_side, n)], [Group(out_side, 1)], mix=True, name='%d:1' % n)


def balancer(n, m=None, in_side='E', out_side='W', mix=True):
    """n inputs, m outputs, every output gets an equal share of every input."""
    m = n if m is None else m
    return Spec([Group(in_side, n)], [Group(out_side, m)], mix=mix, name='%d:%d' % (n, m))


def parse_ratio(text):
    a, b = text.split(':')
    return int(a), int(b)
