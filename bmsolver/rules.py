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
rule('lane-ceiling', 'A channel of h rows carries at most floor(13h/8) belts in one direction once it is 8 lines long. '
     'Per row and column line there is one surface link and at most one same-direction tunnel; a tunnel\'s first and '
     'last line has no surface link (entrance and exit tiles), and the line from an exit to the next entrance has no '
     'tunnel, so at most 5 of any 8 consecutive lines are double. Counts links, so it holds with splitters and merges.',
     'hand argument (cut counting)', 'all heights, all layouts, stretches of 8+ lines')
rule('squeeze', 'Exact long-run capacity of h rows (belts that are not split): 1, 3, 4, 6, 7, 9, 10, 12 for h = 1..8, '
     'then 14 (h=9), 15 (h=10), 19 (h=12): floor(1.5h) up to 8 rows, more above (staircase over staggered tunnel chains, '
     'vertical hops at the walls). Longest open-ended stretch that still takes one belt more: 2 in 1 row 6, 4 in 2 rows 6, '
     '5 in 3 rows 8, 6 in 3 rows 6, 7 or 8 in 4 rows 6, 8 in 5 rows 10, 9 in 5 rows 6, 10 in 6 rows 8. With walls at the '
     'ends (belts must get in and out inside the window): 2 in 1 row L<=4, 4 in 2 rows L<=2, 5 in 3 rows L<=4.',
     'CP-SAT: repeating patterns (tools/routing/lanes.py, every belt traced) for the values; open-ended strips '
     '(tools/routing/strip.py) for the impossibles, which then hold for every longer channel',
     'h<=6 proven for every length; h=7 (11 belts) and h=8 (13 belts) only excluded for periods 8-16 so far')
rule('two-way', 'East and west tunnels may share a row, so opposing traffic is cheaper: a + b <= floor(19h/8) with each '
     'direction <= floor(13h/8). Exact frontier (a east, most b west): 1 row (1,1); 2 rows (1,3) (2,2) (3,1); '
     '3 rows (3,4) (4,3); 4 rows (2,6) (4,5) (5,4) (6,2).',
     'hand argument for the sum (each direction loses 3 of 8 lines, only one loss can coincide); CP-SAT repeating '
     'patterns for the frontier', 'frontier: periods 8 and 16')
rule('corner', 'A corridor of width h turning 90 degrees carries 1, 2, 4, 6, 7, 9 belts for h = 1..6: the same as straight '
     'except width 2, which carries 2, not 3. Hand bound: every surface path crosses the corner diagonal (h tiles), and '
     'only rows 2.. and columns ..h-3 can hold a tunnel that jumps it and still ends inside: <= h + 2 max(0, h-2).',
     'CP-SAT on the corner shape with free mouths (tools/routing/region.py); diagonal argument for h <= 2',
     'h<=4 proven with arms of 1 tile (so any arms); h=5,6 with arms of 16')
rule('no-gain', 'Splitters and merges never raise what a place can carry: the most flow any network pushes through a '
     'region equals the most unsplit belts that fit. Proof: tiles and tunnels each pass <= 1 belt, so by the integral '
     'flow theorem a network carrying more than k belts of flow contains k+1 tile-disjoint paths; keep only those '
     'pieces, turn each splitter on a path into a belt facing the output used. Tunnel links are unchanged because '
     'removing pieces never puts a nearer exit in front of an entrance. So every one-way capacity table here also '
     'bounds total flow of any splitter network.', 'hand argument', 'one direction of traffic; all shapes')
rule('hop-cost', 'k belts through h rows: at every column at least k - h belts are underground, and a hop hides a belt '
     'for at most 6 columns, so a stretch of L columns needs at least (k - h) L / 6 hops.', 'hand argument', 'all channels')
rule('junction', 'Two corridors crossing (arms 8, free mouths), belts east + belts south: 1 wide 1+1; 2 wide 3+2 or 2+3, '
     'never 3+3; 3 wide 4+3 (4+4 not excluded yet).', 'CP-SAT on the plus shape (tools/routing/crossing.py)', 'widths 1-3')
rule('streams', 'A lane holds one stream whatever its fill: k part-filled belts need the lanes of k full belts. Merge '
     'streams before a narrow place, split after it. Every tile and every tunnel passes at most one belt of flow, so '
     'any set of tiles and tunnels that separates inputs from outputs bounds the throughput.',
     'hand argument', 'all layouts')
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
LONG_RUN = {1: 1, 2: 3, 3: 4, 4: 6, 5: 7, 6: 9, 7: 10, 8: 12, 9: 14, 10: 15, 12: 19}   # found patterns (lower bounds)
PROVEN_MAX = {1: 1, 2: 3, 3: 4, 4: 6, 5: 7, 6: 9}                      # proven for every long stretch
CORNER = {1: 1, 2: 2, 3: 4, 4: 6, 5: 7, 6: 9}


def lane_ceiling(h):
    """proven upper bound on belts through h rows over a long stretch (sound for pruning)"""
    return PROVEN_MAX.get(h, (13 * h) // 8) if h > 0 else 0


def lane_capacity(h, long=True):
    """belts h open rows are known to carry past an obstacle (a pattern exists); long=False adds the one extra belt
    that fits over a short stretch"""
    if h <= 0:
        return 0
    base = LONG_RUN.get(h, (3 * h) // 2)
    return base if long else base + 1


def two_way_ceiling(h):
    """proven upper bound on east + west belts through h rows"""
    return (19 * h) // 8


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
    assert lane_capacity(9) == 14 and lane_ceiling(9) == 14 and lane_ceiling(5) == 7 and lane_ceiling(7) == 11
