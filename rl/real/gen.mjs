// Stage scripts for one item speedrun (john 2026-11-17): research stage by stage, each stage at the lab spot best
// for that stage's resources (relocating is free apart from items left on belts), extractors pre-built near the next
// spot while the current research runs.
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { GAMEDATA } from '../../site/js/gamesim.js';
import { Run } from './runner.mjs';
import { bestSpots, nearestUnused } from './sites.mjs';
const SPOT = { K: 3, near: 6, gap: 12 };   // lab spots anywhere on the map, near the stage's nodes
const HERE = path.dirname(fileURLToPath(import.meta.url));
const T = JSON.parse(fs.readFileSync(path.join(HERE, '..', 'data', 'tables.json')));
const RAW = new Set(['Wood Log', 'Iron Ore', 'Copper Ore', 'Stone', 'Coal', 'Wolframite', 'Uranium Ore']);
const REC = Object.fromEntries(Object.entries(GAMEDATA.recipes).map(([k, r]) => [k.replace(/^recipe_/, ''), r]));
export const MAIN = {};
for (const [k, r] of Object.entries(REC)) if (!r.alt && !MAIN[r.out[0].item]) MAIN[r.out[0].item] = k;
const BID = Object.fromEntries(Object.entries(GAMEDATA.buildings).map(([k, d]) => [k, d.id]));

// extractors per raw for one machine of item's main chain at tier 1 (as the planner sizes it)
export function rawNeeds(item, mult = 1, acc = {}) {
  if (RAW.has(item)) { acc[item] = (acc[item] || 0) + mult; return acc; }
  const r = REC[MAIN[item]];
  for (const inp of r.in) {
    const per = inp.amount * 60 / r.seconds;
    if (RAW.has(inp.item)) acc[inp.item] = (acc[inp.item] || 0) + Math.max(1, Math.ceil(per / 15 - 1e-9));
    else { const r2 = REC[MAIN[inp.item]]; rawNeeds(inp.item, Math.max(1, Math.ceil(per / (r2.out[0].amount * 60 / r2.seconds) - 1e-9)), acc); }
  }
  return acc;
}

// techs needed before item can be made, in research order
export function techsFor(item) {
  const need = new Set();
  const add = id => { if (need.has(id)) return; need.add(id); for (const r of T.techs.find(t => t.id === id).requires) add(r); };
  const visit = it => {
    if (RAW.has(it)) return;
    const r = REC[MAIN[it]];
    for (const t of T.techs) for (const m of t.modifiers)
      if (m.UnlockedRecipeId === r.id || m.UnlockedBuildingId === BID[r.building]) add(t.id);
    for (const i of r.in) visit(i.item);
  };
  visit(item);
  const order = [];
  const done = new Set();
  while (order.length < need.size) for (const id of need) if (!done.has(id) && T.techs.find(t => t.id === id).requires.every(r => done.has(r) || !need.has(r))) { done.add(id); order.push(id); }
  return order.map(id => T.techs.find(t => t.id === id)).filter(t => t.id !== 4000);
}

// params: per stage {site: index into best spots, feeds: extra raw feeds, pre: extractors pre-built for next stage}
export function script(item, params = []) {
  const run = new Run();
  const stages = techsFor(item).map(t => ({ name: t.name, id: t.id, items: Object.keys(t.cost).filter(k => k !== 'Gold') }));
  stages.push({ name: 'goal', items: [item] });
  const out = [];
  let site = null, prev = null;
  const used = new Set();                                   // nodes earlier stages' extractors take
  stages.forEach((st, i) => {
    const p = params[i] || {};
    const needs = {};
    for (const it of st.items) for (const [r, n] of Object.entries(rawNeeds(it))) needs[r] = (needs[r] || 0) + n;
    const spots = bestSpots(run, needs, { ...SPOT, used, center: site || [0, 0] });
    let sp = spots[Math.min(p.site || 0, spots.length - 1)];
    if (site && !p.site) {                                  // stay put unless the move saves real belt length
      const here = bestSpots(run, needs, { reach: 0, K: 1, center: site, used })[0];
      if (here && here.score <= spots[0].score + 8) sp = here;
    }
    if (i > 0) out.push(['waitTech', stages[i - 1].id]);   // the previous research must finish first
    if (!site) out.push(['lab', [sp.x, sp.y]]);
    else if (site[0] !== sp.x || site[1] !== sp.y) { out.push(['movelab', [sp.x, sp.y]]); prev = site; }
    site = [sp.x, sp.y];
    if (p.clean && i > 0) out.push(['clean']);             // old stage lines no lab can take from: delete for gold
    for (const it of st.items) {
      const mult = RAW.has(it) ? 1 + (p.feeds || 0) : 1 + (p.lines || 0);
      for (const [r, n] of Object.entries(rawNeeds(it))) for (const k of nearestUnused(run, r, sp.x, sp.y, n * mult, used)) used.add(k);
    }
    for (const it of st.items) {
      if (RAW.has(it)) for (let k = 0; k < 1 + (p.feeds || 0); k++) out.push(['feed', it]);
      else for (let k = 0; k < 1 + (p.lines || 0); k++) out.push(['line', MAIN[it]]);
    }
    if (p.relab && prev) { out.push(['lab', prev]); prev = null; }   // a lab back on the old site: its backlog arrives
    // pre-build for the next stage near its spot (filled while this research runs)
    const nx = stages[i + 1];
    if (nx && p.pre) {
      const nneeds = {};
      for (const it of nx.items) for (const [r, n] of Object.entries(rawNeeds(it))) nneeds[r] = (nneeds[r] || 0) + n;
      const nsp = bestSpots(run, nneeds, { ...SPOT, used, center: site });
      const ns = nsp[Math.min((params[i + 1] || {}).site || 0, nsp.length - 1)];
      for (const [r, n] of Object.entries(nneeds)) for (let k = 0; k < Math.min(n, p.pre); k++) out.splice(out.length, 0, ['pre', r, [ns.x, ns.y]]);
    }
  });
  return { stages: stages.map(s => s.name), script: out };
}
