// Game-exact factory simulator (Steam build rules), a port of the Python gs2 simulator.
//
// Same pipeline as the game: buildings -> belt nodes and building ports, links where both sides agree,
// belt segments with exact polylines and lengths (float32), then a 60 Hz tick: belts in segment creation order,
// machines, extractors, arms, storage output. Positions on a segment are integers, 800 units per tile.
// Item spacing 240, a belt moves 32 units per tick: a full belt carries 480 items per minute.

import { GAMEDATA } from './gamedata.js';
export { GAMEDATA };

const f32 = Math.fround;
export const DT = f32(1 / 60);
export const SP = 240, SNAP = 28, M_DEFAULT = 32;
export const UG_RANGE = 6;                         // exit 1..7 tiles away
const ARM_DROP = 2.7333333333333334, ARM_END = 4.0;
const OUT_CAP = 10;                                // no new craft while this many products wait
export const DV = [[0, -1], [-1, 0], [0, 1], [1, 0]];   // 0 N, 1 W, 2 S, 3 E
const NB_ORDER = [[0, -1], [-1, 0], [0, 1], [1, 0]];
const SIDE = { N: 0, W: 1, S: 2, E: 3 };
const BELT_IDS = new Set([3000, 3001, 3002, 3003]);
const BELT_SUB = { 3000: 0, 3001: 1, 3002: 2, 3003: 3 };

// ------------------------------------------------------------------ game tables
export const BDEF = {};
for (const [key, b] of Object.entries(GAMEDATA.buildings)) BDEF[b.id] = { ...b, key };
export const RECIPES = {};
for (const r of Object.values(GAMEDATA.recipes)) RECIPES[r.id] = r;
export const FACTORY_IDS = new Set([3005, 3006, 3007, 3008, 3009, 3010, 3014]);
const STORAGE_ID = 3011, ARM_ID = 3012, EXTRACTOR_ID = 3004;

// Extractor settings by world generation (Gen 2: 15/30/60/120/150 per min, boost +60%).
export const SETTINGS = {
  2: { extSecs: 4.0, extMult: [1, 2, 4, 8, 10], boost: 0.6 },
  1: { extSecs: 8.0, extMult: [1, 1.5, 2, 3, 4], boost: 0.4 },
};

// Arm filters in blueprints are the game's item ids. Only these are confirmed so far.
export const ITEM_BY_GAME_ID = { 1001: 'Wood Plank', 1053: 'Nuclear Fuel Cell' };

const key = (x, y) => x + ',' + y;

export function grot(b) {         // blueprints store belts as is, every other building turned 180 degrees
  return BELT_IDS.has(b.id) ? b.o : (b.o + 2) & 3;
}

function rot(x, y, o, ox, oy) {
  switch (o) {
    case 0: return [x + ox, y + oy];
    case 1: return [x + oy, y - ox];
    case 2: return [x - ox, y - oy];
    default: return [x - oy, y + ox];
  }
}

export function footprint(b) {
  const d = BDEF[b.id] || { w: 1, h: 1 };
  const w = d.w || 1, l = d.h || 1, o = grot(b), out = [];
  for (let ox = -Math.floor((w - 1) / 2); ox <= Math.floor(w / 2); ox++)
    for (let oy = -Math.floor((l - 1) / 2); oy <= Math.floor(l / 2); oy++) out.push(rot(b.x, b.y, o, ox, oy));
  return out;
}

export function tierSpeed(b) {
  const t = (BDEF[b.id] || {}).tier_speed;
  if (!t || !t.length) return 1.0;
  const i = Math.min(Math.max(0, (b.tier || 1) - 1), t.length - 1);
  return t[i] == null ? 1.0 : t[i];
}

// ------------------------------------------------------------------ float helpers (PC: separate mul and add)
const madd = (a, b, c) => f32(f32(f32(a) * f32(b)) + f32(c));
const dot2 = (a, b, c, d) => f32(f32(f32(a) * f32(b)) + f32(f32(c) * f32(d)));
const progAdd = (p, sp) => p + f32(sp * DT);     // progress is a double in the game
const progSub = (p, sp) => p - f32(sp * DT);

function roundHalfEven(x) {
  const r = Math.round(x);
  return (Math.abs(x % 1) === 0.5 && r % 2 !== 0) ? r - 1 : r;
}

function seglen(a, b) {
  const dx = f32(b[0] - a[0]), dy = f32(b[1] - a[1]);
  return f32(Math.sqrt(dot2(dy, dy, dx, dx)));
}

function arc(c, start, end) {
  const [cx, cy] = c, [sx, sy] = start, [ex, ey] = end;
  const kx = f32(f32(ex - cx) + sx), ky = f32(f32(ey - cy) + sy);
  const neg = (cx < ex && sy < cy) || (cx < sx && cy < ey) || (ex < cx && cy < sy) || (sx < cx && ey < cy);
  const a0 = f32(Math.atan2(f32(sy - ky), f32(sx - kx)));
  const step = neg ? f32(-0.01745329424738884) : f32(0.01745329424738884);
  const pts = [];
  for (let i = 0; i <= 90; i += 15) {
    const a = madd(i, step, a0);
    pts.push([madd(f32(Math.cos(a)), 0.5, kx), madd(f32(Math.sin(a)), 0.5, ky)]);
  }
  return pts;
}

const ptEq = (a, b) => a[0] === b[0] && a[1] === b[1];

// ------------------------------------------------------------------ nodes and links
class Node {
  constructor(kind, sub, dir, x, y, owner) {     // kind 0 belt piece, 1 building input port, 2 output port
    this.kind = kind; this.sub = sub; this.dir = dir; this.x = x; this.y = y; this.owner = owner;
    this.inputs = []; this.outputs = [];
  }
}

function codeIn(p, nb) {
  if (nb[0] === p[0]) return p[1] < nb[1] ? 0 : 2;
  return p[0] <= nb[0] ? 1 : 3;
}
function codeOut(p, nb) {
  if (p[0] === nb[0]) return nb[1] < p[1] ? 0 : 2;
  return nb[0] <= p[0] ? 1 : 3;
}
function nbOutputsTo(sub, d, c) {
  if (sub === 1) return ((d + 2) & 3) !== c;
  if (sub === 2) return false;
  return d === c;
}
function nbAcceptsFrom(sub, d, c) {
  if (sub === 0) return d !== ((c + 2) & 3);
  if (sub === 1 || sub === 2) return d === c;
  return false;
}

// ------------------------------------------------------------------ belt segments
export class Seg {
  constructor(idx, pts, flag, nodes) {
    this.idx = idx; this.pts = pts; this.flag = flag; this.nodes = nodes;
    let tot = 0;
    const cum = [];
    for (let i = 0; i < pts.length - 1; i++) {
      cum.push(roundHalfEven(f32(tot * 800)));
      tot = f32(tot + seglen(pts[i], pts[i + 1]));
    }
    cum.push(roundHalfEven(f32(tot * 800)));
    this.cum = cum;
    this.L = cum[cum.length - 1];
    const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]), e = f32(1e-4);
    this.bbox = [f32(Math.min(...xs) - e), f32(Math.min(...ys) - e), f32(Math.max(...xs) + e), f32(Math.max(...ys) + e)];
    this.items = []; this.kinds = [];          // items[i] = gap in front of item i, item 0 at the front
    this.free = this.L;
    this.first = -1; this.fmin = 0;
    this.target = null;                       // Seg, Split, {bld} or {sink}
    this.inputs = [];
    this.chosen = -1;
    this.moved = 0;                           // items that left the end
  }

  recompute() {
    const it = this.items;
    for (let i = 0; i < it.length; i++) {
      if ((i === 0 ? this.fmin : SP) < it[i]) { this.first = i; return; }
    }
    this.first = -1;
  }

  insert(m, kind) {
    let g = Math.max(SP, this.free - m);
    if (g !== SP && Math.abs(g - SP) <= SNAP) g = SP;
    this.items.push(g); this.kinds.push(kind);
    this.free -= g;
    this.recompute();
  }

  remove(m) {
    this.items.shift();
    const k = this.kinds.shift();
    if (this.items.length) {
      this.items[0] = Math.max(0, this.items[0] - m);
      this.free += m;
    }
    this.recompute();
    this.moved++;
    return k;
  }

  project(p) {                                // where an arm at tile p touches this segment, or null
    let best = f32(999), bq = null;
    const px = f32(p[0]), py = f32(p[1]);
    const P = this.pts;
    for (let i = 0; i < P.length - 1; i++) {
      const [ax, ay] = P[i], [bx, by] = P[i + 1];
      const l2 = dot2(f32(ay - by), f32(ay - by), f32(ax - bx), f32(ax - bx));
      let q = [ax, ay];
      if (l2 !== 0) {
        let t = f32(dot2(f32(by - ay), f32(py - ay), f32(bx - ax), f32(px - ax)) / l2);
        t = f32(Math.max(0, Math.min(1, t)));
        q = [madd(f32(bx - ax), t, ax), madd(f32(by - ay), t, ay)];
      }
      const d = f32(Math.sqrt(dot2(f32(py - q[1]), f32(py - q[1]), f32(px - q[0]), f32(px - q[0]))));
      if (d < best) { best = d; bq = q; }
    }
    if (bq === null || !(best < 0.4)) return null;
    const [x0, y0, x1, y1] = this.bbox;
    if (!(x0 <= bq[0] && bq[0] <= x1 && y0 <= bq[1] && bq[1] <= y1)) return null;
    let dist = 0;
    for (let i = 0; i < P.length - 1; i++) {
      const a = P[i], b = P[i + 1];
      const d1 = seglen(bq, b), d2 = seglen(a, bq), ln = seglen(a, b);
      if (Math.abs(f32(f32(d1 + d2) - ln)) < 9.999999747378752e-06) { dist = f32(dist + seglen(a, bq)); break; }
      dist = f32(dist + ln);
    }
    return Math.max(0, Math.min(this.L, roundHalfEven(f32(dist * 800))));
  }

  canDrop(pos) {
    let x = this.L;
    for (const g of this.items) { x -= g; if (Math.abs(x - pos) < 232) return false; }
    return true;
  }

  drop(pos, kind) {
    let x = this.L, rem = this.L;
    for (let i = 0; i < this.items.length; i++) {
      x -= this.items[i];
      if (x < pos) {
        this.items[i] = pos - x;
        this.items.splice(i, 0, rem - pos);
        this.kinds.splice(i, 0, kind);
        this.recompute();
        return;
      }
      rem -= this.items[i];
    }
    this.items.push(rem - pos); this.kinds.push(kind);
    this.free -= rem - pos;
    this.recompute();
  }

  pick(pos, filt) {
    let x = this.L;
    for (let i = 0; i < this.items.length; i++) {
      const g = this.items[i];
      x -= g;
      if (Math.abs(pos - x) <= 180 && (filt === 0 || filt === this.kinds[i])) {
        const k = this.kinds[i];
        this.items.splice(i, 1); this.kinds.splice(i, 1);
        if (i === this.items.length) this.free += g; else this.items[i] += g;
        this.recompute();
        return [k, i];
      }
    }
    return null;
  }

  // item positions measured from the start of the segment (for drawing)
  positions() {
    const out = [];
    let x = this.L;
    for (let i = 0; i < this.items.length; i++) { x -= this.items[i]; out.push(x); }
    return out;
  }

  // point at distance u (units) along the polyline
  pointAt(u) {
    const P = this.pts, C = this.cum;
    for (let i = 0; i < P.length - 1; i++) {
      if (u <= C[i + 1] || i === P.length - 2) {
        const span = C[i + 1] - C[i] || 1, t = Math.max(0, Math.min(1, (u - C[i]) / span));
        return [P[i][0] + (P[i + 1][0] - P[i][0]) * t, P[i][1] + (P[i + 1][1] - P[i][1]) * t];
      }
    }
    return P[0];
  }
}

class Split { constructor(outs) { this.outs = outs; this.idx = 0; } }

function mergeNext(t) {
  const n = t.inputs.length;
  if (n < 2) return n - 1;
  const cur = t.chosen;
  let i, stop;
  if (cur !== -1) { i = (cur + 1) % n; stop = cur; } else { i = 0; stop = n - 1; }
  while (i !== stop) {
    const src = t.inputs[i];
    if (src.items.length && src.items[0] <= SP) return i;
    i = (i + 1) % n;
  }
  return -1;
}

function mergeGate(s) {
  if (s.items[0] > 480) return;
  const t = s.target;
  if (t.chosen === -1) {
    let best = -1, bg = 100000;
    t.inputs.forEach((src, i) => { if (src.items.length && src.items[0] < bg) { best = i; bg = src.items[0]; } });
    t.chosen = best;
  }
  s.fmin = (t.chosen < 0 || t.inputs[t.chosen] !== s) ? SP : Math.max(0, SP - t.free);
  s.recompute();
}

function segTick(s, M, w) {
  if (!s.items.length) return;
  const t = s.target;
  if (t instanceof Seg && t.inputs.length > 1) mergeGate(s);
  let left = M;
  const k = s.first;
  if (k !== -1) {
    const mn = k === 0 ? s.fmin : SP;
    const g = s.items[k], ng = Math.max(mn, g - M);
    s.items[k] = ng;
    const used = g - ng;
    s.free += used;
    left = M - used;
    if (used !== M) s.recompute();
  }
  if (s.items[0] !== 0 || t === null) return;
  const kind = s.kinds[0];
  if (t instanceof Split) {
    if (!t.outs.length) return;
    const o = t.outs[t.idx];
    if (o.free > 239) { o.insert(left, kind); s.remove(left); o.chosen = mergeNext(o); }
    t.idx = (t.idx + 1) % t.outs.length;
  } else if (t instanceof Seg) {
    if (t.free > 239) { t.insert(left, kind); s.remove(left); t.chosen = mergeNext(t); }
  } else if (t.sink) {
    w.sinkCount(s, kind);
    s.remove(left);
  } else if (w.accept(t.bld, kind)) {
    s.remove(left);
  }
}

// ------------------------------------------------------------------ the world: links and segments
export class World {
  constructor(buildings, opts = {}) {
    this.M = opts.M || M_DEFAULT;
    this.qt = opts.qt || null;
    this.buildings = buildings;
    this.belt = new Map(); this.inport = new Map(); this.outport = new Map(); this.occ = new Map();
    for (const b of buildings) {
      const d = BDEF[b.id];
      if (!d) continue;
      for (const [x, y] of footprint(b)) this.occ.set(key(x, y), b);
      if (BELT_IDS.has(b.id)) {
        this.belt.set(key(b.x, b.y), new Node(0, BELT_SUB[b.id], b.o, b.x, b.y, b));
      } else {
        const o = grot(b);
        for (const c of d.inputs || []) {
          const [x, y] = rot(b.x, b.y, o, c.x, c.y);
          this.inport.set(key(x, y), new Node(1, 0, (SIDE[c.side] + o) & 3, x, y, b));
        }
        for (const c of d.outputs || []) {
          const [x, y] = rot(b.x, b.y, o, c.x, c.y);
          this.outport.set(key(x, y), new Node(2, 0, (SIDE[c.side] + o) & 3, x, y, b));
        }
      }
    }
    for (const m of [this.belt, this.inport, this.outport]) for (const n of m.values()) this.link(n);
    this.buildSegments(opts.ugRange ?? UG_RANGE);
    if (this.qt) { this.qt.clear(); for (const g of this.segs) this.qt.insert(g.bbox, g); }
  }

  findInput(p, sub, d, nb) {
    const c = codeIn(p, nb);
    let ok;
    if (sub === 0) ok = d !== ((c - 2) & 3);
    else if (sub === 1 || sub === 2) ok = d === c;
    else return null;
    if (!ok) return null;
    let n = this.belt.get(key(nb[0], nb[1]));
    if (n) return nbOutputsTo(n.sub, n.dir, c) ? n : null;
    n = this.outport.get(key(nb[0], nb[1]));
    return n && n.dir === c ? n : null;
  }

  findOutput(p, sub, d, nb) {
    const c = codeOut(p, nb);
    let ok;
    if (sub === 1) ok = ((d + 2) & 3) !== c;
    else if (sub === 2) return null;
    else ok = d === c;
    if (!ok) return null;
    let n = this.belt.get(key(nb[0], nb[1]));
    if (n) return nbAcceptsFrom(n.sub, n.dir, c) ? n : null;
    n = this.inport.get(key(nb[0], nb[1]));
    return n && ((n.dir + 2) & 3) === c ? n : null;
  }

  link(n) {
    const p = [n.x, n.y];
    n.inputs = []; n.outputs = [];
    for (const [dx, dy] of NB_ORDER) {
      const nb = [n.x + dx, n.y + dy];
      let i = null, o = null;
      if (n.kind === 0) { i = this.findInput(p, n.sub, n.dir, nb); o = this.findOutput(p, n.sub, n.dir, nb); }
      else if (n.kind === 1) i = this.findInput(p, 0, (n.dir + 2) & 3, nb);
      else o = this.findOutput(p, 0, n.dir, nb);
      if (i) n.inputs.push(i);
      if (o) n.outputs.push(o);
    }
  }

  static isSplit(n) { return n.kind === 0 && n.sub === 1; }

  walkFwd(n) {
    const seen = new Set();
    while (n.outputs.length === 1) {
      const u = n.outputs[0];
      if (this.visited.has(u) || seen.has(u) || World.isSplit(u)) return n;
      seen.add(n); n = u;
    }
    return n;
  }

  walkBack(n) {
    const seen = new Set();
    let steps = 1;
    while (n.inputs.length === 1) {
      const u = n.inputs[0];
      if (this.visited.has(u) || seen.has(u) || steps === 60 || World.isSplit(u)) return n;
      seen.add(n); steps++; n = u;
    }
    return n;
  }

  collect(cur) {
    const nodes = [];
    let i = 0, multi = cur.inputs.length > 1;
    while (!this.visited.has(cur)) {
      if (World.isSplit(cur) || i === 60) break;
      nodes.push(cur);
      if (multi && i > 0) return nodes;
      this.visited.add(cur);
      if (cur.outputs.length !== 1) return nodes;
      cur = cur.outputs[0];
      i++;
      multi = cur.inputs.length > 1;
    }
    nodes.push(cur);
    if (!multi && i !== 60) this.visited.add(cur);
    return nodes;
  }

  buildSegments(ugRange) {
    this.visited = new Set(); this.segs = []; this.startOf = new Map();
    const splitOuts = new Map();
    for (const n of this.inport.values()) {
      const nodes = this.collect(this.walkBack(n));
      if (nodes.length > 1) this.makeSeg(nodes, true);
    }
    for (const n of this.belt.values()) {
      while (!this.visited.has(n)) {
        const before = this.visited.size;
        const nodes = this.collect(this.walkBack(this.walkFwd(n)));
        if (nodes.length > 1) this.makeSeg(nodes, true);
        else if (this.visited.size === before) break;
      }
    }
    for (const n of this.belt.values())
      if (n.sub === 1) splitOuts.set(n, n.outputs.map(o => this.makeSeg([n, o], true)));
    for (const n of this.belt.values()) {
      if (n.sub !== 2) continue;
      for (let k = 1; k <= ugRange + 1; k++) {
        const e = this.belt.get(key(n.x + DV[n.dir][0] * k, n.y + DV[n.dir][1] * k));
        if (e && e.sub === 3 && e.dir === n.dir) { this.makeSeg([n, e], false); break; }
      }
    }
    for (const n of this.outport.values()) {
      if (!this.visited.has(n)) {
        const nodes = this.collect(n);
        if (nodes.length > 1) this.makeSeg(nodes, true);
      }
    }
    const splitOf = new Map();
    for (const [sp, outs] of splitOuts) splitOf.set(sp, new Split(outs));
    this.splitOf = splitOf;
    for (const g of this.segs) {
      const last = g.nodes[g.nodes.length - 1];
      if (last.kind === 1) g.target = { bld: last.owner };
      else if (World.isSplit(last)) g.target = splitOf.get(last);
      else {
        const t = this.startOf.get(last);
        if (t && t !== g) { g.target = t; t.inputs.push(g); }
      }
    }
    this.outSeg = new Map();
    for (const g of this.segs) if (g.nodes[0].kind === 2) this.outSeg.set(g.nodes[0].owner, g);
  }

  // segments whose bounding box can touch the 1x1 box around tile p, in creation order (same result as
  // scanning every segment, which is what the game's lookup amounts to on a fresh world)
  segsNear(p) {
    if (!this.grid) {
      this.grid = new Map();
      for (const g of this.segs) {
        const [x0, y0, x1, y1] = g.bbox;
        for (let x = Math.floor(x0); x <= Math.floor(x1); x++)
          for (let y = Math.floor(y0); y <= Math.floor(y1); y++) {
            const c = key(x, y);
            const L = this.grid.get(c);
            if (L) L.push(g); else this.grid.set(c, [g]);
          }
      }
    }
    const out = new Set();
    for (let x = Math.floor(p[0] - 0.5); x <= Math.floor(p[0] + 0.5); x++)
      for (let y = Math.floor(p[1] - 0.5); y <= Math.floor(p[1] + 0.5); y++)
        for (const g of this.grid.get(key(x, y)) || []) out.add(g);
    return [...out].sort((a, b) => a.idx - b.idx);
  }

  makeSeg(nodes, flag) {
    const g = new Seg(this.segs.length, this.points(nodes), flag, nodes);
    this.segs.push(g);
    if (!this.startOf.has(nodes[0])) this.startOf.set(nodes[0], g);
    return g;
  }

  // geometry: the polyline items follow
  nodePoints(n) {
    const cx = f32(n.x), cy = f32(n.y), half = 0.5;
    if (n.inputs.length < 2 && n.outputs.length < 2 && !World.isSplit(n)) {
      const a = this.arcPoints(n);
      if (a) return a;
      if (n.kind === 0 && (n.sub === 2 || n.sub === 3)) return [[cx, cy]];
      const first = this.entryPoint(n);
      const [dx, dy] = DV[n.dir];
      let second;
      if (n.kind === 1) {
        const bx = f32(n.x + dx), by = f32(n.y + dy);
        second = [dot2(bx, 0.1, cx, 0.9), dot2(by, 0.1, cy, 0.9)];
      } else second = [f32(cx + f32(dx * half)), f32(cy + f32(dy * half))];
      return [first, second];
    }
    return [[cx, cy]];
  }

  entryPoint(n) {
    const cx = f32(n.x), cy = f32(n.y), half = f32(0.5);
    if (n.inputs.length > 1) return [cx, cy];
    let d;
    if (n.kind === 0) {
      if (n.sub === 0 && n.inputs.length === 1) {
        const m = n.inputs[0];
        return [f32(f32(cx * half) + f32(f32(m.x) * half)), f32(f32(cy * half) + f32(f32(m.y) * half))];
      }
      if (n.sub === 3) return [cx, cy];
      d = n.dir;
    } else if (n.kind === 1) d = (n.dir + 2) & 3;
    else {
      const o = n.outputs[0];
      return [dot2(cx, 0.9, f32(o.x), 0.1), dot2(cy, 0.9, f32(o.y), 0.1)];
    }
    const [dx, dy] = DV[d];
    return [f32(cx - f32(dx * half)), f32(cy - f32(dy * half))];
  }

  arcPoints(n) {
    if (n.kind !== 0) return null;
    const cx = f32(n.x), cy = f32(n.y), half = f32(0.5);
    if (n.outputs.length === 1) {
      const o = n.outputs[0];
      const c = n.x === o.x ? (o.y < n.y ? 0 : 2) : (o.x <= n.x ? 1 : 3);
      if (n.dir !== c) {
        const [dx, dy] = DV[n.dir];
        return arc([cx, cy], [f32(cx - f32(dx * half)), f32(cy - f32(dy * half))],
          [f32(f32(f32(o.x) * half) + f32(cx * half)), f32(f32(f32(o.y) * half) + f32(cy * half))]);
      }
    }
    if (n.inputs.length === 1) {
      const m = n.inputs[0];
      const c = m.x === n.x ? (n.y < m.y ? 0 : 2) : (n.x <= m.x ? 1 : 3);
      if (n.dir !== c) {
        const [dx, dy] = DV[n.dir];
        return arc([cx, cy], [f32(f32(cx * half) + f32(f32(m.x) * half)), f32(f32(cy * half) + f32(f32(m.y) * half))],
          [f32(cx + f32(dx * half)), f32(cy + f32(dy * half))]);
      }
    }
    return null;
  }

  points(nodes) {
    let out = [];
    const last = nodes.length - 1;
    nodes.forEach((n, i) => {
      let pts = this.nodePoints(n);
      if (i === 0) out.push(...pts);
      else if (i !== last || !n.outputs.length) {
        if (ptEq(pts[0], out[out.length - 1])) pts = pts.slice(1);
        out.push(...pts);
      } else if (!ptEq(pts[0], out[out.length - 1])) out.push(pts[0]);
    });
    const res = [out[0]];
    for (let k = 1; k < out.length - 1; k++) {
      const a = out[k - 1], b = out[k], c = out[k + 1];
      if ((a[0] !== b[0] || b[0] !== c[0]) && (a[1] !== b[1] || b[1] !== c[1])) res.push(b);
    }
    if (out.length > 1) res.push(out[out.length - 1]);
    return res;
  }
}

// ------------------------------------------------------------------ buildings
class Machine {
  constructor(b, rec, mult) {
    this.b = b; this.rec = rec; this.mult = f32(mult);
    this.cap = {}; for (const m of rec.in) this.cap[m.item] = m.amount;   // input cap = one craft
    this.have = {}; for (const m of rec.in) this.have[m.item] = 0;
    this.outItem = rec.out[0].item; this.outAmount = rec.out[0].amount;
    this.out = 0; this.prog = -1.0; this.boost = 0; this.made = 0; this.seg = null;
  }
  tick(w) {
    const mats = this.rec.in;
    if (this.prog < 0 && this.out < OUT_CAP && mats.every(m => this.have[m.item] >= m.amount)) {
      for (const m of mats) this.have[m.item] -= m.amount;
      this.prog = 0.0;
    }
    const sp = f32(f32(this.boost + 1) * this.mult);
    if (this.prog >= 0) this.prog = progAdd(this.prog, sp);
    if (this.rec.seconds <= this.prog) {
      this.out += this.outAmount; this.made += this.outAmount; this.prog = -1.0;
    }
    w.pushOut(this, this.outItem);
  }
}

class Storage {
  constructor(b, limit) { this.b = b; this.limit = limit; this.item = null; this.count = 0; this.seg = null; }
  get out() { return this.count; }
}

class Arm {
  constructor(b, mult) {
    this.b = b; this.mult = f32(mult);
    const [dx, dy] = DV[b.o];
    this.src = [b.x - dx, b.y - dy];          // takes from behind, puts in front
    this.dst = [b.x + dx, b.y + dy];
    this.filt = b.filter || 0;
    this.prog = -1.0; this.held = null; this.boost = 0; this.moved = 0;
  }
}

class Extractor {
  constructor(b, item, gen, boosted) {
    const cfg = SETTINGS[gen];
    this.b = b; this.item = item; this.dur = cfg.extSecs;
    const t = Math.min(Math.max(0, (b.tier || 1) - 1), cfg.extMult.length - 1);
    this.mult = f32(cfg.extMult[t]);
    this.boost = f32(boosted ? cfg.boost : 0);
    this.prog = null; this.count = 0; this.seg = null; this.made = 0;
  }
  get out() { return this.count; }
  tick(w) {
    const sp = f32(f32(this.boost + 1) * this.mult);
    if (this.prog !== null && this.dur <= this.prog) { this.count++; this.made++; this.prog = 0.0; }
    if (this.prog === null) this.prog = progAdd(0.0, sp);
    else if (this.count < 10) this.prog = Math.min(progAdd(this.prog, sp), this.dur);
    w.pushOut(this, this.item);
  }
}

// An open input belt: items of one kind at up to `perMin` (480 = a full belt, as fast as it can take them).
// `buffer` items may wait while the belt is blocked (1 = an extractor-like source that loses time; 10 = a machine's
// output store, as when the input comes from upstream production).
class Feed {
  constructor(seg, kind, perMin, buffer = 1) { this.seg = seg; this.kind = kind; this.perMin = perMin; this.buffer = buffer; this.n = 0; this.acc = 0; }
  tick(M) {
    if (this.perMin < 480) {
      this.acc = Math.min(this.acc + this.perMin / 3600, this.buffer);
      if (this.acc < 1) return;
    }
    if (this.seg.free > 239) { this.seg.insert(M, this.kind); this.n++; this.acc -= 1; }
  }
}

// ------------------------------------------------------------------ the factory: everything that ticks
export class Factory extends World {
  // opts: feeds [{at: [x, y] first tile of a segment, kind, perMin}], extractors {"x,y": item},
  // gen 1|2, boosted, newestFirst (building pools tick newest first, traced), qt (QuadTree for arm lookups)
  constructor(buildings, opts = {}) {
    super(buildings, opts);
    this.machines = []; this.storages = []; this.arms = []; this.feeds = []; this.extractors = [];
    this.bobj = new Map();
    this.sinks = new Map();                    // segment -> {kind: count}
    const gen = opts.gen || 2, ext = opts.extractors || {};
    for (const b of buildings) {
      const d = BDEF[b.id];
      if (!d) continue;
      if (FACTORY_IDS.has(b.id)) {
        const rec = RECIPES[b.recipe];
        if (!rec) continue;
        const m = new Machine(b, rec, tierSpeed(b));
        m.seg = this.outSeg.get(b) || null;
        this.machines.push(m); this.bobj.set(b, m);
      } else if (b.id === STORAGE_ID) {
        const st = new Storage(b, 250);
        st.seg = this.outSeg.get(b) || null;
        this.storages.push(st); this.bobj.set(b, st);
      } else if (b.id === ARM_ID) {
        this.arms.push(new Arm(b, tierSpeed(b)));
      } else if (b.id === EXTRACTOR_ID && ext[key(b.x, b.y)]) {
        const e = new Extractor(b, ext[key(b.x, b.y)], gen, !!opts.boosted);
        e.seg = this.outSeg.get(b) || null;
        this.extractors.push(e); this.bobj.set(b, e);
      }
    }
    if (opts.newestFirst !== false) for (const L of [this.machines, this.storages, this.arms, this.extractors]) L.reverse();
    for (const g of this.segs) {                // open ends: an endless belt takes everything
      const last = g.nodes[g.nodes.length - 1];
      if (g.target === null && !last.outputs.length && last.kind === 0 && last.sub === 0) g.target = { sink: true };
    }
    for (const f of opts.feeds || []) {
      const g = this.segs.find(s => s.nodes[0].x === f.at[0] && s.nodes[0].y === f.at[1]);
      if (g) this.feeds.push(new Feed(g, f.kind, f.perMin ?? 480, f.buffer ?? 1));
    }
    this.labsTakeAll = opts.labsTakeAll !== false;
    this.tickNo = 0;
  }

  sinkCount(seg, kind) {
    let m = this.sinks.get(seg);
    if (!m) { m = {}; this.sinks.set(seg, m); }
    m[kind] = (m[kind] || 0) + 1;
  }

  accept(b, kind) {
    const o = this.bobj.get(b);
    if (o instanceof Machine) {
      const cap = o.cap[kind] || 0;
      if (cap > o.have[kind]) { o.have[kind]++; return true; }
      return false;
    }
    if (o instanceof Storage) {
      if (o.count === 0) { o.item = kind; o.count = 1; return true; }
      if (kind !== o.item || o.limit <= o.count) return false;
      o.count++; return true;
    }
    if (b && !o && !BELT_IDS.has(b.id) && this.labsTakeAll) {   // labs, plants: take everything (not traced yet)
      this.consumed = this.consumed || new Map();
      const m = this.consumed.get(b) || {};
      m[kind] = (m[kind] || 0) + 1;
      this.consumed.set(b, m);
      return true;
    }
    return false;
  }

  pushOut(o, item) {
    if (o.out && o.seg && o.seg.free > 239) {
      o.seg.insert(this.M, item);
      if (o instanceof Machine) o.out--; else o.count--;
    }
  }

  segAt(p, needFlag = true) {
    const res = [];
    let cand = this.segs;
    if (!this.qt) cand = this.segsNear(p);
    else {
      const x0 = f32(f32(p[0]) - 0.5), y0 = f32(f32(p[1]) - 0.5);
      cand = this.qt.query([x0, y0, f32(x0 + 1), f32(y0 + 1)]);
    }
    for (const g of cand) {
      if (needFlag && !g.flag) continue;
      const q = g.project(p);
      if (q !== null) res.push([g, q]);
    }
    return res;
  }

  armTick(a) {
    if (a.prog < 0) {
      let got = null;
      const b = this.occ.get(key(a.src[0], a.src[1]));
      const o = b ? this.bobj.get(b) : undefined;
      if (o && o.out && (o instanceof Machine || o instanceof Storage)) {
        const item = o instanceof Machine ? o.outItem : o.item;
        if (a.filt === 0 || a.filt === item) {
          if (o instanceof Machine) o.out--; else o.count--;
          got = item;
        }
      } else if (!o) {
        if (!a.srcCache) a.srcCache = this.segAt(a.src);
        for (const [g, q] of a.srcCache) {
          const r = g.pick(q, a.filt);
          if (r) {
            got = r[0];
            a.lastPick = [g, q];
            if (r[1] === 0 && g.target instanceof Seg && g.target.inputs.length > 1) g.target.chosen = mergeNext(g.target);
            break;
          }
        }
      }
      if (got !== null) { a.held = got; a.prog = 0.0; }
    }
    const sp = f32(f32(a.boost + 1) * a.mult);
    if (a.prog >= 0) a.prog = progAdd(a.prog, sp);
    if (ARM_DROP <= a.prog && a.held !== null) {
      if (this.armDrop(a)) { a.held = null; a.moved++; } else a.prog = progSub(a.prog, sp);
    }
    if (ARM_END <= a.prog) a.prog = -1.0;
  }

  armDrop(a) {
    const b = this.occ.get(key(a.dst[0], a.dst[1]));
    if (b && (this.bobj.get(b) instanceof Machine || this.bobj.get(b) instanceof Storage ||
              (this.labsTakeAll && !BELT_IDS.has(b.id)))) return this.accept(b, a.held);
    if (!a.dstCache) a.dstCache = this.segAt(a.dst).slice(0, 1);
    for (const [g, q] of a.dstCache) {
      if (g.canDrop(q)) { g.drop(q, a.held); return true; }
      return false;
    }
    return false;
  }

  tick() {
    const M = this.M;
    for (const g of this.segs) segTick(g, M, this);
    for (const m of this.machines) m.tick(this);
    for (const e of this.extractors) e.tick(this);
    for (const a of this.arms) this.armTick(a);
    for (const st of this.storages) this.pushOut(st, st.item);
    for (const f of this.feeds) f.tick(M);
    this.tickNo++;
  }
}

// ------------------------------------------------------------------ belt-segment quadtree (arm lookups)
// As read from the Steam exe: root (-10000,-10000)-(10000,10000); a leaf holds up to 33 segments, the 34th insert
// splits it at float midpoints and re-inserts its segments (each into the first child that fully contains it, else
// it stays). Containment and overlap inclusive. A query returns a node's own overlapping segments in insertion
// order, then children 0..3 (a node fully inside the query box gives its whole subtree). Clearing keeps the
// children, so the tree's shape depends on build history; loading a save starts a fresh tree.
class QNode { constructor(b) { this.b = b; this.kids = null; this.els = []; } }
const qContains = (c, e) => c[0] <= e[0] && c[1] <= e[1] && e[2] <= c[2] && e[3] <= c[3];
const qOverlap = (a, b) => a[0] <= b[2] && b[0] <= a[2] && a[1] <= b[3] && b[1] <= a[3];
export class QuadTree {
  constructor() { const r = f32(10000); this.root = new QNode([-r, -r, r, r]); this.where = new Map(); }
  _subdivide(n) {
    const [x0, y0, x1, y1] = n.b;
    const hw = f32(f32(x1 - x0) * f32(0.5)), hh = f32(f32(y1 - y0) * f32(0.5));
    const mx = f32(x0 + hw), my = f32(y0 + hh);
    n.kids = [new QNode([x0, y0, mx, my]), new QNode([mx, y0, f32(mx + hw), my]),
      new QNode([x0, my, mx, f32(my + hh)]), new QNode([mx, my, f32(mx + hw), f32(my + hh)])];
    const old = n.els; n.els = [];
    for (const e of old) this._insertFrom(n, e);
  }
  _insertFrom(n, e) {
    for (;;) {
      if (n.kids === null) {
        if (n.els.length <= 32) { n.els.push(e); this.where.set(e[1], n); return; }
        this._subdivide(n);
      }
      const c = n.kids.find(k => qContains(k.b, e[0]));
      if (!c) { n.els.push(e); this.where.set(e[1], n); return; }
      n = c;
    }
  }
  insert(box, obj) { this._insertFrom(this.root, [box, obj]); }
  clear() { this.where.clear(); const st = [this.root]; while (st.length) { const n = st.pop(); n.els = []; if (n.kids) st.push(...n.kids); } }
  // the cell holding obj, its depth, and whether the cell is split (then obj sits on a split line, listed before
  // every segment in the smaller cells below it)
  locate(obj) {
    const n = this.where.get(obj);
    if (!n) return null;
    let d = 0, c = this.root;
    while (c !== n && c.kids) { c = c.kids.find(k => k.b[0] <= n.b[0] && k.b[1] <= n.b[1] && n.b[2] <= k.b[2] && n.b[3] <= k.b[3]); d++; }
    return { node: n, depth: d, onSplit: n.kids !== null };
  }
  // every cell overlapping box q (for drawing)
  cells(q, out = [], n = this.root, d = 0) {
    if (!qOverlap(q, n.b)) return out;
    out.push([n, d]);
    if (n.kids) for (const c of n.kids) this.cells(q, out, c, d + 1);
    return out;
  }
  query(q) {
    const out = [];
    const all = n => { for (const e of n.els) out.push(e[1]); if (n.kids) for (const c of n.kids) all(c); };
    const rec = n => {
      if (!qOverlap(q, n.b)) return;
      if (qContains(q, n.b)) { all(n); return; }
      for (const e of n.els) if (qOverlap(q, e[0])) out.push(e[1]);
      if (n.kids) for (const c of n.kids) rec(c);
    };
    rec(this.root);
    return out;
  }
}
