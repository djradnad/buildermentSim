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

// Blueprint codes go through Human-Crow's worker, as his tools do: POST the code to /downloadBlp (answers the
// game's blueprint file), then POST that file to /readBlp (answers the decoded form below).
// Both go as text/plain: the worker answers Access-Control-Allow-Origin * but no Allow-Headers, so a browser's
// preflight for any other content type (or a Content-Encoding header) fails and the fetch dies with no detail.
//   decoded: u32 metadata length, JSON metadata, then 4-byte aligned columns of `count` values each:
//   id u16, x i32, y i32, rotation u8, tier u8, recipe/filter i16, colour u32
async function gunzipIfNeeded(buf) {
  const u = new Uint8Array(buf);
  if (u[0] !== 0x1f || u[1] !== 0x8b) return buf;
  return new Response(new Blob([buf]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer();
}

async function post(path, body) {
  let r;
  try {
    r = await fetch(WORKER + path, { method: 'POST', body, headers: { 'Content-Type': 'text/plain' } });
  } catch (e) {
    throw new Error(`could not reach the blueprint service (${path}): ${e.message}`);
  }
  if (!r.ok) throw new Error(`blueprint service ${path}: ${r.status} ${(await r.text()).slice(0, 200)}`);
  return gunzipIfNeeded(await r.arrayBuffer());
}

export function decodeBlueprint(buf) {
  const dv = new DataView(buf);
  const ml = dv.getUint32(0, true);
  const meta = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, ml)));
  if (!meta.buildings) return { meta, rows: [] };
  const n = meta.count;
  let off = 4 + ml;
  const cols = [];
  for (const [get, size] of [['getUint16', 2], ['getInt32', 4], ['getInt32', 4], ['getUint8', 1], ['getUint8', 1],
                             ['getInt16', 2], ['getUint32', 4]]) {
    off = (off + 3) & ~3;
    const c = new Array(n);
    for (let i = 0; i < n; i++) c[i] = dv[get](off + i * size, true);
    cols.push(c);
    off += n * size;
  }
  return { meta, rows: cols[0].map((_, i) => cols.map(c => c[i])) };
}

export async function fetchBlueprint(id) {
  id = id.trim().replace(/^.*[\/=]/, '');
  const file = await post('/downloadBlp', new TextEncoder().encode(id));
  const { meta, rows } = decodeBlueprint(await post('/readBlp', file));
  if (!rows.length) throw new Error(`blueprint ${id} has no buildings`);
  return { meta: { name: id, ...meta }, rows };
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
// Machines a belt segment reaches: along belts and splitters, and through arms that take from it (into a
// machine directly, or onto more belts).
function machinesReached(w, seg) {
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
  const found = new Set(), seen = new Set([seg]), queue = [seg];
  const add = b => { const m = w.bobj.get(b); if (m && m.rec) found.add(m); };
  while (queue.length) {
    const g = queue.shift();
    const t = g.target, next = [];
    if (t && t.outs) next.push(...t.outs);
    else if (t && t.items) next.push(t);
    else if (t && t.bld) add(t.bld);
    for (const a of armsFrom.get(g) || []) {
      const b = w.occ.get(a.dst[0] + ',' + a.dst[1]);
      if (b && b.id >= 3004) add(b);
      else for (const [h] of w.segAt(a.dst).slice(0, 1)) next.push(h);
    }
    for (const h of next) if (!seen.has(h)) { seen.add(h); queue.push(h); }
  }
  return [...found];
}

const rawNeeds = m => m.rec.in.map(i => i.item).filter(i => RAW_ITEMS.includes(i));

export function guessFeedItem(w, seg, fallback) {
  return guessFeedItems(w, [seg], [fallback])[0];
}

// Raw item for each input belt, chosen together: a machine that needs several raw items (graphite: coal and
// logs) is reached by several inputs, and each should bring a different one. Inputs whose machines need only
// one raw item are settled first; then, one at a time, the input and item that cover the most machines still
// missing that item.
export function guessFeedItems(w, segs, fallbacks) {
  const reach = segs.map(g => machinesReached(w, g));
  const items = segs.map(() => null);
  const cands = reach.map(ms => [...new Set(ms.flatMap(rawNeeds))]);
  const covered = new Set();              // machine index + item already brought by a chosen input
  const mid = new Map();
  reach.flat().forEach(m => { if (!mid.has(m)) mid.set(m, mid.size); });
  const assign = (f, it) => {
    items[f] = it;
    for (const m of reach[f]) if (rawNeeds(m).includes(it)) covered.add(mid.get(m) + '|' + it);
  };
  cands.forEach((c, f) => { if (c.length === 1) assign(f, c[0]); });
  for (;;) {
    let best = null;
    cands.forEach((c, f) => {
      if (items[f] !== null) return;
      for (const it of c) {
        const need = reach[f].filter(m => rawNeeds(m).includes(it));
        const missing = need.filter(m => !covered.has(mid.get(m) + '|' + it)).length;
        const score = [missing, need.length];
        if (!best || score[0] > best.score[0] || (score[0] === best.score[0] && score[1] > best.score[1]))
          best = { f, it, score };
      }
    });
    if (!best) break;
    assign(best.f, best.it);
  }
  return items.map((it, f) => it ?? fallbacks[f]);
}
