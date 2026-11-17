"""Macro-action speedrun env: the AI picks strategy, a rate calculator does the placing.

Plain EconEnv needs ~7000 one-building decisions per run, too long for PPO to credit (as in Factoriax). Here one
decision is "add a production line for recipe r" (x1 or x4): the top machine plus every machine and extractor the
exact rates say it needs underneath (main recipes, current tiers), added to a plan. The env then builds the plan's
shortfalls shallow-first whenever gold allows (1 s of game time per building, as before) and lets time run.
Other decisions: upgrade a class, lab, vault, gem trade, remove a top machine, wait (until the next event: a planned
build becomes affordable, a research completes or an item is first made; or 1 min; or 5 min).
A run is a few hundred decisions."""
import numpy as np

from . import econ as E

M_WAIT = 0                                   # 3 waits as in econ
M_ADD1 = len(E.WAITS)                        # + recipe
M_ADD4 = M_ADD1 + E.NR
M_UP = M_ADD4 + E.NR                         # + class
M_LAB = M_UP + E.NK
M_VAULT = M_LAB + 1
M_GEMS = M_VAULT + 1
M_DEL = M_GEMS + 1                           # + recipe
M_X1 = M_DEL + E.NR                          # + resource: one more extractor in the plan (raw straight to the lab)
M_X4 = M_X1 + E.NX
M_PRE = M_X4 + E.NX                          # + resource: place an extractor with no belt (fills its buffer)
M_CONN = M_PRE + E.NX                        # + resource: belt every waiting extractor of it (buffers arrive)
M_LABAT = M_CONN + E.NX                      # + site: a lab at lab site 1..2 (M_LAB = site 0)
M_DLAB = M_LABAT + E.NS                      # + site: delete a lab there (runners move the lab, john 2026-11-17)
NM = M_DLAB + E.NS

MAIN = {}
for _j, _r in enumerate(E.RECIPES):
    if not _r['alt'] and not E.R_TELE[_j]:
        for _n in _r['out']:
            MAIN[E.IX[_n]] = _j
RAW = {it: x for x, it in enumerate(E.RES_ITEM)}
DEPTH_ORDER = [i for i in E.ITEM_ORDER]
# build order: extractors first, then recipes by the depth of what they make
BUILD_ORDER = [('x', x) for x in range(E.NX)] + sorted(
    [('r', j) for j in range(E.NR)], key=lambda t: DEPTH_ORDER.index(int(np.nonzero(E.R_OUT[t[1]])[0][0])))


class MacroEnv:
    def __init__(self, batch=64, seed=0, max_builds=64, **kw):
        kw.setdefault('max_steps', 10 ** 9)
        self.e = E.EconEnv(batch=batch, seed=seed, **kw)
        self.B = batch
        self.max_builds = max_builds
        self.cfg = self.e.cfg
        self.decisions = np.zeros(batch, np.int64)
        self.reset()

    def reset(self):
        self.e.reset()
        self.plan_r = np.zeros((self.B, E.NR))
        self.plan_x = np.zeros((self.B, E.NX))
        self.decisions[:] = 0
        return self.obs()

    def reset_where(self, m):
        if not m.any():
            return
        self.e.reset_where(m)
        self.plan_r[m] = 0; self.plan_x[m] = 0; self.decisions[m] = 0

    @property
    def finished(self):
        return self.e.finished

    @property
    def done_t(self):
        return self.e.done_t

    # ---------- planning ----------
    def _chain(self, b_mask, j, k):
        """add k top machines of recipe j and their full supply chain to the plan (rows b_mask)"""
        e = self.e
        tiers = e.tier
        cls = lambda jj: max(E.R_CLASS[jj], 0)
        rate = lambda jj: e.cpm[jj][np.minimum(tiers[:, cls(jj)], 3)] if not E.R_TELE[jj] else np.full(self.B, e.cpm[jj, 0])
        G = (k * rate(j))[:, None] * E.R_IN[j][None]                    # (B, NI) items/min needed
        add_r = np.zeros((self.B, E.NR)); add_r[:, j] += k
        for i in reversed(E.ITEM_ORDER):
            if i in RAW or i not in MAIN:
                continue
            g = G[:, i]
            if not (g > 0).any():
                continue
            jj = MAIN[i]
            n = g / (rate(jj) * E.R_OUT[jj, i])
            add_r[:, jj] += n
            G += (n * rate(jj))[:, None] * E.R_IN[jj][None]
        add_x = np.zeros((self.B, E.NX))
        for i, x in RAW.items():
            r = E.T['uranium_per_min'][self.cfg['gen']] if x == 6 else e.ext_rate[tiers[:, 0]]
            add_x[:, x] = G[:, i] / r
        self.plan_r += add_r * b_mask[:, None]
        self.plan_x += add_x * b_mask[:, None]

    def _deficit(self):
        dr = np.ceil(self.plan_r - 1e-6) - self.e.n_rec
        dx = np.ceil(self.plan_x - 1e-6) - self.e.n_ext
        return np.maximum(dr, 0), np.maximum(dx, 0)

    def _event_wait(self):
        """seconds until the next thing that can change a decision: a planned build becomes affordable, a research
        completes, or an item is made for the first time (1..300 s)"""
        e = self.e
        B = self.B
        best = np.full(B, np.inf)
        dr, dx = self._deficit()
        rate = np.maximum(e.gold_rate, 1e-9) / 60.0
        for kind, idx in BUILD_ORDER:
            need = (dx[:, idx] if kind == 'x' else dr[:, idx]) > 0
            if not need.any():
                continue
            if kind == 'x':
                cost = e._ext_cost(idx)
            elif E.R_TELE[idx]:
                cost = np.full(B, float(E.BCOST['building_teleporter']))
            else:
                cost = e._build_cost(E.R_CLASS[idx]) + e._belt_gold()
            t = np.where(need & (e.gold_rate > 0), np.maximum(cost - e.gold, 0) / rate, np.inf)
            best = np.minimum(best, t)
        avail = e._available()
        rem = np.maximum(E.TECH_COST[None] - e.prog, 0)
        lr = e.lab_in[:, None, :] / 60.0
        tt = np.where(rem > 0, np.where(lr > 0, rem / np.maximum(lr, 1e-12), np.inf), 0).max(-1)
        tt = np.where(avail & (E.TECH_COST.sum(1) > 0)[None], tt, np.inf).min(1)
        best = np.minimum(best, tt)
        for x in range(E.NX):
            c = e.rs_x[:, x] + e.x_period[:, x] * 60
            best = np.minimum(best, np.where(np.isinf(e.first[:, E.RES_ITEM[x]]), c - e.t, np.inf))
        for j in range(E.NR):
            ins = E.R_IN[j] > 0
            ready = np.max(np.where(ins[None], e.first, -np.inf), 1)
            c = np.maximum(e.rs_r[:, j], ready) + e.r_period[:, j] * 60
            outs = E.R_OUT[j] > 0
            pend = np.isinf(np.where(outs[None], e.first, 0)).any(1)
            best = np.minimum(best, np.where(pend, c - e.t, np.inf))
        best = np.where(np.isfinite(best), best, 10.0)      # nothing pending: give the turn back soon
        return np.clip(np.ceil(best), 1.0, 300.0)

    def _build_action(self):
        """per env: the next primitive build (shallow-first shortfall that is affordable), else -1"""
        m = self.e.action_mask()
        dr, dx = self._deficit()
        a = np.full(self.B, -1)
        for kind, idx in BUILD_ORDER:
            need = (dx[:, idx] if kind == 'x' else dr[:, idx]) > 0
            if kind == 'x':
                act = np.where(self.e.n_free[:, idx] > 0, E.A_CONN + idx, E.A_EXTRACT + idx)
            else:
                act = np.full(self.B, E.A_BUILD + idx)
            ok = need & m[np.arange(self.B), act] & (a < 0)  # any affordable shortfall, shallow first
            a = np.where(ok, act, a)
        return a

    # ---------- interface ----------
    def action_mask(self):
        e = self.e
        m = np.zeros((self.B, NM), bool)
        em = e.action_mask()
        m[:, :M_ADD1] = True
        rec_ok = (e.done_t[:, :, None] & E.TECH_UNLOCK_RECIPE[None]).any(1)
        for j in range(E.NR):
            k = E.R_CLASS[j]
            unlocked = e.done_t[:, E.CLASS_UNLOCK_TECH[k]] if k in E.CLASS_UNLOCK_TECH else True
            if E.R_TELE[j]:
                unlocked = e.done_t[:, E.TELE_TECH]
            ok = rec_ok[:, j] & unlocked & (e.labs > 0)
            m[:, M_ADD1 + j] = ok
            m[:, M_ADD4 + j] = ok & ~E.R_TELE[j]
            m[:, M_DEL + j] = e.n_rec[:, j] > 0
        xok = (e.labs > 0)[:, None] & np.array([True] * 6 + [False])[None]
        xok[:, 6] = (e.labs > 0) & e.done_t[:, E.URANIUM_TECH]
        m[:, M_X1:M_X1 + E.NX] = xok
        m[:, M_X4:M_X4 + E.NX] = xok
        m[:, M_PRE:M_PRE + E.NX] = em[:, E.A_PREX:E.A_PREX + E.NX]
        m[:, M_CONN:M_CONN + E.NX] = em[:, E.A_CONN:E.A_CONN + E.NX]
        m[:, M_UP:M_UP + E.NK] = em[:, E.A_UPGRADE:E.A_UPGRADE + E.NK]
        m[:, M_LAB] = em[:, E.A_LAB] & ((e.labs == 0) | (e.gold_rate > 0))   # a 2nd lab with no income = dead end
        for i in range(1, E.NS):
            m[:, M_LABAT + i] = em[:, E.A_LABAT + i] & ((e.labs == 0) | (e.gold_rate > 0))
        m[:, M_DLAB:M_DLAB + E.NS] = em[:, E.A_DLAB:E.A_DLAB + E.NS]
        m[:, M_VAULT] = em[:, E.A_VAULT]
        m[:, M_GEMS] = em[:, E.A_GEMS]
        m[e.finished] = False
        m[e.finished, M_WAIT] = True
        return m

    def step(self, a):
        a = np.asarray(a)
        e = self.e
        B = self.B
        legal = self.action_mask()[np.arange(B), a]
        a = np.where(legal, a, M_WAIT)
        live = ~e.finished
        self.decisions += live
        reward = np.zeros(B)
        prim = np.full(B, E.A_WAIT)
        for j in range(E.NR):
            s1, s4 = (a == M_ADD1 + j) & live, (a == M_ADD4 + j) & live
            if s1.any(): self._chain(s1, j, 1)
            if s4.any(): self._chain(s4, j, 4)
            sd = (a == M_DEL + j) & live
            if sd.any():
                self.plan_r[sd, j] = np.maximum(self.plan_r[sd, j] - 1, 0)
                prim = np.where(sd, E.A_REMOVE + j, prim)
        for x in range(E.NX):
            self.plan_x[(a == M_X1 + x) & live, x] += 1
            self.plan_x[(a == M_X4 + x) & live, x] += 4
        for x in range(E.NX):
            prim = np.where((a == M_PRE + x) & live, E.A_PREX + x, prim)
            prim = np.where((a == M_CONN + x) & live, E.A_CONN + x, prim)
        for k in range(E.NK):
            prim = np.where((a == M_UP + k) & live, E.A_UPGRADE + k, prim)
        prim = np.where((a == M_LAB) & live, E.A_LAB, prim)
        for i in range(E.NS):
            prim = np.where((a == M_LABAT + i) & live, E.A_LABAT + i, prim)
            prim = np.where((a == M_DLAB + i) & live, E.A_DLAB + i, prim)
        prim = np.where((a == M_VAULT) & live, E.A_VAULT, prim)
        prim = np.where((a == M_GEMS) & live, E.A_GEMS, prim)
        done = np.zeros(B, bool)
        info = None
        t_before = e.t.copy()

        fin = np.zeros(B, bool); trunc = np.zeros(B, bool)

        def run(p, final=False):
            nonlocal done, info, reward
            if not final:                                    # runs that sit out an inner step take no time
                e.wait_override = np.zeros(B)
            p = np.where(done | ~live, E.A_WAIT, p)
            _, r, d, info = e.step(p)
            reward += np.where(done, 0, r)
            fin[:] |= info['finished'] & ~done; trunc[:] |= info['truncated'] & ~done
            done |= d

        direct = prim != E.A_WAIT
        if direct.any():
            run(np.where(direct, prim, E.A_WAIT) if True else prim)
        for x in range(E.NX):                                # "connect" means all waiting extractors of that resource
            for _ in range(32):
                go = (a == M_CONN + x) & live & ~done & (e.n_free[:, x] > 0) & e.action_mask()[:, E.A_CONN + x]
                if not go.any():
                    break
                run(np.where(go, E.A_CONN + x, E.A_WAIT))
        for _ in range(self.max_builds):                     # build the plan's shortfalls while gold allows
            b = self._build_action()
            go = (b >= 0) & ~done & live
            if not go.any():
                break
            run(np.where(go, b, E.A_WAIT) * go + E.A_WAIT * ~go)
        # waits advance time; any other decision ends right after its builds (1 s if it built nothing, so a run
        # can't stall time)
        is_wait = a < M_ADD1
        ev = self._event_wait()
        e.wait_override = np.where(is_wait, ev, np.where(t_before == e.t, 1.0, 0.0))
        run(np.where(is_wait, a, M_WAIT), final=True)
        e.wait_override = None
        return self.obs(), reward, done, dict(info, finished=fin, truncated=trunc, decisions=self.decisions.copy())

    def obs(self):
        dr, dx = self._deficit()
        return np.concatenate([self.e.obs(), np.log1p(dr), np.log1p(dx), np.log1p(self.plan_r)], 1).astype(np.float32)
