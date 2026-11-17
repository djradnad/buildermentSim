// Lab spots near the right resource clusters (john 2026-11-17: runners pick clusters anywhere on the map per stage).
import { footprint } from '../../site/js/gamesim.js';
const key = (x, y) => x + ',' + y;
const lab_belts = (x, y, lx, ly) => Math.max(Math.max(Math.abs(x - lx) - 1, 0) + Math.max(Math.abs(y - ly) - 1, 0) - 1, 0);

// needs: {res: count of extractors wanted}; returns up to K spots [{x, y, score}] (score = belt tiles to the
// needed nodes, nearest first), at least `gap` tiles apart
export function bestSpots(run, needs, { reach = 60, K = 3, gap = 6, center = [0, 0] } = {}) {
  const occ = new Set();
  for (const k of run.node.keys()) occ.add(k);
  for (const b of run.bs) if (b.id !== 3013) for (const [x, y] of footprint(b)) occ.add(key(x, y));
  const byRes = {};
  for (const [k, v] of run.node) (byRes[v] = byRes[v] || []).push(k.split(',').map(Number));
  const out = [];
  for (let lx = center[0] - reach; lx <= center[0] + reach; lx++) for (let ly = center[1] - reach; ly <= center[1] + reach; ly++) {
    let free = true;
    for (let i = -1; i <= 1 && free; i++) for (let j = -1; j <= 1; j++) if (occ.has(key(lx + i, ly + j))) { free = false; break; }
    if (!free) continue;
    let sc = 0;
    for (const [res, n] of Object.entries(needs)) {
      const ds = (byRes[res] || []).map(([x, y]) => lab_belts(x, y, lx, ly)).sort((a, b) => a - b);
      if (ds.length < n) { sc = Infinity; break; }
      for (let i = 0; i < n; i++) sc += ds[i];
    }
    out.push({ x: lx, y: ly, score: sc });
  }
  out.sort((a, b) => a.score - b.score);
  const pick = [];
  for (const s of out) { if (pick.every(p => Math.abs(p.x - s.x) + Math.abs(p.y - s.y) >= gap)) pick.push(s); if (pick.length >= K) break; }
  return pick;
}
