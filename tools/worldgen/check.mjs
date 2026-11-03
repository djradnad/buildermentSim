// Node-for-node check of site/wasm/bldgen.wasm against a file of real worlds:
//   node tools/worldgen/check.mjs <worlds file> <gen2|ios1|pc1> [max worlds]
// File format: "W <seed> <world size> <resources>" lines, each followed by "<item id 11..17> <count> x y x y ..."
import fs from 'fs';
import zlib from 'zlib';
import { generator, decodeSinCos } from '../../site/js/worldgen.js';
const here = new URL('.', import.meta.url);
const module = await WebAssembly.compile(fs.readFileSync(new URL('../../site/wasm/bldgen.wasm', here)));
const IDF = { 11: 1, 12: 8, 13: 2, 14: 4, 15: 16, 16: 32, 17: 64 };
const [file, mode, N] = process.argv.slice(2);
const sincos = mode === 'pc1' ? decodeSinCos(new Uint8Array(zlib.gunzipSync(fs.readFileSync(new URL('../../site/wasm/sincos_pc.bin.gz', here))))) : null;
const g = await generator(mode, { module, sincos });
let worlds = [], cur;
for (const l of fs.readFileSync(file, 'utf8').split('\n')) {
  const p = l.trim().split(/\s+/); if (!p[0]) continue;
  if (p[0] === 'W') { cur = { s: +p[1], ws: +p[2], ra: +p[3], obs: new Map() }; worlds.push(cur); }
  else { const v = p.slice(2).map(Number); for (let i = 0; i < v.length; i += 2) cur.obs.set(v[i] + ',' + v[i + 1], IDF[p[0]]); }
}
worlds = worlds.slice(0, +N || worlds.length);
let ok = 0;
for (const w of worlds) {
  const a = g(w.s, w.ws, w.ra);
  let same = a.length / 3 === w.obs.size;
  for (let i = 0; same && i < a.length; i += 3) same = w.obs.get(a[i] + ',' + a[i + 1]) === a[i + 2];
  if (same) ok++; else console.log('MISS', w.s, w.ws, w.ra);
}
console.log(`${mode}: ${ok}/${worlds.length} worlds exact`);
