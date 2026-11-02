// Blueprints: reading them, and finding the open belt ends that become inputs and outputs.

import { World, Factory, DV, ITEM_BY_GAME_ID } from './gamesim.js';

export const WORKER = 'https://builderment.hcrow.workers.dev';

// Blueprint rows [id, x, y, rotation, tier (0 = tier 1), recipe or arm filter (-1 = none), colour] -> buildings.
export function rowsToBuildings(rows) {
  return rows.map(r => {
    const arm = r[0] === 3012;
    const rf = r[5] ?? -1;
    return {
      id: r[0], x: r[1], y: r[2], o: r[3], tier: (r[4] ?? 0) + 1,
      recipe: arm || rf < 0 ? 0 : rf,
      filter: arm && rf > 0 ? (ITEM_BY_GAME_ID[rf] || 'item#' + rf) : 0,   // 0 and -1: no filter
    };
  });
}

export function buildingsToRows(bs) {
  return bs.map(b => [b.id, b.x, b.y, b.o, (b.tier || 1) - 1, b.id === 3012 ? -1 : (b.recipe || -1), 0]);
}

// Accepts {"rows": [...]} / {"meta", "rows"} json, a bare rows array, or a text grid of belt codes.
export function parseBlueprintText(text) {
  text = text.trim();
  if (text.startsWith('{') || text.startsWith('[')) {
    const d = JSON.parse(text);
    const rows = Array.isArray(d) ? d : d.rows;
    if (!rows) throw new Error('no "rows" in this json');
    return { meta: (d.meta || {}), rows };
  }
  return { meta: { name: 'text grid' }, rows: gridToRows(text) };
}

// Text grids as the solver writes them (.txt files): two characters per tile, piece and direction, e.g.
// "B1S3..I2" (B belt, S splitter, I/O underground in/out; 0 N, 1 W, 2 S, 3 E; ".." empty).
// A grid of plain arrows (< ^ v >) also works.
export function gridToRows(text) {
  const lines = text.split(/\r?\n/).map(l => l.trim()).filter(l => l.length);
  const rows = [];
  const two = lines.every(l => /^([BSIO][0-3]|\.\.)+$/.test(l));
  lines.forEach((line, y) => {
    if (two) {
      for (let i = 0, x = 0; i < line.length; i += 2, x++) {
        const tok = line.slice(i, i + 2);
        if (tok !== '..') rows.push([{ B: 3000, S: 3001, I: 3002, O: 3003 }[tok[0]], x, y, +tok[1], 0, -1, 0]);
      }
      return;
    }
    [...line].forEach((ch, x) => {
      const belt = { '^': 0, '<': 1, 'v': 2, '>': 3 }[ch];
      if (belt !== undefined) rows.push([3000, x, y, belt, 0, -1, 0]);
    });
  });
  if (rows.length === 0) throw new Error('could not read this text');
  return rows;
}

export async function fetchBlueprint(id) {
  id = id.trim().replace(/^.*[\/=]/, '');
  const urls = [`${WORKER}/blueprint/${id}`, `${WORKER}/${id}`, `${WORKER}/?id=${id}`];
  let last;
  for (const u of urls) {
    try {
      const r = await fetch(u);
      if (!r.ok) { last = new Error(`${u}: ${r.status}`); continue; }
      const d = await r.json();
      if (d.rows) return { meta: d.meta || { name: id }, rows: d.rows };
      last = new Error('unexpected answer from the blueprint service');
    } catch (e) { last = e; }
  }
  throw last;
}

const k = (x, y) => x + ',' + y;

// Belt starts with nothing feeding them (no input link, no arm dropping there): one belt tile is added behind
// each, pointing in. Returns [{at: [x, y] of the new tile, into: [x, y]}]. Same rule as the Python sim.
export function addFeeders(buildings, skip = new Set()) {
  const w = new Factory(buildings);
  const drops = new Set(w.arms.map(a => k(a.dst[0], a.dst[1])));
  const starts = new Set(w.segs.map(g => g.nodes[0]));
  const added = [];
  for (const n of w.belt.values()) {
    if (n.inputs.length || n.sub === 3 || drops.has(k(n.x, n.y)) || skip.has(k(n.x, n.y))) continue;
    if (n.sub === 0 && !starts.has(n)) continue;
    const [dx, dy] = DV[n.dir];
    const p = [n.x - dx, n.y - dy];
    if (w.occ.has(k(p[0], p[1]))) continue;
    buildings.push({ id: 3000, x: p[0], y: p[1], o: n.dir, tier: 1, recipe: 0, filter: 0, added: 'in' });
    added.push({ at: p, into: [n.x, n.y] });
  }
  return added;
}

// Open output ends (a belt or underground exit with nothing taking from it): one belt tile in front, which
// becomes a sink. Splitters drain only to free sides facing exitDir out of the layout (default: the side most
// open ends face).
export function addDrains(buildings, exitDir = null) {
  const w = new World(buildings.slice());
  const xs = [], ys = [];
  for (const s of w.occ.keys()) { const [x, y] = s.split(',').map(Number); xs.push(x); ys.push(y); }
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const inside = p => x0 <= p[0] && p[0] <= x1 && y0 <= p[1] && p[1] <= y1;
  if (exitDir === null) {              // the side most open ends face out of (splitters on a corner face two)
    const votes = [0, 0, 0, 0];
    for (const n of w.belt.values()) {
      if (n.sub === 2 || (n.outputs.length && n.sub !== 1)) continue;
      const dirs = n.sub !== 1 ? [n.dir] : [n.dir, (n.dir + 1) % 4, (n.dir + 3) % 4];
      for (const d of dirs) {
        const p = [n.x + DV[d][0], n.y + DV[d][1]];
        if (!w.occ.has(k(p[0], p[1])) && !inside(p)) votes[d]++;
      }
    }
    exitDir = votes.indexOf(Math.max(...votes));
  }
  const added = [];
  for (const n of [...w.belt.values()]) {
    if (n.sub === 2 || (n.outputs.length && n.sub !== 1)) continue;
    const dirs = n.sub !== 1 ? [n.dir] : [n.dir, (n.dir + 1) % 4, (n.dir + 3) % 4];
    for (const d of dirs) {
      const p = [n.x + DV[d][0], n.y + DV[d][1]];
      if (w.occ.has(k(p[0], p[1])) || (n.sub === 1 && (inside(p) || d !== exitDir))) continue;
      const b = { id: 3000, x: p[0], y: p[1], o: d, tier: 1, recipe: 0, filter: 0, added: 'out' };
      buildings.push(b);
      w.occ.set(k(p[0], p[1]), b);
      added.push(p);
    }
  }
  return added;
}

export const RAW_ITEMS = ['Wood Log', 'Stone', 'Iron Ore', 'Copper Ore', 'Coal', 'Wolframite', 'Uranium Ore', 'Gold'];

// Which item an open input most likely carries: follow the belts (and arms picking from them) to the machines
// they reach and take the raw ingredient those need most often. Falls back to `fallback`.
const armsFromCache = new WeakMap();
export function guessFeedItem(w, seg, fallback) {
  let armsFrom = armsFromCache.get(w);
  if (!armsFrom) {
    armsFrom = new Map();
    for (const a of w.arms) {
      for (const g of w.segAt(a.src)) {
        const L = armsFrom.get(g[0]) || [];
        L.push(a); armsFrom.set(g[0], L);
      }
    }
    armsFromCache.set(w, armsFrom);
  }
  const votes = {}, seen = new Set([seg]), queue = [seg];
  const vote = b => {
    const m = w.bobj.get(b);
    if (m && m.rec) for (const i of m.rec.in) if (RAW_ITEMS.includes(i.item)) votes[i.item] = (votes[i.item] || 0) + 1;
  };
  while (queue.length) {
    const g = queue.shift();
    const t = g.target, next = [];
    if (t && t.outs) next.push(...t.outs);
    else if (t && t.items) next.push(t);
    else if (t && t.bld) vote(t.bld);
    for (const a of armsFrom.get(g) || []) {
      const b = w.occ.get(a.dst[0] + ',' + a.dst[1]);
      if (b && b.id >= 3004) vote(b);
      else for (const [h] of w.segAt(a.dst).slice(0, 1)) next.push(h);
    }
    for (const h of next) if (!seen.has(h)) { seen.add(h); queue.push(h); }
  }
  let best = fallback, bn = 0;
  for (const [k, n] of Object.entries(votes)) if (n > bn) { best = k; bn = n; }
  return best;
}
