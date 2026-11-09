// Designer tab: pick a splitter or balancer and a symmetry rule, and see the best known layout for it
// (from catalogue.json: community wiki designs and our solver results, all checked with exact flows), or watch a
// running solver (python -m bmsolver ... --live).

import { World } from './gamesim.js';
import { rowsToBuildings, bpLink, bpLinkBits, makeBlueprint } from './blueprint.js';
import { View } from './view.js';

const $ = id => document.getElementById(id);
const CODE_ID = { B: 3000, S: 3001, I: 3002, O: 3003 };
const MODE_NAME = { sym: 'symmetric', quasi: 'quasi-symmetric', asym: 'asymmetric' };
const MODE_HELP = {
  sym: 'Mirror symmetric top to bottom.',
  quasi: 'Mirror symmetric except one tile pair in the middle: turned another way, or a splitter whose mirror tile is a plain belt (like the wiki splitters, where the input splitter in the centre has a belt opposite it). Symmetric layouts count too.',
  asym: 'Any layout.',
};
// which layouts each rule accepts
const ALLOWED = { sym: ['sym'], quasi: ['sym', 'quasi'], asym: ['sym', 'quasi', 'asym'] };
const LOOSE = { sym: 0, quasi: 1, asym: 2 };

export function gridRows(grid) {
  const rows = [];
  grid.forEach((line, y) => {
    for (let i = 0; i < line.length; i += 2) {
      const t = line.slice(i, i + 2);
      if (t !== '..') rows.push([CODE_ID[t[0]], i / 2, y, +t[1], 0, -1, 0]);
    }
  });
  return rows;
}

export function initDesigner({ openInSim }) {
  const sheet = () => {
    const p = $('dpanel');
    return getComputedStyle(p).position === 'absolute' ? p.getBoundingClientRect().height : 0;
  };
  const view = new View($('dview'), { insetBottom: sheet });
  const st = { kind: 'split', mode: 'quasi', goal: 'square', cat: [], current: null };
  let timer = null, lastVersion = -1;

  $('dsheetHandle').addEventListener('click', () => $('dpanel').classList.toggle('collapsed'));

  function show(rows, meta) {
    st.current = { rows, meta };
    view.setWorld(new World(rowsToBuildings(rows)));
    st.bpId = null;                                // set again by pick() for catalogue designs that have one
    st.bpName = meta.name || 'layout';
    $('dSim').disabled = $('dCopy').disabled = $('dLink').disabled = $('dMirror').disabled = false;
  }

  function seg(id, key, after) {
    $(id).querySelectorAll('button').forEach(b => b.addEventListener('click', () => {
      $(id).querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b));
      st[key] = b.dataset.v;
      after && after();
      update();
    }));
  }
  seg('dKind', 'kind', () => {
    if (st.kind === 'split') { $('dN').value = 1; if (+$('dM').value < 2) $('dM').value = 8; }
    if (st.kind === 'balance') { if (+$('dN').value < 2) $('dN').value = 4; if (+$('dM').value < 2) $('dM').value = 4; }
  });
  seg('dMode', 'mode');
  seg('dGoal', 'goal');
  for (const id of ['dN', 'dM']) $(id).addEventListener('input', () => {
    const n = +$('dN').value, m = +$('dM').value;
    const kind = n === 1 ? 'split' : 'balance';
    if (kind !== st.kind) {
      st.kind = kind;
      $('dKind').querySelectorAll('button').forEach(x => x.classList.toggle('on', x.dataset.v === kind));
    }
    update();
  });

  // same box: smaller footprint (tiles inside the outline), fewer pieces, fewer splitters facing out of the edge,
  // less gold to build; then the stricter rule and community designs first
  const tail = d => [d.footprint ?? d.W * d.H, d.pieces, d.edge_splitters ?? 0, d.gold ?? 0, LOOSE[d.mode],
    d.source === 'ours' ? 1 : 0];
  const score = d => st.goal === 'area'
    ? [d.W * d.H, Math.max(d.W, d.H), ...tail(d)]
    : [Math.max(d.W, d.H), d.W * d.H, ...tail(d)];
  const cmp = (a, b) => { const x = score(a), y = score(b); for (let i = 0; i < x.length; i++) if (x[i] !== y[i]) return x[i] - y[i]; return 0; };

  function command(n, m) {
    const flag = st.mode === 'sym' ? ' --sym' : st.mode === 'quasi' ? ' --quasi' : '';
    const goal = st.goal === 'square' ? ' --goal square' : '';
    if (n === 1) return `python -m bmsolver split ${m}${flag}${goal}`;
    if (m === 1) return `python -m bmsolver merge ${n}${flag}${goal}`;
    return `python -m bmsolver balance ${n}${n === m ? '' : ' ' + m}${flag}${goal}`;
  }

  // smallest area proven: the solver showed no smaller layout exists under this rule (or a looser one)
  function proven(d, all) {
    return all.some(e => e.proven_for && LOOSE[e.proven_for] >= LOOSE[st.mode] && e.W * e.H === d.W * d.H);
  }

  function label(d, all) {
    const src = d.source === 'wiki' ? 'community design from the wiki' : d.source === 'ours' ? 'found by our solver' : 'by ' + d.source;
    const pr = st.goal === 'area' && proven(d, all) ? ' · <span class="good">smallest possible area (proven)</span>' : '';
    const fp = d.footprint != null && d.footprint < d.W * d.H ? ` (footprint ${d.footprint})` : '';
    const edge = d.edge_splitters ? ` · ${d.edge_splitters} splitter face${d.edge_splitters > 1 ? 's' : ''} out of the edge` : '';
    return `${d.W}×${d.H}${fp} · ${d.pieces} pieces · ${d.splitters} splitters${d.ug ? ' · ' + d.ug + ' underground' : ''}${edge}` +
      `${d.gold ? ' · ' + d.gold + ' gold' : ''} · ${MODE_NAME[d.mode]} · ${src}${pr}`;
  }

  // top <-> bottom mirror image of a catalogue layout (north and south swap; east and west stay)
  const FLIP = { 0: 2, 2: 0 };
  const mirrorGrid = g => [...g].reverse().map(r => r.replace(/([A-Z])([0-3])/g, (_, k, o) => k + (FLIP[o] ?? o)));
  const mirrorPorts = (ps, H) => ps && ps.map(([x, y, o]) => [x, H - 1 - y, FLIP[o] ?? o]);

  function pick(d, mirrored = false) {
    st.picked = d; st.mirrored = mirrored;
    $('dMirror').classList.toggle('on', mirrored);
    $('dMirror').hidden = d.mode === 'sym';         // a symmetric layout is its own mirror image
    const grid = mirrored ? mirrorGrid(d.grid) : d.grid;
    show(gridRows(grid), { name: `${d.n}:${d.m} ${MODE_NAME[d.mode]} ${d.W}x${d.H}${mirrored ? ' mirrored' : ''}`,
      outputs: mirrored ? mirrorPorts(d.outs, d.H) : d.outs, inputs: mirrored ? mirrorPorts(d.ins, d.H) : d.ins,
      // different amounts on every input, all below the belts' limit: a working balancer shows the same total on
      // every output (equal steady inputs can stay in step through the splitters and look unmixed)
      feedRates: d.ins && d.ins.map((_, i) => Math.round(480 * Math.min(1, d.m / d.n) * (i + 1) / d.n)) });
    $('dinfo').textContent = (d.name ? `${d.name}${d.trimmed ? ` (${d.trimmed} unused pieces removed)` : ''}` : '') +
      (d.stray_out ? ' · Note: a splitter at the end of the output side also faces out on a row that is not an output; leave that tile empty.' : '');
    $('dstatus').textContent = `${d.n}:${d.m} ${d.W}×${d.H}, ${d.pieces} pieces, ${MODE_NAME[d.mode]}${mirrored ? ', mirrored' : ''}`;
    st.bpId = (mirrored ? d.mirror_id : d.id) || null;   // the mirror image has its own blueprint (made if missing)
    st.bpName = `${d.n}:${d.m} ${d.kind === 'split' ? 'splitter' : 'balancer'} ${d.W}x${d.H}${mirrored ? ' mirrored' : ''}`;
    $('dLink').disabled = false;
    $('dLink').title = st.bpId ? 'Copies ' + bpLink(st.bpId) + ' (paste it into the game\'s Import New Blueprint box)'
      : 'Makes a game blueprint of this layout, then copies its link';
    if (st.bpId) $('dinfo').append(' ', bpLinkBits(st.bpId));
    document.querySelectorAll('#dAll .gal').forEach(x => x.classList.toggle('on', x.dataset.k === d._k));
  }

  function update() {
    const n = Math.max(1, +$('dN').value || 1), m = Math.max(1, +$('dM').value || 1);
    $('dModeHelp').textContent = MODE_HELP[st.mode];
    $('dCmd').textContent = command(n, m);
    const all = st.cat.filter(d => d.n === n && d.m === m).sort(cmp);
    const ok = all.filter(d => ALLOWED[st.mode].includes(d.mode));
    const best = ok[0];
    $('dAll').innerHTML = '';
    for (const d of all) {
      const el = document.createElement('div');
      el.className = 'gal' + (ALLOWED[st.mode].includes(d.mode) ? '' : ' off');
      el.dataset.k = d._k;
      const fp = d.footprint != null && d.footprint < d.W * d.H ? ' (' + d.footprint + ')' : '';
      const edge = d.edge_splitters ? ` · ${d.edge_splitters} edge` : '';
      el.innerHTML = `<span>${d.W}×${d.H}${fp} <span class="muted">· ${d.pieces} pcs${d.gold ? ' · ' + d.gold + 'g' : ''}${edge}</span></span>` +
        `<span class="badge ${d.mode}">${d.mode}</span><span class="muted small">${d.source}${d.id ? ' · ' + d.id : ''}</span>`;
      el.addEventListener('click', () => pick(d));
      $('dAll').appendChild(el);
    }
    if (!all.length) $('dAll').innerHTML = '<span class="muted small">None yet.</span>';
    if (best) {
      $('dBest').innerHTML = `<div class="best"><b>${n}:${m}</b> ${label(best, all)}</div>`;
      pick(best);
    } else {
      // mirror image of side-by-side ports: n inputs and m outputs both centred on the axis, so n and m need the
      // same parity (quasi only lets a single port sit on one of the two middle rows)
      const single = n === 1 || m === 1;
      const why = st.mode === 'sym' && (n - m) % 2
        ? `A mirror-symmetric ${n}:${m} can't exist with the inputs side by side and the outputs side by side: ${n} lanes and ${m} lanes can't both be centred on the same middle line.${single ? ' Quasi is the closest form.' : ''}`
        : st.mode === 'quasi' && (n - m) % 2 && !single
        ? `A quasi-symmetric ${n}:${m} can't exist with the ports side by side (${n} and ${m} lanes can't both be centred). The asymmetric layouts are listed below.`
        : m === 1 && n > 1 ? `The catalogue has no ${n}:1 mergers: belts that are known not to go over a full belt together can simply be joined.`
        : `No ${MODE_NAME[st.mode]} ${n}:${m} layout known yet${all.length ? ' (other rules have some, listed below)' : ''}. Run the command below to search for one.`;
      $('dBest').innerHTML = `<div class="muted small">${why}</div>`;
      st.current = null;
      $('dSim').disabled = $('dCopy').disabled = $('dLink').disabled = $('dMirror').disabled = true;
      $('dinfo').innerHTML = '&nbsp;';
      view.setWorld(null);
      $('dstatus').textContent = 'Nothing known for this choice yet';
    }
  }

  // submissions: a GitHub issue the submit workflow checks (exact flows, scores) and commits when it is a new best
  $('dSubmit').addEventListener('click', () => {
    const raw = $('dSubmitCode').value.trim();
    const code = (raw.match(/[?&]id=([0-9A-Za-z]+)/) || raw.match(/^([0-9A-Za-z]{4,12})$/) || [])[1];
    if (!code) { $('dSubmitCode').focus(); return; }
    const title = 'Layout submission: ' + code.toLowerCase();
    const body = 'Blueprint: https://builderment.com/blueprints?id=' + code.toLowerCase() +
      '\n\nKeep the title as it is: a check runs on it and answers here.';
    window.open('https://github.com/djradnad/buildermentSim/issues/new?title=' + encodeURIComponent(title) +
      '&body=' + encodeURIComponent(body), '_blank', 'noopener');
  });
  $('dMirror').addEventListener('click', () => st.picked && pick(st.picked, !st.mirrored));
  $('dSim').addEventListener('click', () => st.current && openInSim(st.current.rows, st.current.meta, st.bpId));
  $('dCopy').addEventListener('click', async () => {
    if (!st.current) return;
    const json = JSON.stringify({ meta: { name: st.current.meta.name || 'layout', count: st.current.rows.length }, rows: st.current.rows });
    try { await navigator.clipboard.writeText(json); $('dinfo').textContent = 'Copied layout json.'; }
    catch { $('dinfo').textContent = json; }
  });

  // Layouts without a blueprint yet get one made on the spot (uploaded, downloaded again and checked). The
  // clipboard write starts inside the tap with a pending value, so Safari still allows it after the upload.
  async function linkFor(rows) {
    if (st.bpId) return bpLink(st.bpId);
    const cur = st.current;
    const code = await makeBlueprint(rows, st.bpName || 'layout');
    if (st.current === cur) {
      st.bpId = code;
      $('dinfo').append(' ', bpLinkBits(code));
    }
    return bpLink(code);
  }
  $('dLink').addEventListener('click', async () => {
    if (!st.current) return;
    const btn = $('dLink'), label = btn.textContent;
    const p = linkFor(st.current.rows);
    if (!st.bpId) { btn.disabled = true; btn.textContent = 'Making blueprint…'; }
    try {
      if (window.ClipboardItem && navigator.clipboard.write) {
        await navigator.clipboard.write([new ClipboardItem({ 'text/plain': p.then(t => new Blob([t], { type: 'text/plain' })) })]);
      } else {
        await navigator.clipboard.writeText(await p);
      }
      btn.textContent = 'Copied';
    } catch (e) {
      try { const t = await p; $('dinfo').textContent = t; btn.textContent = label; }
      catch (e2) { $('dinfo').textContent = 'Could not make a blueprint: ' + e2.message; btn.textContent = label; }
    }
    btn.disabled = false;
    setTimeout(() => { btn.textContent = label; }, 2000);
  });

  fetch('catalogue.json').then(r => r.json()).then(c => {
    st.cat = c.designs.map((d, i) => ({ ...d, _k: String(i) }));
    update();
  }).catch(() => { $('dBest').textContent = 'Could not load the catalogue.'; });

  // ---------------------------------------------------------------- live solver
  async function poll() {
    const base = $('liveUrl').value.replace(/\/+$/, '');
    try {
      const s = await (await fetch(base + '/state.json', { cache: 'no-store' })).json();
      if (s.version !== lastVersion) {
        lastVersion = s.version;
        if (s.design && s.design.length) {
          const rows = s.design.map(([x, y, c]) => [CODE_ID[c[0]], x, y, +c[1], 0, -1, 0]);
          show(rows, { name: `${s.W}x${s.H}, ${s.pieces} pieces, ${s.note}` });
          $('dinfo').textContent = `live: ${s.W}x${s.H}, ${s.pieces} pieces, ${s.note}`;
        }
        $('dstatus').textContent = `${s.title}: ${s.design ? s.W + 'x' + s.H + ' ' + s.note : 'searching…'}`;
        $('liveSizes').innerHTML = (s.sizes || []).map(([W, H, stt, sec]) =>
          `<span class="chip ${stt === 'FOUND' || stt === 'OPTIMAL' || stt === 'FEASIBLE' ? 'found' : stt === 'INFEASIBLE' ? 'no' : ''}">${W}x${H} ${sec}s</span>`).join('');
        $('liveLog').hidden = false;
        $('liveLog').textContent = (s.log || []).slice(-40).join('\n');
      }
    } catch (e) {
      $('dstatus').textContent = 'Cannot reach the solver at ' + base + ' (is it running with --live?)';
    }
  }
  $('liveConnect').addEventListener('click', () => {
    clearInterval(timer);
    lastVersion = -1;
    poll();
    timer = setInterval(poll, 700);
    $('liveConnect').textContent = 'Connected';
  });
  window.addEventListener('resize', () => view.resize());
}
