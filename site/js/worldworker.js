// Runs the world generator off the main thread.
import { generator } from './worldgen.js';

self.onmessage = async ({ data: q }) => {
  try {
    const g = await generator(q.mode);
    if (q.op === 'nodes') {
      const nodes = g(q.seed, q.ws, q.ra);
      self.postMessage({ id: q.id, nodes, half: g.half(q.seed, q.ws, q.ra) }, [nodes.buffer]);
    } else {
      const t = g.terrain(q.seed, q.ws, q.ra, q.x0, q.y0, q.w, q.h, q.step);
      self.postMessage({ id: q.id, terrain: t, step: q.step, x0: q.x0, y0: q.y0, w: q.w, h: q.h }, [t.buffer]);
    }
  } catch (e) { self.postMessage({ id: q.id, error: String(e.message || e) }); }
};
