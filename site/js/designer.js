// Designer tab: pick a splitter, merger or balancer and a symmetry rule, and see the best known layout for it
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
  quasi: 'Mirror symmetric except one tile pair in the middle that faces another way, like the wiki splitters with one turned splitter in the centre. Symmetric layouts count too.',
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
    $('dSim').disabled = $('dCopy').disabled = $('dLink').disabled = false;
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
    if (st.kind === 'merge') { $('dM').value = 1; if (+$('dN').value < 2) $('dN').value = 4; }
    if (st.kind === 'balance') { if (+$('dN').value < 2) $('dN').value = 4; if (+$('dM').value < 2) $('dM').value = 4; }
  });
  seg('dMode', 'mode');
  seg('dGoal', 'goal');
  for (const id of ['dN', 'dM']) $(id).addEventListener('input', () => {
    const n = +$('dN').value, m = +$('dM').value;
    const kind = n === 1 ? 'split' : m === 1 ? 'merge' : 'balance';
    if (kind !== st.kind) {
      st.kind = kind;
      $('dKind').querySelectorAll('button').forEach(x => x.classList.toggle('on', x.dataset.v === kind));
    }
    update();
  });

  // ties: fewer pieces, stricter rule, no splitter facing out of a non-output edge, a ready blueprint, wiki first
  const tail = d => [d.pieces, LOOSE[d.mode], d.stray_out ? 1 : 0, d.id ? 0 : 1, d.source === 'wiki' ? 0 : 1];
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
    const src = d.source === 'wiki' ? `community design from the wiki${d.id ? ' (blueprint ' + d.id + ')' : ''}` : 'found by our solver';
    const pr = st.goal === 'area' && proven(d, all) ? ' · <span class="good">smallest possible area (proven)</span>' : '';
    return `${d.W}×${d.H} · ${d.pieces} pieces · ${d.splitters} splitters${d.ug ? ' · ' + d.ug + ' underground' : ''} · ${MODE_NAME[d.mode]} · ${src}${pr}`;
  }

  function pick(d) {
    show(gridRows(d.grid), { name: `${d.n}:${d.m} ${MODE_NAME[d.mode]} ${d.W}x${d.H}` });
    $('dinfo').textContent = (d.name ? `${d.name}${d.trimmed ? ` (${d.trimmed} unused pieces removed)` : ''}` : '') +
      (d.stray_out ? ' · Note: a splitter at the end of the output side also faces out on a row that is not an output; leave that tile empty.' : '');
    $('dstatus').textContent = `${d.n}:${d.m} ${d.W}×${d.H}, ${d.pieces} pieces, ${MODE_NAME[d.mode]}`;
    st.bpId = d.id || null;
    st.bpName = `${d.n}:${d.m} ${d.kind === 'split' ? 'splitter' : d.kind === 'merge' ? 'merger' : 'balancer'} ${d.W}x${d.H}`;
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
      el.innerHTML = `<span>${d.W}×${d.H} <span class="muted">· ${d.pieces} pcs</span></span>` +
        `<span class="badge ${d.mode}">${d.mode}</span><span class="muted small">${d.source === 'wiki' ? 'wiki' : 'ours'}${d.id ? ' · ' + d.id : ''}</span>`;
      el.addEventListener('click', () => pick(d));
      $('dAll').appendChild(el);
    }
    if (!all.length) $('dAll').innerHTML = '<span class="muted small">None yet.</span>';
    if (best) {
      $('dBest').innerHTML = `<div class="best"><b>${n}:${m}</b> ${label(best, all)}</div>`;
      pick(best);
    } else {
      const why = st.mode === 'sym' && ((n === 1 && m % 2 === 0) || (m === 1 && n % 2 === 0))
        ? `A mirror-symmetric ${n}:${m} can't exist with the ${n === 1 ? 'outputs' : 'inputs'} side by side: an even number of lanes has no middle lane for the single ${n === 1 ? 'input' : 'output'}. Quasi is the closest form.`
        : `No ${MODE_NAME[st.mode]} ${n}:${m} layout known yet${all.length ? ' (other rules have some, listed below)' : ''}. Run the command below to search for one.`;
      $('dBest').innerHTML = `<div class="muted small">${why}</div>`;
      st.current = null;
      $('dSim').disabled = $('dCopy').disabled = $('dLink').disabled = true;
      $('dinfo').innerHTML = '&nbsp;';
      view.setWorld(null);
      $('dstatus').textContent = 'Nothing known for this choice yet';
    }
  }

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
