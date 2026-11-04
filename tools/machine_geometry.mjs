// Footprint and port tiles of every machine for each rotation, from the game-exact World (site/js/gamesim.js).
//   node tools/machine_geometry.mjs > bmsolver/machines.json
// Offsets are relative to the blueprint anchor (x, y) of a building placed with blueprint rotation o.
// Port dir = the side the port faces (0 N, 1 W, 2 S, 3 E): an input takes only items pushed straight in across
// that side (a belt, splitter side or exit on the outside neighbour); an output pushes items out through that side.
import { World, GAMEDATA } from '../site/js/gamesim.js';
const out = {};
const D2 = { 0: 'N', 1: 'W', 2: 'S', 3: 'E' };
for (const [k, b] of Object.entries(GAMEDATA.buildings)) {
  if (b.id < 3004 || b.id === 3011 || b.id === 3012) continue;
  if (!b.inputs.length && !b.outputs.length) continue;
  const rots = [];
  for (let o = 0; o < 4; o++) {
    const w = new World([{ id: b.id, x: 0, y: 0, o, tier: 1, recipe: 0, filter: 0 }]);
    const tiles = [...w.occ.keys()].map(s => s.split(',').map(Number));
    const ins = [...w.inport.values()].map(n => [n.x, n.y, n.dir]);
    const outs = [...w.outport.values()].map(n => [n.x, n.y, n.dir]);
    rots.push({ o, tiles, inputs: ins, outputs: outs });
  }
  out[b.id] = { key: k, name: b.name, w: b.w, h: b.h, tier_speed: b.tier_speed, rots };
}
console.log(JSON.stringify(out));
