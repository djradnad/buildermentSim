// Headless run of the JavaScript sim, printing the same state digests as the Python reference (for parity checks).
//   node site/tools/run.mjs blueprint.json ticks every [x0 y0 x1 y1]
import { readFileSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { Factory } from '../js/gamesim.js';
import { rowsToBuildings, addFeeders, addDrains, guessFeedItem } from '../js/blueprint.js';

const [path, ticks, every, ...box] = process.argv.slice(2);
const d = JSON.parse(readFileSync(path, 'utf8'));
let rows = Array.isArray(d) ? d : d.rows;
rows = rows.map(r => Array.isArray(r) ? r : [r.id, r.x, r.y, r.o, r.tier ?? 0, r.recipe ?? r.filter ?? -1, 0]);
if (box.length === 4) {
  const [x0, y0, x1, y1] = box.map(Number);
  rows = rows.filter(r => x0 <= r[1] && r[1] <= x1 && y0 <= r[2] && r[2] <= y1);
}
const bs = rowsToBuildings(rows);
const added = addFeeders(bs);
addDrains(bs, -1);   // as the Python reference: splitters never drain
const probe = new Factory(bs);
const feeds = added.map((a, i) => {
  const g = probe.segs.find(s => s.nodes[0].x === a.at[0] && s.nodes[0].y === a.at[1]);
  return { at: a.at, kind: g ? guessFeedItem(probe, g, 'In' + i) : 'In' + i };
});
const w = new Factory(bs, { feeds, labsTakeAll: !process.env.STRICT });
const sortedHave = h => Object.entries(h).filter(e => e[1] !== 0).sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
const digest = () => {
  const st = [w.segs.map(g => [g.items, g.kinds, g.chosen]),
    w.machines.map(m => [m.made, m.out, sortedHave(m.have)]),
    w.arms.map(a => [a.moved, a.held])];
  if (process.env.DUMP) console.log(JSON.stringify(st));
  return createHash('md5').update(JSON.stringify(st)).digest('hex').slice(0, 12);
};
const sinkTotal = () => { let n = 0; for (const m of w.sinks.values()) for (const v of Object.values(m)) n += v; return n; };
console.log(JSON.stringify({ segs: w.segs.length, feeds: feeds.map(f => f.at), L: w.segs.map(g => g.L).slice(0, 4000),
  n_machines: w.machines.length, n_arms: w.arms.length }));
if (process.env.KINDS_OUT) writeFileSync(process.env.KINDS_OUT, JSON.stringify(feeds.map(f => f.kind)));
const t0 = Date.now();
for (let t = 1; t <= +ticks; t++) {
  w.tick();
  if (t % +every === 0) console.log(JSON.stringify({ t, d: digest(), made: w.machines.reduce((s, m) => s + m.made, 0), sink: sinkTotal() }));
}
console.error('ms', Date.now() - t0);
