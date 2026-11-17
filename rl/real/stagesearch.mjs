// Coordinate search over the stage parameters (lab spot, extra feeds/lines, pre-built extractors) with exact
// timing. usage: node rl/real/stagesearch.mjs <out.json> [items...]
import fs from 'fs';
import { script, techsFor } from './gen.mjs';
import { evaluate } from './search.mjs';
const P_ = (...a) => fs.writeSync(1, a.join(' ') + '\n');
const DOM = { site: [0, 1, 2], feeds: [0, 1, 2, 3, 5], lines: [0, 1, 2], pre: [0, 2, 4, 6, 8], relab: [0, 1] };
const [dst, ...items] = process.argv.slice(2);
const out = fs.existsSync(dst) ? JSON.parse(fs.readFileSync(dst)) : {};
for (const it of items) {
  if (out[it] && out[it].done) { P_('SKIP', it, out[it].seconds.toFixed(1)); continue; }
  const n = techsFor(it).length + 1;
  let params = out[it] ? out[it].params : Array.from({ length: n }, () => ({ site: 0, feeds: 1, lines: 0, pre: 4 }));
  const ev = (p, lim = 1800) => { try { return evaluate(it, script(it, p).script, Math.min(1800, lim)); } catch (e) { return Infinity; } };
  let best = ev(params), evals = 1;
  for (let pass = 0; pass < 4; pass++) {
    let improved = false;
    for (let i = 0; i < n; i++) for (const [k, vals] of Object.entries(DOM)) for (const v of vals) {
      if (params[i][k] === v) continue;
      const c = params.map(p => ({ ...p })); c[i][k] = v;
      const t = ev(c, isFinite(best) ? best + 1 : 1800); evals++;
      if (t < best - 1e-9) { best = t; params = c; improved = true; }
    }
    P_(it, 'pass', pass, 'best', best.toFixed(1), 'evals', evals);
    out[it] = { seconds: best, params, done: false }; fs.writeFileSync(dst, JSON.stringify(out));
    if (!improved) break;
  }
  out[it] = { seconds: best, params, script: script(it, params).script, stages: script(it, params).stages, done: true };
  fs.writeFileSync(dst, JSON.stringify(out));
  P_('DONE', it, best.toFixed(1));
}
