// World tab: generate a seed's map (terrain and every resource node), place the simulator's build on it, and run
// the build with its extractors mining the nodes under them.

import { View } from './view.js';
import { footprint } from './gamesim.js';
import { NODE_ITEM, MODES, parseSeed, seedText, generator } from './worldgen.js';

const $ = id => document.getElementById(id);

// terrain classes from the generator (1 = void/water) and deposit colours close to the game's icons
const TERRAIN = { 0: [12, 14, 18], 1: [56, 82, 128], 2: [84, 132, 96], 3: [168, 150, 122], 4: [40, 92, 74], 5: [48, 108, 66], 6: [56, 122, 76] };
export const NODE_COLOR = { 1: '#2fa36f', 2: '#4891d7', 4: '#ee651b', 8: '#a4b1b8', 16: '#3a2f3c', 32: '#c0473f', 64: '#8ad164' };
const ORDER = [1, 8, 2, 4, 16, 32, 64];

// --------------------------------------------------------------------------- generator, in a worker when possible
let worker = null, nextId = 1;
const waiting = new Map();
function ask(q) {
  if (worker === null) {
    try {
      worker = new Worker(new URL('./worldworker.js', import.meta.url), { type: 'module' });
      worker.onmessage = ({ data }) => { const w = waiting.get(data.id); if (w) { waiting.delete(data.id); data.error ? w[1](new Error(data.error)) : w[0](data); } };
      worker.onerror = () => { worker = false; for (const [, w] of waiting) w[1](new Error('worker failed')); waiting.clear(); };
    } catch { worker = false; }
  }
  if (worker) return new Promise((res, rej) => { const id = nextId++; waiting.set(id, [res, rej]); worker.postMessage({ ...q, id }); });
  return generator(q.mode).then(g => q.op === 'nodes'
    ? { nodes: g(q.seed, q.ws, q.ra), half: g.half(q.seed, q.ws, q.ra) }
    : { terrain: g.terrain(q.seed, q.ws, q.ra, q.x0, q.y0, q.w, q.h, q.step), step: q.step, x0: q.x0, y0: q.y0, w: q.w, h: q.h });
}

// --------------------------------------------------------------------------- map view
class MapView extends View {
  constructor(canvas, opts) {
    super(canvas, opts);
    this.minScale = 0.05;
    this.map = null;           // {half, nodes: Map "x,y" -> flag, img: canvas, step, x0, y0}
    this.build = null;         // {tiles: [[x, y, kind]], ox, oy} kind: 'ex' extractor, 'b' other
    this.hover = null;
  }
  fitMap() {
    if (!this.map) return;
    const H = this.map.half;
    this.bbox = [-H, -H, H, H];
    this.fit();
  }
  draw() {
    const c = this.ctx, s = this.scale;
    if (!c) return;
    c.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    c.fillStyle = '#0b0d11'; c.fillRect(0, 0, this.cw, this.ch);
    const m = this.map;
    if (!m) return;
    if (m.img) {
      c.imageSmoothingEnabled = false;
      const [sx, sy] = this.toScreen(m.x0 - 0.5, m.y0 - 0.5);
      c.drawImage(m.img, sx, sy, m.img.width * m.step * s, m.img.height * m.step * s);
    }
    // nodes: visible ones only
    const [tx0, ty0] = this.toTile(0, 0), [tx1, ty1] = this.toTile(this.cw, this.ch);
    const sz = Math.max(s, 1.6);
    for (const [k, f] of m.nodes) {
      const i = k.indexOf(','), x = +k.slice(0, i), y = +k.slice(i + 1);
      if (x < tx0 - 1 || x > tx1 + 1 || y < ty0 - 1 || y > ty1 + 1) continue;
      const [px, py] = this.toScreen(x, y);
      c.fillStyle = NODE_COLOR[f];
      c.fillRect(px - sz / 2, py - sz / 2, sz, sz);
      if (s >= 10) { c.strokeStyle = 'rgba(0,0,0,.35)'; c.lineWidth = 1; c.strokeRect(px - sz / 2 + .5, py - sz / 2 + .5, sz - 1, sz - 1); }
    }
    // the build
    const b = this.build;
    if (b) {
      for (const [x, y, kind, ok] of b.tiles) {
        const [px, py] = this.toScreen(x + b.ox, y + b.oy);
        c.fillStyle = kind === 'ex' ? (ok ? 'rgba(255,255,255,.55)' : 'rgba(255,80,80,.8)') : 'rgba(230,233,240,.22)';
        c.fillRect(px - s / 2, py - s / 2, s, s);
        if (kind === 'ex' && s >= 6) { c.strokeStyle = ok ? '#fff' : '#ff5050'; c.lineWidth = 1.5; c.strokeRect(px - s / 2 + 1, py - s / 2 + 1, s - 2, s - 2); }
      }
      const [bx0, by0] = this.toScreen(b.x0 + b.ox - 0.5, b.y0 + b.oy - 0.5), [bx1, by1] = this.toScreen(b.x1 + b.ox + 0.5, b.y1 + b.oy + 0.5);
      c.strokeStyle = '#4fa3ff'; c.lineWidth = 2; c.setLineDash([6, 4]);
      c.strokeRect(bx0, by0, bx1 - bx0, by1 - by0); c.setLineDash([]);
    }
    // origin cross
    const [ox, oy] = this.toScreen(0, 0);
    c.strokeStyle = 'rgba(255,255,255,.5)'; c.lineWidth = 1;
    c.beginPath(); c.moveTo(ox - 6, oy); c.lineTo(ox + 6, oy); c.moveTo(ox, oy - 6); c.lineTo(ox, oy + 6); c.stroke();
  }
}

// --------------------------------------------------------------------------- tab
export function initWorld({ getBuild, useInSim }) {
  const sheet = () => { const p = $('wpanel'); return getComputedStyle(p).position === 'absolute' ? p.getBoundingClientRect().height : 0; };
  const view = new MapView($('wview'), { insetBottom: sheet, onPick: (t, click) => pick(t, click) });
  const st = { world: null, placing: false, build: null };
  $('wsheetHandle').addEventListener('click', () => $('wpanel').classList.toggle('collapsed'));
  for (const [k, v] of Object.entries(MODES)) { const o = document.createElement('option'); o.value = k; o.textContent = v; $('wMode').appendChild(o); }

  function pick([x, y], click) {
    if (st.world) {
      const f = st.world.nodes.get(x + ',' + y);
      $('wtip').textContent = `tile ${x}, ${y}${f ? ' · ' + NODE_ITEM[f] + ' node' : ''}`;
    }
    if (click && st.placing && st.build) {
      const b = st.build;
      b.ox = x - Math.round((b.x0 + b.x1) / 2); b.oy = y - Math.round((b.y0 + b.y1) / 2);
      checkBuild();
    }
  }

  async function go() {
    let seed;
    try { seed = parseSeed($('wSeed').value); } catch (e) { $('wInfo').textContent = e.message; return; }
    const q = { mode: $('wMode').value, seed, ws: +$('wSize').value, ra: +$('wRes').value };
    $('wInfo').textContent = 'Generating…';
    const t0 = performance.now();
    try {
      const r = await ask({ op: 'nodes', ...q });
      const nodes = new Map(), counts = {};
      for (let i = 0; i < r.nodes.length; i += 3) { nodes.set(r.nodes[i] + ',' + r.nodes[i + 1], r.nodes[i + 2]); counts[r.nodes[i + 2]] = (counts[r.nodes[i + 2]] || 0) + 1; }
      st.world = { ...q, nodes, half: r.half, counts };
      view.map = { half: r.half, nodes, img: null, step: 1, x0: 0, y0: 0 };
      view.fitMap();
      $('wStatus').textContent = `${seedText(seed)} · ${MODES[q.mode]} · ${q.ws}% size · ${q.ra}% resources`;
      $('wCounts').innerHTML = ORDER.map(f => `<div class="item"><span class="dot" style="background:${NODE_COLOR[f]}"></span><span>${NODE_ITEM[f]}</span><span class="num">${counts[f] || 0}</span></div>`).join('')
        + `<div class="item"><span></span><b>Total</b><span class="num"><b>${nodes.size}</b></span></div>`;
      $('wInfo').textContent = `${nodes.size} nodes in ${Math.round(performance.now() - t0)} ms. Drawing the terrain…`;
      history.replaceState(null, '', `#seed=${seedText(seed)}&mode=${q.mode}&ws=${q.ws}&ra=${q.ra}`);
      checkBuild();
      // terrain: coarse first, then every tile
      const H = r.half;
      for (const step of [4, 1]) {
        const n = Math.ceil((2 * H + 1) / step);
        const tr = await ask({ op: 'terrain', ...q, x0: -H, y0: -H, w: n, h: n, step });
        if (st.world.seed !== seed || st.world.mode !== q.mode || st.world.ws !== q.ws || st.world.ra !== q.ra) return;
        const cv = document.createElement('canvas'); cv.width = n; cv.height = n;
        const cx = cv.getContext('2d'), img = cx.createImageData(n, n);
        for (let i = 0; i < n * n; i++) { const c = TERRAIN[tr.terrain[i]] || TERRAIN[0]; img.data.set([c[0], c[1], c[2], 255], 4 * i); }
        cx.putImageData(img, 0, 0);
        Object.assign(view.map, { img: cv, step, x0: -H, y0: -H });
        view.draw();
      }
      $('wInfo').textContent = `${nodes.size} nodes, generated in ${Math.round(performance.now() - t0)} ms.`;
    } catch (e) { $('wInfo').textContent = 'Could not generate: ' + e.message; }
  }

  function checkBuild() {
    const b = st.build;
    if (!b) { view.build = null; view.draw(); return; }
    let ok = 0, bad = 0;
    for (const t of b.tiles) if (t[2] === 'ex') {
      const f = st.world && st.world.nodes.get((t[0] + b.ox) + ',' + (t[1] + b.oy));
      t[3] = !!f; f ? ok++ : bad++;
    }
    view.build = b;
    $('wBuildInfo').textContent = `${b.name}: placed at ${b.ox}, ${b.oy}. ${b.ex ? `${ok} of ${b.ex} extractors on a node${bad ? ` (${bad} off a node, shown red: they mine nothing)` : ''}.` : 'No extractors in this build.'}`;
    $('wUse').disabled = !st.world;
    view.draw();
  }

  $('wGo').addEventListener('click', go);
  $('wSeed').addEventListener('keydown', e => { if (e.key === 'Enter') go(); });
  $('wPlace').addEventListener('click', () => {
    const bp = getBuild();
    if (!bp) { $('wBuildInfo').textContent = 'Load a blueprint in the simulator first.'; return; }
    const tiles = [];
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity, ex = 0;
    for (const b of bp.buildings) {
      if (b.added) continue;
      const kind = b.id === 3004 ? 'ex' : 'b';
      if (kind === 'ex') ex++;
      for (const [x, y] of footprint(b)) { tiles.push([x, y, kind, true]); x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); }
    }
    const prev = st.build;
    st.build = { tiles, x0, y0, x1, y1, ex, name: bp.name, ox: prev ? prev.ox : 0, oy: prev ? prev.oy : 0 };
    st.placing = true;
    $('wPlace').textContent = 'Reload build';
    checkBuild();
    $('wInfo').textContent = 'Tap the map to move the build there.';
  });
  $('wUse').addEventListener('click', () => {
    if (!st.world || !st.build) return;
    const b = st.build, items = {};
    for (const t of b.tiles) if (t[2] === 'ex') {
      const f = st.world.nodes.get((t[0] + b.ox) + ',' + (t[1] + b.oy));
      items[t[0] + ',' + t[1]] = f ? NODE_ITEM[f] : null;
    }
    useInSim({ items, gen: st.world.mode === 'gen2' ? 2 : 1, label: `${seedText(st.world.seed)} at ${b.ox}, ${b.oy}` });
  });

  // a link like #seed=4Uth4P&mode=gen2&ws=50&ra=200 opens that world
  const h = new URLSearchParams(location.hash.slice(1));
  if (h.get('seed')) {
    $('wSeed').value = h.get('seed');
    if (h.get('mode') && MODES[h.get('mode')]) $('wMode').value = h.get('mode');
    if (h.get('ws')) $('wSize').value = h.get('ws');
    if (h.get('ra')) $('wRes').value = h.get('ra');
    document.querySelector('.tab[data-tab="world"]').click();
    go();
  }
  window.addEventListener('resize', () => view.resize());
}
