// Page controller: loading blueprints, running the sim at any game speed, measuring rates, settling.

import { Factory, GAMEDATA, QuadTree, World } from './gamesim.js';
import { rowsToBuildings, parseBlueprintText, fetchBlueprint, addFeeders, addDrains, guessFeedItem, guessFeedItems, RAW_ITEMS } from './blueprint.js';
import { View, itemColor, fmtRate } from './view.js';
import { initDesigner } from './designer.js';
import { initWorld } from './world.js';

const $ = id => document.getElementById(id);
const TPS = 60;
const WINDOW = 60 * TPS;          // rates over the last game minute

const state = {
  rows: null, meta: null,
  buildings: null, feeds: [], extractors: [],   // user settings per input
  w: null, running: false, speed: 1, mode: 'exact',
  history: [],                  // per game second: snapshot of counters
  perMinute: [],                // output per game minute (for the chart)
  settled: null,                // {tick, rates...} after a settle run
  settling: false,
};

const sheetCover = () => {             // phone layout: the panel is a sheet over the bottom of the map
  const p = $('panel');
  if (getComputedStyle(p).position !== 'absolute') return 0;
  return p.getBoundingClientRect().height;
};
const view = new View($('view'), { onPick: (t, click) => pickTile(t, click), insetBottom: sheetCover });

// ------------------------------------------------------------------ tabs and panel
document.querySelectorAll('.tab').forEach(b => b.addEventListener('click', () => {
  document.querySelectorAll('.tab').forEach(x => x.classList.toggle('active', x === b));
  document.querySelectorAll('.tabpane').forEach(p => p.classList.toggle('active', p.id === 'tab-' + b.dataset.tab));
  window.dispatchEvent(new Event('resize'));
}));
$('sheetHandle').addEventListener('click', () => $('panel').classList.toggle('collapsed'));

// ------------------------------------------------------------------ loading
async function loadFromInput(text) {
  text = text.trim();
  if (!text) return;
  try {
    setStatus('Loading…');
    let bp;
    if (/^[{\[]/.test(text) || text.includes('\n')) bp = parseBlueprintText(text);
    else bp = await fetchBlueprint(text);
    loadBlueprint(bp.rows, bp.meta, /^[a-z0-9]{6}$/i.test(text) ? text : null);
  } catch (e) {
    setStatus('Could not load: ' + e.message);
  }
}

function loadBlueprint(rows, meta = {}, id = null) {
  state.rows = rows; state.meta = meta;
  if (id) history.replaceState(null, '', '#bp=' + id);
  const n = rows.length;
  $('bpMeta').textContent = `${meta.name || id || 'blueprint'}: ${n} pieces`;
  // inputs: open belt starts; their item guessed from the machines downstream
  const bs = rowsToBuildings(rows);
  const added = addFeeders(bs);
  addDrains(bs);
  const probe = new Factory(bs, { extractors: {} });
  const feedSegs = added.map(a => probe.segs.find(s => s.nodes[0].x === a.at[0] && s.nodes[0].y === a.at[1]));
  const found = feedSegs.filter(g => g);
  const kinds = guessFeedItems(probe, found, found.map(() => null));
  // an open start that reaches no machine in a build that has machines is a product belt's start (arms or
  // machines put onto it further on): it gets nothing, not a full belt of a made-up item
  const hasMachines = probe.machines.length > 0;
  state.feeds = added.map((a, i) => {
    const j = found.indexOf(feedSegs[i]);
    const kind = j >= 0 && kinds[j];
    return { at: a.at, kind: kind || 'In' + (i + 1), perMin: kind || !hasMachines ? 480 : 0 };
  });
  state.extractors = bs.filter(b => b.id === 3004).map(b => {
    const g = probe.outSeg.get(b);
    return { x: b.x, y: b.y, item: g ? guessFeedItem(probe, g, 'Wood Log') : 'Wood Log' };
  });
  state.buildings = bs;
  renderInputs();
  rebuild();
  view.fit();
  ['play', 'step', 'reset'].forEach(k => $(k).disabled = false);
}

function rebuild() {
  const ext = {};
  for (const e of state.extractors) ext[e.x + ',' + e.y] = e.item;
  const bs = state.buildings.map(b => ({ ...b }));
  state.w = new Factory(bs, {
    feeds: state.feeds.map(f => ({ at: f.at, kind: f.kind, perMin: f.perMin })),
    extractors: ext, gen: +$('gen').value, boosted: $('boosted').checked,
    qt: new QuadTree(),             // loading a build in game starts a fresh belt quadtree (arm lookups)
  });
  state.history = [snapshot()];
  state.perMinute = [];
  state.settled = null;
  $('settleState').textContent = '';
  view.setWorld(state.w, true);
  measure();
  updateClock();
  view.draw();
}

$('bpLoad').addEventListener('click', () => loadFromInput($('bpInput').value));
$('bpInput').addEventListener('keydown', e => { if (e.key === 'Enter') loadFromInput($('bpInput').value); });
$('bpFile').addEventListener('change', async e => {
  const f = e.target.files[0];
  if (!f) return;
  const text = await f.text();
  try { const bp = parseBlueprintText(text); loadBlueprint(bp.rows, { name: f.name, ...bp.meta }); }
  catch (err) { setStatus('Could not read ' + f.name + ': ' + err.message); }
});

fetch('examples/index.json').then(r => r.json()).then(list => {
  for (const ex of list) {
    const o = document.createElement('option');
    o.value = ex.file; o.textContent = ex.name;
    $('examples').appendChild(o);
  }
}).catch(() => {});
$('examples').addEventListener('change', async e => {
  const f = e.target.value;
  if (!f) return;
  const text = await (await fetch('examples/' + f)).text();
  const bp = parseBlueprintText(text);
  loadBlueprint(bp.rows, { ...bp.meta, name: e.target.selectedOptions[0].textContent });
  e.target.value = '';
});

// ------------------------------------------------------------------ inputs panel
const ITEM_CHOICES = () => [...new Set([...RAW_ITEMS, ...GAMEDATA.items, ...state.feeds.map(f => f.kind)])];

function renderInputs() {
  const box = $('inputs');
  box.innerHTML = '';
  if (!state.feeds.length && !state.extractors.length) {
    box.innerHTML = '<div class="muted small">No open belt starts or extractors. Tap a belt start on the map to add an input.</div>';
    return;
  }
  const choices = ITEM_CHOICES();
  state.feeds.forEach((f, i) => {
    const row = document.createElement('div');
    row.className = 'item input';
    row.innerHTML = `<span class="dot" style="background:${itemColor(f.kind)}"></span><span class="small muted">belt ${i + 1}</span>`;
    const sel = document.createElement('select');
    for (const c of choices) { const o = document.createElement('option'); o.value = o.textContent = c; sel.appendChild(o); }
    sel.value = f.kind;
    sel.addEventListener('change', () => { f.kind = sel.value; renderInputs(); rebuild(); });
    const rate = document.createElement('input');
    rate.type = 'number'; rate.min = 0; rate.max = 480; rate.value = f.perMin; rate.title = 'items per minute (480 = full belt)';
    rate.addEventListener('change', () => { const v = rate.value === '' ? 480 : +rate.value; f.perMin = Math.max(0, Math.min(480, isNaN(v) ? 480 : v)); rebuild(); });
    row.append(sel, rate);
    box.appendChild(row);
  });
  const hint = document.createElement('div');
  hint.className = 'small muted';
  hint.textContent = 'Tap a belt start on the map to add or remove an input there.';
  box.appendChild(hint);
  if (state.extractors.length) {
    const counts = {};
    for (const e of state.extractors) counts[e.item] = (counts[e.item] || 0) + 1;
    const d = document.createElement('div');
    d.className = 'small muted';
    d.textContent = 'Extractors: ' + Object.entries(counts).map(([k, n]) => `${n} on ${k === "null" ? "no node (idle)" : k}`).join(', ') +
      ' (tap one on the map to change its resource)';
    box.appendChild(d);
  }
}
$('gen').addEventListener('change', rebuild);
$('boosted').addEventListener('change', rebuild);

// ------------------------------------------------------------------ running
document.querySelectorAll('#speeds button').forEach(b => b.addEventListener('click', () => {
  state.speed = +b.dataset.speed;
  document.querySelectorAll('#speeds button').forEach(x => x.classList.toggle('on', x === b));
}));
document.querySelectorAll('#modeSeg button').forEach(b => b.addEventListener('click', () => {
  state.mode = b.dataset.mode;
  document.querySelectorAll('#modeSeg button').forEach(x => x.classList.toggle('on', x === b));
  $('settleBox').hidden = state.mode !== 'settled';
  measure(); view.draw();
}));
$('play').addEventListener('click', () => setRunning(!state.running));
$('step').addEventListener('click', () => { if (state.w) { tickOnce(); measure(); updateClock(); view.draw(); } });
$('reset').addEventListener('click', () => { setRunning(false); rebuild(); });
$('zin').addEventListener('click', () => view.zoomAt(1.4, view.cw / 2, view.ch / 2));
$('zout').addEventListener('click', () => view.zoomAt(1 / 1.4, view.cw / 2, view.ch / 2));
$('zfit').addEventListener('click', () => view.fit());
for (const k of ['showItems', 'showFlow', 'showNumbers', 'showPieces']) $(k).addEventListener('change', e => { view[k] = e.target.checked; view.draw(); });

function setRunning(on) {
  state.running = on && !!state.w;
  $('play').innerHTML = state.running ? '&#10074;&#10074; Pause' : '&#9654; Play';
  if (state.running) { last = performance.now(); due = 0; requestAnimationFrame(loop); }
}

function tickOnce() {
  const w = state.w;
  w.tick();
  if (w.tickNo % TPS === 0) {
    state.history.push(snapshot());
    if (state.history.length > 3 * 60 + 2) state.history.shift();
    if (w.tickNo % WINDOW === 0) {
      const h = state.history, a = h[h.length - 1], b = h.find(s => s.tick === w.tickNo - WINDOW);
      if (b) state.perMinute.push(sum(a.out) - sum(b.out) + sum(Object.values(a.consumed)) - sum(Object.values(b.consumed)));
    }
  }
}

let last = 0, due = 0;
function loop(now) {
  if (!state.running) return;
  const dt = Math.min(0.25, (now - last) / 1000);
  last = now;
  const t0 = performance.now();
  if (state.speed === 0) {
    while (performance.now() - t0 < 28) for (let i = 0; i < 30; i++) tickOnce();
  } else {
    due += dt * TPS * state.speed;
    let n = Math.floor(due);
    due -= n;
    while (n-- > 0) { tickOnce(); if (performance.now() - t0 > 40) { due = 0; break; } }
  }
  measure();
  updateClock();
  view.draw();
  requestAnimationFrame(loop);
}

// ------------------------------------------------------------------ measuring
const sum = a => a.reduce((s, x) => s + x, 0);

function snapshot() {
  const w = state.w;
  return {
    tick: w.tickNo,
    seg: w.segs.map(g => g.moved),
    out: drains().map(g => g.moved),
    outKinds: drains().map(g => ({ ...(w.sinks.get(g) || {}) })),
    made: w.machines.map(m => m.made),
    arms: w.arms.map(a => a.moved),
    consumed: consumedTotals(),
  };
}

function drains() { return state.w.segs.filter(g => g.target && g.target.sink); }
function consumedTotals() {
  const t = {};
  if (state.w.consumed) for (const m of state.w.consumed.values()) for (const [k, v] of Object.entries(m)) t[k] = (t[k] || 0) + v;
  return t;
}

// rates between two snapshots, per minute
function ratesBetween(a, b) {
  const mins = (b.tick - a.tick) / 3600;
  if (mins <= 0) return null;
  const w = state.w;
  const segRate = w.segs.map((g, i) => (b.seg[i] - a.seg[i]) / mins);
  const outs = drains().map((g, i) => {
    const kinds = {};
    for (const [k, v] of Object.entries(b.outKinds[i])) kinds[k] = (v - (a.outKinds[i][k] || 0)) / mins;
    return { total: (b.out[i] - a.out[i]) / mins, kinds };
  });
  const made = {};
  w.machines.forEach((m, i) => { made[m.outItem] = (made[m.outItem] || 0) + (b.made[i] - a.made[i]) / mins; });
  const consumed = {};
  for (const [k, v] of Object.entries(b.consumed)) consumed[k] = (v - (a.consumed[k] || 0)) / mins;
  const arms = w.arms.map((x, i) => (b.arms[i] - a.arms[i]) / mins);
  return { mins, segRate, outs, made, consumed, arms };
}

function measure() {
  if (!state.w) return;
  let r;
  if (state.mode === 'settled' && state.settled) r = state.settled.rates;
  else {
    // whole game seconds only, over exactly one game minute: a window that ends mid-second holds a fraction
    // of an item period and reads 479 or 481 on a full 480 belt
    const h = state.history;
    const cur = h[h.length - 1];
    const base = h.find(s => s.tick === cur.tick - WINDOW) || h[0];
    r = ratesBetween(base, cur);
    $('outWindow').textContent = r ? `per min, last ${r.mins >= 0.999 ? 'minute' : Math.round(r.mins * 60) + ' s'}` : '';
  }
  if (!r) { view.tileRates = null; view.segRates = null; renderOutputs(null); return; }
  applyRates(r);
  renderOutputs(r);
  drawChart();
}

// Per-tile rates. A segment's counter is what leaves its end; tiles upstream of an arm that drops onto the segment
// carry that much less (and tiles upstream of an arm that picks from it carry more). A splitter tile shows what
// enters it: its output stubs start on the splitter tile, and an arm dropping onto the splitter tile puts items on
// a stub (240 + 80 on one output of a full splitter), which never passed through the splitter itself. When the arm's
// drop lands on the end of the input belt instead (inside the splitter tile), the splitter tile still shows what the
// belt brings (480) and the arm's 80 shows on the arm and on the outputs.
function applyRates(r) {
  const w = state.w, tile = new Map(), seg = new Map();
  const add = (n, v) => { const k = n.x + ',' + n.y; tile.set(k, (tile.get(k) || 0) + v); };
  const events = new Map();                    // seg -> [[pos, items/min leaving the segment because of it]]
  const ev = (g, q, v) => { if (!events.has(g)) events.set(g, []); events.get(g).push([q, v]); };
  w.arms.forEach((a, i) => {
    if (a.dstCache && a.dstCache[0]) ev(a.dstCache[0][0], a.dstCache[0][1], r.arms[i]);
    if (a.lastPick) ev(a.lastPick[0], a.lastPick[1], -r.arms[i]);
  });
  w.segs.forEach((g, i) => {
    const v = r.segRate[i];
    const kind = g.kinds[0] ?? lastKind.get(g);
    if (g.kinds.length) lastKind.set(g, g.kinds[0]);
    seg.set(g, { total: v, kind: kind ?? 'In1' });
    if (!g.flag) return;                       // underground tunnels: counted at their exits
    const nodes = g.nodes, evs = events.get(g), pos = evs && nodePos(g);
    // what leaves tile k (pos[k] + 400 = its far edge, 800 units per tile); onto a splitter: what reaches it
    const at = (k, edge = 400) => {
      if (!evs) return v;
      let x = v;
      for (const [q, d] of evs) if (q > pos[k] + edge) x -= d;    // added further down the segment
      return x;
    };
    for (let k = 0; k < nodes.length - 1; k++) {
      if (nodes[k].kind !== 0 || (k === 0 && World.isSplit(nodes[k]))) continue;
      add(nodes[k], at(k));
    }
    const lastN = nodes[nodes.length - 1];
    if (World.isSplit(lastN)) add(lastN, at(nodes.length - 1, -400));
    else if (lastN.kind === 0 && (!g.target || g.target.sink)) add(lastN, v);
  });
  w.arms.forEach((a, i) => tile.set(a.b.x + ',' + a.b.y, r.arms[i]));
  view.tileRates = tile;
  view.segRates = seg;
}
const nodePosCache = new WeakMap();
function nodePos(g) {
  let p = nodePosCache.get(g);
  if (!p) {
    p = g.nodes.map((n, k) => g.project([n.x, n.y]) ?? (k === 0 ? 0 : g.L));
    nodePosCache.set(g, p);
  }
  return p;
}
const lastKind = new WeakMap();

function renderOutputs(r) {
  const box = $('outputs'), made = $('made');
  box.innerHTML = ''; made.innerHTML = '';
  if (!r) return;
  r.outs.forEach((o, i) => {
    const kinds = Object.entries(o.kinds).filter(([, v]) => v > 0.004);
    const main = kinds.sort((a, b) => b[1] - a[1])[0];
    const row = document.createElement('div');
    row.className = 'item';
    row.innerHTML = `<span class="dot" style="background:${main ? itemColor(main[0]) : '#444'}"></span>` +
      `<span>out ${i + 1}${kinds.length > 1 ? ' <span class="muted small">' + kinds.map(([k, v]) => k + ' ' + fmtRate(v)).join(', ') + '</span>' : main ? ' <span class="muted small">' + main[0] + '</span>' : ''}</span>` +
      `<span class="num">${fmtRate(o.total)}</span>`;
    box.appendChild(row);
  });
  if (!r.outs.length) box.innerHTML = '<div class="muted small">No open belt ends.</div>';
  const entries = Object.entries(r.made).concat(Object.entries(r.consumed).map(([k, v]) => [k + ' (into labs)', v]));
  for (const [k, v] of entries.sort((a, b) => b[1] - a[1])) {
    const row = document.createElement('div');
    row.className = 'item';
    row.innerHTML = `<span class="dot" style="background:${itemColor(k.replace(' (into labs)', ''))}"></span><span>${k}</span><span class="num">${fmtRate(v)}</span>`;
    made.appendChild(row);
  }
  if (!entries.length) made.innerHTML = '<div class="muted small">No machines.</div>';
}

function drawChart() {
  const c = $('chart'), ctx = c.getContext('2d');
  const dpr = window.devicePixelRatio || 1, W = c.clientWidth, H = c.clientHeight;
  if (c.width !== W * dpr) { c.width = W * dpr; c.height = H * dpr; }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  const d = state.perMinute;
  if (d.length < 1) return;
  const mx = Math.max(...d, 1);
  ctx.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue('--accent');
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  d.forEach((v, i) => {
    const x = d.length === 1 ? W / 2 : (i / (d.length - 1)) * (W - 4) + 2, y = H - 4 - (v / mx) * (H - 12);
    i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  });
  ctx.stroke();
  $('chartNote').textContent = `Output per game minute: ${d.map(v => fmtRate(v)).slice(-6).join(', ')}`;
}

// ------------------------------------------------------------------ settling
$('runSettle').addEventListener('click', () => {
  if (!state.w || state.settling) return;
  setRunning(false);
  state.settling = true;
  state.settled = null;
  const w = state.w, start = w.tickNo, maxTicks = 180 * 3600;
  const t0 = performance.now();
  const step = () => {
    const f0 = performance.now();
    let done = null;
    while (performance.now() - f0 < 30 && done === null) {
      const n = state.perMinute.length;
      while (state.perMinute.length === n && w.tickNo - start <= maxTicks) tickOnce();
      done = settledAt(state.perMinute);
      if (w.tickNo - start > maxTicks) break;
    }
    const mins = (w.tickNo - start) / 3600;
    $('settleState').textContent = `running… ${mins.toFixed(1)} game min`;
    updateClock(); measure(); view.draw();
    if (done !== null || w.tickNo - start > maxTicks) {
      // measure the long-run average over the next 10 game minutes
      const a = snapshot();
      const target = w.tickNo + 10 * 3600;
      const finish = () => {
        const f1 = performance.now();
        while (w.tickNo < target && performance.now() - f1 < 30) for (let i = 0; i < 60; i++) tickOnce();
        updateClock(); view.draw();
        if (w.tickNo < target) { $('settleState').textContent = 'measuring 10 game minutes…'; return requestAnimationFrame(finish); }
        const rates = ratesBetween(a, snapshot());
        state.settled = { rates, at: done };
        state.settling = false;
        const secs = ((performance.now() - t0) / 1000).toFixed(1);
        $('settleState').textContent = done !== null
          ? `settled after about ${done} game min (${secs} s real time)`
          : `not steady after ${Math.round(mins)} game min; showing the last 10`;
        $('outWindow').textContent = 'per min, long-run average';
        measure(); view.draw();
      };
      return requestAnimationFrame(finish);
    }
    requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
});

// Steady once the last 4 game minutes agree within 0.5% (or 1 item). Returns the first minute from which every
// minute stays that close to their average (the settle time), or null while still changing.
function settledAt(pm) {
  if (pm.length < 5) return null;
  const tail = pm.slice(-4), m = sum(tail) / 4, tol = Math.max(1, m * 0.005);
  if (!tail.every(v => Math.abs(v - m) <= tol)) return null;
  let k = pm.length - 4;
  while (k > 0 && Math.abs(pm[k - 1] - m) <= tol) k--;
  return k;
}

// ------------------------------------------------------------------ picking a tile
function pickTile([x, y], click) {
  const w = state.w;
  if (!w) return;
  const k = x + ',' + y;
  const b = w.occ.get(k);
  const lines = [];
  if (b) {
    const name = (Object.values(GAMEDATA.buildings).find(d => d.id === b.id) || {}).name || 'building ' + b.id;
    lines.push(`${name} at ${b.x}, ${b.y}, tier ${b.tier}${b.added ? ' (added ' + (b.added === 'in' ? 'input' : 'output') + ')' : ''}`);
    const o = w.bobj.get(b);
    if (o && o.rec) lines.push(`recipe: ${o.rec.in.map(i => i.amount + ' ' + i.item).join(' + ')} -> ${o.outAmount} ${o.outItem} in ${o.rec.seconds} s`,
      `holding: ${Object.entries(o.have).map(([k2, v]) => v + ' ' + k2).join(', ')}; waiting output ${o.out}; made ${o.made}`);
    if (b.id === 3004) {
      const e = state.extractors.find(e2 => e2.x === b.x && e2.y === b.y);
      if (e) lines.push(`mining ${e.item}`);
      if (click && e) {
        const raws = RAW_ITEMS;
        e.item = raws[(raws.indexOf(e.item) + 1) % raws.length];
        renderInputs(); rebuild();
        lines.push(`changed to ${e.item} (tap again for the next)`);
      }
    }
    const arm = w.arms.find(a => a.b === b);
    if (arm) {
      const ai = w.arms.indexOf(arm);
      const r = state.settled && state.mode === 'settled' ? state.settled.rates : null;
      lines.push(`arm speed x${arm.mult}${arm.filt ? ', filter ' + arm.filt : ''}; moved ${arm.moved}${r ? '; ' + fmtRate(r.arms[ai]) + '/min' : ''}`);
      const dst = w.segAt(arm.dst);
      const db = w.occ.get(arm.dst[0] + ',' + arm.dst[1]);
      if (dst.length && (!db || (db.id >= 3000 && db.id <= 3003)))
        lines.push(`drops on piece #${dst[0][0].idx} (grid order here: ${dst.map(([g]) => '#' + g.idx).join(', ')}; the first one is used even when full)`);
      const src = w.segAt(arm.src);
      if (src.length) lines.push(`picks from ${src.map(([g]) => '#' + g.idx).join(', then ')} (the next is tried when one has no item in reach)`);
    }
  }
  const tr = view.tileRates && view.tileRates.get(k);
  if (tr != null) lines.push(`throughput ${fmtRate(tr)} items/min`);
  for (const g of w.segs) {
    if (!g.nodes.some(n => n.x === x && n.y === y)) continue;
    const loc = w.qt && w.qt.locate(g);
    const stub = g.nodes.length === 2 && World.isSplit(g.nodes[0]);
    lines.push(`piece #${g.idx}${stub ? ' (splitter output stub)' : ''}: ${g.nodes.length} tiles, length ${(g.L / 800).toFixed(3)}, ${g.items.length} items${g.flag ? '' : ' (tunnel)'}` +
      (loc ? `; grid cell depth ${loc.depth}${loc.onSplit ? ', ON A SPLIT LINE (listed first)' : ''}` : ''));
  }
  const actions = click ? inputActions(x, y) : [];
  if (!b && actions.length) lines.push(`empty tile at ${x}, ${y}`);
  if (!lines.length) { $('pickCard').hidden = true; view.pick = null; view.draw(); return; }
  if (click) {
    const box = $('pickActions');
    box.innerHTML = '';
    for (const [label, run] of actions) {
      const btn = document.createElement('button');
      btn.textContent = label;
      btn.onclick = () => { run(); renderInputs(); rebuild(); pickTile([x, y], true); };
      box.appendChild(btn);
    }
    view.pick = [x, y];
    $('pickCard').hidden = false;
    $('pickInfo').textContent = lines.join('\n');
    $('panel').classList.remove('collapsed');
    $('pickCard').scrollIntoView({ block: 'nearest' });
    view.draw();
  }
}

// Input belts by hand. On an empty tile: place an input belt facing each neighbour that would take items from
// it (checked with the game's own link rules). On an input: remove it (and its belt, if the page added it).
// On a belt piece nothing feeds: feed it directly.
function inputActions(x, y) {
  const k = x + ',' + y;
  const acts = [];
  const fi = state.feeds.findIndex(f => f.at[0] === x && f.at[1] === y);
  if (fi >= 0) {
    acts.push(['Remove the input here', () => {
      state.feeds.splice(fi, 1);
      const bi = state.buildings.findIndex(b => b.x === x && b.y === y && b.added === 'in');
      if (bi >= 0) state.buildings.splice(bi, 1);
    }]);
    return acts;
  }
  const w = state.w;
  if (!w.occ.has(k)) {
    const names = ['north', 'west', 'south', 'east'];
    for (let d = 0; d < 4; d++) {
      const nb = { id: 3000, x, y, o: d, tier: 1, recipe: 0, filter: 0, added: 'in' };
      const t = new World([...state.buildings, nb]);
      const n = t.belt.get(k);
      if (!n || !n.outputs.length) continue;
      acts.push([`Place an input belt facing ${names[d]}`, () => {
        state.buildings.push(nb);
        const probe = new Factory(state.buildings.map(b => ({ ...b })), { extractors: {} });
        const g = probe.segs.find(s => s.nodes[0].x === x && s.nodes[0].y === y);
        state.feeds.push({ at: [x, y], kind: g ? guessFeedItem(probe, g, 'In' + (state.feeds.length + 1)) : 'In' + (state.feeds.length + 1), perMin: 480 });
      }]);
    }
    return acts;
  }
  const start = w.segs.find(g => g.nodes[0].x === x && g.nodes[0].y === y && g.flag && !g.nodes[0].inputs.length);
  if (start) acts.push(['Feed this belt directly', () => {
    state.feeds.push({ at: [x, y], kind: guessFeedItem(w, start, 'In' + (state.feeds.length + 1)), perMin: 480 });
  }]);
  return acts;
}

// ------------------------------------------------------------------ misc
function updateClock() {
  const t = state.w ? state.w.tickNo : 0, s = t / TPS;
  const m = Math.floor(s / 60);
  $('clock').textContent = `${m}:${(s - m * 60).toFixed(1).padStart(4, '0')}`;
  const sp = state.speed === 0 ? 'max' : state.speed + '×';
  if (state.w) setStatus(`tick ${t} · ${state.running ? 'running at ' + sp : 'paused'} · ${state.w.segs.length} belt segments`);
}
function setStatus(t) { $('status').textContent = t; }

// start: #bp=<id> in the link, else the first example
const m = location.hash.match(/bp=([a-z0-9]+)/i);
if (m) { $('bpInput').value = m[1]; loadFromInput(m[1]); }
else fetch('examples/split_1_5_3x5.txt').then(r => r.text()).then(t => loadBlueprint(parseBlueprintText(t).rows, { name: '1:5 splitter 3x5' }));

initDesigner({ openInSim: (rows, meta) => {
  document.querySelector('.tab[data-tab="sim"]').click();
  loadBlueprint(rows, meta);
} });

initWorld({
  getBuild: () => state.buildings && { buildings: state.buildings, name: state.meta.name || 'build' },
  useInSim: ({ items, gen, label }) => {
    for (const e of state.extractors) e.item = items[e.x + ',' + e.y] || null;
    $('gen').value = String(gen);
    state.worldLabel = label;
    renderInputs();
    rebuild();
    document.querySelector('.tab[data-tab="sim"]').click();
    setStatus('Extractors mine the nodes of ' + label);
  },
});
window.bmsim = { state, get view() { return view; } };   // for console debugging and tests
