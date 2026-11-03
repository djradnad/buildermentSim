// Exact production rate of every recipe at every building tier, from the game's tick rules (site/js/gamesim.js).
//   node tools/recipe_rates.mjs [boost] > rates.json      boost: power plant bonus (0, 0.2 coal, 0.4 nuclear)
// For each recipe and tier it reports
//   ticks       ticks per craft from the progress rule alone (progress is a double; each tick adds the float
//               (boost + 1) * tier speed * (1/60), and the craft ends on the tick it reaches the recipe time)
//   ideal       output per minute from that alone = amount * 3600 / ticks
//   sim         output per minute measured in the tick-exact sim over one game hour, every input fed by its own
//               full belt and the output on an open belt; lower than ideal when the output cap of 10 or a belt
//               (480/min, one item per 7.5 ticks in or out) holds the machine up (the "frozen" ticks)
//   period      the sim's repeating cycle: {ticks, items, crafts}; sim = items * 3600 / ticks exactly
//   exact       that rate as a fraction (items per minute), frozen = idle ticks per craft
import { Factory, World, GAMEDATA, BDEF, DV, DT } from '../site/js/gamesim.js';

const f32 = Math.fround;
const boost = +(process.argv[2] || 0);
const key = (x, y) => x + ',' + y;
const BID = {};
for (const [k, b] of Object.entries(GAMEDATA.buildings)) BID[k] = b.id;

function ticksPerCraft(seconds, mult) {
  const sp = f32(f32(boost + 1) * f32(mult));
  const inc = f32(sp * DT);
  let p = 0.0, k = 0;
  do { p = p + inc; k++; } while (!(seconds <= p));
  return k;
}

// a machine with a 4-tile feeder belt into each input port and a 4-tile belt away from its output
function layout(bid, recipeId, tier) {
  const m = { id: bid, x: 0, y: 0, o: 0, tier, recipe: recipeId, filter: 0 };
  const w0 = new World([m]);
  const bs = [m], feeds = [];
  const occ = new Set(w0.occ.keys());
  const tryChain = (port, isIn) => {
    for (let k = 0; k < 4; k++) {
      const [dx, dy] = DV[k];
      const tiles = [];
      for (let s = 1; s <= 4; s++) tiles.push(isIn ? [port.x - dx * s, port.y - dy * s] : [port.x + dx * s, port.y + dy * s]);
      if (tiles.some(t => occ.has(key(...t)))) continue;
      const belts = tiles.map(([x, y]) => ({ id: 3000, x, y, o: k, tier: 1, recipe: 0, filter: 0 }));
      const w = new World([...bs, ...belts]);
      const n = (isIn ? w.inport : w.outport).get(key(port.x, port.y));
      if (n && (isIn ? n.inputs.length : n.outputs.length)) {
        bs.push(...belts); tiles.forEach(t => occ.add(key(...t)));
        return isIn ? tiles[tiles.length - 1] : true;
      }
    }
    return null;
  };
  const ins = [...w0.inport.values()], outs = [...w0.outport.values()];
  const starts = ins.map(p => tryChain(p, true));
  outs.forEach(p => tryChain(p, false));
  return { bs, starts };
}

const rows = [];
for (const [name, r] of Object.entries(GAMEDATA.recipes)) {
  const bdef = GAMEDATA.buildings[r.building];
  const speeds = bdef.tier_speed || [1];
  for (let t = 1; t <= speeds.length; t++) {
    const mult = speeds[t - 1];
    const ticks = ticksPerCraft(r.seconds, mult);
    const amount = r.out[0].amount;
    const ideal = amount * 3600 / ticks;
    const { bs, starts } = layout(bdef.id, r.id, t);
    const feeds = starts.map((at, i) => at && i < r.in.length ? { at, kind: r.in[i].item, perMin: 480 } : null).filter(Boolean);
    let sim = null, period = null, note = '', exact = null, frozen = null;
    if (feeds.length < r.in.length) note = `only ${feeds.length} input ports for ${r.in.length} ingredients`;
    else {
      const w = new Factory(bs, { feeds, boosted: false });
      const mc = w.machines[0];
      const B = f32(boost);                                               // a plant in range keeps it constant
      const step = () => { mc.boost = B; w.tick(); };
      for (let i = 0; i < 3600 * 10; i++) step();                         // 10 game minutes to settle
      const made0 = mc.made, hist = [];
      const H = 3600 * 60;
      for (let i = 0; i < H; i++) { step(); if (mc.made !== (hist.length ? hist[hist.length - 1][1] : made0)) hist.push([w.tickNo, mc.made]); }
      sim = (mc.made - made0) * 3600 / H;
      // the repeating cycle: smallest number of crafts c after which tick gaps repeat
      const ts = hist.map(h => h[0]);
      const gaps = ts.slice(1).map((v, i) => v - ts[i]);
      for (let c = 1; c <= 64 && c < gaps.length / 4; c++) {
        let ok = true;
        for (let i = c; i < gaps.length && ok; i++) ok = gaps[i] === gaps[i - c];
        if (ok) { const tk = gaps.slice(0, c).reduce((a, b) => a + b, 0); period = { ticks: tk, items: c * amount, crafts: c }; break; }
      }
      if (period) {
        exact = `${period.items * 3600}/${period.ticks}`;
        frozen = period.ticks / period.crafts - ticks;                      // ticks per craft the machine sits idle
        sim = period.items * 3600 / period.ticks;
      } else note = 'no repeating cycle found';
      if (boost) note = 'boost ' + boost;
    }
    rows.push({ recipe: name.replace(/^recipe_/, ''), id: r.id, building: bdef.name, tier: t, seconds: r.seconds, amount, ticks,
      ideal: +ideal.toFixed(4), sim: sim === null ? null : +sim.toFixed(4), exact, frozen, period, note });
  }
}
console.log(JSON.stringify(rows, null, 0).replace(/},{/g, '},\n{'));
