// World generator: john's reimplementation of Builderment's resource-node placement (bldgen2.cpp, checked node for
// node against real saves), compiled to WebAssembly. One instance per mode, because the generator reads its mode
// from the environment once at start-up, like the release engine.
//   gen2  iPhone/iPad, Gen 2 world type
//   ios1  iPhone/iPad, Gen 1 world type
// Resource flags: 1 wood, 2 iron, 4 copper, 8 stone, 16 coal, 32 wolframite, 64 uranium.

export const NODE_ITEM = { 1: 'Wood Log', 2: 'Iron Ore', 4: 'Copper Ore', 8: 'Stone', 16: 'Coal', 32: 'Wolframite', 64: 'Uranium Ore' };
export const MODES = { gen2: 'iOS Gen 2', ios1: 'iOS Gen 1' };
export const WORLD_SIZES = [50, 75, 100, 150, 200];
export const RESOURCES = [50, 75, 100, 150, 200];

const B62 = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz';

// Seed as typed in the game (base 62, case-sensitive) or '#123' for the plain number. iOS wraps past 2^32.
export function parseSeed(text) {
  text = String(text).trim();
  if (text.startsWith('#')) {
    const n = Number(text.slice(1));
    if (!Number.isInteger(n) || n < 0) throw new Error('bad seed number');
    return n >>> 0;
  }
  if (!text || /[^0-9A-Za-z]/.test(text)) throw new Error('a seed is letters and digits (base 62)');
  let v = 0n;
  for (const c of text) v = v * 62n + BigInt(B62.indexOf(c));
  return Number(v % 4294967296n);
}

export function seedText(n) {
  let s = '';
  n = n >>> 0;
  do { s = B62[n % 62] + s; n = Math.floor(n / 62); } while (n > 0);
  return s;
}

function wasi(env, memory) {
  const enc = new TextEncoder();
  const vars = env.map(e => enc.encode(e + '\0'));
  const mem = () => new DataView(memory().buffer);
  return {
    environ_sizes_get(pc, ps) { mem().setUint32(pc, vars.length, true); mem().setUint32(ps, vars.reduce((a, v) => a + v.length, 0), true); return 0; },
    environ_get(pp, pb) {
      const d = mem(); const u8 = new Uint8Array(memory().buffer);
      for (const v of vars) { d.setUint32(pp, pb, true); pp += 4; u8.set(v, pb); pb += v.length; }
      return 0;
    },
    fd_write(fd, iov, n, pw) {          // stdout/stderr: count the bytes, print stderr lines to the console
      const d = mem(); let tot = 0, s = '';
      for (let i = 0; i < n; i++) {
        const p = d.getUint32(iov + 8 * i, true), l = d.getUint32(iov + 8 * i + 4, true);
        s += new TextDecoder().decode(new Uint8Array(memory().buffer, p, l)); tot += l;
      }
      if (s.trim()) console.warn('worldgen:', s.trim());
      d.setUint32(pw, tot, true); return 0;
    },
    fd_close: () => 0, fd_seek: () => 70, fd_read: () => 8, fd_fdstat_get: () => 8, fd_fdstat_set_flags: () => 8,
    fd_prestat_get: () => 8, fd_prestat_dir_name: () => 8, path_open: () => 44,
    proc_exit(c) { throw new Error('worldgen exited ' + c); },
  };
}

let modulePromise = null;
const instances = {};

async function loadModule(url) {
  if (!modulePromise) {
    modulePromise = (async () => {
      const r = await fetch(url);
      if (typeof process !== 'undefined' && !r.arrayBuffer) throw new Error('no fetch');
      return WebAssembly.compile(await r.arrayBuffer());
    })();
  }
  return modulePromise;
}

export async function generator(mode = 'gen2', { url = new URL('../wasm/bldgen.wasm', import.meta.url), module = null } = {}) {
  if (instances[mode]) return instances[mode];
  const mod = module || await loadModule(url);
  const env = mode === 'ios1' ? ['GEN1=1', 'IOSG1=1'] : [];
  let inst;
  inst = await WebAssembly.instantiate(mod, { wasi_snapshot_preview1: wasi(env, () => inst.exports.memory) });
  inst.exports._initialize();
  const gen = (seed, worldSize = 100, resources = 100) => {
    const n = inst.exports.bld_generate(seed >>> 0, worldSize, resources);
    if (n < 0) throw new Error('world size must be one of ' + WORLD_SIZES.join(', '));
    return new Int32Array(inst.exports.memory.buffer, inst.exports.bld_nodes(), 3 * n).slice();  // x, y, flag
  };
  gen.half = (seed, ws, ra) => inst.exports.bld_half(seed >>> 0, ws, ra);
  gen.terrain = (seed, ws, ra, x0, y0, w, h, step = 1) => {
    if (inst.exports.bld_terrain(seed >>> 0, ws, ra, x0, y0, w, h, step) < 0) throw new Error('bad world size');
    return new Uint8Array(inst.exports.memory.buffer, inst.exports.bld_terrain_buf(), w * h).slice();
  };
  instances[mode] = gen;
  return gen;
}
