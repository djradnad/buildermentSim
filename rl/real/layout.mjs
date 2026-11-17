// Placement helpers for the real-tile runner: ports of a building, BFS belt routes between ports.
import { World, DV, footprint } from '../../site/js/gamesim.js';
const key = (x, y) => x + ',' + y;

export function ports(b) {
  const w = new World([{ tier: 1, recipe: 0, filter: 0, ...b }]);
  return { ins: [...w.inport.values()].map(n => ({ x: n.x, y: n.y, dir: n.dir })),
           outs: [...w.outport.values()].map(n => ({ x: n.x, y: n.y, dir: n.dir })) };
}

// occupied tiles of the run (buildings) plus ore nodes we must keep free
export function blocked(run, keepNodes = true) {
  const s = new Set();
  for (const b of run.bs) for (const [x, y] of footprint(b)) s.add(key(x, y));
  if (keepNodes) for (const k of run.node.keys()) s.add(k);
  return s;
}

// belts from an output port to an input port: first tile next to the output (in its direction), last tile next to
// the input (on the side it takes from), shortest path over free tiles. Returns belt list or null.
export function route(run, out, inp, extraBlocked = new Set(), maxLen = 200) {
  const occ = blocked(run); for (const k of extraBlocked) occ.add(k);
  const s = [out.x + DV[out.dir][0], out.y + DV[out.dir][1]];
  // an input port accepts from the neighbour on its side: the belt there points back at the port
  const g = [inp.x + DV[inp.dir][0], inp.y + DV[inp.dir][1]];
  const lastDir = (inp.dir + 2) & 3;
  if (occ.has(key(...s)) || occ.has(key(...g))) return null;
  const prev = new Map([[key(...s), null]]);
  let q = [s], found = false;
  for (let depth = 0; q.length && depth < maxLen && !found; depth++) {
    const nq = [];
    for (const [x, y] of q) {
      if (x === g[0] && y === g[1]) { found = true; break; }
      for (let d = 0; d < 4; d++) {
        const nx = x + DV[d][0], ny = y + DV[d][1], k = key(nx, ny);
        if (prev.has(k) || occ.has(k)) continue;
        prev.set(k, [x, y]); nq.push([nx, ny]);
      }
    }
    if (!found) q = nq;
  }
  if (!prev.has(key(...g))) return null;
  const tiles = [];
  for (let p = g; p; p = prev.get(key(...p))) tiles.unshift(p);
  const belts = [];
  for (let i = 0; i < tiles.length; i++) {
    let d = lastDir;
    if (i + 1 < tiles.length) { const [dx, dy] = [tiles[i + 1][0] - tiles[i][0], tiles[i + 1][1] - tiles[i][1]]; d = DV.findIndex(v => v[0] === dx && v[1] === dy); }
    belts.push({ id: 3000, x: tiles[i][0], y: tiles[i][1], o: d });
  }
  return belts;
}
