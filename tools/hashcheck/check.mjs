// Checks the site's TileMap (site/js/gamesim.js) slot for slot against the real robin_hood.h with the game's key hash.
//   g++ -O1 -std=c++17 rh.cpp -o rh && node check.mjs
import { readFileSync, writeFileSync } from 'fs';
import { execFileSync } from 'child_process';
const src = readFileSync(new URL('../../site/js/gamesim.js', import.meta.url), 'utf8')
  .replace(/^class TileMap/m, 'export class TileMap')
  .replace(/from '\.\//g, "from '" + new URL('../../site/js/', import.meta.url).href);
writeFileSync(new URL('./_gs.mjs', import.meta.url), src);
const { TileMap } = await import('./_gs.mjs');
let seed = 1; const rnd = () => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed / 2147483648; };
let bad = 0;
for (let t = 0; t < 120; t++) {
  const n = 1 + Math.floor(rnd() * (t < 100 ? 300 : 1500)), span = [5, 30, 200, 20000][t % 4];
  const seen = new Set(), ks = [];
  while (ks.length < n) {
    const x = Math.floor((rnd() - 0.5) * span * 2), y = Math.floor((rnd() - 0.5) * span * 2), s = x + ',' + y;
    if (seen.has(s)) { if (seen.size >= (2 * span) ** 2) break; continue; }
    seen.add(s); ks.push([x, y]);
  }
  const tm = new TileMap(); ks.forEach((k, i) => tm.insert(k[0], k[1], i));
  const js = tm.keys.map((k, i) => k && tm.info[i] ? `${i}:${k.v}/${tm.info[i]}` : null).filter(Boolean).join(' ');
  const out = execFileSync(new URL('./rh', import.meta.url).pathname, { input: ks.length + '\n' + ks.map(q => q.join(' ')).join('\n') }).toString().split('\n');
  const hdr = `mask ${tm.mask} inc ${tm.inc} shift ${tm.shift} max ${tm.max} mul ${tm.mul.toString(16)}`;
  if (js !== out[1].trim() || !out[0].startsWith(hdr)) { bad++; console.log('mismatch: case', t, 'keys', ks.length); }
}
console.log(bad ? `${bad} of 120 cases differ` : 'all 120 cases identical (slots, info bytes, multiplier)');
