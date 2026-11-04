// Run a layout in the game-exact sim and report settled rates.
//   node tools/simlayout.mjs layout.json      {buildings: [{id,x,y,o,tier,recipe,filter}], feeds: [{at:[x,y], kind, perMin}],
//                                             warmup: minutes, minutes: measured minutes}
// Prints JSON {machines: [{x,y,id,recipe,perMin}], sinks: {"x,y": {item: perMin}}} (per game minute).
import { readFileSync } from 'fs';
import { Factory } from '../site/js/gamesim.js';
const L = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const bs = L.buildings.map(b => ({ tier: 1, recipe: 0, filter: 0, ...b }));
const w = new Factory(bs, { feeds: L.feeds });
const warm = (L.warmup ?? 10) * 3600, H = (L.minutes ?? 30) * 3600;
for (let i = 0; i < warm; i++) w.tick();
const made0 = w.machines.map(m => m.made);
const sink0 = new Map([...w.sinks].map(([g, m]) => [g, { ...m }]));
for (let i = 0; i < H; i++) w.tick();
const mins = H / 3600;
const machines = w.machines.map((m, i) => ({ x: m.b.x, y: m.b.y, id: m.b.id, recipe: m.b.recipe, perMin: (m.made - made0[i]) / mins }));
const sinks = {};
for (const [g, m] of w.sinks) {
  const last = g.nodes[g.nodes.length - 1], b0 = sink0.get(g) || {};
  const r = {};
  for (const k of Object.keys(m)) r[k] = (m[k] - (b0[k] || 0)) / mins;
  sinks[last.x + ',' + last.y] = r;
}
const fed = w.feeds.length;
console.log(JSON.stringify({ machines, sinks, feeds: fed, wanted: (L.feeds || []).length }));
