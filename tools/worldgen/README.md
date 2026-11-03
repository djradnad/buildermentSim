# World generator (WebAssembly)

A standalone reimplementation of Builderment's resource-node placement, written from scratch by matching the
game's output (no game code is included). It powers the World tab of the site: type a seed, see every resource
node, and run a build with its extractors mining the nodes under them.

| Mode | Game | Checked node for node |
|------|------|------------------------|
| `gen2` | iPhone/iPad, Gen 2 worlds | 5,456 / 5,456 community worlds, 56 / 56 own saves |
| `ios1` | iPhone/iPad, Gen 1 worlds | 29 / 29 own saves |
| `pc1`  | Steam (PC), Gen 1 worlds | 42 / 42 own saves |

## Files

- `bldgen2_wasm.cpp`: the generator (terrain noise, land mask, node placement, per-mode arithmetic). The mode is read
  once from the environment, like the command-line engine: nothing = iOS Gen 2, `GEN1=1 IOSG1=1` = iOS Gen 1,
  `GEN1=1 SINCOS=4` = PC Gen 1. Other switches in the file are experiment knobs; the defaults are the checked models.
- `noise2.h`: FastNoise 2D simplex FBM rewritten so it can repeat arm64 fused multiply-add rounding (iOS).
- `FastNoise_bld.cpp`: FastNoise (MIT, Jordan Peck) with one switch that repeats the PC game's `/fp:fast` arithmetic
  in 2D simplex FBM (one folded constant, and 70 * (n0 + n1 + n2) distributed in octaves 2 and up).
- `wasm_main.cpp`: the exports used by `site/js/worldgen.js`.
- `compat.h`: small shims for wasm32 (`_putenv_s`, SSE prefetch).
- `third_party/`: FastNoise.h and robin_hood.h (both MIT).
- `sincos_fast.cpp`: writes the PC sin/cos table. The Steam game computes sin and cos of each placement angle with
  MSVC's `/fp:fast` combined sincos, which differs from other libraries by an ulp on about half the angles, so PC
  Gen 1 needs that exact table. Build it with MSVC (`cl /O2 /fp:fast sincos_fast.cpp`) and run it to get
  `sincos_fast.bin` (8 MB), then `python make_sincos_gz.py sincos_fast.bin ../../site/wasm/sincos_pc.bin.gz`
  (0.48 MB, loaded by the site only when PC Gen 1 is picked).
- `check.mjs`: node-for-node check of the built wasm against a file of real worlds.

## Build

zig 0.14.1 (`pip install ziglang==0.14.1`; 0.13 cannot build libc++ for WASI):

    python -m ziglang c++ -target wasm32-wasi -mexec-model=reactor -O2 -s -std=c++17 -ffp-contract=off \
        -fno-exceptions -include compat.h wasm_main.cpp FastNoise_bld.cpp -o ../../site/wasm/bldgen.wasm

`-ffp-contract=off` matters: every fused multiply-add the models need is written out explicitly.
On wasm32 `size_t` is 32 bits, so the map hash returns `uint64_t`; robin_hood mixes all 64 bits and the order the
generator visits nodes in depends on it.

Exports: `bld_generate(seed, ws, ra)` returns the node count, `bld_nodes()` points at int32 triples (x, y, flag);
flags 1 wood, 2 iron, 4 copper, 8 stone, 16 coal, 32 wolframite, 64 uranium. `bld_terrain(seed, ws, ra, x0, y0, w,
h, step)` fills `bld_terrain_buf()` with a terrain class per sampled tile. `bld_half(seed, ws, ra)` is the half size
of the map square. `bld_sincos_buf()` is where the PC table goes before the first PC world.

About 10 to 35 ms per world in a browser. The map's y axis is drawn as save y going down; that orientation is not
checked against the game's screen yet.
