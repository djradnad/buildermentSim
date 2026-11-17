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
  constructor(run) {
    this.r = run; this.waiting = []; this.trunks = [];
    this.feeds = [];          // lab feeds {res, ext, belts, deps}: an unused one can be re-routed into a machine
    this.owner = new Map();   // belt tile -> feed that laid it
    this.freed = new Set();   // tiles of feeds being re-routed in the plan being built
  }   // trunks: belt chains ending in a lab port

  labs() { return this.r.bs.filter(b => b.id === 3013); }
  center() { const L = this.labs(); return L.length ? [L[L.length - 1].x, L[L.length - 1].y] : SITES.A; }

  // ---- routing to one of many goals: goals = Map(tileKey -> dir the last belt must point)
  occBase() {                                    // blocked tiles, cached until the layout changes
    // belts may cross ore nodes (assumed; machines stay off them, see machineSpot)
    if (this._occN !== this.r.bs.length) { this._occ = blocked(this.r, false); this._occN = this.r.bs.length; }
    return this._occ;
  }
  routeTo(starts, goals, extra = new Set(), maxLen = 40) {
    const base = this.occBase();
    const fr = this.freed;
    const occ = { has: k => (base.has(k) && !fr.has(k)) || extra.has(k) };
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

  markDeps(belts) {              // a belt ending against another feed's belt makes that feed busy
    for (const b of belts) { const f = this.owner.get(key(b.x + DV[b.o][0], b.y + DV[b.o][1])); if (f && !belts.includes(f.belts[0])) f.deps++; }
  }
  sinkGoals(sink) {
    if (!sink.all) return this.goalsFor(sink.port, sink.chain);
    const g = new Map();                                        // the labs: every free port and every trunk
    for (const L of this.labs()) for (const p of ports(L).ins) for (const [k, d] of this.goalsFor(p, [])) g.set(k, d);
    const live = new Set(this.labs().flatMap(L => ports(L).ins.map(p => key(p.x, p.y))));
    for (const t of this.trunks) if (live.has(key(t.port.x, t.port.y))) for (const [k, d] of this.goalsFor(t.port, t.chain)) g.set(k, d);
    return g;
  }
  // ---- a sink = {port, chain}: lab trunks and machine input ports
  labSink() {
    const L = this.labs()[this.labs().length - 1];
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
    // re-route an existing lab feed of this resource that nothing else joins (players reroute ore like this)
    for (const f of this.feeds) if (f.res === res && !f.deps && !f.taken) cands.push([f.ext, 'feed', f]);
    let best = null;
    for (const [e, old, f] of cands) {
      if (f) for (const b of f.belts) this.freed.add(key(b.x, b.y));
      const os = old ? [e.o] : [0, 1, 2, 3];
      for (const o of os) {
        const ex = { ...e, o }, out = ports(ex).outs[0];
        const bl = this.routeTo([[out.x + DV[out.dir][0], out.y + DV[out.dir][1]]], this.sinkGoals(sink), extra, best ? best.belts.length : 60);
        if (bl && (!best || bl.length < best.belts.length)) best = { ext: ex, old, belts: bl, feed: f };
      }
      if (f && !(best && best.feed === f)) for (const b of f.belts) this.freed.delete(key(b.x, b.y));
      if (best && old === true) break;
    }
    if (best && best.feed) best.feed.taken = true;
    return best;
  }

  machineSpots(bid, recipeId, sink, extra, K = 6) {
    // free spots near the sink whose output can reach it, shortest output belt first; [{b, belts}]
    const c = [sink.port.x, sink.port.y];
    const found = [];
    const base = this.occBase();
    for (let r = 2; r <= 16 && found.length < K; r++) for (let dx = -r; dx <= r; dx++) for (let dy = -r; dy <= r; dy++) {
      if (Math.max(Math.abs(dx), Math.abs(dy)) !== r) continue;
      for (const o of [0, 1, 2, 3]) {
        const b = { id: bid, x: c[0] + dx, y: c[1] + dy, o, recipe: recipeId, tier: 1 };
        const fp = footprint(b).map(t => key(...t));
        if (fp.some(k => base.has(k) || extra.has(k) || this.r.node.has(k))) continue;
        const P = ports(b);
        const ex2 = new Set([...extra, ...fp]);
        const out = P.outs[0];
        // every input needs a free tile in front of it with a free way out
        const inT = P.ins.map(p => [p.x + DV[p.dir][0], p.y + DV[p.dir][1]]);
        if (inT.some(([x, y]) => base.has(key(x, y)) || ex2.has(key(x, y)))) continue;
        const ex3 = new Set([...ex2, ...inT.map(t => key(...t))]);
        if (inT.some(([x, y]) => DV.every(([ddx, ddy]) => { const k = key(x + ddx, y + ddy); return base.has(k) || ex3.has(k); }))) continue;
        const bl = this.routeTo([[out.x + DV[out.dir][0], out.y + DV[out.dir][1]]], this.sinkGoals(sink), ex3, 40);
        if (bl) found.push({ b, belts: bl });
      }
    }
    return found.sort((p, q) => p.belts.length - q.belts.length).slice(0, K);
  }

  // full chain for one machine of recipe k feeding sink; pieces appended, null if it can't be laid out
  line(k, sink, extra = new Set(), pieces = [], connect = [], depth = 0) {
    const rec = REC[k], bid = GAMEDATA.buildings[rec.building].id;
    const cands = this.machineSpots(bid, rec.id, sink, extra, depth ? 3 : 6);
    for (const m of cands) {
      const before = pieces.length, waiting0 = [...this.waiting];
      if (this.feedMachine(rec, m, extra, pieces, connect, depth)) {
        if (sink.top) this.topOut = m.belts;
        this.lastOut = m.belts;
        return pieces;
      }
      pieces.length = before; this.waiting = waiting0;
    }
    return null;
  }

  feedMachine(rec, m, extra, pieces, connect, depth) {
    pieces.push(m.b, ...m.belts);
    const ex = new Set([...extra, ...footprint(m.b).map(t => key(...t)), ...m.belts.map(t => key(t.x, t.y))]);
    const P = ports(m.b);
    for (let i = 0; i < rec.in.length; i++) {
      const inp = rec.in[i];
      const s = { port: P.ins[i], chain: [], top: false };
      const per = inp.amount * 60 / rec.seconds;               // items/min at tier 1
      if (RAW.has(inp.item)) {
        const n = Math.max(1, Math.ceil(per / 15 - 1e-9));
        for (let j = 0; j < n; j++) {
          const e = this.extractorInto(inp.item, s, ex);
          if (!e) { if (j) break; return false; }          // extra feeds are best effort
          if (e.feed) this.rewired.push(e.feed);
          else if (!e.old) pieces.push(e.ext); else connect.push(e.ext);
          pieces.push(...e.belts); s.chain.push(...e.belts);
          for (const t of [e.ext, ...e.belts]) ex.add(key(t.x, t.y));
          if (e.old === true) this.waiting = this.waiting.filter(w => w !== e.ext);
        }
      } else {
        const up = MAIN[inp.item], r2 = REC[up];
        const n = Math.max(1, Math.ceil(per / (r2.out[0].amount * 60 / r2.seconds) - 1e-9));
        for (let j = 0; j < n; j++) {
          const before = pieces.length;
          if (!this.line(up, s, ex, pieces, connect, depth + 1)) { pieces.length = before; if (j) break; return false; }
          for (const t of pieces.slice(before)) for (const f of footprint(t)) ex.add(key(...f));
          s.chain.push(...this.lastOut);
        }
      }
    }
    return true;
  }

  // ---- script actions -> pieces to place now (null: not possible)
  build(act) {
    const [a, x] = act;
    for (const f of this.feeds) f.taken = false;
    this.freed = new Set(); this.rewired = [];
    if (a === 'lab') { const p = Array.isArray(x) ? x : SITES[x]; return { pieces: [{ id: 3013, x: p[0], y: p[1], o: 0 }] }; }
    if (a === 'pre') {
      const n = this.nearestNodes(x, act[2] || this.center())[0];
      if (!n) return null;
      const c = act[2] || this.center(), dx = c[0] - n[0], dy = c[1] - n[1];
      const d = Math.abs(dx) >= Math.abs(dy) ? (dx > 0 ? 3 : 1) : (dy > 0 ? 2 : 0);   // face the lab (DV: 0 N 1 W 2 S 3 E)
      // an extractor's output side is its o (ports() of id 3004: o 0 -> dir 0)
      const e = { id: 3004, x: n[0], y: n[1], o: d };
      return { pieces: [e], after: () => this.waiting.push(e) };
    }
    if (a === 'feed') {
      const s = this.labSink(); if (!s) return null;
      const e = this.extractorInto(x, s);
      if (!e) return null;
      if (e.feed) return null;                                   // a feed never steals another feed
      return { pieces: e.old ? e.belts : [e.ext, ...e.belts], after: () => {
        this.trunks.push({ port: s.port, chain: e.belts });
        this.markDeps(e.belts);
        const f = { res: x, ext: e.ext, belts: e.belts, deps: 0 };
        this.feeds.push(f); for (const b of e.belts) this.owner.set(key(b.x, b.y), f);
        if (e.old) this.waiting = this.waiting.filter(w => w !== e.ext);
      } };
    }
    if (a === 'line') {
      const s = this.labSink(); if (!s) return null;
      const saveWaiting = [...this.waiting];
      s.top = true;
      const pieces = this.line(x, s);
      s.top = false;
      if (!pieces) { this.waiting = saveWaiting; return null; }
      const out = this.topOut, rew = [...this.rewired];
      const gone = new Set(rew.flatMap(f => f.belts.map(b => key(b.x, b.y))));
      return { pieces, remove: gone, undo: () => { this.waiting = saveWaiting; }, after: () => {
        this.trunks.push({ port: s.port, chain: out });
        for (const f of rew) { this.feeds = this.feeds.filter(g => g !== f); for (const t of this.trunks) t.chain = t.chain.filter(b => !gone.has(key(b.x, b.y))); }
        this.markDeps(pieces.filter(b => b.id === 3000));
      } };
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
      if (act[0] === 'waitTech') { if (run.done.has(act[1])) { i++; continue; } break; }
      if (act[0] === 'movelab') {
        // john 2026-11-17: only the lab moves; the old site's belts and machines stay and back up until a lab
        // stands there again
        const L = run.bs.filter(b => b.id === 3013);
        const old = L[L.length - 1];
        const lab = { id: 3013, x: act[1][0], y: act[1][1], o: 0 };
        if (old && old.x === lab.x && old.y === lab.y) { i++; continue; }
        if (!old) { if (!run.place([lab])) break; i++; continue; }
        if (run.gold + buildCost(3013) < buildCost(3013)) break;
        run.remove(b => b === old);
        if (!run.place([lab])) { run.place([old]); break; }
        i++; plan = null; continue;
      }
      if (act[0] === 'relocate') {
        // move the lab: delete every lab, machine and belt (full refund), keep extractors as waiting ones (their
        // buffers fill until connected again), then a lab at the new spot
        const lab = { id: 3013, x: act[1][0], y: act[1][1], o: 0 };
        const keep = b => b.id === 3004 || b.id === 3040;
        const refund = run.bs.filter(b => !keep(b)).reduce((s, b) => s + buildCost(b.id, b.tier || 1), 0);
        if (run.gold + refund < buildCost(3013)) break;
        run.remove(b => !keep(b));
        P.waiting = run.bs.filter(keep); P.trunks = []; P.feeds = []; P.owner = new Map();
        if (!run.place([lab])) break;
        i++; plan = null; continue;
      }
      if (act[0] === 'dellab') { const [sx, sy] = SITES[act[1]]; run.remove(b => b.id === 3013 && b.x === sx && b.y === sy); i++; plan = null; continue; }
      if (!plan) {
        if (run.tick < (act.next || 0)) break;                  // re-plan at most once a second
        plan = P.build(act);
        if (!plan) { act.tries = (act.tries || 0) + 1; act.next = run.tick + 60; if (act.tries > 20) i++; break; }
      }
      if (plan.pieces.some(b => run.canPlace(b))) break;         // still locked: wait for research
      const cost = plan.pieces.reduce((s, b) => s + buildCost(b.id, b.tier || 1), 0);
      if (cost > run.gold && !(plan.remove && plan.remove.size)) break;
      if (plan.remove && plan.remove.size) {
        const refund = run.bs.filter(b => b.id === 3000 && plan.remove.has(key(b.x, b.y))).reduce((s, b) => s + buildCost(b.id), 0);
        if (cost > run.gold + refund) break;
        run.remove(b => b.id === 3000 && plan.remove.has(key(b.x, b.y)));
      }
      if (!run.place(plan.pieces)) { if (plan.undo) plan.undo(); plan = null; break; }
      if (plan.after) plan.after();
      plan = null; i++;
    }
    run.step();
  }
  return { first: run.first, log: run.log, gold: run.gold, script };
}
