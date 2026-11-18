// Lab spots near the right resource clusters (john 2026-11-17: runners pick clusters anywhere on the map per stage).
import { footprint } from '../../site/js/gamesim.js';
const key = (x, y) => x + ',' + y;
const lab_belts = (x, y, lx, ly) => Math.max(Math.max(Math.abs(x - lx) - 1, 0) + Math.max(Math.abs(y - ly) - 1, 0) - 1, 0);

// needs: {res: count of extractors wanted}; returns up to K spots [{x, y, score}] (score = belt tiles to the
// needed nodes, nearest first), at least `gap` tiles apart
export function bestSpots(run, needs, { reach = 60, K = 3, gap = 6, center = [0, 0], used = new Set(), near = 0, maxBelt = 40 } = {}) {
  // near > 0: lab spots anywhere on the map within `near` tiles of a needed node (runners use any cluster);
  // otherwise the square of `reach` around center. `used`: node keys earlier stages already took.
  const occ = new Set();
  for (const k of run.node.keys()) occ.add(k);
  for (const b of run.bs) if (b.id !== 3013) for (const [x, y] of footprint(b)) occ.add(key(x, y));
  const byRes = {};
  for (const [k, v] of run.node) if (!used.has(k)) (byRes[v] = byRes[v] || []).push(k.split(',').map(Number));
  const cand = [];
  if (near > 0) {
    const seen = new Set();
    for (const res of Object.keys(needs)) for (const [x, y] of byRes[res] || [])
      for (let dx = -near; dx <= near; dx++) for (let dy = -near; dy <= near; dy++) { const k = key(x + dx, y + dy); if (!seen.has(k)) { seen.add(k); cand.push([x + dx, y + dy]); } }
  } else for (let lx = center[0] - reach; lx <= center[0] + reach; lx++) for (let ly = center[1] - reach; ly <= center[1] + reach; ly++) cand.push([lx, ly]);
  const out = [];
  for (const [lx, ly] of cand) {
    let free = true;
    for (let i = -1; i <= 1 && free; i++) for (let j = -1; j <= 1; j++) if (occ.has(key(lx + i, ly + j))) { free = false; break; }
    if (!free) continue;
    let sc = 0;
    for (const [res, n] of Object.entries(needs)) {
      const all = byRes[res] || [];
      if (all.length < n) { sc = Infinity; break; }
      // n smallest distances without sorting everything
      const best = [];
      for (const [x, y] of all) {
        const d = lab_belts(x, y, lx, ly);
        if (best.length < n) { best.push(d); best.sort((a, b) => a - b); }
        else if (d < best[n - 1]) { best[n - 1] = d; best.sort((a, b) => a - b); }
      }
      for (const d of best) sc += d > maxBelt ? Infinity : d;   // the planner routes at most ~60 tiles
      if (sc === Infinity) break;
    }
    out.push({ x: lx, y: ly, score: sc });
  }
  out.sort((a, b) => a.score - b.score || Math.abs(a.x - center[0]) + Math.abs(a.y - center[1]) - Math.abs(b.x - center[0]) - Math.abs(b.y - center[1]));
  const pick = [];
  for (const s of out) { if (pick.every(p => Math.abs(p.x - s.x) + Math.abs(p.y - s.y) >= gap)) pick.push(s); if (pick.length >= K) break; }
  return pick;
}
// the n nearest unused nodes of res to (x, y)
export function nearestUnused(run, res, x, y, n, used) {
  const all = [];
  for (const [k, v] of run.node) if (v === res && !used.has(k)) { const [nx, ny] = k.split(',').map(Number); all.push([lab_belts(nx, ny, x, y), k]); }
  return all.sort((a, b) => a[0] - b[0]).slice(0, n).map(a => a[1]);
}
