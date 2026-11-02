// Designer tab: a gallery of solved layouts and a live view of a running solver (python -m bmsolver ... --live).

import { World } from './gamesim.js';
import { parseBlueprintText, rowsToBuildings } from './blueprint.js';
import { View } from './view.js';

const $ = id => document.getElementById(id);
const CODE_ID = { B: 3000, S: 3001, I: 3002, O: 3003 };

export function initDesigner({ openInSim }) {
  const view = new View($('dview'));
  let current = null, timer = null, lastVersion = -1;

  function show(rows, meta) {
    current = { rows, meta };
    view.setWorld(new World(rowsToBuildings(rows)));
    $('dinfo').textContent = meta.name || '';
    $('dSim').disabled = $('dCopy').disabled = false;
  }

  $('dSim').addEventListener('click', () => current && openInSim(current.rows, current.meta));
  $('dCopy').addEventListener('click', async () => {
    if (!current) return;
    const json = JSON.stringify({ meta: { name: current.meta.name || 'layout', count: current.rows.length }, rows: current.rows });
    try { await navigator.clipboard.writeText(json); $('dinfo').textContent = 'Copied blueprint json.'; }
    catch { $('dinfo').textContent = json; }
  });

  fetch('examples/index.json').then(r => r.json()).then(list => {
    for (const ex of list) {
      const d = document.createElement('div');
      d.className = 'gal';
      d.innerHTML = `<span>${ex.name}</span><span class="muted small">${ex.file.endsWith('.txt') ? 'solver' : 'blueprint'}</span>`;
      d.addEventListener('click', async () => {
        document.querySelectorAll('.gal').forEach(x => x.classList.toggle('on', x === d));
        const bp = parseBlueprintText(await (await fetch('examples/' + ex.file)).text());
        show(bp.rows, { ...bp.meta, name: ex.name });
        $('dstatus').textContent = ex.name;
      });
      $('gallery').appendChild(d);
    }
  }).catch(() => {});

  async function poll() {
    const base = $('liveUrl').value.replace(/\/+$/, '');
    try {
      const s = await (await fetch(base + '/state.json', { cache: 'no-store' })).json();
      if (s.version !== lastVersion) {
        lastVersion = s.version;
        if (s.design && s.design.length) {
          const rows = s.design.map(([x, y, c]) => [CODE_ID[c[0]], x, y, +c[1], 0, -1, 0]);
          show(rows, { name: `${s.W}x${s.H}, ${s.pieces} pieces, ${s.note}` });
        }
        $('dstatus').textContent = `${s.title}: ${s.design ? s.W + 'x' + s.H + ' ' + s.note : 'searching…'}`;
        $('liveSizes').innerHTML = (s.sizes || []).map(([W, H, st, sec]) =>
          `<span class="chip ${st === 'FOUND' || st === 'OPTIMAL' || st === 'FEASIBLE' ? 'found' : st === 'INFEASIBLE' ? 'no' : ''}">${W}x${H} ${sec}s</span>`).join('');
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
