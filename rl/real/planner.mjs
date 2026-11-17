// Turns high-level speedrun decisions into real placements for the runner.
// Script actions (each waits until it is affordable and unlocked, like the abstract replays):
//   ['lab', site]           research lab at lab site A/B/C (same sites as rl/econ.py)
//   ['dellab', site]        delete the lab at that site (gold back)
//   ['pre', res]            extractor on the nearest free node of res, no belt yet (its 10-item buffer fills)
//   ['feed', res]           a res extractor belted into the labs (a waiting one is connected first)
//   ['line', recipeKey]     one machine of the recipe near the labs, output belted into the labs, every input fed:
//                           raw inputs by extractors (waiting ones first), others by upstream machines (recursive)
import { GAMEDATA, DV, footprint } from '../../site/js/gamesim.js';
import { ports, blocked } from './layout.mjs';
import { buildCost } from './runner.mjs';
const key = (x, y) => x + ',' + y;
const RAW = new Set(['Wood Log', 'Iron Ore', 'Copper Ore', 'Stone', 'Coal', 'Wolframite', 'Uranium Ore']);
export const SITES = { A: [6, -2], B: [5, -10], C: [6, 2] };
const REC = Object.fromEntries(Object.entries(GAMEDATA.recipes).map(([k, r]) => [k.replace(/^recipe_/, ''), r]));
const MAIN = {};   // item -> main (non-alt) recipe
for (const [k, r] of Object.entries(REC)) if (!r.alt && !MAIN[r.out[0].item]) MAIN[r.out[0].item] = k;
const BID = Object.fromEntries(Object.values(GAMEDATA.buildings).map(d => [d.id, d]));

export class Planner {
  constructor(run) { this.r = run; this.waiting = []; this.trunks = []; }   // trunks: belt chains ending in a lab port

  labs() { return this.r.bs.filter(b => b.id === 3013); }
  center() { const L = this.labs(); return L.length ? [L[0].x, L[0].y] : SITES.A; }

  // ---- routing to one of many goals: goals = Map(tileKey -> dir the last belt must point)
  occBase() {                                    // blocked tiles, cached until the layout changes
    // belts may cross ore nodes (assumed; machines stay off them, see machineSpot)
    if (this._occN !== this.r.bs.length) { this._occ = blocked(this.r, false); this._occN = this.r.bs.length; }
    return this._occ;
  }
  routeTo(starts, goals, extra = new Set(), maxLen = 40) {
    const base = this.occBase();
    const occ = { has: k => base.has(k) || extra.has(k) };
    const prev = new Map(); let q = [];
    for (const s of starts) if (!occ.has(key(...s))) { prev.set(key(...s), null); q.push(s); }
    let hit = null;
    for (let depth = 0; q.length && depth < maxLen && !hit; depth++) {
      const nq = [];
      for (const [x, y] of q) {
        if (goals.has(key(x, y))) { hit = [x, y]; break; }
        for (let d = 0; d < 4; d++) {
          const nx = x + DV[d][0], ny = y + DV[d][1], k = key(nx, ny);
          if (prev.has(k) || occ.has(k)) continue;
          prev.set(k, [x, y]); nq.push([nx, ny]);
        }
      }
      q = nq;
    }
    if (!hit) return null;
    const tiles = [];
    for (let p = hit; p; p = prev.get(key(...p))) tiles.unshift(p);
    return tiles.map((t, i) => {
      let d = goals.get(key(...hit));
      if (i + 1 < tiles.length) { const dx = tiles[i + 1][0] - t[0], dy = tiles[i + 1][1] - t[1]; d = DV.findIndex(v => v[0] === dx && v[1] === dy); }
      return { id: 3000, x: t[0], y: t[1], o: d };
    });
  }
  // goals that deliver into an input port, or side-load into a belt chain already feeding it
  goalsFor(port, chain = []) {
    const goals = new Map(), occ = this.occBase();
    const g = [port.x + DV[port.dir][0], port.y + DV[port.dir][1]];
    if (!occ.has(key(...g))) goals.set(key(...g), (port.dir + 2) & 3);
    for (const b of chain) for (let d = 0; d < 4; d++) {
      if (d === b.o) continue;                                  // not from its front
      const n = [b.x + DV[d][0], b.y + DV[d][1]];
      const into = (d + 2) & 3;                                 // the new belt points back into b
      if (into === ((b.o + 2) & 3)) continue;
      if (!occ.has(key(...n))) goals.set(key(...n), into);
    }
    return goals;
  }

  sinkGoals(sink) {
    if (!sink.all) return this.goalsFor(sink.port, sink.chain);
    const g = new Map();                                        // the labs: every free port and every trunk
    for (const L of this.labs()) for (const p of ports(L).ins) for (const [k, d] of this.goalsFor(p, [])) g.set(k, d);
    for (const t of this.trunks) for (const [k, d] of this.goalsFor(t.port, t.chain)) g.set(k, d);
    return g;
  }
  // ---- a sink = {port, chain}: lab trunks and machine input ports
  labSink() {
    const L = this.labs()[0];
    return L ? { all: true, port: ports(L).ins[0], chain: [] } : null;
  }
  labSinkOld() {
    // a lab port that is free, else the shortest trunk to side-load into
    for (const L of this.labs()) for (const p of ports(L).ins) {
      const g = key(p.x + DV[p.dir][0], p.y + DV[p.dir][1]);
      const t = this.trunks.find(t => t.port.x === p.x && t.port.y === p.y);
      if (!t && !blocked(this.r).has(g)) { const nt = { port: p, chain: [] }; this.trunks.push(nt); return nt; }
    }
    const live = this.trunks.filter(t => this.labs().some(L => ports(L).ins.some(p => p.x === t.port.x && p.y === t.port.y)));
    return live.sort((a, b) => a.chain.length - b.chain.length)[0] || null;
  }

  nearestNodes(res, c) {
    const occ = blocked(this.r, false);
    return [...this.r.node].filter(([k, v]) => v === res && !occ.has(k)).map(([k]) => k.split(',').map(Number))
      .sort((a, b) => Math.abs(a[0] - c[0]) + Math.abs(a[1] - c[1]) - Math.abs(b[0] - c[0]) - Math.abs(b[1] - c[1]));
  }

  // pieces to bring one res extractor into sink (reusing a waiting extractor when there is one)
  extractorInto(res, sink, extra = new Set()) {
    const c = sink.port;
    let cands = this.waiting.filter(e => this.r.node.get(key(e.x, e.y)) === res).map(e => [e, true]);
    for (const n of this.nearestNodes(res, [c.x, c.y]).slice(0, 4)) for (const o of [0, 1, 2, 3]) cands.push([{ id: 3004, x: n[0], y: n[1], o }, false]);
    let best = null;
    for (const [e, old] of cands) {
      const os = old ? [e.o] : [0, 1, 2, 3];
      for (const o of os) {
        const ex = { ...e, o }, out = ports(ex).outs[0];
        const bl = this.routeTo([[out.x + DV[out.dir][0], out.y + DV[out.dir][1]]], this.sinkGoals(sink), extra, best ? best.belts.length : 60);
        if (bl && (!best || bl.length < best.belts.length)) best = { ext: ex, old, belts: bl };
      }
      if (best && old) break;
    }
    return best;
  }

  machineSpot(bid, recipeId, sink, extra) {
    // a free spot near the sink whose output can reach it; returns {b, belts}
    const c = [sink.port.x, sink.port.y], d = BID[bid];
    let best = null;
    for (let r = 2; r <= 16 && !best; r++) for (let dx = -r; dx <= r; dx++) for (let dy = -r; dy <= r; dy++) {
      if (Math.max(Math.abs(dx), Math.abs(dy)) !== r) continue;
      for (const o of [0, 1, 2, 3]) {
        const b = { id: bid, x: c[0] + dx, y: c[1] + dy, o, recipe: recipeId, tier: 1 };
        const base = this.occBase();
        if (footprint(b).some(([x, y]) => base.has(key(x, y)) || extra.has(key(x, y)) || this.r.node.has(key(x, y)))) continue;
        const P = ports(b);
        const ex2 = new Set([...extra, ...footprint(b).map(t => key(...t))]);
        const out = P.outs[0];
        // every input needs a free tile in front of it, and room around it for its feed
        const inT = P.ins.map(p => key(p.x + DV[p.dir][0], p.y + DV[p.dir][1]));
        if (inT.some(k => base.has(k) || ex2.has(k))) continue;
        const ex3 = new Set([...ex2, ...inT]);
        const bl = this.routeTo([[out.x + DV[out.dir][0], out.y + DV[out.dir][1]]], this.sinkGoals(sink), ex3, best ? best.belts.length : 40);
        if (bl && (!best || bl.length < best.belts.length)) best = { b, belts: bl };
      }
    }
    return best;
  }

  // full chain for one machine of recipe k feeding sink; returns list of pieces or null
  line(k, sink, extra = new Set(), pieces = [], connect = []) {
    const rec = REC[k], bid = GAMEDATA.buildings[rec.building].id;
    const m = this.machineSpot(bid, rec.id, sink, extra);
    if (!m) return null;
    pieces.push(m.b, ...m.belts);
    if (sink.top) this.topOut = m.belts;
    this.lastOut = m.belts;
    const ex = new Set([...extra, ...footprint(m.b).map(t => key(...t)), ...m.belts.map(t => key(t.x, t.y))]);
    const P = ports(m.b);
    for (let i = 0; i < rec.in.length; i++) {
      const inp = rec.in[i];
      const port = P.ins[i];
      const s = { port, chain: [], top: false };
      const per = inp.amount * 60 / rec.seconds;               // items/min at tier 1
      if (RAW.has(inp.item)) {
        const n = Math.max(1, Math.ceil(per / 15 - 1e-9));
        for (let j = 0; j < n; j++) {
          const e = this.extractorInto(inp.item, s, ex);
          if (!e) { if (j) break; return null; }          // extra feeds are best effort
          if (!e.old) pieces.push(e.ext); else connect.push(e.ext);
          pieces.push(...e.belts); s.chain.push(...e.belts);
          for (const t of [e.ext, ...e.belts]) ex.add(key(t.x, t.y));
          if (e.old) this.waiting = this.waiting.filter(w => w !== e.ext);
        }
      } else {
        const up = MAIN[inp.item];
        const r2 = REC[up];
        const n = Math.max(1, Math.ceil(per / (r2.out[0].amount * 60 / r2.seconds) - 1e-9));
        for (let j = 0; j < n; j++) {
          const before = pieces.length;
          if (!this.line(up, s, ex, pieces, connect)) { pieces.length = before; if (j) break; return null; }
          for (const t of pieces.slice(before)) { for (const f of footprint(t)) ex.add(key(...f)); }
          s.chain.push(...this.lastOut);
        }
      }
    }
    return pieces;
  }

  // ---- script actions -> pieces to place now (null: not possible)
  build(act) {
    const [a, x] = act;
    if (a === 'lab') return { pieces: [{ id: 3013, x: SITES[x][0], y: SITES[x][1], o: 0 }] };
    if (a === 'pre') {
      const n = this.nearestNodes(x, this.center())[0];
      if (!n) return null;
      const e = { id: 3004, x: n[0], y: n[1], o: 0 };
      return { pieces: [e], after: () => this.waiting.push(e) };
    }
    if (a === 'feed') {
      const s = this.labSink(); if (!s) return null;
      const e = this.extractorInto(x, s);
      if (!e) return null;
      return { pieces: e.old ? e.belts : [e.ext, ...e.belts], after: () => { this.trunks.push({ port: s.port, chain: e.belts }); if (e.old) this.waiting = this.waiting.filter(w => w !== e.ext); } };
    }
    if (a === 'line') {
      const s = this.labSink(); if (!s) return null;
      const saveWaiting = [...this.waiting];
      s.top = true;
      const pieces = this.line(x, s);
      s.top = false;
      if (!pieces) { this.waiting = saveWaiting; return null; }
      const out = this.topOut;
      return { pieces, undo: () => { this.waiting = saveWaiting; }, after: () => this.trunks.push({ port: s.port, chain: out }) };
    }
    return null;
  }
}

// play a script; returns {first, log, gold}
export function play(run, script, { maxSeconds = 1800, until = null } = {}) {
  const P = new Planner(run);
  let i = 0, plan = null;
  const T = maxSeconds * 60;
  while (run.tick < T) {
    if (until && until in run.first) break;
    while (i < script.length) {
      const act = script[i];
      if (act[0] === 'dellab') { const [sx, sy] = SITES[act[1]]; run.remove(b => b.id === 3013 && b.x === sx && b.y === sy); i++; plan = null; continue; }
      if (!plan) {
        if (run.tick < (act.next || 0)) break;                  // re-plan at most once a second
        plan = P.build(act);
        if (!plan) { act.tries = (act.tries || 0) + 1; act.next = run.tick + 60; if (act.tries > 20) i++; break; }
      }
      if (plan.pieces.some(b => run.canPlace(b))) break;         // still locked: wait for research
      const cost = plan.pieces.reduce((s, b) => s + buildCost(b.id, b.tier || 1), 0);
      if (cost > run.gold) break;
      if (!run.place(plan.pieces)) { if (plan.undo) plan.undo(); plan = null; break; }
      if (plan.after) plan.after();
      plan = null; i++;
    }
    run.step();
  }
  return { first: run.first, log: run.log, gold: run.gold, script };
}
