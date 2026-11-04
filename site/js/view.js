// Canvas view of a factory: buildings, belts, flow lines sized by throughput, moving items, per-tile numbers.
// Pan with drag, zoom with wheel or pinch; tap/hover a tile for details.

import { BDEF, DV, footprint, grot, World } from './gamesim.js';

const RAW_COLORS = {
  'Wood Log': '#b07a45', 'Stone': '#a3a3a3', 'Iron Ore': '#c86b5a', 'Copper Ore': '#e08a3c', 'Coal': '#5c5c66',
  'Wolframite': '#8f7fc4', 'Uranium Ore': '#7bd36b', 'Gold': '#f0c84a',
};
const PALETTE = ['#4fa3ff', '#ff7aa8', '#5fd4b0', '#ffb84d', '#b48cff', '#7fd85f', '#ff6b5f', '#49d1e6', '#e6d549',
  '#c97bff', '#ff9e5f', '#5f9eff'];
const colorCache = new Map();
export function itemColor(kind) {
  if (RAW_COLORS[kind]) return RAW_COLORS[kind];
  if (!colorCache.has(kind)) {
    let h = 0;
    for (const c of String(kind)) h = (h * 31 + c.charCodeAt(0)) >>> 0;
    colorCache.set(kind, /^In\d+$/.test(kind) ? PALETTE[+kind.slice(2) % PALETTE.length] : PALETTE[h % PALETTE.length]);
  }
  return colorCache.get(kind);
}

const BUILDING_STYLE = {   // fill, label
  3004: ['#3a4a2c', 'Ex'], 3040: ['#2f4a2c', 'U'], 3005: ['#3b3550', 'W'], 3006: ['#503a2c', 'F'],
  3007: ['#2c4250', 'MS'], 3008: ['#2c5046', 'IF'], 3009: ['#50402c', 'Fg'], 3010: ['#4a2c50', 'Mf'],
  3011: ['#3d3d3d', 'St'], 3013: ['#2c3d50', 'Lab'], 3014: ['#502c3d', 'TP'], 3038: ['#4a3a2a', 'Coal'],
  3039: ['#2a4a3a', 'Nuc'], 3029: ['#2c5030', 'Gem'],
};

export function shortName(s) {
  if (!s) return '';
  const w = s.split(' ');
  return w.length === 1 ? s.slice(0, 4) : w.map(x => x[0]).join('');
}

export function fmtRate(v) {
  if (v >= 100) return Math.round(v).toString();
  if (v >= 10) return v.toFixed(1).replace(/\.0$/, '');
  return v.toFixed(2).replace(/\.?0+$/, '');
}

export class View {
  constructor(canvas, opts = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.scale = 32; this.ox = 0; this.oy = 0;   // screen = (tile - origin) * scale
    this.world = null;
    this.tileRates = null;      // Map "x,y" -> items/min
    this.showItems = true; this.showNumbers = true; this.showFlow = true; this.showPieces = false;
    this.onPick = opts.onPick || (() => {});
    this.insetBottom = opts.insetBottom || (() => 0);
    this.dpr = 1;
    this._bindInput();
    new ResizeObserver(() => this.resize()).observe(canvas);
    this.resize();
  }

  setWorld(w, keepView = false) {
    this.world = w;
    this.bbox = null;
    if (w) {
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      for (const s of w.occ.keys()) {
        const [x, y] = s.split(',').map(Number);
        x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y);
      }
      this.bbox = [x0, y0, x1, y1];
      if (!keepView) this.fit();
    }
  }

  resize() {
    const r = this.canvas.getBoundingClientRect();
    this.dpr = window.devicePixelRatio || 1;
    this.canvas.width = Math.max(1, Math.round(r.width * this.dpr));
    this.canvas.height = Math.max(1, Math.round(r.height * this.dpr));
    this.cw = r.width; this.ch = r.height;
    this.draw();
  }

  fit() {
    if (!this.bbox) return;
    const [x0, y0, x1, y1] = this.bbox;
    const w = x1 - x0 + 2, h = y1 - y0 + 2;
    const ch = Math.max(80, this.ch - this.insetBottom());   // part of the canvas a bottom sheet doesn't cover
    this.scale = Math.max(this.minScale || 0.3, Math.min(64, Math.min(this.cw / w, ch / h)));
    this.ox = (x0 + x1) / 2 - this.cw / 2 / this.scale;
    this.oy = (y0 + y1) / 2 - ch / 2 / this.scale;
    this.draw();
  }

  toScreen(x, y) { return [(x - this.ox) * this.scale, (y - this.oy) * this.scale]; }
  toTile(sx, sy) { return [Math.round(sx / this.scale + this.ox), Math.round(sy / this.scale + this.oy)]; }

  _bindInput() {
    const c = this.canvas;
    const pts = new Map();
    let moved = 0, pinch = null;
    c.addEventListener('pointerdown', e => {
      e.preventDefault();                       // no text selection or long-press menu while dragging
      if (e.isPrimary) { pts.clear(); pinch = null; }   // a new first touch: forget fingers whose 'up' got lost
      try { c.setPointerCapture(e.pointerId); } catch { /* pointer already gone */ }
      pts.set(e.pointerId, [e.offsetX, e.offsetY]);
      moved = 0;
    });
    c.addEventListener('pointermove', e => {
      if (!pts.has(e.pointerId)) {
        if (e.pointerType === 'mouse') this.onPick(this.toTile(e.offsetX, e.offsetY), false);
        return;
      }
      const [px, py] = pts.get(e.pointerId);
      pts.set(e.pointerId, [e.offsetX, e.offsetY]);
      if (pts.size === 1) {
        this.ox -= (e.offsetX - px) / this.scale; this.oy -= (e.offsetY - py) / this.scale;
        moved += Math.abs(e.offsetX - px) + Math.abs(e.offsetY - py);
        this.draw();
      } else if (pts.size >= 2) {
        const [a, b] = [...pts.values()].slice(-2);
        const d = Math.hypot(a[0] - b[0], a[1] - b[1]);
        const mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2;
        if (pinch) this.zoomAt(d / pinch, mx, my);
        pinch = d; moved += 10;
      }
    });
    const up = e => {
      if (pts.size === 1 && moved < 6) this.onPick(this.toTile(e.offsetX, e.offsetY), true);
      pts.delete(e.pointerId);
      if (pts.size < 2) pinch = null;
    };
    c.addEventListener('pointerup', up);
    c.addEventListener('pointercancel', up);
    c.addEventListener('lostpointercapture', e => { pts.delete(e.pointerId); if (pts.size < 2) pinch = null; });
    c.addEventListener('contextmenu', e => e.preventDefault());
    c.addEventListener('wheel', e => { e.preventDefault(); this.zoomAt(Math.exp(-e.deltaY * 0.0015), e.offsetX, e.offsetY); }, { passive: false });
  }

  zoomAt(k, sx, sy) {
    const ns = Math.max(this.minScale || 0.3, Math.min(160, this.scale * k));
    const tx = sx / this.scale + this.ox, ty = sy / this.scale + this.oy;
    this.scale = ns;
    this.ox = tx - sx / ns; this.oy = ty - sy / ns;
    this.draw();
  }

  draw() {
    const ctx = this.ctx, s = this.scale;
    if (!ctx) return;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    const css = getComputedStyle(document.documentElement);
    const bg = css.getPropertyValue('--canvas-bg').trim() || '#0d0f13';
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, this.cw, this.ch);
    const w = this.world;
    if (!w) return;
    const vx0 = this.ox - 1, vy0 = this.oy - 1, vx1 = this.ox + this.cw / s + 1, vy1 = this.oy + this.ch / s + 1;
    const vis = (x, y) => x >= vx0 && x <= vx1 && y >= vy0 && y <= vy1;

    // grid
    if (s >= 10) {
      ctx.strokeStyle = css.getPropertyValue('--grid').trim() || '#171a20';
      ctx.lineWidth = 1;
      ctx.beginPath();
      for (let x = Math.floor(vx0) + 0.5; x < vx1; x++) { const [sx] = this.toScreen(x, 0); ctx.moveTo(sx, 0); ctx.lineTo(sx, this.ch); }
      for (let y = Math.floor(vy0) + 0.5; y < vy1; y++) { const [, sy] = this.toScreen(0, y); ctx.moveTo(0, sy); ctx.lineTo(this.cw, sy); }
      ctx.stroke();
    }

    // buildings
    for (const b of w.buildings) {
      if (b.id >= 3000 && b.id <= 3003) continue;
      if (!vis(b.x, b.y) && !(BDEF[b.id] && BDEF[b.id].w > 2)) continue;
      this._drawBuilding(b);
    }

    // belts
    const beltCol = css.getPropertyValue('--belt').trim() || '#2a2f3a';
    const arrowCol = css.getPropertyValue('--belt-arrow').trim() || '#566074';
    // outputs (open ends that take items) red, inputs green; any other open end just stops
    const outEnds = new Set();
    for (const g of w.segs) if (g.target && g.target.sink) outEnds.add(g.nodes[g.nodes.length - 1]);
    const feedStarts = new Set((w.feeds || []).map(f => f.seg.nodes[0]));
    for (const n of w.belt.values()) {
      if (!vis(n.x, n.y)) continue;
      const [sx, sy] = this.toScreen(n.x, n.y);
      const h = s * 0.46;
      ctx.fillStyle = outEnds.has(n) || n.owner.added === 'out' ? '#3a1f24' : feedStarts.has(n) || n.owner.added === 'in' ? '#1f3a2a' : beltCol;
      if (n.sub === 1) {
        ctx.fillRect(sx - h, sy - h, 2 * h, 2 * h);
        ctx.strokeStyle = arrowCol; ctx.lineWidth = Math.max(1, s * 0.06);
        ctx.strokeRect(sx - h * 0.8, sy - h * 0.8, 1.6 * h, 1.6 * h);
      } else {
        const [dx, dy] = DV[n.dir];
        const wide = Math.abs(dx) ? [h, h * 0.8] : [h * 0.8, h];
        ctx.fillRect(sx - wide[0], sy - wide[1], 2 * wide[0], 2 * wide[1]);
      }
      if (s >= 8) this._arrow(sx, sy, n.dir, s, arrowCol, n.sub);
    }

    // flow lines
    if (this.showFlow && this.segRates) {
      ctx.lineCap = 'round'; ctx.lineJoin = 'round';
      for (const g of w.segs) {
        const r = this.segRates.get(g);
        if (!r || r.total <= 0.01) continue;
        if (!g.pts.some(p => vis(p[0], p[1]))) continue;
        ctx.strokeStyle = this._dim(itemColor(r.kind));
        ctx.lineWidth = Math.max(1, s * 0.22 * Math.min(1, r.total / 480));
        ctx.beginPath();
        g.pts.forEach((p, i) => { const [sx, sy] = this.toScreen(p[0], p[1]); i ? ctx.lineTo(sx, sy) : ctx.moveTo(sx, sy); });
        ctx.stroke();
      }
    }

    if (this.showPieces) this._drawPieces(vx0, vy0, vx1, vy1);

    // items
    if (this.showItems && s >= 6) {
      const r = Math.max(1.5, s * 0.1);
      let drawn = 0;
      for (const g of w.segs) {
        if (!g.flag || !g.items.length) continue;
        if (!g.pts.some(p => vis(p[0], p[1]))) continue;
        let x = g.L;
        for (let i = 0; i < g.items.length; i++) {
          x -= g.items[i];
          const [px, py] = g.pointAt(x);
          if (!vis(px, py)) continue;
          const [sx, sy] = this.toScreen(px, py);
          ctx.fillStyle = itemColor(g.kinds[i]);
          ctx.beginPath(); ctx.arc(sx, sy, r, 0, 6.2832); ctx.fill();
          if (++drawn > 20000) break;
        }
      }
      for (const a of w.arms || []) {
        if (a.held === null || !vis(a.b.x, a.b.y)) continue;
        const t = Math.min(1, Math.max(0, a.prog / 2.7333));
        const px = a.src[0] + (a.dst[0] - a.src[0]) * t, py = a.src[1] + (a.dst[1] - a.src[1]) * t;
        const [sx, sy] = this.toScreen(px, py);
        ctx.fillStyle = itemColor(a.held);
        ctx.beginPath(); ctx.arc(sx, sy, r, 0, 6.2832); ctx.fill();
      }
    }

    // numbers
    if (this.showNumbers && this.tileRates && s >= 26) {
      const px = Math.max(8, Math.min(14, s * 0.3));
      ctx.font = `${px}px ui-monospace, Menlo, Consolas, monospace`;
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      for (const [k, v] of this.tileRates) {
        const [x, y] = k.split(',').map(Number);
        if (!vis(x, y) || v < 0.005) continue;
        const [sx, sy] = this.toScreen(x, y);
        const t = fmtRate(v);
        const tw = ctx.measureText(t).width + 4;
        ctx.fillStyle = 'rgba(8,9,12,0.78)';
        ctx.fillRect(sx - tw / 2, sy + s * 0.18 - px * 0.6, tw, px * 1.2);
        ctx.fillStyle = v > 480.5 ? '#ff6b5f' : '#e8ebf2';
        ctx.fillText(t, sx, sy + s * 0.18);
      }
    }

    // output labels, numbered as in the Outputs list
    if (s >= 10) {
      let i = 0;
      ctx.font = `${Math.max(8, Math.min(12, s * 0.28))}px ui-monospace, monospace`;
      ctx.textAlign = 'center'; ctx.textBaseline = 'bottom';
      for (const g of w.segs) {
        if (!(g.target && g.target.sink)) continue;
        i++;
        const n = g.nodes[g.nodes.length - 1];
        if (!vis(n.x, n.y)) continue;
        const [sx, sy] = this.toScreen(n.x, n.y);
        ctx.fillStyle = '#ff8a80';
        ctx.fillText('out ' + i, sx, sy - s * 0.3);
      }
    }

    if (this.pick) {
      const [sx, sy] = this.toScreen(this.pick[0], this.pick[1]);
      ctx.strokeStyle = '#ffd34d'; ctx.lineWidth = 2;
      ctx.strokeRect(sx - s / 2, sy - s / 2, s, s);
    }
  }

  // The game's hidden structure: the belt quadtree's cells (arms look belts up through it), every belt piece (one
  // segment = one polyline items move along, including the short output stubs inside a splitter tile), and which
  // pieces sit on a split line (those are listed first, so an arm dropping there uses them: a dead splitter).
  _drawPieces(vx0, vy0, vx1, vy1) {
    const ctx = this.ctx, s = this.scale, w = this.world;
    const qt = w.qt;
    if (qt) {
      ctx.lineWidth = 1;
      const [ox, oy] = w.qtOff || [0, 0];           // the tree is in world coordinates, the view in build tiles
      for (const [n, d] of qt.cells([vx0 + ox, vy0 + oy, vx1 + ox, vy1 + oy])) {
        if (!n.kids) continue;                       // draw the split lines of every split cell
        const x0 = n.b[0] - ox, y0 = n.b[1] - oy, x1 = n.b[2] - ox, y1 = n.b[3] - oy;
        const mx = n.kids[0].b[2] - ox, my = n.kids[0].b[3] - oy;
        ctx.strokeStyle = `hsla(${(d * 47) % 360},80%,62%,0.75)`;
        ctx.setLineDash(d < 3 ? [] : [4, 3]);
        ctx.beginPath();
        let [a, b] = this.toScreen(mx, Math.max(y0, vy0)), [c, e] = this.toScreen(mx, Math.min(y1, vy1));
        ctx.moveTo(a, b); ctx.lineTo(c, e);
        [a, b] = this.toScreen(Math.max(x0, vx0), my); [c, e] = this.toScreen(Math.min(x1, vx1), my);
        ctx.moveTo(a, b); ctx.lineTo(c, e);
        ctx.stroke();
        if (s >= 14) {
          ctx.fillStyle = ctx.strokeStyle; ctx.font = '10px ui-monospace, monospace'; ctx.textAlign = 'left'; ctx.textBaseline = 'top';
          const [tx, ty] = this.toScreen(Math.max(mx, vx0 + 0.6), Math.max(my, vy0 + 0.6));
          ctx.fillText(`split ${d} at world x ${n.kids[0].b[2]}, y ${n.kids[0].b[3]}`, tx + 3, ty + 2);
        }
      }
      ctx.setLineDash([]);
      // the grid cell of each piece on the picked tile
      for (const n of this.pickCells || []) {
        const [a, b] = this.toScreen(n.b[0] - ox, n.b[1] - oy), [c, e] = this.toScreen(n.b[2] - ox, n.b[3] - oy);
        const W = this.canvas.width, H = this.canvas.height;
        ctx.strokeStyle = '#ffd34d'; ctx.lineWidth = 2; ctx.setLineDash([6, 4]);
        ctx.strokeRect(Math.max(a, -10), Math.max(b, -10), Math.min(c, W + 10) - Math.max(a, -10), Math.min(e, H + 10) - Math.max(b, -10));
        ctx.setLineDash([]);
      }
    }
    ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    for (const g of w.segs) {
      if (!g.pts.some(p => p[0] >= vx0 && p[0] <= vx1 && p[1] >= vy0 && p[1] <= vy1)) continue;
      const hue = (g.idx * 137.5) % 360;
      const loc = qt && qt.locate(g);
      ctx.strokeStyle = `hsl(${hue},85%,60%)`;
      ctx.lineWidth = Math.max(1.5, s * (g.nodes.length === 2 && World.isSplit(g.nodes[0]) ? 0.12 : 0.07));
      ctx.setLineDash(g.flag ? [] : [3, 4]);
      ctx.beginPath();
      g.pts.forEach((p, i) => { const [sx, sy] = this.toScreen(p[0], p[1]); i ? ctx.lineTo(sx, sy) : ctx.moveTo(sx, sy); });
      ctx.stroke();
      ctx.setLineDash([]);
      const [hx, hy] = this.toScreen(g.pts[0][0], g.pts[0][1]);
      ctx.fillStyle = ctx.strokeStyle;
      ctx.beginPath(); ctx.arc(hx, hy, Math.max(2, s * 0.07), 0, 6.2832); ctx.fill();
      const pe = g.pts[g.pts.length - 1], pb = g.pts[Math.max(0, g.pts.length - 2)];
      const [ex, ey] = this.toScreen(pe[0], pe[1]), [bx, by] = this.toScreen(pb[0], pb[1]);
      const an = Math.atan2(ey - by, ex - bx), al = Math.max(4, s * 0.14);
      ctx.beginPath(); ctx.moveTo(ex, ey);
      ctx.lineTo(ex - al * Math.cos(an - 0.5), ey - al * Math.sin(an - 0.5));
      ctx.lineTo(ex - al * Math.cos(an + 0.5), ey - al * Math.sin(an + 0.5)); ctx.closePath(); ctx.fill();
      if (loc && loc.onSplit) {                    // on a split line: drawn with its box
        const [x0, y0, x1, y1] = g.bbox;
        const [a, b] = this.toScreen(x0, y0), [c, e] = this.toScreen(x1, y1);
        ctx.strokeStyle = '#ffb02e'; ctx.lineWidth = 1.5; ctx.setLineDash([2, 2]);
        ctx.strokeRect(a - 3, b - 3, c - a + 6, e - b + 6); ctx.setLineDash([]);
      }
      if (s >= 30) {
        const m = g.pointAt(g.L / 2), [sx, sy] = this.toScreen(m[0], m[1]);
        ctx.font = '9px ui-monospace, monospace'; ctx.textAlign = 'center'; ctx.textBaseline = 'bottom';
        ctx.fillStyle = `hsl(${hue},85%,75%)`;
        ctx.fillText('#' + g.idx, sx, sy - s * 0.12);
      }
    }
  }

  _dim(hex) {
    const n = parseInt(hex.slice(1), 16);
    const r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
    return `rgba(${r},${g},${b},0.55)`;
  }

  _arrow(sx, sy, d, s, col, sub) {
    const ctx = this.ctx;
    const [dx, dy] = DV[d];
    const a = s * 0.22;
    ctx.strokeStyle = col; ctx.lineWidth = Math.max(1, s * 0.07);
    ctx.beginPath();
    const tx = sx + dx * a, ty = sy + dy * a;
    ctx.moveTo(tx - dx * a * 1.4 - dy * a, ty - dy * a * 1.4 + dx * a);
    ctx.lineTo(tx, ty);
    ctx.lineTo(tx - dx * a * 1.4 + dy * a, ty - dy * a * 1.4 - dx * a);
    ctx.stroke();
    if (sub === 2 || sub === 3) {        // underground: bar on the tunnel side
      const off = sub === 2 ? 1 : -1;
      ctx.beginPath();
      ctx.moveTo(sx + dx * s * 0.42 * off - dy * s * 0.35, sy + dy * s * 0.42 * off + dx * s * 0.35);
      ctx.lineTo(sx + dx * s * 0.42 * off + dy * s * 0.35, sy + dy * s * 0.42 * off - dx * s * 0.35);
      ctx.stroke();
    }
  }

  _drawBuilding(b) {
    const ctx = this.ctx, s = this.scale;
    const fp = footprint(b);
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const [x, y] of fp) { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); }
    const [sx0, sy0] = this.toScreen(x0 - 0.5, y0 - 0.5), [sx1, sy1] = this.toScreen(x1 + 0.5, y1 + 0.5);
    if (b.id === 3012) {                      // arm
      const [sx, sy] = this.toScreen(b.x, b.y);
      ctx.fillStyle = '#3a3320';
      ctx.fillRect(sx - s * 0.4, sy - s * 0.4, s * 0.8, s * 0.8);
      const [dx, dy] = DV[b.o];
      ctx.strokeStyle = '#d8b84a'; ctx.lineWidth = Math.max(1, s * 0.08);
      ctx.beginPath(); ctx.moveTo(sx - dx * s * 0.35, sy - dy * s * 0.35); ctx.lineTo(sx + dx * s * 0.35, sy + dy * s * 0.35); ctx.stroke();
      ctx.beginPath(); ctx.arc(sx + dx * s * 0.35, sy + dy * s * 0.35, Math.max(1, s * 0.08), 0, 6.2832); ctx.fillStyle = '#d8b84a'; ctx.fill();
      return;
    }
    const st = BUILDING_STYLE[b.id] || ['#333', '?'];
    const pad = Math.max(0.5, s * 0.05);
    ctx.fillStyle = st[0];
    ctx.fillRect(sx0 + pad, sy0 + pad, sx1 - sx0 - 2 * pad, sy1 - sy0 - 2 * pad);
    ctx.strokeStyle = 'rgba(255,255,255,0.12)'; ctx.lineWidth = 1;
    ctx.strokeRect(sx0 + pad, sy0 + pad, sx1 - sx0 - 2 * pad, sy1 - sy0 - 2 * pad);
    const m = this.world.bobj && this.world.bobj.get(b);
    const label = m && m.outItem ? shortName(m.outItem) : (m && m.item ? shortName(m.item) : st[1]);
    const w = sx1 - sx0;
    if (w >= 14) {
      const px = Math.min(16, Math.max(7, w * 0.22));
      ctx.font = `600 ${px}px system-ui, sans-serif`;
      ctx.fillStyle = m && m.outItem ? itemColor(m.outItem) : '#c9ced8';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(label, (sx0 + sx1) / 2, (sy0 + sy1) / 2 - (w > 40 ? px * 0.5 : 0));
    }
  }
}
