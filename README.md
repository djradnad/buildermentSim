# bmsolver: layout solver for Builderment

Finds compact layouts of belts, splitters and underground belts for Builderment:
splitters (1:N), mergers (N:1), balancers (N:M) and your own shapes with inputs and outputs on any side.
Small layouts come with a proof that no smaller window works. Results are checked with exact fractions and can be
run through a tick-by-tick belt simulation that follows the game's rules.

```
$ python -m bmsolver split 5
  3x5 12 pieces best (proven smallest)
<..
S<S
ssv
SS<
S<.
```

(`<^v>` belts, `S` splitter, `I`/`O` underground in/out; upper case faces east/west, lower case north/south.
Items flow from the east side to the west side here.)

## Install

Python 3.9 or newer, then:

```
pip install ortools
git clone <this repo> && cd <repo>
python -m bmsolver -h
```

(or `pip install .` to get a `bmsolver` command)

## What you can ask for

| command | what it does |
|---|---|
| `python -m bmsolver split 12` | smallest 1:12 splitter |
| `python -m bmsolver split 7 --quasi` | mirror symmetric except the middle row (or two) |
| `python -m bmsolver split 9 --sym` | fully mirror symmetric |
| `python -m bmsolver merge 4` | 4:1 merger |
| `python -m bmsolver balance 3` | 3:3 balancer, exact search |
| `python -m bmsolver balance 8` | 8:8 balancer, built from stages then shrunk (see below) |
| `python -m bmsolver balance 4 6` | 4:6, every output gets an equal share of every input |
| `python -m bmsolver custom examples/two_sides.json` | your own inputs/outputs |
| `python -m bmsolver improve layout.json` | make an existing layout smaller |
| `python -m bmsolver check layout.json` | is it balanced? (exact) |
| `python -m bmsolver show layout.json` | print it as text |

Useful options: `--goal area|square|both` (smallest area, most square, or both), `--in N --out W` (sides),
`--timeout 60` (seconds per window size), `--time-limit 600` (overall), `--live` (watch it in your browser),
`-o out` (where results go).

Results are saved as blueprint json (`{"meta": ..., "rows": [[id, x, y, rotation, tier, recipe, filter], ...]}`,
the format community blueprint tools use) and as a text grid.

### Live view

Add `--live` and a page opens at `http://127.0.0.1:8765/`. It shows the best layout so far, every window size
tried (green found, red proven impossible, grey ran out of time) and a log, updating twice a second. Nothing leaves
your computer. Use it to watch a long run and to decide when a result is good enough to stop.

### Custom shapes

```json
{
  "name": "2 top + 2 bottom to 4 left, equal totals",
  "inputs": [{"side": "N", "count": 2}, {"side": "S", "count": 2}],
  "outputs": [{"side": "W", "count": 4}],
  "mix": false
}
```

Each group is a run of neighbouring lanes on one side. Leave out `at` to let the solver place the group, or give
`at` (offset of the first lane, from the top for E/W sides, from the left for N/S) or `positions` (every lane).
`"rate": 0.5` makes an input half a belt. `"mix": true` means every output gets an equal share of every input;
`false` only asks for equal totals, which is faster and enough when every input carries the same item.

## How it works

**Game rules.** A belt takes items from its back and both sides and pushes out of its front. A splitter takes from
its back only and pushes out of front, left and right, alternating between the sides that can take items. An
underground entrance sends items to the first exit of the same direction 1 to 7 tiles ahead (several entrances can
share an exit). Machines and other buildings are not modelled yet.

**Exact model** (`model.py`). A W x H window, each tile empty or one piece in one direction, solved with Google
OR-Tools CP-SAT. Flows are integers (a full belt is D units, one counter per input when mixing matters). Every link
carries at least one unit and at most a full belt, every piece passes on what it gets, and a splitter sends the same
amount of every input through each of its outputs, which is what the game settles into. Inputs and outputs are
ports on the window's edges; their positions can be left free.

**Size search** (`search.py`). Window sizes are tried from the smallest up. Each size is either solved, proven
impossible, or runs out of time. The first size that works is the answer, and it is *proven smallest* when every
smaller size was proven impossible. Then the pieces are polished: fewest pieces (belts preferred over splitters
and undergrounds), then fewest turns.

**Improver** (`improve.py`). Takes any working layout and re-solves a narrow strip of columns exactly, one column
narrower, keeping every flow that crosses the strip's edges (same amounts, same mixtures). If it works, the layout
lost a column. Rows are done the same way on the layout turned a quarter turn. Lanes with the same mixture of
inputs count as one inside a strip, which keeps strips fast even for big balancers. Strips start small and grow
when nothing more comes off.

**Big balancers** (`compose.py`). An N:N balancer for N made of 2s and 3s (4, 6, 8, 9, 12, ...) is built from
stages: each stage mixes groups of 2 or 3 lanes with a small balancer block, and shuffle strips between the stages
bring the right lanes together. That gives a working (big) layout in seconds, which the improver then shrinks.
The mixer blocks are in `bmsolver/blocks.json` (rebuild with `python -m bmsolver.compose`).

**Checks** (`check.py`, `sim.py`). Every result is checked with exact fractions, independently of the solver
(per output, the share of each input; no belt over capacity; nothing stuck; nothing idle). `sim.py` runs a layout
tick by tick with the game's own belt rules (segment lengths with curves, item spacing, splitter round robin,
merge priority, float32 maths), so you can see real throughput once items back up:

```python
from bmsolver.design import Design
from bmsolver.check import find_ports
from bmsolver.sim import simulate
d = Design.load('out/3to3_area_8x5.json')
ins, outs = find_ports(d)
print(simulate(d, ins, outs, minutes=3, feed=[0])['out_per_min'])   # feed only the first input
```

## What to expect

On a 4-core laptop: 1:5 well under a second, 1:12 a second, 1:24 about 90 s (all proven smallest), 3:3 one to
two minutes. Splitters up to about 1:35 and balancers up to 9:9 are in range; 4:4 and up use the stage builder
and are good but not proven smallest. Run times vary a lot between runs (the solver uses several threads that race).

Limits for now: big N:N balancers from the stage builder work but are far from the best known sizes (4:4 comes
out 10x6 to 11x6 where 9x6 is known; 8:8 20x12, 9:9 21x15), so better builders are a good place to help; the
stage builder makes plain (not mirrored) balancers; mixing custom shapes with 4+ inputs get slow; the improver needs inputs and outputs on
two opposite sides; machines, arms and recipes are not in the model yet.

## Layout of the code

```
bmsolver/
  pieces.py    piece codes, directions, how pieces connect
  design.py    a layout: text and blueprint files, mirror/rotate, exact steady-state flows
  check.py     exact check of a layout, finding its inputs/outputs, removing idle pieces
  model.py     the CP-SAT window model
  spec.py      what to build: input/output groups on sides, splitter/merger/balancer helpers
  search.py    smallest window search with proofs
  improve.py   shrink an existing layout strip by strip
  compose.py   build big N:N balancers from stages
  sim.py       tick-by-tick belt simulation (game rules)
  live.py      local live web page
  cli.py       command line
tests/         python -m pytest tests (known designs, pins, search, sim)
examples/      custom shape files and library use
```

## Contributing

Ideas and fixes are welcome: open an issue or a pull request. Before changing `model.py`, run the tests: the pin
test fixes every known working layout in `tests/designs/` into the model and fails if the model would reject it,
so a new rule can't silently rule out real layouts. New good layouts for `tests/designs/` (text grid, inputs east,
outputs west) are very welcome too.

## Credits

Started by john (djradnad) with Claude. Thanks to the Builderment community and to Human-Crow's tools and
simulator, whose work made checking layouts possible. The lane-class idea for balancers comes from
[Factorio-SAT](https://github.com/R-O-C-K-E-T/Factorio-SAT).

MIT licence, see LICENSE.
