// Resource nodes of a world as JSON:  node tools/mega/world_nodes.mjs <seed> [size 100] [resources 200] [mode gen2]
import fs from 'fs';
import { generator, parseSeed, NODE_ITEM } from '../../site/js/worldgen.js';
const here = new URL('.', import.meta.url);
const [seed, ws = '100', ra = '200', mode = 'gen2'] = process.argv.slice(2);
const module = await WebAssembly.compile(fs.readFileSync(new URL('../../site/wasm/bldgen.wasm', here)));
const g = await generator(mode, { module });
const a = g(parseSeed(seed), +ws, +ra);
const nodes = [];
for (let i = 0; i < a.length; i += 3) nodes.push([a[i], a[i + 1], NODE_ITEM[a[i + 2]]]);
console.log(JSON.stringify({ seed, size: +ws, resources: +ra, mode, nodes }));
