# Evolving solver heuristics on our CNFs

The AESAT / AE-Kissat-MAB method applied to Builderment layout CNFs, with Claude writing the candidate code.

- `make_pool.py` writes the cube CNFs that `sets/train.txt` and `sets/test.txt` point to (~1.2 GB in `~/cnfs`).
- `evo.py build` compiles a candidate: a base solver tree (e.g. the SAT Competition 2025 `AE_kissat2025_MAB`
  source, built once with `./configure --competition && make`) with one source file replaced.
- `evo.py table` scores solvers by PAR-2 on a set; SAT models are checked clause by clause; `--csv` keeps per-instance rows.
- `cand/` holds the candidate sources (`restart_*.c` replace `src/restart.c`, the VSIDS/CHB bandit).

Competition sources: `satcompetition/2025` on GitHub, folder `downloads/solvers/main/` (the github.io site is
blocked by the container proxy; a sparse git checkout works). Delete `.git` in an unpacked tree before `make`, or
its build-header script fails.
