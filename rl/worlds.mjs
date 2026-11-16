// Dump the resource nodes of Gen 2 worlds for the RL envs: node rl/worlds.mjs <out.json> <seed text>... [--ws 100 --ra 100]
import { generator, parseSeed } from '../site/js/worldgen.js';
import fs from 'fs';
const args = process.argv.slice(2);
const out = args.shift();
let ws = 100, ra = 100, mode = 'gen2';
const seeds = [];
for (let i = 0; i < args.length; i++) {
  if (args[i] === '--ws') ws = +args[++i]; else if (args[i] === '--ra') ra = +args[++i];
  else if (args[i] === '--mode') mode = args[++i]; else seeds.push(args[i]);
}
const module = await WebAssembly.compile(fs.readFileSync(new URL('../site/wasm/bldgen.wasm', import.meta.url)));
const gen = await generator(mode, { module });
const worlds = {};
for (const s of seeds) worlds[s] = { ws, ra, mode, nodes: Array.from(gen(parseSeed(s), ws, ra)) };
fs.writeFileSync(out, JSON.stringify(worlds));
for (const s of seeds) console.log(s, worlds[s].nodes.length / 3, 'nodes');
