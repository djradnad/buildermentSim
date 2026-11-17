// Game-exact speedrun runner: plays a build script on the real map with the tick-exact game sim
// (site/js/gamesim.js) plus the economy rules traced from the Steam exe:
//  - building cost = BuildCost + UpgradeCost of every tier up to its own (FUN_140eaacd0, charged by FUN_140ef90a0)
//  - delete refunds that same amount in full (FUN_140efdd50)
//  - items entering a lab pay their SellValue into the wallet and add progress (items and their gold value) to every
//    available tech, capped at its cost (FUN_140ee9560 -> FUN_140ee7500 / FUN_140ee3800); gold vaults pay only
//  - a tech whose progress reached its cost is unlocked by a free click (FUN_140f03bc0); we click at once
//  - placing or deleting rebuilds every belt segment and moves the cargo by world position (FUN_140eabaa0)
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { Factory, GAMEDATA, RECIPES } from '../../site/js/gamesim.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const T = JSON.parse(fs.readFileSync(path.join(HERE, '..', 'data', 'tables.json')));
const WORLDS = JSON.parse(fs.readFileSync(path.join(HERE, '..', 'data', 'worlds.json')));
const BKEY = {}; for (const [k, d] of Object.entries(GAMEDATA.buildings)) BKEY[d.id] = k;
const RES = { 1: 'Wood Log', 2: 'Iron Ore', 4: 'Copper Ore', 8: 'Stone', 16: 'Coal', 32: 'Wolframite', 64: 'Uranium Ore' };
const key = (x, y) => x + ',' + y;
export const TICKS = 60;

export function buildCost(id, tier = 1) {
  const d = T.buildings[BKEY[id]];
  if (!d) return 0;
  let g = (d.cost || {}).Gold || 0;
  const ups = d.upgrades || [];
  for (let t = 1; t < tier; t++) g += ((ups[t - 1] || {}).Gold) || 0;
  return g;
}

export class Run {
  constructor({ world = '2CCJhI', gen = 2, gold = 2000 } = {}) {
    const n = WORLDS[world].nodes;
    this.node = new Map();
    for (let i = 0; i < n.length; i += 3) this.node.set(key(n[i], n[i + 1]), RES[n[i + 2]]);
    this.gen = gen; this.gold = gold; this.tick = 0;
    this.bs = []; this.occ = new Map();
    this.done = new Set([4000]);
    this.prog = new Map();                  // tech id -> {item: n, Gold: n}
    this.first = {};                        // item -> tick first made
    this.log = [];
    this.f = null; this.rebuild();
  }
  techById(id) { return T.techs.find(t => t.id === id); }
  available() { return T.techs.filter(t => !this.done.has(t.id) && t.requires.every(r => this.done.has(r))); }
  unlockedBuilding(id) { return T.techs.some(t => this.done.has(t.id) && t.modifiers.some(m => m.UnlockedBuildingId === id)); }
  unlockedRecipe(id) { return T.techs.some(t => this.done.has(t.id) && t.modifiers.some(m => m.UnlockedRecipeId === id)); }

  canPlace(b) {
    if (!this.unlockedBuilding(b.id)) return 'locked building';
    if (b.recipe && RECIPES[b.recipe] && !this.unlockedRecipe(b.recipe)) return 'locked recipe';
    return null;
  }
  // place a group of buildings at once (one rebuild); false if not affordable/unlocked yet
  place(list) {
    let cost = 0;
    for (const b of list) { if (this.canPlace(b)) return false; cost += buildCost(b.id, b.tier || 1); }
    if (cost > this.gold) return false;
    this.gold -= cost;
    for (const b of list) this.bs.push({ tier: 1, recipe: 0, filter: 0, ...b });
    this.log.push([this.tick, 'place', list.length, cost]);
    this.rebuild();
    return true;
  }
  remove(pred) {
    const gone = this.bs.filter(pred);
    for (const b of gone) this.gold += buildCost(b.id, b.tier || 1);
    this.bs = this.bs.filter(b => !pred(b));
    this.log.push([this.tick, 'remove', gone.length]);
    this.rebuild();
  }

  rebuild() {
    const old = this.f;
    const ext = {};
    for (const b of this.bs) if ((b.id === 3004 || b.id === 3040) && this.node.has(key(b.x, b.y))) ext[key(b.x, b.y)] = this.node.get(key(b.x, b.y));
    const f = new Factory(this.bs, { extractors: ext, gen: this.gen, labsTakeAll: true });
    if (old) {
      // building state follows the building
      const ids = b => b.id + '@' + b.x + ',' + b.y;
      const prev = new Map();
      for (const [b, o] of old.bobj) prev.set(ids(b), o);
      for (const [b, o] of f.bobj) {
        const p = prev.get(ids(b));
        if (!p) continue;
        for (const k of ['have', 'out', 'prog', 'made', 'count', 'item', 'held']) if (Object.prototype.hasOwnProperty.call(p, k)) o[k] = typeof p[k] === 'object' && p[k] ? { ...p[k] } : p[k];
      }
      // belt cargo by world position (FUN_140eabaa0 "transferring cargo"); items that find no room are dropped
      const put = new Map();
      for (const g of old.segs) {
        let d = 0;
        for (let i = 0; i < g.items.length; i++) {
          d += g.items[i];
          const pos = g.L - d, p = pointAt(g, pos);
          let hit = null;
          for (const h of f.segsNear ? f.segsNear(p) : f.segs) { const q = h.project(p); if (q !== null) { hit = [h, q]; break; } }
          if (!hit) { this.dropped = (this.dropped || 0) + 1; continue; }
          if (!put.has(hit[0])) put.set(hit[0], []);
          put.get(hit[0]).push([hit[1], g.kinds[i]]);
        }
      }
      for (const [h, arr] of put) {
        arr.sort((a, b) => b[0] - a[0]);
        let front = h.L;
        h.items = []; h.kinds = [];
        for (const [q, k] of arr) {
          const gap = front - q;
          if (h.items.length && gap < 240) { this.dropped = (this.dropped || 0) + 1; continue; }
          h.items.push(gap); h.kinds.push(k); front = q;
        }
        h.free = front; h.recompute();
      }
      f.tickNo = old.tickNo;
    }
    this.f = f;
  }

  step() {
    const f = this.f;
    f.tick();
    this.tick++;
    for (const o of [...f.machines, ...f.extractors]) {
      const it = o.outItem || o.item;
      if (o.made > 0 && !(it in this.first)) this.first[it] = this.tick;
    }
    if (f.consumed && f.consumed.size) {
      for (const [b, m] of f.consumed) {
        const lab = b.id === 3013;
        for (const [it, c] of Object.entries(m)) {
          const gold = (T.sell[it] || 0) * c;
          this.gold += gold;
          if (!lab) continue;
          for (const t of this.available()) {
            const p = this.prog.get(t.id) || {};
            for (const [k, amt] of [[it, c], ['Gold', gold]]) if (t.cost[k]) p[k] = Math.min((p[k] || 0) + amt, t.cost[k]);
            this.prog.set(t.id, p);
          }
        }
      }
      f.consumed.clear();
      for (const t of this.available()) {
        const p = this.prog.get(t.id) || {};
        if (Object.entries(t.cost).every(([k, v]) => (p[k] || 0) >= v)) { this.done.add(t.id); this.log.push([this.tick, 'unlock', t.name]); }
      }
    }
  }
}

function pointAt(g, pos) {
  const P = g.pts, c = g.cum;
  for (let i = 0; i < P.length - 1; i++) {
    if (pos <= c[i + 1] || i === P.length - 2) {
      const t = c[i + 1] === c[i] ? 0 : (pos - c[i]) / (c[i + 1] - c[i]);
      return [P[i][0] + (P[i + 1][0] - P[i][0]) * t, P[i][1] + (P[i + 1][1] - P[i][1]) * t];
    }
  }
  return P[P.length - 1];
}
