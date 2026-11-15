"""Proven layout rules, in one place, with the checks solvers call before searching.

Every rule says what it claims, how it was proven (search, SAT box proof, column reachability or hand argument),
and how far the proof reaches (flow unit D, widths). A rule only goes in once it agrees with every known case.
Planners and box sweeps call these instead of searching boxes the rules already settle.

    python -m bmsolver.rules            # print the rule book
"""
from fractions import Fraction
from math import ceil

RULES = []


def rule(key, claim, proof, scope=''):
    RULES.append(dict(key=key, claim=claim, proof=proof, scope=scope))


# ---------------------------------------------------------------- lanes and space
rule('one-wide', 'A 1-wide strip cannot hold any splitter that splits (a splitter needs a free side to push into '
     'and its input from behind).', 'hand argument', 'all layouts')
rule('squeeze', 'h open rows carry at most floor(1.5 h) belts over a long stretch: 1->1, 2->3, 3->4, 4->6. One '
     'extra belt fits only over short stretches (2 in 1 row for L<=4, 4 in 2 rows for L<=2, 5 in 3 rows for L<=4). '
     'Getting in and out costs about 2 tiles at each end.', 'gadget SAT proofs (tools/routing/gadgets.py sq<k>_<h>)',
     'L up to 12, impossibles proven to width L+8')
rule('tunnels', 'Underground reach is 1..7 tiles. Same-direction tunnels in one row cannot overlap: an entrance links '
     'to the first exit ahead of it.', 'traced from the game code', 'all layouts')
rule('gadgets', 'Minimum boxes: reverse 2 belts 3x3, reverse 3 5x4/4x5, cross 4+4 6x4, cross 6+6 8x6, turn 4 6x5/5x6, '
     'swap 2+2 4x6/8x5/10x4, riffle 2+2 6x4/3x5. A crossover of 2 belts does not fit in 2 rows at any width.',
     'gadget SAT proofs; 2-row cross by column reachability (D=10, 12)', 'boxes as listed')

# ---------------------------------------------------------------- mixing arithmetic
rule('primes', 'Pair moves that only halve or take thirds (no loops) make shares whose denominators are 2^a 3^b. '
     'Any other prime (5, 7, ...) needs a loop: flow fed back so that 1/(1-g) brings the new prime in.',
     'hand argument (splitters divide by 2 or 3)', 'all layouts')
rule('splitter-min', 'Fewest splitters (game rules: one input belt, merging up to 3, split 2 or 3 ways, belts <= 1): '
     '2-belt 1/5-4/5 mix = 4 (3 impossible); 2-belt 1/7-6/7 mix = 5 (4 impossible, inner belts in 1/21); 3-belt (q,q,x) -> (3/5,3/5,4/5 q) piece = 6 (5 impossible).',
     'CP-SAT netlist search (tools/routing/netlist_cp.py)', 'flow unit 1/360')
rule('two-rows', 'Two full belts in 2 rows: halves, crossovers, fifths and every split in tenths are impossible at any '
     'width; 2:2 at 2 tall only ever reaches repeated quarter swaps (x -> 3/4 x + 1/4 y).',
     'column reachability (bmsolver/colreach.py)', 'flow units D=4,6,8 (2:2), 10, 15; D=12 only 0/12, 1/12 so far')
rule('loop-rows', 'The 2-belt 1/5-4/5 loop piece needs 3 rows (found 12x3, 9x4, 8x5, 7x6; 2 rows impossible). With a '
     'third belt passing through the same 3 rows: none up to 18 wide (<= 8 splitters).',
     'two-rows rule + gadget SAT (mixp1)', 'widths <= 18')

# ---------------------------------------------------------------- balancers
rule('nn-ntall', 'N:N at N tall exists for N = 3, 4, 5, 6 (18x3, 19x4, 28x5, 28x6); 2:2 at 2 tall is impossible.',
     'exact flow check of the built layouts; 2:2 by column reachability', '')


# ---------------------------------------------------------------- checks used by solvers
def lane_capacity(h, long=True):
    """belts h open rows can carry past an obstacle"""
    if h <= 0:
        return 0
    base = (3 * h) // 2
    return base if long else base + 1


def rows_needed(k, long=True):
    """fewest open rows that let k belts pass"""
    h = 0
    while lane_capacity(h, long) < k:
        h += 1
    return h


def section_fits(H, piece_rows, passing, long=True):
    """a piece that fills piece_rows rows while `passing` belts go by in the rest of an H-row band"""
    return lane_capacity(H - piece_rows, long) >= passing


def needs_loop(shares):
    """True when some share has a prime other than 2 or 3 in its denominator"""
    for s in shares:
        d = Fraction(s).denominator
        for p in (2, 3):
            while d % p == 0:
                d //= p
        if d != 1:
            return True
    return False


def book():
    out = []
    for r in RULES:
        out.append('%s: %s\n    proof: %s%s' % (r['key'], r['claim'], r['proof'], ('; scope: ' + r['scope']) if r['scope'] else ''))
    return '\n\n'.join(out)


if __name__ == '__main__':
    print(book())
    # sanity: the 5:5 5-tall section (3-row loop piece + 3 passing belts in 2 rows) must fit, a 4-row piece must not
    assert section_fits(5, 3, 3) and not section_fits(5, 4, 3)
    assert needs_loop([Fraction(1, 5)]) and not needs_loop([Fraction(1, 12)])
    assert rows_needed(3) == 2 and rows_needed(4) == 3
