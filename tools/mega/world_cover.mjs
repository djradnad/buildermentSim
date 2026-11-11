// Which resource nodes the power plants of data/mega_site.json boost:
//   node tools/mega/world_cover.mjs <nodes.json from world_nodes.mjs>   ->  JSON {nodes: [[x, y, item, boosted 0/1]]}
// A plant in the save keeps the blueprint rule: its facing is the stored one turned 180 degrees.
import fs from 'fs';
import { buildingBox } from '../../site/js/gamesim.js';
const here = new URL('.', import.meta.url);
const nodes = JSON.parse(fs.readFileSync(process.argv[2])).nodes;
const pp = JSON.parse(fs.readFileSync(new URL('../../data/mega_site.json', here))).power_plants_3039;
const boxes = pp.map(([x, y, r]) => { const b = buildingBox({ id: 3039, x, y, o: (r + 2) & 3 }); return [b[0] - 9, b[1] - 9, b[2] + 9, b[3] + 9]; });
const e = 0.4998;
console.log(JSON.stringify({ plants: pp.map(([x, y, r], i) => ({ x, y, rot: r, box: boxes[i].map(v => v + 0) })),
  nodes: nodes.map(([x, y, t]) => [x, y, t, +boxes.some(b => x - e <= b[2] && b[0] <= x + e && y - e <= b[3] && b[1] <= y + e)]) }));
