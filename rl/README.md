# Builderment RL: learning to speedrun and to build compact

Inspired by [Factoriax](https://arxiv.org/abs/2610.05569) (Beurskens, Tomilin, Simão, 2026): a Factorio-like
simulator in JAX that runs ~2M steps/s on one A100, with PPO trained on an "Easy Rocket" benchmark. Their agent
learned to mine, craft and place machines but **never built a connected, working factory**: it acted one tile
and one tick at a time, and nothing rewarded a working layout. We split the problem so that both halves are
learnable and checkable:

| stage | env | the agent decides | time step | exact parts |
|---|---|---|---|---|
| 1 | `rl/econ.py` speedrun economy | what to build and when: extractors on real Gen 2 nodes, machines per recipe, tier upgrades, labs, vaults | event steps (10 s / 1 min / 5 min waits, 1 s per build) | tech tree, build/upgrade costs, gold per item, tick-exact recipe rates, Gen 2 extractor rates |
| 2 | `rl/layout.py` (next) | where: place modules/machines on the grid and connect them with belts | per placement | flows from the game's settled belt rules (`bmsolver.check`, exact fractions); final layouts replayed in the game-exact tick sim (`site/js/gamesim.js`) |
| 3 | `rl/tiles.py` (next) | tile-by-tile pieces (belts, splitters, undergrounds, machines) in a small window | per tile | reward = proven throughput, then area; we know the SAT-proven minimum for many targets, so RL is graded against the optimum |

Stage 1 alone answers "what is the fastest build order"; stage 2 turns a build order into a real factory and is where
compactness is rewarded; stage 3 is a benchmark where RL meets our proven-optimal solver results.

## Stage 1 details (`rl/econ.py`)

- **Data**: `rl/data/tables.json`, built by `rl/data/build_tables.py` from the game tables (`D_BaseTechnology`,
  `D_BaseBuildings`, `D_BaseItems` SellValue, `site/js/gamedata.js`) and the tick-exact rate tables
  (`tools/recipe_rates.mjs`). Worlds: `node rl/worlds.mjs rl/data/worlds.json <seed>...` (exact Gen 2 generator).
- **Actions (78)**: 3 waits, build one machine of any of 58 recipes, build an extractor on the nearest free node of
  any of 7 resources (pays its belt to the lab, 4 gold per tile), upgrade a building class one tier (pays per
  building), build a lab, build a gold vault. A mask gives the legal ones.
- **Flow**: steady rates recomputed after each change. Items are pooled per type, each item's supply is shared
  among its consumers in proportion to demand, machine use = its scarcest input; everything left over goes to the
  labs/vaults (4 belts each). Labs give gold (SellValue) and research progress.
- **Reward** (john's speedrun rules, 2026-11-16): −minutes elapsed, +1 the first time each item type is made (the
  in-game "time to first item" card; times kept in `env.first`), +0.2 per tech. The run ends at the first Earth
  Token, the time cap, or bankruptcy (no income and nothing affordable: charged the rest of the cap).
- **Start**: 2000 gold, no lab (the tutorial has you place one for 1000).
- **Speed**: ~14k env steps/s for random play on 4 CPU cores with 256 worlds in one batch; ~7k/s while training PPO.
- **Baseline**: `python -m rl.baseline` is a calculator-style greedy planner (chase the cheapest main-line research,
  build the machine counts a rate calculator asks for). It reaches 67 of 93 techs in 25 h of game time on 2CCJhI but
  stalls before Matter Duplication: the target for RL to beat.

### Open assumptions (switches in `SETTINGS`, to confirm or trace from the exe)

- traced in the PC exe (2026-11-16): labs (FUN_140ee9560) and gold vaults (FUN_140ee9200) both hand their items
  each tick to FUN_140ee7500, which converts them through the item table's SellValue (FUN_140ee5ce0) and adds
  the result to the wallet; only labs also call the research update (FUN_140ee3800)
- confirmed by john: 2000 start gold, no lab placed; a delivered item counts for every available tech
  (`progress='all'`); finish = first Earth Token, with per-item first-production milestones
- builds cost 1 s of game time each; fill-up delays are ignored (settled rates only)

## After the speedrun: max ET (john, 2026-11-16)

Phase 2 goal: the highest **steady** ET/min (real production, no stockpiles in storage), then, at max ET, the most
**compact** layout: all production buildings (not extractors, power plants, belts, undergrounds, arms, splitters)
inside a small area, usually the big open area of the map. Time to max ET matters little; the AI may need to farm
gold first. This needs the stage 2 layout env (placement on the real map + belts) with the steady rate checked by
the exact flow rules and the game-exact sim.

## Train

```
pip install numpy jax optax
python -m rl.ppo --steps 3e7 --envs 512 --out runs/econ1
```

PPO: shared MLP 256x256, masked categorical, GAE (γ 0.999, λ 0.95), clip 0.2, Adam 3e-4, running obs
normalisation (the Factoriax recipe). On a CUDA machine install `jax[cuda12]`; the env is NumPy, so the GPU only
helps the network; a JAX port of `econ.py` (all fixed-shape array ops already) is the next speed step.
