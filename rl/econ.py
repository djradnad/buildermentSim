"""Speedrun economy environment for Builderment, batched over many worlds at once (NumPy).

Stage 1 of the RL plan (see rl/README.md). The agent decides WHAT to build and WHEN: extractors on real Gen 2
resource nodes, machines per recipe, tier upgrades, labs and gold vaults. Layout is abstracted: every building's
belts are paid for (gold per tile), but routing, merging and compactness come in stage 2 (rl/layout.py).

Exact parts, from the game's tables (rl/data/tables.json):
  tech tree (93 techs: costs, prerequisites, unlocks, upgrade tiers), build and upgrade costs, gold per item
  (SellValue), recipe rates from exact tick periods (tools/recipe_rates.mjs), Gen 2 extractor rates.
Assumptions still to confirm with john / trace from the exe (each is a SETTINGS switch):
  start_gold, lab position, labs take 4 belts each, every item into a lab gives its SellValue in gold,
  research progress goes to every available tech that needs the item ('all') or only the chosen one ('select').

Time is event-free: the steady flow is recomputed after every change, then integrated over the wait. Long-run
settled rates are what john cares about; fill-up transients are ignored (a known optimism, a few seconds per build).
"""
import json
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
T = json.load(open(os.path.join(HERE, 'data', 'tables.json')))

SETTINGS = dict(
    gen='gen2', boost='0', world='2CCJhI', start_gold=2000, start_labs=0, first_lab_free=False, lab_xy=(0, 0),
    act_seconds=1.0,          # game time a build/upgrade action costs (player taps)
    wait_seconds=10.0,        # one "wait" step
    max_minutes=600.0, goal='first_et', progress='all', lab_inputs=4, belt_tiles_per_building=6,
    tech_bonus=0.2,           # shaping reward per tech researched (0 = pure -time)
    milestone_bonus=1.0,      # reward per item type produced for the first time (john's 'time to first item' card)
)

ITEMS = T['items']
IX = {n: i for i, n in enumerate(ITEMS)}
NI = len(ITEMS)
RECIPES = sorted((dict(r, id=int(k)) for k, r in T['recipes'].items()), key=lambda r: r['id'])
NR = len(RECIPES)
RES_FLAGS = [1, 2, 4, 8, 16, 32, 64]
RES_ITEM = [IX[T['resources'][str(f)]] for f in RES_FLAGS]
NX = len(RES_FLAGS)
CLASSES = ['building_extractor', 'factory_workshop', 'factory_furnace', 'factory_machine_shop', 'factory_forge',
           'factory_industrial', 'factory_manufacturer']
NK = len(CLASSES)
TECHS = T['techs']
NT = len(TECHS)
TX = {t['id']: i for i, t in enumerate(TECHS)}
SELL = np.array([T['sell'].get(n, 0) for n in ITEMS], np.float64)
BCOST = {k: v['cost'].get('Gold', 0) for k, v in T['buildings'].items()}
UPCOST = {k: [u.get('Gold', 0) for u in v['upgrades']] for k, v in T['buildings'].items()}

# recipe matrices
R_IN = np.zeros((NR, NI))
R_OUT = np.zeros((NR, NI))
R_CLASS = np.zeros(NR, np.int64)
R_TELE = np.zeros(NR, bool)
for j, r in enumerate(RECIPES):
    for n, a in r['in'].items(): R_IN[j, IX[n]] = a
    for n, a in r['out'].items(): R_OUT[j, IX[n]] = a
    R_TELE[j] = r['building'] == 'building_teleporter'
    R_CLASS[j] = CLASSES.index(r['building']) if not R_TELE[j] else -1


def crafts_per_min(boost):
    """(NR, 4) crafts per minute per building tier: exact repeating tick period from the rate tables."""
    c = np.zeros((NR, 4))
    for j, r in enumerate(RECIPES):
        per = r['period'][boost]
        for tier in range(4):
            p = per.get(str(tier + 1)) or per.get(str(1))
            c[j, tier] = p['crafts'] * 3600.0 / p['ticks']
    return c


# unlocks
TECH_UNLOCK_RECIPE = np.zeros((NT, NR), bool)
TECH_UNLOCK_BLD = {}
TECH_UPGRADE = {}                      # tech index -> (class index, tier)
RID = {r['id']: j for j, r in enumerate(RECIPES)}
BID = {v['id']: k for k, v in T['buildings'].items()}
for i, t in enumerate(TECHS):
    for m in t['modifiers']:
        if 'UnlockedRecipeId' in m and m['UnlockedRecipeId'] in RID: TECH_UNLOCK_RECIPE[i, RID[m['UnlockedRecipeId']]] = True
        if 'UnlockedBuildingId' in m: TECH_UNLOCK_BLD.setdefault(i, []).append(BID.get(m['UnlockedBuildingId']))
        if 'UpgradeBuildingId' in m:
            k = BID.get(m['UpgradeBuildingId'])
            if k in CLASSES: TECH_UPGRADE[i] = (CLASSES.index(k), m['UpgradeBuildingTier'])
TECH_REQ = np.zeros((NT, NT), bool)
for i, t in enumerate(TECHS):
    for r in t['requires']: TECH_REQ[i, TX[r]] = True
TECH_COST = np.zeros((NT, NI))
for i, t in enumerate(TECHS):
    for n, a in t['cost'].items():
        if n in IX: TECH_COST[i, IX[n]] = a
TECH_GOLD_COST = np.array([t['cost'].get('Gold', 0) for t in TECHS], np.float64)   # gold vault tech: paid in gold
CLASS_UNLOCK_TECH = {}
for i, bl in TECH_UNLOCK_BLD.items():
    for k in bl:
        if k in CLASSES: CLASS_UNLOCK_TECH[CLASSES.index(k)] = i
LAB_TECH = TX[4000]
VAULT_TECH = TX[4090]
URANIUM_TECH = TX[4087]
TELE_TECH = TX[4026]


def _item_order():
    depth = {n: 0 for n in ITEMS}
    for _ in range(NI):
        for r in RECIPES:
            d = 1 + max(depth[n] for n in r['in'])
            for n in r['out']: depth[n] = max(depth[n], d)
    return sorted(range(NI), key=lambda i: depth[ITEMS[i]])


ITEM_ORDER = _item_order()

# actions
A_WAIT = 0                               # waits: 0 short, 1 one minute, 2 five minutes
WAITS = (None, 60.0, 300.0)              # None = SETTINGS wait_seconds
A_BUILD = len(WAITS)                     # + recipe index
A_EXTRACT = A_BUILD + NR                 # + resource index
A_UPGRADE = A_EXTRACT + NX               # + class index
A_LAB = A_UPGRADE + NK
A_VAULT = A_LAB + 1
NA = A_VAULT + 1


class EconEnv:
    """Batch of B independent speedruns. reset() -> obs (B, D); step(actions (B,)) -> obs, reward, done, info.
    action_mask() gives the legal actions; illegal actions act as wait."""

    def __init__(self, batch=64, seed=0, **kw):
        self.cfg = dict(SETTINGS, **kw)
        self.B = batch
        self.rng = np.random.default_rng(seed)
        self.cpm = crafts_per_min(self.cfg['boost'])
        self.ext_rate = np.array(T['extractor_per_min'][self.cfg['gen']], np.float64)
        worlds = json.load(open(os.path.join(HERE, 'data', 'worlds.json')))
        n = np.array(worlds[self.cfg['world']]['nodes']).reshape(-1, 3)
        lx, ly = self.cfg['lab_xy']
        self.node_dist = []                                 # per resource: sorted tile distances to the lab
        for f in RES_FLAGS:
            m = n[:, 2] == f
            self.node_dist.append(np.sort(np.abs(n[m, 0] - lx) + np.abs(n[m, 1] - ly)).astype(np.float64))
        g = self.cfg['goal']
        self.goal_item = IX['Earth Token'] if g == 'first_et' else None
        self.goal = TX[4026] if g in ('first_et', 'end_game') else TX[g]
        self.reset()

    # ---------------- state ----------------
    def reset(self):
        B = self.B
        self.t = np.zeros(B)                                 # game seconds
        self.gold = np.full(B, float(self.cfg['start_gold']))
        self.done_t = np.zeros((B, NT), bool)
        self.done_t[:, LAB_TECH] = True                      # starting tech
        self.prog = np.zeros((B, NT, NI))
        self.n_rec = np.zeros((B, NR))
        self.n_ext = np.zeros((B, NX), np.int64)
        self.tier = np.zeros((B, NK), np.int64)              # current tier index of every building in the class
        self.labs = np.full(B, self.cfg['start_labs'], np.int64)
        self.vaults = np.zeros(B, np.int64)
        self.finished = np.zeros(B, bool)
        self.first = np.full((B, NI), np.inf)               # game second each item was first made (milestones)
        self._flow()
        return self.obs()

    def reset_where(self, m):
        """restart the runs in boolean mask m (auto-reset for training)"""
        if not m.any():
            return
        self.t[m] = 0; self.gold[m] = self.cfg['start_gold']
        self.done_t[m] = False; self.done_t[m, LAB_TECH] = True
        self.prog[m] = 0; self.n_rec[m] = 0; self.n_ext[m] = 0; self.tier[m] = 0
        self.labs[m] = self.cfg['start_labs']; self.vaults[m] = 0; self.finished[m] = False; self.first[m] = np.inf
        self._flow()

    def _available(self):
        """techs whose prerequisites are all researched and that are not researched yet"""
        req_ok = ~((TECH_REQ[None] & ~self.done_t[:, None, :]).any(-1))
        return req_ok & ~self.done_t

    def _flow(self):
        """steady state per minute: machine utilisation, lab deliveries, gold income"""
        B = self.B
        rate = self.cpm[np.arange(NR)[None], np.clip(self.tier[:, np.maximum(R_CLASS, 0)], 0, 3)]   # (B, NR)
        rate = np.where(R_TELE[None], self.cpm[:, 0][None], rate)
        crafts = self.n_rec * rate                           # nominal crafts/min
        sup = np.zeros((B, NI))
        for x in range(NX):
            k = self.n_ext[:, x]
            if x == 6:
                sup[:, RES_ITEM[x]] += k * T['uranium_per_min'][self.cfg['gen']]
            else:
                sup[:, RES_ITEM[x]] += k * self.ext_rate[self.tier[:, 0]]
        util = np.ones((B, NR))
        share = np.ones((B, NI))
        for i in ITEM_ORDER:
            prod = (crafts * util * R_OUT[:, i]).sum(1)
            sup[:, i] += prod
            dem = (crafts * R_IN[:, i]).sum(1)
            share[:, i] = np.where(dem > 0, np.minimum(1.0, sup[:, i] / np.maximum(dem, 1e-12)), 1.0)
            users = R_IN[:, i] > 0
            util[:, users] = np.minimum(util[:, users], share[:, i][:, None])
        self.made = sup.copy()                               # items/min made of each item (incl. raw)
        per_machine = np.where(self.n_rec > 0, rate, 0)
        # minutes until a running machine finishes its first craft: one craft period of the fastest active one
        act = (crafts * util) > 0
        self.first_lag = np.full((B, NI), np.inf)
        for j in range(NR):
            lag = np.where(act[:, j], 1.0 / np.maximum(per_machine[:, j], 1e-12), np.inf)
            for i in np.nonzero(R_OUT[j])[0]:
                self.first_lag[:, i] = np.minimum(self.first_lag[:, i], lag)
        for x in range(NX):
            r = T['uranium_per_min'][self.cfg['gen']] if x == 6 else self.ext_rate[self.tier[:, 0]]
            self.first_lag[:, RES_ITEM[x]] = np.where(self.n_ext[:, x] > 0, 1.0 / r, np.inf)
        used = (crafts * util) @ R_IN
        surplus = np.maximum(sup - used, 0.0)
        cap = (self.labs + self.vaults) * self.cfg['lab_inputs'] * T['belt_per_min']
        tot = surplus.sum(1)
        f = np.where(tot > cap, cap / np.maximum(tot, 1e-12), 1.0)
        self.deliver = surplus * f[:, None]                  # items/min into labs and vaults
        self.lab_share = np.where(self.labs + self.vaults > 0, self.labs / np.maximum(self.labs + self.vaults, 1), 0)
        self.gold_rate = (self.deliver * SELL).sum(1)
        self.util = util

    # ---------------- costs ----------------
    def _build_cost(self, cls):
        """gold for one more building of class index cls at the class's current tier (base + upgrades so far)"""
        base = BCOST[CLASSES[cls]]
        ups = np.cumsum([0] + UPCOST[CLASSES[cls]])
        return base + ups[self.tier[:, cls]]

    def _ext_cost(self, x):
        k = self.n_ext[:, x]
        d = self.node_dist[x]
        dist = np.where(k < len(d), d[np.minimum(k, len(d) - 1)], np.inf)
        base = BCOST['building_uranium_extractor'] if x == 6 else self._build_cost(0)
        return base + T['buildings']['belt_straight']['cost']['Gold'] * dist

    def _lab_cost(self):
        # john 2026-11-16: start with 2000 gold and no lab; the tutorial has you place one (1000 gold)
        free = self.cfg['first_lab_free'] & (self.labs == 0)
        return np.where(free, 0.0, BCOST['research_lab'])

    def _belt_gold(self):
        return T['buildings']['belt_straight']['cost']['Gold'] * self.cfg['belt_tiles_per_building']

    def _n_class(self, k):
        if k == 0: return self.n_ext[:, :6].sum(1)
        return self.n_rec[:, R_CLASS == k].sum(1)

    def _up_cost(self, k):
        ups = np.array(UPCOST[CLASSES[k]] + [np.inf])
        return ups[np.minimum(self.tier[:, k], len(ups) - 1)] * np.maximum(self._n_class(k), 0)

    def _unlocked_tier(self, k):
        t = np.zeros(self.B, np.int64)
        for i, (kk, tier) in TECH_UPGRADE.items():
            if kk == k: t = np.where(self.done_t[:, i], np.maximum(t, tier), t)
        return t

    def action_mask(self):
        B = self.B
        m = np.zeros((B, NA), bool)
        m[:, :A_BUILD] = True
        rec_ok = (self.done_t[:, :, None] & TECH_UNLOCK_RECIPE[None]).any(1)
        for j in range(NR):
            if R_TELE[j]:
                ok = rec_ok[:, j] & self.done_t[:, TELE_TECH] & (self.gold >= BCOST['building_teleporter'])
            else:
                k = R_CLASS[j]
                unlocked = self.done_t[:, CLASS_UNLOCK_TECH[k]] if k in CLASS_UNLOCK_TECH else True
                ok = rec_ok[:, j] & unlocked & (self.gold >= self._build_cost(k) + self._belt_gold())
            m[:, A_BUILD + j] = ok
        for x in range(NX):
            ok = self.gold >= self._ext_cost(x)
            if x == 6: ok &= self.done_t[:, URANIUM_TECH]
            m[:, A_EXTRACT + x] = ok
        for k in range(NK):
            m[:, A_UPGRADE + k] = (self._unlocked_tier(k) > self.tier[:, k]) & (self.gold >= self._up_cost(k)) \
                & (self._n_class(k) > 0)
        m[:, A_LAB] = (self.labs < 3) & (self.gold >= self._lab_cost())
        m[:, A_VAULT] = (self.vaults < 3) & self.done_t[:, VAULT_TECH] & (self.gold >= BCOST['building_gold_vault'])
        m[self.finished] = False
        m[self.finished, A_WAIT] = True
        return m

    # ---------------- step ----------------
    def step(self, a):
        a = np.asarray(a)
        B = self.B
        legal = self.action_mask()[np.arange(B), a]
        a = np.where(legal, a, A_WAIT)
        live = ~self.finished
        changed = np.zeros(B, bool)
        for j in range(NR):
            s = (a == A_BUILD + j) & live
            if s.any():
                cost = BCOST['building_teleporter'] if R_TELE[j] else self._build_cost(R_CLASS[j]) + self._belt_gold()
                self.gold -= np.where(s, cost, 0)
                self.n_rec[s, j] += 1
                changed |= s
        for x in range(NX):
            s = (a == A_EXTRACT + x) & live
            if s.any():
                self.gold -= np.where(s, self._ext_cost(x), 0)
                self.n_ext[s, x] += 1
                changed |= s
        for k in range(NK):
            s = (a == A_UPGRADE + k) & live
            if s.any():
                self.gold -= np.where(s, self._up_cost(k), 0)
                self.tier[s, k] += 1
                changed |= s
        s = (a == A_LAB) & live
        self.gold -= np.where(s, self._lab_cost(), 0); self.labs += s; changed |= s
        s = (a == A_VAULT) & live
        self.gold -= np.where(s, BCOST['building_gold_vault'], 0); self.vaults += s; changed |= s
        if changed.any():
            self._flow()
        wait = np.array([self.cfg['wait_seconds'] if w is None else w for w in WAITS])
        dt = np.where(a < A_BUILD, wait[np.minimum(a, A_BUILD - 1)], self.cfg['act_seconds']) * live
        before = self.done_t.sum(1)
        self._advance(dt)
        newly = self.done_t.sum(1) - before
        self.t += dt
        # milestones: an item counts as made one craft period after its machine starts running (settled view)
        cand = np.where(np.isfinite(self.first_lag), self.t[:, None] - dt[:, None] + self.first_lag * 60, np.inf)
        new_first = np.isinf(self.first) & (cand <= self.t[:, None]) & live[:, None]
        self.first = np.where(new_first, cand, self.first)
        n_first = new_first.sum(1)
        if self.goal_item is not None:
            fin = np.isfinite(self.first[:, self.goal_item]) & live
        else:
            fin = self.done_t[:, self.goal] & live
        trunc = (self.t >= self.cfg['max_minutes'] * 60) & live & ~fin
        reward = -dt / 60.0 + self.cfg['tech_bonus'] * newly + self.cfg['milestone_bonus'] * n_first
        # bankrupt: no income and nothing affordable, so the run can never move again; end it now and charge the
        # rest of the time cap (the game allows this dead end too, e.g. a second lab bought with the start gold)
        stuck = live & ~fin & ~trunc & (self.gold_rate <= 0) & ~self.action_mask()[:, A_BUILD:].any(1)
        reward = reward - stuck * np.maximum(self.cfg['max_minutes'] - self.t / 60.0, 0)
        trunc = trunc | stuck
        self.finished |= fin | trunc
        return self.obs(), reward, fin | trunc, dict(finished=fin, truncated=trunc, minutes=self.t / 60)

    def _advance(self, dt):
        mins = dt / 60.0
        self.gold += self.gold_rate * mins
        lab_items = self.deliver * (self.lab_share * mins)[:, None]           # (B, NI) items into labs
        avail = self._available()
        need = np.maximum(TECH_COST[None] - self.prog, 0) * avail[:, :, None]
        if self.cfg['progress'] == 'all':
            self.prog += np.minimum(need, lab_items[:, None, :])
        else:
            raise NotImplementedError('select mode: add a research-choice action')
        gold_ok = self.gold[:, None] >= TECH_GOLD_COST[None]
        complete = avail & ((self.prog >= TECH_COST[None] - 1e-9).all(-1)) & gold_ok
        if complete.any():
            self.gold -= (complete * TECH_GOLD_COST[None]).sum(1)
            self.done_t |= complete
            self._flow()

    def obs(self):
        lg = lambda v: np.log1p(np.maximum(v, 0))
        frac = (self.prog / np.maximum(TECH_COST[None], 1)).mean(-1)
        return np.concatenate([
            lg(self.gold)[:, None], (self.t / 3600.0)[:, None], lg(self.gold_rate)[:, None],
            self.done_t.astype(np.float64), frac * self._available(), lg(self.n_rec), lg(self.n_ext),
            self.tier / 4.0, self.labs[:, None] / 3.0, self.vaults[:, None] / 3.0, lg(self.deliver),
            self.util, np.isfinite(self.first),
        ], 1).astype(np.float32)


OBS_DIM = None


def obs_dim():
    global OBS_DIM
    if OBS_DIM is None:
        OBS_DIM = EconEnv(batch=1).obs().shape[1]
    return OBS_DIM
