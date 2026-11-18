// Coordinate search over the stage parameters (lab spot, extra feeds/lines, pre-built extractors) with exact
// timing. usage: node rl/real/stagesearch.mjs <out.json> [items...]
import fs from 'fs';
import { script, techsFor } from './gen.mjs';
import { evaluate } from './search.mjs';
const P_ = (...a) => fs.writeSync(1, a.join(' ') + '\n');
const CAP = +(process.env.CAP || 1800);
const DOM = { site: [0, 1, 2], feeds: [0, 1, 2, 3, 5], lines: [0, 1, 2, 4], pre: [0, 2, 4, 6, 8], relab: [0, 1], clean: [0, 1] };
const [dst, ...items] = process.argv.slice(2);
const out = fs.existsSync(dst) ? JSON.parse(fs.readFileSync(dst)) : {};
for (const it of items) {
  if (out[it] && out[it].done) { P_('SKIP', it, out[it].seconds.toFixed(1)); continue; }
  const n = techsFor(it).length + 1;
  let params = out[it] ? out[it].params : Array.from({ length: n }, () => ({ site: 0, feeds: 1, lines: 0, pre: 4 }));
  // seed (SEED=file of solved items): the stages a solved item shares with this one (same tech prefix) start
  // from its found params
  if (!out[it] && process.env.SEED && fs.existsSync(process.env.SEED)) {
    const seeds = JSON.parse(fs.readFileSync(process.env.SEED)), mine = techsFor(it).map(t => t.id);
    let bestK = 0, bestP = null;
    for (const [o, v] of Object.entries(seeds)) {
      if (o === it || !v.params) continue;
      const th = techsFor(o).map(t => t.id);
      let k = 0; while (k < th.length && k < mine.length && th[k] === mine[k]) k++;
      if (k === th.length && k > bestK) { bestK = k; bestP = v.params; }   // its whole tech list is my prefix
    }
    if (bestP) { for (let i = 0; i < bestK; i++) params[i] = { ...bestP[i] }; P_(it, 'seeded', bestK, 'stages'); }
  }
  const ev = (p, lim = CAP) => { try { return evaluate(it, script(it, p).script, Math.min(CAP, lim)); } catch (e) { return Infinity; } };
  let best = ev(params), evals = 1;
  for (let pass = 0; pass < 4; pass++) {
    let improved = false;
    for (let i = 0; i < n; i++) for (const [k, vals] of Object.entries(DOM)) for (const v of vals) {
      if (params[i][k] === v) continue;
      const c = params.map(p => ({ ...p })); c[i][k] = v;
      const t = ev(c, isFinite(best) ? best + 1 : CAP); evals++;
      if (t < best - 1e-9) { best = t; params = c; improved = true; out[it] = { seconds: best, params, done: false }; fs.writeFileSync(dst, JSON.stringify(out)); }
    }
    P_(it, 'pass', pass, 'best', best.toFixed(1), 'evals', evals);
    out[it] = { seconds: best, params, done: false }; fs.writeFileSync(dst, JSON.stringify(out));
    if (!improved) break;
  }
  out[it] = { seconds: best, params, script: script(it, params).script, stages: script(it, params).stages, done: true };
  fs.writeFileSync(dst, JSON.stringify(out));
  P_('DONE', it, best.toFixed(1));
}
