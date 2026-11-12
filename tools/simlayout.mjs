// Run a layout in the game-exact sim and report settled rates.
//   node tools/simlayout.mjs layout.json      {buildings: [{id,x,y,o,tier,recipe,filter}], feeds: [{at:[x,y], kind, perMin}],
//                                             warmup: minutes, minutes: measured minutes, sinks?: [[x,y]] (only
//                                             these open belt ends take items; default every open belt end)}
// Prints JSON {machines: [{x,y,id,recipe,perMin}], sinks: {"x,y": {item: perMin}}, fedRates: [{at,kind,want,perMin}]}
// (per game minute; fedRates = what each feed really got onto its belt).
import { readFileSync } from 'fs';
import { Factory } from '../site/js/gamesim.js';
const L = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const bs = L.buildings.map(b => ({ tier: 1, recipe: 0, filter: 0, ...b }));
const w = new Factory(bs, { feeds: L.feeds, sinks: L.sinks, sinkRates: L.sinkRates });
const warm = (L.warmup ?? 10) * 3600, H = (L.minutes ?? 30) * 3600;
for (let i = 0; i < warm; i++) w.tick();
const made0 = w.machines.map(m => m.made);
const fed0 = w.feeds.map(f => f.n);
const probeSeg = (L.probes || []).map(p => w.segs.find(g => g.nodes.some(n => n.x === p[0] && n.y === p[1])));
const probe0 = probeSeg.map(g => g ? (g.ins || 0) : 0);
const sink0 = new Map([...w.sinks].map(([g, m]) => [g, { ...m }]));
const idle = w.machines.map(() => ({}));               // measured ticks each machine stood still, by what it was missing
for (let i = 0; i < H; i++) {
  w.tick();
  for (let k = 0; k < w.machines.length; k++) {
    const m = w.machines[k];
    if (m.prog >= 0) continue;
    const miss = m.rec.in.filter(q => m.have[q.item] < q.amount);
    if (!miss.length) idle[k].output = (idle[k].output || 0) + 1;
    for (const q of miss) idle[k][q.item] = (idle[k][q.item] || 0) + 1;
  }
}
const mins = H / 3600;
const machines = w.machines.map((m, i) => ({ x: m.b.x, y: m.b.y, id: m.b.id, recipe: m.b.recipe, perMin: (m.made - made0[i]) / mins,
  out: m.out, lack: m.rec.in.filter(q => m.have[q.item] < q.amount).map(q => q.item),
  idle: Object.fromEntries(Object.entries(idle[i]).map(([k, v]) => [k, +(v / H).toFixed(4)])) }));
const sinks = {};
for (const [g, m] of w.sinks) {
  const last = g.nodes[g.nodes.length - 1], b0 = sink0.get(g) || {};
  const r = {};
  for (const k of Object.keys(m)) r[k] = (m[k] - (b0[k] || 0)) / mins;
  sinks[last.x + ',' + last.y] = r;
}
const fed = w.feeds.length;
const fedRates = w.feeds.map((f, i) => ({ at: f.at, kind: f.kind, want: f.perMin, perMin: (f.n - fed0[i]) / mins }));
const probes = probeSeg.map((g, i) => g ? ((g.ins || 0) - probe0[i]) / mins : null);   // items per minute entering the belt run at each probe tile
console.log(JSON.stringify({ machines, sinks, feeds: fed, fedRates, probes, wanted: (L.feeds || []).length }));
