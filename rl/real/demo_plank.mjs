import { Run, TICKS } from './runner.mjs';
import { ports, route, blocked } from './layout.mjs';
const r = new Run();
const LAB = {id:3013, x:6, y:-2, o:0};
const near = (res, cx, cy, skip=new Set()) => [...r.node].filter(([k,v])=>v===res && !skip.has(k)).map(([k])=>k.split(',').map(Number)).sort((a,b)=>Math.abs(a[0]-cx)+Math.abs(a[1]-cy)-Math.abs(b[0]-cx)-Math.abs(b[1]-cy));
function extTo(xy, inp) {   // best orientation for an extractor at xy feeding input port inp
  let best=null;
  for (const o of [0,1,2,3]) { const e={id:3004,x:xy[0],y:xy[1],o}; const out=ports(e).outs[0]; const bl=route(r,out,inp); if (bl && (!best || bl.length<best[1].length)) best=[e,bl]; }
  return best;
}
const used = new Set();
console.log(r.place([LAB]));
const labIns = ports(LAB).ins;
const w = near('Wood Log', 6, -2)[0]; used.add(w.join(','));
let [e, bl] = extTo(w, labIns[0]); for (const b of [e]) ; console.log('ext', w, bl.length, r.place([e, ...bl]));
let phase = 1;
for (let i = 0; i < 60 * 300; i++) {
  r.step();
  if (phase === 1 && r.done.has(4001)) {
    // workshop line: extractor -> workshop -> lab
    const w2 = near('Wood Log', 6, -2, used)[0]; used.add(w2.join(','));
    let plan = null;
    for (let dx=-3; dx<=3 && !plan; dx++) for (let dy=-3; dy<=3 && !plan; dy++) for (const o of [0,1,2,3]) {
      const ws = {id:3005, x:w2[0]+dx, y:w2[1]+dy, o, recipe:2000};
      if (blocked(r).has(ws.x+','+ws.y)) continue;
      const p = ports(ws);
      r.bs.push(ws); // temp block for routing
      const a = extTo(w2, p.ins[0]);
      const b2 = a && route(r, p.outs[0], labIns[1], new Set(a[1].map(t=>t.x+','+t.y)));
      r.bs.pop();
      if (a && b2) { const all=[ws, a[0], ...a[1], ...b2]; if (!plan || all.length<plan.length) plan = all; }
    }
    phase = 2; r.pending = plan; console.log('t', r.tick/60, 'planks unlocked, plan pieces', plan.length);
  }
  if (r.pending && r.place(r.pending)) { console.log('placed line at', r.tick/60, 'gold', r.gold); r.pending = null; }
  if ('Wood Plank' in r.first) { console.log('FIRST PLANK at', r.first['Wood Plank']/60, 's'); break; }
}
console.log(r.log.slice(0,10), 'gold', r.gold, 'dropped', r.dropped||0);
