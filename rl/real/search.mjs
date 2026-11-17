// Exact (tick-level, real map) re-timing and local search of the abstract best runs.
// usage: node rl/real/search.mjs <best.json> <out.json> [rounds] [items...]
import fs from 'fs';
import { Run } from './runner.mjs';
import { play } from './planner.mjs';
const P_ = (...a) => fs.writeSync(1, a.join(' ') + '\n');

const SITE = { lab: 'A', 'lab@base': 'A', 'lab@wood': 'B', 'lab@iron': 'C' };
export function convert(desc) {
  const out = [];
  let waiting = {};
  for (const d of desc) {
    if (d === 'wait') continue;
    // lab moves need trunks re-routed to the new lab: not in the real planner yet, so keep the first lab only
    if (SITE[d]) { if (!out.some(a => a[0] === 'lab')) out.push(['lab', SITE[d]]); continue; }
    let m;
    if ((m = d.match(/^del lab@(\w+)/))) continue;
    if ((m = d.match(/^pre-extractor (.+)$/))) { out.push(['pre', m[1]]); waiting[m[1]] = (waiting[m[1]] || 0) + 1; continue; }
    if ((m = d.match(/^connect (.+)$/))) { for (let i = 0; i < (waiting[m[1]] || 0); i++) out.push(['feed', m[1]]); waiting[m[1]] = 0; continue; }
    if ((m = d.match(/^\+(\d) extractor (.+)$/))) { for (let i = 0; i < +m[1]; i++) out.push(['feed', m[2]]); continue; }
    if ((m = d.match(/^\+(\d) (\w+)$/))) { for (let i = 0; i < +m[1]; i++) out.push(['line', m[2]]); continue; }
  }
  return out;
}

export function evaluate(item, script, maxSeconds = 1500) {
  const r = new Run();
  const s = script.map(a => [...a]);
  play(r, s, { maxSeconds, until: item });
  return item in r.first ? r.first[item] / 60 : Infinity;
}

const POOL_RAW = ['Wood Log', 'Iron Ore', 'Copper Ore', 'Stone', 'Coal'];
function mutate(s, rng) {
  const p = s.map(a => [...a]);
  const i = Math.floor(rng() * p.length);
  const k = Math.floor(rng() * 7);
  if (k === 0 && p.length > 1) p.splice(i, 1);
  else if (k === 1) p.splice(i, 0, [...p[i]]);
  else if (k === 2 && p.length > 1) { const j = Math.min(i + 1, p.length - 1); [p[i], p[j]] = [p[j], p[i]]; }
  else if (k === 3) p.splice(i, 0, ['pre', POOL_RAW[Math.floor(rng() * 5)]]);
  else if (k === 4) p.splice(i, 0, ['feed', POOL_RAW[Math.floor(rng() * 5)]]);
  else if (k === 5) { const L = p.findIndex(a => a[0] === 'lab'); if (L >= 0) p[L] = ['lab', 'ABC'[Math.floor(rng() * 3)]]; }
  else { const lines = p.filter(a => a[0] === 'line'); if (lines.length) p.splice(i, 0, [...lines[Math.floor(rng() * lines.length)]]); }
  return p;
}

function rngOf(seed) { let x = seed >>> 0 || 1; return () => ((x ^= x << 13, x ^= x >>> 17, x ^= x << 5) >>> 0) / 4294967296; }

if (process.argv[1].endsWith('search.mjs')) {
  const [src, dst, roundsS, ...items] = process.argv.slice(2);
  const best = JSON.parse(fs.readFileSync(src));
  const out = fs.existsSync(dst) ? JSON.parse(fs.readFileSync(dst)) : {};
  const rounds = +(roundsS || 20);
  const rng = rngOf(7);
  for (const it of (items.length ? items : Object.keys(best))) {
    const desc = best[it][3] || null;
    let s = out[it] ? out[it].script : convert(best[it][1]);
    let t = evaluate(it, s);
    const t0 = t;
    for (let r = 0; r < rounds; r++) {
      for (let m = 0; m < 24; m++) {
        const c = mutate(s, rng), tc = evaluate(it, c, Math.min(1500, isFinite(t) ? t + 2 : 1500));
        if (tc < t) { t = tc; s = c; }
      }
    }
    out[it] = { seconds: t, script: s, abstract: best[it][0] };
    fs.writeFileSync(dst, JSON.stringify(out));
    P_(it.padEnd(14), 'abstract', best[it][0], 'exact start', isFinite(t0) ? t0.toFixed(1) : 'inf', '-> best', isFinite(t) ? t.toFixed(1) : 'inf');
  }
}
