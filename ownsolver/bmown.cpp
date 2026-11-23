// bmown: a tile search solver for Builderment belt layouts (belts, splitters, hops), written for the game.
// Semantics follow bmsolver/model.py (pieces, links, ports) and bmsolver/check.py (exact steady-state flows).
// See TEAM_LOG.md "DESIGN NOTE" for the propagators and why each is sound.
#include <algorithm>
#include <random>
#include <functional>
#include <map>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

#include "json.hpp"
#include "rational.hpp"

QCtx qctx;

using namespace std;
typedef uint32_t Mask;

static const int MAXN = 4096;
static const int MAXC = 64;         // commodities
static const int REACH = 7;         // hop reach (exit 1..7 tiles ahead)
static const int DX[4] = {0, -1, 0, 1}, DY[4] = {-1, 0, 1, 0};
static const char KCH[4] = {'B', 'S', 'I', 'O'};
enum { KB = 0, KS = 1, KI = 2, KO = 3 };

static inline int VAL(int k, int o) { return 1 + k * 4 + o; }
static inline int KIND(int v) { return (v - 1) / 4; }
static inline int DIRV(int v) { return (v - 1) % 4; }
static inline Mask BIT(int v) { return (Mask)1 << v; }
static inline int OPP(int d) { return (d + 2) & 3; }
static const Mask BIT_E = 1;
static const Mask ALL = (1u << 17) - 1;

Mask PUSH[4], ACC[4];

static void init_masks() {
    for (int d = 0; d < 4; d++) {
        PUSH[d] = BIT(VAL(KB, d)) | BIT(VAL(KO, d)) | BIT(VAL(KS, d)) | BIT(VAL(KS, (d + 3) & 3)) | BIT(VAL(KS, (d + 1) & 3));
        ACC[d] = BIT(VAL(KS, d)) | BIT(VAL(KI, d));
        for (int o = 0; o < 4; o++)
            if (o != OPP(d)) ACC[d] |= BIT(VAL(KB, o));
    }
}
// directions value v pushes into (surface)
static inline int push_dirs(int v, int* out) {
    if (v == 0) return 0;
    int k = KIND(v), o = DIRV(v);
    if (k == KB || k == KO) { out[0] = o; return 1; }
    if (k == KS) { out[0] = o; out[1] = (o + 1) & 3; out[2] = (o + 3) & 3; return 3; }
    return 0;
}
static inline bool single(Mask m) { return m && !(m & (m - 1)); }
static inline int only(Mask m) { return __builtin_ctz(m); }
static inline int popc(Mask m) { return __builtin_popcount(m); }

// ------------------------------------------------------------------ problem
struct Port { int cell, d; int kind; long long vec[MAXC]; bool live; };   // live: some commodity > 0   // kind 1 in, 2 out

int W, H, N, NC;
long long D;
vector<Port> fixedPorts;
vector<vector<vector<Port>>> portGroups;     // groups -> options -> ports
vector<pair<int, Mask>> fixedTiles;

// active port layout
int portKind[MAXN][4];
long long portVec[MAXN][4][MAXC];
vector<Port> ins, outs;

static inline int nb(int p, int d) {
    int x = p % W + DX[d], y = p / W + DY[d];
    if (x < 0 || y < 0 || x >= W || y >= H) return -1;
    return y * W + x;
}
static inline int nbn(int p, int d, int n) {
    int x = p % W + DX[d] * n, y = p / W + DY[d] * n;
    if (x < 0 || y < 0 || x >= W || y >= H) return -1;
    return y * W + x;
}

// ------------------------------------------------------------------ state
Mask dom[MAXN];
vector<pair<int, Mask>> trail;
long long nodes = 0, fails = 0, leaves = 0, uncertain = 0;
chrono::steady_clock::time_point t0;
double timeout = 60;
bool use_lp = true, use_sat = true, use_bounds = true, lp_split = false;
int lp_every = 1, cdcl_lpevery = 5;
static size_t path_depth();
struct Lit;
extern vector<vector<pair<int, Mask>>> nogoods;
int fail_code = 0;   // which check failed last: 0 local, 1 reach, 2 lb, 3 ub, 4 maxflow, 5 lp
bool lp_nowarm = false;
static bool nogood_prop(bool& changed);
int lp_maxvars = 3000;
bool timed_out = false;

// ---- reasons (CDCL mode, --cdcl): every value removal is an event (cell, value) with a decision level and a reason.
// A reason is a list of pairs (cell q, mask M) meaning "the values M had been removed from q", or DEC (implied by
// the decisions up to the event's level: always true, used where no exact explanation is computed), or DECISION.
typedef vector<pair<int, Mask>> Pairs;
bool R_ON = false;
int curlvl = 0;
static int ev_lvl[MAXN * 17], ev_rs[MAXN * 17], ev_rl[MAXN * 17];   // rl: -1 decision, -2 DEC, >= 0 pairs in pool
vector<pair<int, Mask>> rpool;
vector<int> evtrail;
vector<int> trail_ev, trail_pool;
bool deciding = false;
// conflict reason of the last failure
bool CR_dec = true;
Pairs CRp;
static inline bool setdom(int p, Mask m, bool& changed, const Pairs* why = nullptr) {
    if (m == dom[p]) return true;
    if (!m) {
        if (R_ON) {
            if (why == nullptr || deciding) CR_dec = true;
            else { CR_dec = false; CRp = *why; CRp.push_back({p, ALL & ~dom[p]}); }
        }
        return false;
    }
    trail_ev.push_back(evtrail.size());
    trail_pool.push_back(rpool.size());
    trail.push_back({p, dom[p]});
    if (R_ON) {
        int rs = rpool.size(), rl;
        if (deciding) rl = -1;
        else if (why == nullptr) rl = -2;
        else { rl = why->size(); rpool.insert(rpool.end(), why->begin(), why->end()); }
        for (Mask r = dom[p] & ~m; r; r &= r - 1) {
            int ev = p * 17 + __builtin_ctz(r);
            ev_lvl[ev] = curlvl; ev_rs[ev] = rs; ev_rl[ev] = rl;
            evtrail.push_back(ev);
        }
    }
    dom[p] = m;
    changed = true;
    return true;
}
// ---- flow bounds (CDCL only): integer bounds fb[0][j] = hi, fb[1][j] = lo per flow variable j. Every change is an
// event (id BEV0 + index into bevs) with a reason, undone through the trail (entries with a negative cell id).
// Atom (aenc(j, side), b) in reasons and nogoods: "hi_j <= b" (side 0) or "lo_j >= b" (side 1). Flows are real,
// so the negation of an atom is strict: bound atoms are never asserted, only used to detect conflicts.
struct BEv { int j, side; long long val, old; int lvl, rs, rl; };
vector<BEv> bevs;
vector<long long> fb[2];
vector<vector<int>> fhist[2];          // per variable: its hi / lo events (indices into bevs), oldest first
static const int BEV0 = MAXN * 17;
static inline int aenc(int j, int side) { return -1 - 2 * j - side; }
static inline bool setb(int j, int side, long long v, const Pairs& why) {
    long long& cur = fb[side][j];
    if (side == 0 ? v >= cur : v <= cur) return true;
    long long other = fb[side ^ 1][j];
    if (side == 0 ? v < other : v > other) {
        CR_dec = false; CRp = why; CRp.push_back({aenc(j, side ^ 1), (Mask)other});
        return false;
    }
    trail_ev.push_back(evtrail.size());
    trail_pool.push_back(rpool.size());
    trail.push_back({-1 - j, 0});
    fhist[side][j].push_back(bevs.size());
    evtrail.push_back(BEV0 + bevs.size());
    bevs.push_back({j, side, v, cur, curlvl, (int)rpool.size(), (int)why.size()});
    rpool.insert(rpool.end(), why.begin(), why.end());
    cur = v;
    return true;
}
size_t ng_qhead = 0;                   // nogood watches: trail entries already seen by nogood_prop
bool ng_rewatch = true;
static inline void undo(size_t to) {
    ng_qhead = min(ng_qhead, to);
    if (trail.size() > to) {
        evtrail.resize(trail_ev[to]);
        rpool.resize(trail_pool[to]);
        trail_ev.resize(to);
        trail_pool.resize(to);
    }
    for (; trail.size() > to; trail.pop_back()) {
        if (trail.back().first >= 0) { dom[trail.back().first] = trail.back().second; continue; }
        BEv& e = bevs.back();
        fb[e.side][e.j] = e.old;
        fhist[e.side][e.j].pop_back();
        bevs.pop_back();
    }
}

// ------------------------------------------------------------------ local supports
// Is there a possible user of output direction d from tile p (neighbour acceptor or out port)?
static inline bool out_possible(int p, int d) {
    int q = nb(p, d);
    if (q < 0) return portKind[p][d] == 2;
    return dom[q] & ACC[d];
}
// Possible feeder into p of items moving in direction e (neighbour pusher at p - e, or an in port).
static inline bool in_possible(int p, int e) {
    int r = nb(p, OPP(e));
    if (r < 0) return portKind[p][OPP(e)] == 1;
    return dom[r] & PUSH[e];
}
// I d at p: a possible first exit within reach.
static inline bool exit_possible(int p, int d) {
    Mask od = BIT(VAL(KO, d));
    for (int n = 1; n <= REACH; n++) {
        int q = nbn(p, d, n);
        if (q < 0) return false;
        if (dom[q] & od) return true;
    }
    return false;
}
// O d at p: a possible entrance whose first exit could be p.
static inline bool entrance_possible(int p, int d) {
    Mask od = BIT(VAL(KO, d)), id = BIT(VAL(KI, d));
    for (int n = 1; n <= REACH; n++) {
        int r = nbn(p, OPP(d), n);
        if (r < 0) return false;
        if (dom[r] & id) return true;
        if (dom[r] == od) return false;          // a forced exit between catches any entrance further back
    }
    return false;
}

static bool supported(int p, int v) {
    if (v == 0) return true;
    int k = KIND(v), o = DIRV(v);
    switch (k) {
    case KB:
        if (!out_possible(p, o)) return false;
        return in_possible(p, o) || in_possible(p, (o + 1) & 3) || in_possible(p, (o + 3) & 3);
    case KS:
        if (!(out_possible(p, o) || out_possible(p, (o + 1) & 3) || out_possible(p, (o + 3) & 3))) return false;
        return in_possible(p, o);
    case KI:
        return in_possible(p, o) && exit_possible(p, o);
    case KO:
        return out_possible(p, o) && entrance_possible(p, o);
    }
    return false;
}


// Why value v is unsupported at p (mirrors supported()): pairs of (cell, mask removed there). Edges are static.
static void explain_unsupported(int p, int v, Pairs& out) {
    int k = KIND(v), o = DIRV(v);
    auto outfail = [&](int d) { int q = nb(p, d); if (q >= 0) out.push_back({q, ACC[d]}); };
    auto infail = [&](int e) { int r = nb(p, OPP(e)); if (r >= 0) out.push_back({r, PUSH[e]}); };
    switch (k) {
    case KB:
        if (!out_possible(p, o)) { outfail(o); return; }
        infail(o); infail((o + 1) & 3); infail((o + 3) & 3); return;
    case KS:
        if (!(out_possible(p, o) || out_possible(p, (o + 1) & 3) || out_possible(p, (o + 3) & 3))) {
            outfail(o); outfail((o + 1) & 3); outfail((o + 3) & 3); return;
        }
        infail(o); return;
    case KI:
        if (!in_possible(p, o)) { infail(o); return; }
        for (int n = 1; n <= REACH; n++) { int q = nbn(p, o, n); if (q < 0) break; out.push_back({q, BIT(VAL(KO, o))}); }
        return;
    case KO: {
        if (!out_possible(p, o)) { outfail(o); return; }
        Mask od = BIT(VAL(KO, o)), id = BIT(VAL(KI, o));
        for (int n = 1; n <= REACH; n++) {
            int r = nbn(p, OPP(o), n);
            if (r < 0) break;
            out.push_back({r, id});
            if (dom[r] == od) { out.push_back({r, ALL & ~od}); break; }
        }
        return;
    }
    }
}

// ------------------------------------------------------------------ unit rules on forced tiles
static bool unit_rules(bool& changed) {
    Pairs why;
    for (int p = 0; p < N; p++) {
        if (!single(dom[p]) || dom[p] == BIT_E) continue;
        int v = only(dom[p]), k = KIND(v), o = DIRV(v);
        const Pairs* W = R_ON ? &why : nullptr;
        auto base = [&]() { why.clear(); why.push_back({p, ALL & ~BIT(v)}); };   // p is forced to v
        // outputs
        if (k == KB || k == KO) {
            int q = nb(p, o);
            if (R_ON) base();
            if (q >= 0 && !setdom(q, dom[q] & ACC[o], changed, W)) return false;
        } else if (k == KS) {
            int cnt = 0, lastd = -1;
            int ds[3] = {o, (o + 1) & 3, (o + 3) & 3};
            for (int d : ds) if (out_possible(p, d)) { cnt++; lastd = d; }
            if (cnt == 0) return false;
            if (cnt == 1) {
                int q = nb(p, lastd);
                if (R_ON) { base(); for (int d : ds) if (d != lastd) { int t = nb(p, d); if (t >= 0) why.push_back({t, ACC[d]}); } }
                if (q >= 0 && !setdom(q, dom[q] & ACC[lastd], changed, W)) return false;
            }
        } else if (k == KI) {
            Mask od = BIT(VAL(KO, o));
            int cnt = 0, last = -1;
            for (int n = 1; n <= REACH; n++) {
                int q = nbn(p, o, n);
                if (q < 0) break;
                if (dom[q] & od) { cnt++; last = q; if (dom[q] == od) break; }
            }
            if (cnt == 0) return false;
            if (cnt == 1) {
                if (R_ON) {
                    base();
                    for (int n = 1; n <= REACH; n++) { int q = nbn(p, o, n); if (q < 0 || q == last) break; why.push_back({q, od}); }
                    // cells after `last` are only reached if last is not a forced exit; if it is, forcing it is a no-op
                    bool after = false;
                    for (int n = 1; n <= REACH; n++) { int q = nbn(p, o, n); if (q < 0) break; if (after) why.push_back({q, od}); if (q == last) after = true; }
                }
                if (!setdom(last, od, changed, W)) return false;
            }
        }
        // inputs
        if (k == KO) {
            Mask od = BIT(VAL(KO, o)), id = BIT(VAL(KI, o));
            int cnt = 0, last = -1;
            for (int n = 1; n <= REACH; n++) {
                int r = nbn(p, OPP(o), n);
                if (r < 0) break;
                if (dom[r] & id) { cnt++; last = r; }
                if (dom[r] == od) break;
            }
            if (cnt == 0) return false;
            if (cnt == 1) {
                if (R_ON) {
                    base();
                    for (int n = 1; n <= REACH; n++) {
                        int r = nbn(p, OPP(o), n);
                        if (r < 0) break;
                        if (r != last) why.push_back({r, id});
                        if (dom[r] == od) { why.push_back({r, ALL & ~od}); break; }
                    }
                }
                if (!setdom(last, id, changed, W)) return false;
            }
        } else {
            int es[3], ne = 0;
            if (k == KB) { es[0] = o; es[1] = (o + 1) & 3; es[2] = (o + 3) & 3; ne = 3; }
            else { es[0] = o; ne = 1; }
            int cnt = 0, laste = -1, portfed = 0;
            for (int j = 0; j < ne; j++) {
                int e = es[j];
                int r = nb(p, OPP(e));
                if (r < 0) { if (portKind[p][OPP(e)] == 1) portfed = 1; continue; }
                if (dom[r] & PUSH[e]) { cnt++; laste = e; }
            }
            if (!portfed) {
                if (cnt == 0) return false;
                if (cnt == 1) {
                    int r = nb(p, OPP(laste));
                    if (R_ON) { base(); for (int j = 0; j < ne; j++) { int e = es[j]; if (e == laste) continue; int t = nb(p, OPP(e)); if (t >= 0) why.push_back({t, PUSH[e]}); } }
                    if (!setdom(r, dom[r] & PUSH[laste], changed, W)) return false;
                }
            }
        }
    }
    return true;
}

// ------------------------------------------------------------------ reachability
static int qbuf[MAXN];
static void bfs_fwd(const vector<int>& starts, vector<char>& seen) {
    seen.assign(N, 0);
    int h = 0, t = 0;
    for (int s : starts) if (!seen[s]) { seen[s] = 1; qbuf[t++] = s; }
    while (h < t) {
        int r = qbuf[h++];
        Mask m = dom[r];
        for (int e = 0; e < 4; e++) {
            if (!(m & PUSH[e])) continue;
            int q = nb(r, e);
            if (q >= 0 && !seen[q] && (dom[q] & ACC[e])) { seen[q] = 1; qbuf[t++] = q; }
        }
        for (int d = 0; d < 4; d++) {
            if (!(m & BIT(VAL(KI, d)))) continue;
            Mask od = BIT(VAL(KO, d));
            for (int n = 1; n <= REACH; n++) {
                int q = nbn(r, d, n);
                if (q < 0) break;
                if (dom[q] & od) { if (!seen[q]) { seen[q] = 1; qbuf[t++] = q; } if (dom[q] == od) break; }
            }
        }
    }
}
static void bfs_bwd(const vector<int>& starts, vector<char>& seen) {
    seen.assign(N, 0);
    int h = 0, t = 0;
    for (int s : starts) if (!seen[s]) { seen[s] = 1; qbuf[t++] = s; }
    while (h < t) {
        int q = qbuf[h++];
        Mask m = dom[q];
        for (int e = 0; e < 4; e++) {
            if (!(m & ACC[e])) continue;
            int r = nb(q, OPP(e));
            if (r >= 0 && !seen[r] && (dom[r] & PUSH[e])) { seen[r] = 1; qbuf[t++] = r; }
        }
        for (int d = 0; d < 4; d++) {
            Mask od = BIT(VAL(KO, d)), id = BIT(VAL(KI, d));
            if (!(m & od)) continue;
            for (int n = 1; n <= REACH; n++) {
                int r = nbn(q, OPP(d), n);
                if (r < 0) break;
                if ((dom[r] & id) && !seen[r]) { seen[r] = 1; qbuf[t++] = r; }
                if (dom[r] == od) break;
            }
        }
    }
}

// Cut explanations: why nothing outside the set S is linked from S (forward) / links into S (backward).
static void cut_fwd(const vector<char>& S, Pairs& out) {
    for (int r = 0; r < N; r++) {
        if (!S[r]) continue;
        for (int e = 0; e < 4; e++) {
            int q = nb(r, e);
            if (q < 0 || S[q]) continue;
            if (!(dom[r] & PUSH[e])) out.push_back({r, PUSH[e]}); else out.push_back({q, ACC[e]});
        }
        for (int d = 0; d < 4; d++) {
            Mask od = BIT(VAL(KO, d)), id = BIT(VAL(KI, d));
            if (!(dom[r] & id)) { out.push_back({r, id}); continue; }
            for (int n = 1; n <= REACH; n++) {
                int q = nbn(r, d, n);
                if (q < 0) break;
                if (!(dom[q] & od)) { if (!S[q]) out.push_back({q, od}); continue; }
                if (dom[q] == od) { out.push_back({q, ALL & ~od}); break; }
            }
        }
    }
}
static void cut_bwd(const vector<char>& S, Pairs& out) {
    for (int q = 0; q < N; q++) {
        if (!S[q]) continue;
        for (int e = 0; e < 4; e++) {
            int r = nb(q, OPP(e));
            if (r < 0 || S[r]) continue;
            if (!(dom[q] & ACC[e])) out.push_back({q, ACC[e]}); else out.push_back({r, PUSH[e]});
        }
        for (int d = 0; d < 4; d++) {
            Mask od = BIT(VAL(KO, d)), id = BIT(VAL(KI, d));
            if (!(dom[q] & od)) { out.push_back({q, od}); continue; }
            for (int n = 1; n <= REACH; n++) {
                int r = nbn(q, OPP(d), n);
                if (r < 0) break;
                if (!S[r] && !(dom[r] & id)) out.push_back({r, id});
                if (dom[r] == od) { out.push_back({r, ALL & ~od}); break; }
            }
        }
    }
}
static bool reach(bool& changed) {
    vector<int> st;
    for (auto& pt : ins) if (pt.live) st.push_back(pt.cell);
    vector<char> f, b;
    bfs_fwd(st, f);
    st.clear();
    for (auto& pt : outs) st.push_back(pt.cell);
    bfs_bwd(st, b);
    Pairs whyf, whyb;
    bool hf = false, hb = false;
    for (int p = 0; p < N; p++) {
        if (f[p] && b[p]) continue;
        if (!(dom[p] & ~BIT_E)) continue;
        const Pairs* W = nullptr;
        if (R_ON) {
            if (!f[p]) { if (!hf) { cut_fwd(f, whyf); hf = true; } W = &whyf; }
            else { if (!hb) { cut_bwd(b, whyb); hb = true; } W = &whyb; }
        }
        if (!setdom(p, dom[p] & BIT_E, changed, W)) return false;
    }
    // per input lane: must reach an out; every out must be reached by a lane of each commodity it needs
    vector<char> got(outs.size() * NC, 0);
    vector<vector<char>> lanes;
    for (auto& ip : ins) {
        if (!ip.live) continue;
        vector<int> s1 = {ip.cell};
        bfs_fwd(s1, f);
        bool any = false;
        for (size_t j = 0; j < outs.size(); j++)
            if (f[outs[j].cell]) {
                any = true;
                for (int c = 0; c < NC; c++) if (ip.vec[c] > 0) got[j * NC + c] = 1;
            }
        if (!any) { if (R_ON) { CRp.clear(); cut_fwd(f, CRp); CR_dec = false; } return false; }
        if (R_ON) lanes.push_back(f);
    }
    for (size_t j = 0; j < outs.size(); j++)
        for (int c = 0; c < NC; c++)
            if (outs[j].vec[c] > 0 && !got[j * NC + c]) {
                if (R_ON) {
                    CRp.clear(); CR_dec = false;
                    size_t li = 0;
                    for (auto& ip : ins) { if (!ip.live) continue; if (ip.vec[c] > 0) cut_fwd(lanes[li], CRp); li++; }
                }
                return false;
            }
    return true;
}

// ------------------------------------------------------------------ flow lower bounds (doubles)
static double xs[MAXN][MAXC], xn[MAXN][MAXC], srcv[MAXN][MAXC];

static bool flow_lb() {
    // definite links among forced tiles
    static int lto[MAXN][3], nl[MAXN];
    static double share[MAXN];
    static int sinkd[MAXN][3], ns[MAXN];
    for (int p = 0; p < N; p++) {
        nl[p] = ns[p] = 0; share[p] = 0;
        if (!single(dom[p]) || dom[p] == BIT_E) continue;
        int v = only(dom[p]), k = KIND(v), o = DIRV(v);
        if (k == KI) {
            Mask od = BIT(VAL(KO, o));
            for (int n = 1; n <= REACH; n++) {
                int q = nbn(p, o, n);
                if (q < 0) break;
                if (dom[q] & od) { if (dom[q] == od) lto[p][nl[p]++] = q; break; }
            }
            share[p] = 1;
            continue;
        }
        int ds[3]; int nd = push_dirs(v, ds);
        int poss = 0;
        for (int j = 0; j < nd; j++) {
            int d = ds[j], q = nb(p, d);
            if (q < 0) {
                if (portKind[p][d] == 2) { poss++; sinkd[p][ns[p]++] = d; }
                continue;
            }
            if (dom[q] & ACC[d]) {
                poss++;
                if (single(dom[q])) lto[p][nl[p]++] = q;
            }
        }
        share[p] = poss ? 1.0 / poss : 0;
    }
    for (int p = 0; p < N; p++) for (int c = 0; c < NC; c++) xs[p][c] = srcv[p][c];
    double cap = (double)D * (1 + 1e-9);
    for (int it = 0; it < 4 * N + 10; it++) {
        for (int p = 0; p < N; p++) for (int c = 0; c < NC; c++) xn[p][c] = srcv[p][c];
        for (int p = 0; p < N; p++)
            for (int j = 0; j < nl[p]; j++) {
                int q = lto[p][j];
                for (int c = 0; c < NC; c++) xn[q][c] += xs[p][c] * share[p];
            }
        double diff = 0;
        for (int p = 0; p < N; p++) {
            double tot = 0;
            for (int c = 0; c < NC; c++) { diff = max(diff, xn[p][c] - xs[p][c]); xs[p][c] = xn[p][c]; tot += xs[p][c]; }
            if (tot > cap) return false;
        }
        if (diff < 1e-12 * D) break;
    }
    for (auto& op : outs) {
        int p = op.cell;
        if (!single(dom[p])) continue;
        for (int j = 0; j < ns[p]; j++) {
            if (sinkd[p][j] != op.d) continue;
            for (int c = 0; c < NC; c++)
                if (xs[p][c] * share[p] > op.vec[c] * (1 + 1e-9) + 1e-9) return false;
        }
    }
    return true;
}

// ------------------------------------------------------------------ max-flow relaxation
// Sound relaxation: in any valid layout the real flows route every input's units to the out ports along links
// that are still possible, with at most D per tile (per commodity: at most D minus the other commodities' lower
// bounds). So a max flow below the total demand proves the node infeasible. Splitter equal shares are ignored.
struct Dinic {
    struct E { int to; long long cap; };
    vector<E> es; vector<vector<int>> g; vector<int> lvl, it;
    void init(int n) { es.clear(); g.assign(n, {}); }
    void add(int a, int b, long long c) { g[a].push_back(es.size()); es.push_back({b, c}); g[b].push_back(es.size()); es.push_back({a, 0}); }
    bool bfs(int s, int t) {
        lvl.assign(g.size(), -1); lvl[s] = 0;
        vector<int> q = {s};
        for (size_t h = 0; h < q.size(); h++) for (int id : g[q[h]]) if (es[id].cap > 0 && lvl[es[id].to] < 0) { lvl[es[id].to] = lvl[q[h]] + 1; q.push_back(es[id].to); }
        return lvl[t] >= 0;
    }
    long long dfs(int v, int t, long long f) {
        if (v == t) return f;
        for (int& i = it[v]; i < (int)g[v].size(); i++) {
            E& e = es[g[v][i]];
            if (e.cap > 0 && lvl[e.to] == lvl[v] + 1) {
                long long r = dfs(e.to, t, min(f, e.cap));
                if (r > 0) { e.cap -= r; es[g[v][i] ^ 1].cap += r; return r; }
            }
        }
        return 0;
    }
    long long run(int s, int t, long long need) {
        long long fl = 0;
        while (fl < need && bfs(s, t)) { it.assign(g.size(), 0); long long f; while ((f = dfs(s, t, need - fl)) > 0) fl += f; }
        return fl;
    }
};
static Dinic dn;
static const long long INF = (long long)4e18;
static vector<pair<int, int>> possible_links() {
    vector<pair<int, int>> L;
    for (int r = 0; r < N; r++) {
        Mask m = dom[r];
        if (m == BIT_E) continue;
        for (int e = 0; e < 4; e++) {
            if (!(m & PUSH[e])) continue;
            int q = nb(r, e);
            if (q >= 0 && (dom[q] & ACC[e])) L.push_back({r, q});
        }
        for (int d = 0; d < 4; d++) {
            if (!(m & BIT(VAL(KI, d)))) continue;
            Mask od = BIT(VAL(KO, d));
            for (int n = 1; n <= REACH; n++) {
                int q = nbn(r, d, n);
                if (q < 0) break;
                if (dom[q] & od) { L.push_back({r, q}); if (dom[q] == od) break; }
            }
        }
    }
    return L;
}
// com < 0: all commodities together
static bool maxflow_ok(const vector<pair<int, int>>& L, int com) {
    int S = 2 * N, T = 2 * N + 1;
    dn.init(2 * N + 2);
    for (int p = 0; p < N; p++) {
        if (dom[p] == BIT_E) continue;
        long long cap = D;
        if (com >= 0) {
            double other = 0;
            for (int c = 0; c < NC; c++) if (c != com) other += xs[p][c];
            cap = (long long)ceil(D - other + 1e-6 * D);      // rounded UP (real flows may be fractional), so it
            if (cap < 0) cap = 0;                             // stays a relaxation
        }
        dn.add(2 * p, 2 * p + 1, cap * 1);
    }
    for (auto& l : L) dn.add(2 * l.first + 1, 2 * l.second, INF);
    long long need = 0;
    for (auto& ip : ins) {
        long long v = 0;
        for (int c = 0; c < NC; c++) if (com < 0 || c == com) v += ip.vec[c];
        if (v > 0) dn.add(S, 2 * ip.cell, v);
    }
    for (auto& op : outs) {
        long long v = 0;
        for (int c = 0; c < NC; c++) if (com < 0 || c == com) v += op.vec[c];
        need += v;
        if (v > 0) dn.add(2 * op.cell + 1, T, v);
    }
    return dn.run(S, T, need) >= need;
}
static bool flow_relax() {
    vector<pair<int, int>> L = possible_links();
    if (!maxflow_ok(L, -1)) return false;
    if (NC > 1 && NC <= 8)
        for (int c = 0; c < NC; c++) if (!maxflow_ok(L, c)) return false;
    return true;
}

// ------------------------------------------------------------------ LP relaxation with an exact Farkas certificate
// Variables (all >= 0, per commodity): flow on every possible surface link; underground channel flow u[p][d] from p to
// p+d, entry e[p][d] (I d possible at p), exit x[p][d] (O d possible at p). Rows: conservation at every tile and in
// every channel cell; tile capacity sum_c inflow <= D; forced splitter: all definite outputs carry the same amount of
// each commodity (an out-port output carries exactly its vec), every uncertain output carries at most that amount.
// Every valid layout gives a feasible point (its real flows), so an infeasible LP proves the node infeasible. Floating
// simplex only proposes a dual vector y; the node is pruned only if y, scaled to integers, satisfies A^T z <= 0 and
// b^T z > 0 in exact integer arithmetic (Farkas), so rounding errors can only cost pruning, never soundness.
struct LPRow { vector<pair<int, int>> a; long long b; };   // sparse integer row, sum a x = b
long long lp_calls = 0, lp_prunes = 0, lp_cert_fail = 0, lp_warm = 0;
vector<double> lastsols[MAXC + 1];
int lp_only = -1;     // LP over one commodity only (others enter through their lower bounds in the capacity rows)
static double lp_flow(int key) {
    double t = 0;
    if ((int)lastsols[0].size() == 16 * N * NC) { for (int c = 0; c < NC; c++) t += lastsols[0][key + c * 16 * N]; return t; }
    for (int c = 0; c < NC; c++) if ((int)lastsols[c + 1].size() == 16 * N * NC) t += lastsols[c + 1][key + c * 16 * N];
    return t;
}
// Phase-1 simplex (dense tableau) on rows (sum a x = b, x >= 0; rows are flipped so b >= 0). Returns 1 with a
// feasible point in val, 0 with a proposed Farkas dual y (to be verified exactly by the caller), -1 on failure.
static int phase1(vector<LPRow>& rows, int n, vector<double>& val, vector<double>& y) {
    int m = rows.size();
    // flip rows so b >= 0
    for (auto& r : rows) if (r.b < 0) { r.b = -r.b; for (auto& t : r.a) t.second = -t.second; }
    // slack columns (+1 in exactly one row) start basic; other rows get an artificial (not stored as a column:
    // the dual y is recomputed from the final basis at the end)
    vector<int> colcount(n, 0);
    for (auto& r : rows) for (auto& t : r.a) colcount[t.first]++;
    int cols = n + 1, RH = n;
    vector<double> T((size_t)(m + 1) * cols, 0.0);
    auto at = [&](int i, int j) -> double& { return T[(size_t)i * cols + j]; };
    vector<int> basis(m);
    vector<char> isart(m, 1);
    vector<char> inbasis(n, 0);
    for (int i = 0; i < m; i++) {
        for (auto& t : rows[i].a) at(i, t.first) += t.second;
        at(i, RH) = (double)rows[i].b;
        basis[i] = -1 - i;
        for (auto& t : rows[i].a)
            if (t.second == 1 && colcount[t.first] == 1 && !inbasis[t.first]) { basis[i] = t.first; isart[i] = 0; inbasis[t.first] = 1; break; }
    }
    for (int j = 0; j < n; j++) { double s2 = 0; for (int i = 0; i < m; i++) if (isart[i]) s2 += at(i, j); at(m, j) = -s2; }
    { double s2 = 0; for (int i = 0; i < m; i++) if (isart[i]) s2 += at(i, RH); at(m, RH) = -s2; }
    const double eps = 1e-9;
    int iter = 0, maxit = 20 * (m + n);
    vector<int> nzc;
    while (iter++ < maxit) {
        int ej = -1; double best = -eps;
        for (int j = 0; j < n; j++) if (at(m, j) < best) { best = at(m, j); ej = j; }
        if (ej < 0) break;
        int pi = -1; double br = 0;
        for (int i = 0; i < m; i++) {
            double a2 = at(i, ej);
            if (a2 > eps) { double r = at(i, RH) / a2; if (pi < 0 || r < br - 1e-12 || (r < br + 1e-12 && basis[i] < basis[pi])) { pi = i; br = r; } }
        }
        if (pi < 0) return -1;     // unbounded cannot happen in phase 1; be safe
        double pv = at(pi, ej);
        nzc.clear();
        double* rp = &T[(size_t)pi * cols];
        for (int j = 0; j < cols; j++) if (rp[j] != 0) { rp[j] /= pv; if (fabs(rp[j]) < 1e-14) rp[j] = 0; else nzc.push_back(j); }
        for (int i = 0; i <= m; i++) {
            if (i == pi) continue;
            double* ri = &T[(size_t)i * cols];
            double f = ri[ej];
            if (f == 0) continue;
            for (int j : nzc) { ri[j] -= f * rp[j]; if (fabs(ri[j]) < 1e-14) ri[j] = 0; }
            ri[ej] = 0;
        }
        basis[pi] = ej;
    }
    double obj = -at(m, RH);
    if (obj < 1e-7 * D) {
        val.assign(n, 0.0);
        for (int i = 0; i < m; i++) if (basis[i] >= 0) val[basis[i]] = max(0.0, at(i, RH));
        return 1;
    }
    // dual y = c_B B^-1: solve B^T y = c_B (c = 1 for artificials) by dense Gauss with partial pivoting
    y.assign(m, 0.0);
    {
        vector<vector<double>> Bt(m, vector<double>(m + 1, 0.0));   // row k of B^T = column basis[k] of A
        vector<int> posof(n, -1);
        for (int k = 0; k < m; k++) if (basis[k] >= 0) posof[basis[k]] = k;
        for (int i = 0; i < m; i++) for (auto& t : rows[i].a) if (posof[t.first] >= 0) Bt[posof[t.first]][i] += t.second;
        for (int k = 0; k < m; k++) if (basis[k] < 0) { Bt[k][-1 - basis[k]] = 1; Bt[k][m] = 1; }
        for (int c = 0; c < m; c++) {
            int pv = c;
            for (int r = c + 1; r < m; r++) if (fabs(Bt[r][c]) > fabs(Bt[pv][c])) pv = r;
            if (fabs(Bt[pv][c]) < 1e-12) return -1;
            swap(Bt[c], Bt[pv]);
            for (int r = 0; r < m; r++) {
                if (r == c || Bt[r][c] == 0) continue;
                double f = Bt[r][c] / Bt[c][c];
                for (int k = c; k <= m; k++) Bt[r][k] -= f * Bt[c][k];
            }
        }
        for (int i = 0; i < m; i++) y[i] = Bt[i][m] / Bt[i][i];
    }
    return 0;
}
static bool bounds_prop(vector<LPRow>& rows, int n, int nv, int per, const vector<int>& vkey, bool& changed);
static bool lp_check(int maxvars, int mode = 0, bool* bchanged = nullptr) {
    if (mode == 0) lp_calls++;
    int lonly = mode == 0 ? lp_only : -1;
    vector<double>& lastsol = lastsols[lonly + 1];
    int nv = 0;
    vector<LPRow> rows;
    // per tile: surface links
    vector<pair<int, int>> L;   // (r, q) with dir
    vector<int> Ldir;
    for (int r = 0; r < N; r++) {
        Mask m = dom[r];
        if (m == BIT_E) continue;
        for (int e = 0; e < 4; e++) {
            if (!(m & PUSH[e])) continue;
            int q = nb(r, e);
            if (q >= 0 && (dom[q] & ACC[e])) { L.push_back({r, q}); Ldir.push_back(e); }
        }
    }
    // channel cells that can carry flow
    static int uvar[MAXN][4], evar[MAXN][4], xvar[MAXN][4];
    static char ucan[MAXN][4];
    memset(ucan, 0, sizeof(char) * N * 4);
    for (int r = 0; r < N; r++)
        for (int d = 0; d < 4; d++) {
            if (!(dom[r] & BIT(VAL(KI, d)))) continue;
            Mask od = BIT(VAL(KO, d));
            int last = -1;
            for (int n = 1; n <= REACH; n++) {
                int q = nbn(r, d, n);
                if (q < 0) break;
                if (dom[q] & od) { last = n; if (dom[q] == od) break; }
            }
            for (int n = 0; n < last; n++) ucan[nbn(r, d, n)][d] = 1;   // u from r+n d to r+(n+1) d
        }
    int NL = L.size();
    // variable numbering: commodity-major blocks
    int per = 0;
    vector<int> lvar(NL);
    vector<int> vkey;
    for (int i = 0; i < NL; i++) { lvar[i] = per++; vkey.push_back(L[i].first * 4 + Ldir[i]); }
    for (int p = 0; p < N; p++)
        for (int d = 0; d < 4; d++) {
            uvar[p][d] = ucan[p][d] ? per++ : -1;
            if (uvar[p][d] >= 0) vkey.push_back(4 * N + p * 4 + d);
            evar[p][d] = (dom[p] & BIT(VAL(KI, d))) && ucan[p][d] ? per++ : -1;
            if (evar[p][d] >= 0) vkey.push_back(8 * N + p * 4 + d);
            xvar[p][d] = -1;
        }
    for (int p = 0; p < N; p++)
        for (int d = 0; d < 4; d++) {
            if (!(dom[p] & BIT(VAL(KO, d)))) continue;
            int r = nb(p, OPP(d));
            if (r >= 0 && uvar[r][d] >= 0) { xvar[p][d] = per++; vkey.push_back(12 * N + p * 4 + d); }
        }
    nv = per * NC;
    if (nv > maxvars) return true;
    auto V = [&](int base, int c) { return base + c * per; };
    auto skipc = [&](int c) { return lonly >= 0 && c != lonly; };
    // in/out lists per tile
    vector<vector<int>> lin(N), lout(N);
    for (int i = 0; i < NL; i++) { lout[L[i].first].push_back(i); lin[L[i].second].push_back(i); }
    for (int c = 0; c < NC; c++) {
        if (skipc(c)) continue;
        for (int p = 0; p < N; p++) {
            if (dom[p] == BIT_E) continue;
            LPRow row; row.b = 0;
            // inflow - outflow = drain - src
            for (int i : lin[p]) row.a.push_back({V(lvar[i], c), 1});
            for (int d = 0; d < 4; d++) if (xvar[p][d] >= 0) row.a.push_back({V(xvar[p][d], c), 1});
            for (int i : lout[p]) row.a.push_back({V(lvar[i], c), -1});
            for (int d = 0; d < 4; d++) if (evar[p][d] >= 0) row.a.push_back({V(evar[p][d], c), -1});
            long long src = 0, drain = 0;
            for (int d = 0; d < 4; d++) {
                if (portKind[p][d] == 1) src += portVec[p][d][c];
                if (portKind[p][d] == 2) drain += portVec[p][d][c];
            }
            row.b = drain - src;
            if (row.a.empty() && row.b == 0) continue;
            rows.push_back(row);
        }
        // channel conservation at q: u[q-d] + e[q] = u[q] + x[q]
        for (int q = 0; q < N; q++)
            for (int d = 0; d < 4; d++) {
                int r = nb(q, OPP(d));
                int uin = (r >= 0) ? uvar[r][d] : -1;
                LPRow row; row.b = 0;
                if (uin >= 0) row.a.push_back({V(uin, c), 1});
                if (evar[q][d] >= 0) row.a.push_back({V(evar[q][d], c), 1});
                if (uvar[q][d] >= 0) row.a.push_back({V(uvar[q][d], c), -1});
                if (xvar[q][d] >= 0) row.a.push_back({V(xvar[q][d], c), -1});
                if (!row.a.empty()) rows.push_back(row);
            }
    }
    // capacity: sum_c inflow + slack = D - src
    int nslack = 0;
    vector<LPRow> caprows;
    for (int p = 0; p < N; p++) {
        if (dom[p] == BIT_E) continue;
        LPRow row;
        long long src = 0;
        for (int c = 0; c < NC; c++) {
            if (skipc(c)) continue;
            for (int i : lin[p]) row.a.push_back({V(lvar[i], c), 1});
            for (int d = 0; d < 4; d++) if (xvar[p][d] >= 0) row.a.push_back({V(xvar[p][d], c), 1});
            for (int d = 0; d < 4; d++) if (portKind[p][d] == 1) src += portVec[p][d][c];
        }
        row.b = D - src;
        if (lonly >= 0) {         // the other commodities take at least their lower bounds (xs, from flow_lb on these
            double other = 0;     // same domains), rounded in our favour so the row stays a relaxation
            for (int c = 0; c < NC; c++) if (c != lonly) other += xs[p][c];
            row.b = (long long)ceil(D - other + 1e-6 * D) - src;
        }
        if (row.b < 0) return false;
        if (row.a.empty()) continue;
        row.a.push_back({nv + nslack++, 1});
        rows.push_back(row);
    }
    // forced splitters
    for (int p = 0; p < N; p++) {
        if (!single(dom[p]) || KIND(only(dom[p])) != KS || dom[p] == BIT_E) continue;
        int o = DIRV(only(dom[p]));
        int ds[3] = {o, (o + 1) & 3, (o + 3) & 3};
        vector<int> def, unc; int portd = -1;
        for (int d : ds) {
            int q = nb(p, d);
            if (q < 0) { if (portKind[p][d] == 2) portd = d; continue; }
            if (!(dom[q] & ACC[d])) continue;
            int li = -1;
            for (int i : lout[p]) if (L[i].second == q) li = i;
            if (li < 0) continue;
            if (single(dom[q])) def.push_back(li); else unc.push_back(li);
        }
        for (int c = 0; c < NC; c++) {
            if (skipc(c)) continue;
            if (portd >= 0) {
                for (int li : def) { LPRow row; row.a.push_back({V(lvar[li], c), 1}); row.b = portVec[p][portd][c]; rows.push_back(row); }
                for (int li : unc) { LPRow row; row.a.push_back({V(lvar[li], c), 1}); row.a.push_back({nv + nslack++, 1}); row.b = portVec[p][portd][c]; rows.push_back(row); }
            } else if (!def.empty()) {
                for (size_t k = 1; k < def.size(); k++) { LPRow row; row.a.push_back({V(lvar[def[k]], c), 1}); row.a.push_back({V(lvar[def[0]], c), -1}); row.b = 0; rows.push_back(row); }
                for (int li : unc) { LPRow row; row.a.push_back({V(lvar[li], c), 1}); row.a.push_back({V(lvar[def[0]], c), -1}); row.a.push_back({nv + nslack++, 1}); row.b = 0; rows.push_back(row); }
            }
        }
    }
    int n = nv + nslack, m = rows.size();
    if (mode == 1) return bounds_prop(rows, n, nv, per, vkey, *bchanged);
    if (m == 0) return true;
    // warm check: is the last LP solution still feasible here? then nothing to prune
    if (!lp_nowarm && (int)lastsol.size() == 16 * N * NC) {
        vector<double> xv(nv);
        for (int c = 0; c < NC; c++) for (int k = 0; k < per; k++) xv[k + c * per] = lastsol[vkey[k] + c * 16 * N];
        // every key not in this LP must carry zero in the stored solution
        double used = 0, tot = 0;
        for (int k = 0; k < nv; k++) used += xv[k];
        for (double v : lastsol) tot += v;
        bool ok = fabs(used - tot) < 1e-9 * D;
        for (size_t i = 0; ok && i < rows.size(); i++) {
            double lhs = 0; bool hasslack = false;
            for (auto& t : rows[i].a) { if (t.first >= nv) hasslack = true; else lhs += t.second * xv[t.first]; }
            double res = rows[i].b - lhs;
            if (hasslack ? res < -1e-9 * D : fabs(res) > 1e-9 * D) ok = false;
        }
        if (ok) { lp_warm++; return true; }
    }
    if ((long long)m * (n + m) > 4000000) return true;
    // presolve (exact, integer): substitute equality rows a - b = 0 (merge), a + b = 0 (both 0) and single-variable
    // rows +-x = b (fix x). Any x >= 0 solving the original rows gives one of the reduced rows, so a Farkas
    // certificate for the reduced system still proves the node infeasible.
    vector<int> uf(n); for (int j = 0; j < n; j++) uf[j] = j;
    vector<long long> fixv(n, -1);
    function<int(int)> fnd = [&](int a) { while (uf[a] != a) { uf[a] = uf[uf[a]]; a = uf[a]; } return a; };
    vector<char> alive(rows.size(), 1);
    bool pch = true;
    while (pch) {
        pch = false;
        for (size_t i = 0; i < rows.size(); i++) {
            if (!alive[i]) continue;
            LPRow& r = rows[i];
            map<int, long long> acc;
            long long b = r.b;
            for (auto& t : r.a) { int v = fnd(t.first); if (fixv[v] >= 0) b -= (long long)t.second * fixv[v]; else acc[v] += t.second; }
            r.a.clear();
            for (auto& kv : acc) if (kv.second) r.a.push_back({kv.first, (int)kv.second});
            r.b = b;
            if (r.a.empty()) { if (b != 0) return false; alive[i] = 0; pch = true; continue; }
            if (r.a.size() == 1 && abs(r.a[0].second) == 1) {
                long long val = b * r.a[0].second;
                if (val < 0) return false;
                fixv[r.a[0].first] = val; alive[i] = 0; pch = true; continue;
            }
            if (r.a.size() == 2 && b == 0 && abs(r.a[0].second) == 1 && abs(r.a[1].second) == 1) {
                if (r.a[0].second == -r.a[1].second) uf[r.a[1].first] = r.a[0].first;
                else { fixv[r.a[0].first] = 0; fixv[r.a[1].first] = 0; }
                alive[i] = 0; pch = true; continue;
            }
        }
    }
    vector<int> newidx(n, -1);
    int n2 = 0;
    vector<LPRow> rows2;
    for (size_t i = 0; i < rows.size(); i++) if (alive[i]) {
        LPRow r; r.b = rows[i].b;
        map<int, long long> acc;
        for (auto& t : rows[i].a) { int v = fnd(t.first); if (fixv[v] >= 0) r.b -= (long long)t.second * fixv[v]; else acc[v] += t.second; }
        for (auto& kv : acc) if (kv.second) { if (newidx[kv.first] < 0) newidx[kv.first] = n2++; r.a.push_back({newidx[kv.first], (int)kv.second}); }
        if (r.a.empty()) { if (r.b != 0) return false; continue; }
        rows2.push_back(r);
    }
    rows.swap(rows2);
    int n0 = n;
    n = n2; m = rows.size();
    if (m == 0) return true;
    if (getenv("BMLPDBG")) fprintf(stderr, "lp m=%d n=%d nv=%d per=%d NL=%d\n", m, n, nv, per, NL);
    vector<double> val, y;
    int st = phase1(rows, n, val, y);
    if (st < 0) { lp_cert_fail++; return true; }
    if (st == 1) {
        lastsol.assign(16 * N * NC, 0.0);
        for (int v = 0; v < nv && v < n0; v++) {
            int r = fnd(v);
            double x = fixv[r] >= 0 ? (double)fixv[r] : (newidx[r] >= 0 ? val[newidx[r]] : 0.0);
            int k = v % per, c = v / per;
            lastsol[vkey[k] + c * 16 * N] = x;
        }
        return true;
    }
    static const long long Ls[] = {1, 2, 3, 4, 6, 8, 12, 24, 60, 120, 360, 720, 2520, 5040, 27720};
    for (long long Lm : Ls) {
        vector<long long> z(m);
        bool okz = true;
        for (int i = 0; i < m; i++) { double v = y[i] * Lm; if (fabs(v) > 1e15) { okz = false; break; } z[i] = llround(v); }
        if (!okz) continue;
        vector<i128> col(n, 0);
        i128 bz = 0;
        for (int i = 0; i < m; i++) {
            if (!z[i]) continue;
            bz += (i128)z[i] * rows[i].b;
            for (auto& t : rows[i].a) col[t.first] += (i128)z[i] * t.second;
        }
        if (bz <= 0) continue;
        bool good = true;
        for (int j = 0; j < n; j++) if (col[j] > 0) { good = false; break; }
        if (good) { lp_prunes++; return false; }
    }
    lp_cert_fail++;
    return true;
}

// ------------------------------------------------------------------ saturation and full-output rules (exact, integer)
// A tile that certainly receives a full belt (D units) from one feeder can take no other in-link, because every link
// carries a positive amount (every piece carries flow, and a splitter shares its flow over all its used outputs).
// Saturated: an in-port tile whose port brings exactly D; the definite target of a forced belt / exit / entrance that
// is saturated. Then any other possible feeder of that tile must not push into it.
static bool sat_rules(bool& changed) {
    static int satfrom[MAXN];       // -2 not saturated, -1 fed by its in port, else feeder tile
    for (int p = 0; p < N; p++) satfrom[p] = -2;
    for (auto& ip : ins) {
        long long t = 0;
        for (int c = 0; c < NC; c++) t += ip.vec[c];
        if (t == D) satfrom[ip.cell] = -1;
    }
    bool grow = true;
    while (grow) {
        grow = false;
        for (int r = 0; r < N; r++) {
            if (satfrom[r] == -2 || !single(dom[r]) || dom[r] == BIT_E) continue;
            int v = only(dom[r]), k = KIND(v), o = DIRV(v);
            int q = -1;
            if (k == KB || k == KO) { q = nb(r, o); if (q >= 0 && !(single(dom[q]) && (dom[q] & ACC[o]))) q = -1; }
            else if (k == KI) {
                Mask od = BIT(VAL(KO, o));
                for (int n = 1; n <= REACH; n++) {
                    int t = nbn(r, o, n);
                    if (t < 0) break;
                    if (dom[t] & od) { if (dom[t] == od) q = t; break; }
                }
            }
            if (q >= 0 && satfrom[q] == -2) { satfrom[q] = r; grow = true; }
        }
    }
    for (int q = 0; q < N; q++) {
        if (satfrom[q] == -2) continue;
        int f = satfrom[q];
        // a full in-port tile with more than one in port is overfull
        if (f == -1) {
            long long t = 0;
            for (int d = 0; d < 4; d++) if (portKind[q][d] == 1) for (int c = 0; c < NC; c++) t += portVec[q][d][c];
            if (t > D) return false;
        } else {
            for (int d = 0; d < 4; d++) if (portKind[q][d] == 1) {
                long long t = 0;
                for (int c = 0; c < NC; c++) t += portVec[q][d][c];
                if (t > 0 && (dom[q] & ~ACC[OPP(d)]) == 0) return false;     // that port feeds it for sure
            }
        }
        for (int e = 0; e < 4; e++) {
            int r = nb(q, OPP(e));
            if (r < 0 || r == f) continue;
            if ((dom[q] & ~ACC[e]) == 0) {                    // q certainly accepts e: r must not push e
                if (!setdom(r, dom[r] & ~PUSH[e], changed)) return false;
            } else if (single(dom[r]) && (dom[r] & PUSH[e])) { // r certainly pushes e: q must not accept e
                if (!setdom(q, dom[q] & ~ACC[e], changed)) return false;
            }
        }
        if (single(dom[q]) && KIND(only(dom[q])) == KO) {
            int d = DIRV(only(dom[q]));
            Mask od = BIT(VAL(KO, d)), id = BIT(VAL(KI, d));
            bool between_possible = false;                    // some tile between could still become O d
            for (int n = 1; n <= REACH; n++) {
                int r = nbn(q, OPP(d), n);
                if (r < 0) break;
                if (r != f && (dom[r] & id) && !between_possible)
                    if (!setdom(r, dom[r] & ~id, changed)) return false;
                if (dom[r] == od) break;
                if (dom[r] & od) between_possible = true;
            }
        }
    }
    return true;
}
// A splitter at an out port sends exactly the port's total t through every used output, so it has at most D / t
// used outputs (its tile carries at most D).
static bool outsplit_rules(bool& changed) {
    for (auto& op : outs) {
        long long t = 0;
        for (int c = 0; c < NC; c++) t += op.vec[c];
        if (t <= 0) continue;
        long long kmax = D / t;
        if (kmax >= 3) continue;
        int p = op.cell;
        for (int o = 0; o < 4; o++) {
            int v = VAL(KS, o);
            if (!(dom[p] & BIT(v))) continue;
            int ds[3] = {o, (o + 1) & 3, (o + 3) & 3};
            bool pushes = false;
            for (int d : ds) if (d == op.d) pushes = true;
            if (!pushes) continue;
            int sure = 0;
            vector<int> maybe;
            for (int d : ds) {
                int q = nb(p, d);
                if (q < 0) { if (portKind[p][d] == 2) sure++; continue; }
                if ((dom[q] & ACC[d]) == 0) continue;
                if ((dom[q] & ~ACC[d]) == 0) sure++; else maybe.push_back(d);
            }
            if (sure > kmax) { if (!setdom(p, dom[p] & ~BIT(v), changed)) return false; continue; }
            if (sure == kmax && dom[p] == BIT(v))
                for (int d : maybe) { int q = nb(p, d); if (!setdom(q, dom[q] & ~ACC[d], changed)) return false; }
        }
    }
    return true;
}
// Upper bounds per commodity (doubles, valid at every iterate from D: a loop can carry more than the input): a tile gets at most its
// source plus what every possible feeder could send (a forced splitter sends at most 1/(certainly used outputs)).
// An out port whose upper bound is below its demand fails.
static double ub[MAXN][MAXC], ubn[MAXN][MAXC];
static bool flow_ub() {
    static double tot[MAXC];
    for (int c = 0; c < NC; c++) { tot[c] = 0; for (auto& ip : ins) tot[c] += ip.vec[c]; }
    static double msh[MAXN];
    for (int r = 0; r < N; r++) {
        msh[r] = 1;
        if (single(dom[r]) && dom[r] != BIT_E && KIND(only(dom[r])) == KS) {
            int o = DIRV(only(dom[r]));
            int ds[3] = {o, (o + 1) & 3, (o + 3) & 3}, sure = 0;
            for (int d : ds) {
                int q = nb(r, d);
                if (q < 0) { if (portKind[r][d] == 2) sure++; continue; }
                if ((dom[q] & ACC[d]) && (dom[q] & ~ACC[d]) == 0) sure++;
            }
            if (sure > 1) msh[r] = 1.0 / sure;
        }
        for (int c = 0; c < NC; c++) ub[r][c] = dom[r] == BIT_E ? 0 : (double)D;   // not min(D, input): loops carry more
    }
    vector<pair<int, int>> L = possible_links();
    for (int it = 0; it < 4 * N + 10; it++) {
        for (int q = 0; q < N; q++) for (int c = 0; c < NC; c++) ubn[q][c] = srcv[q][c];
        for (auto& l : L) for (int c = 0; c < NC; c++) ubn[l.second][c] += ub[l.first][c] * msh[l.first];
        double diff = 0;
        for (int q = 0; q < N; q++) for (int c = 0; c < NC; c++) {
            double v = min(ub[q][c], ubn[q][c]);
            diff = max(diff, ub[q][c] - v);
            ub[q][c] = v;
        }
        if (diff < 1e-9 * D) break;
    }
    for (auto& op : outs) {
        int p = op.cell;
        for (int c = 0; c < NC; c++)
            if (ub[p][c] * (1 + 1e-9) + 1e-9 < op.vec[c]) return false;
    }
    return true;
}

// ---- interval bounds on the LP rows (exact integers: every coefficient is +-1 and every right side an integer, so
// all bounds stay integers; they are valid real bounds for the real flows, which satisfy every row). A link, entry or
// exit whose flow is bounded by 0 for every commodity cannot exist (every link carries a positive amount): filter the
// domains accordingly. lo > hi anywhere: the node is infeasible.
long long bnd_calls = 0, bnd_fails = 0, bnd_filters = 0;
static bool bounds_prop(vector<LPRow>& rows, int n, int nv, int per, const vector<int>& vkey, bool& changed) {
    bnd_calls++;
    vector<long long> lo(n, 0), hi(n, D);
    bool ch = true;
    int it = 0;
    while (ch && it++ < 200) {
        ch = false;
        for (auto& r : rows) {
            long long sl = 0, sh = 0;
            bool unit = true;
            for (auto& t : r.a) {
                if (t.second == 1) { sl += lo[t.first]; sh += hi[t.first]; }
                else if (t.second == -1) { sl -= hi[t.first]; sh -= lo[t.first]; }
                else { unit = false; break; }
            }
            if (!unit) continue;
            if (r.b < sl || r.b > sh) { bnd_fails++; return false; }
            for (auto& t : r.a) {
                int j = t.first;
                long long tl = t.second == 1 ? lo[j] : -hi[j], th = t.second == 1 ? hi[j] : -lo[j];
                long long nl = r.b - (sh - th), nh = r.b - (sl - tl);   // bounds of this term
                long long vl, vh;
                if (t.second == 1) { vl = nl; vh = nh; } else { vl = -nh; vh = -nl; }
                if (vl > lo[j]) { lo[j] = vl; ch = true; }
                if (vh < hi[j]) { hi[j] = vh; ch = true; }
                if (lo[j] > hi[j]) { bnd_fails++; return false; }
            }
        }
    }
    // filtering: keys with zero upper bound in every commodity
    for (int k = 0; k < per; k++) {
        bool zero = true;
        for (int c = 0; c < NC && zero; c++) if (hi[k + c * per] > 0) zero = false;
        if (!zero) continue;
        int key = vkey[k];
        int kind = key / (4 * N), rest = key % (4 * N), cell = rest / 4, d = rest % 4;
        if (kind == 0) {            // surface link cell -> nb(cell, d)
            int q = nb(cell, d);
            if ((dom[cell] & ~PUSH[d]) == 0) { bnd_filters++; if (!setdom(q, dom[q] & ~ACC[d], changed)) return false; }
            else if ((dom[q] & ~ACC[d]) == 0) { bnd_filters++; if (!setdom(cell, dom[cell] & ~PUSH[d], changed)) return false; }
        } else if (kind == 2) {     // entry: I d at cell carries nothing
            if (dom[cell] & BIT(VAL(KI, d))) { bnd_filters++; if (!setdom(cell, dom[cell] & ~BIT(VAL(KI, d)), changed)) return false; }
        } else if (kind == 3) {     // exit: O d at cell gets nothing
            if (dom[cell] & BIT(VAL(KO, d))) { bnd_filters++; if (!setdom(cell, dom[cell] & ~BIT(VAL(KO, d)), changed)) return false; }
        }
    }
    (void)nv;
    return true;
}

// ------------------------------------------------------------------ flow bounds with reasons (CDCL)
// The LP's flow variables over the root domains of the combo (key x commodity, j = k * NC + c) and its rows with
// +-1 coefficients: conservation per tile and per channel cell, capacity per tile, forced-splitter shares. Rows are
// fixed; a variable whose key is no longer possible gets hi = 0. A splitter row holds while its enforcement items
// hold (dom[q] inside M). Integer bounds through +-1 rows with integer right sides stay valid for real flows.
struct FKey { int kind, p, d; };       // 0 link p -> nb(p, d), 1 entry (I d at p), 2 exit (O d at p), 3 channel p -> p+d
struct FRow { vector<pair<int, int>> a; long long b; bool eq; Pairs enf; };
vector<FKey> fkeys;
vector<FRow> frows;
bool lcg_on = false, use_lcg = true, lcg_lean = true;   // lean: with flow bounds on, skip the checks without reasons (LP excepted)
long long lcg_props = 0, lcg_fails = 0;
// key possible on the current domains (monotone); otherwise why gets the removals that rule it out
static bool key_possible(const FKey& k, Pairs* why) {
    int p = k.p, d = k.d;
    if (k.kind == 0) {
        int q = nb(p, d);
        if (!(dom[p] & PUSH[d])) { if (why) why->push_back({p, PUSH[d]}); return false; }
        if (!(dom[q] & ACC[d])) { if (why) why->push_back({q, ACC[d]}); return false; }
        return true;
    }
    if (k.kind == 1 || k.kind == 2) {
        Mask b = BIT(VAL(k.kind == 1 ? KI : KO, d));
        if (dom[p] & b) return true;
        if (why) why->push_back({p, b});
        return false;
    }
    // channel p -> p+d: some I d at p - n d (0 <= n < 7) with some O d at p + m d (m >= 1, n + m <= 7)
    Mask bi = BIT(VAL(KI, d)), bo = BIT(VAL(KO, d));
    for (int n = 0; n < REACH; n++) {
        int r = nbn(p, OPP(d), n);
        if (r < 0) break;
        if (!(dom[r] & bi)) continue;
        for (int m = 1; n + m <= REACH; m++) { int q = nbn(p, d, m); if (q < 0) break; if (dom[q] & bo) return true; }
    }
    if (why) {
        for (int n = 0; n < REACH; n++) { int r = nbn(p, OPP(d), n); if (r < 0) break; if (!(dom[r] & bi)) why->push_back({r, bi}); }
        for (int m = 1; m <= REACH; m++) { int q = nbn(p, d, m); if (q < 0) break; if (!(dom[q] & bo)) why->push_back({q, bo}); }
    }
    return false;
}
// build on the current (root) domains; false if a row is already infeasible
static bool lcg_build() {
    fkeys.clear(); frows.clear();
    static int lk[MAXN][4], ek[MAXN][4], xk[MAXN][4], uk[MAXN][4];
    for (int p = 0; p < N; p++) for (int d = 0; d < 4; d++) lk[p][d] = ek[p][d] = xk[p][d] = uk[p][d] = -1;
    for (int p = 0; p < N; p++)
        for (int d = 0; d < 4; d++) {
            FKey k{0, p, d};
            if (nb(p, d) >= 0 && key_possible(k, nullptr)) { lk[p][d] = fkeys.size(); fkeys.push_back(k); }
            k.kind = 3;
            if (key_possible(k, nullptr)) { uk[p][d] = fkeys.size(); fkeys.push_back(k); }
        }
    for (int p = 0; p < N; p++)
        for (int d = 0; d < 4; d++) {
            if ((dom[p] & BIT(VAL(KI, d))) && uk[p][d] >= 0) { ek[p][d] = fkeys.size(); fkeys.push_back({1, p, d}); }
            int r = nb(p, OPP(d));
            if ((dom[p] & BIT(VAL(KO, d))) && r >= 0 && uk[r][d] >= 0) { xk[p][d] = fkeys.size(); fkeys.push_back({2, p, d}); }
        }
    int K = fkeys.size(), NV = K * NC;
    for (int s = 0; s < 2; s++) { fb[s].assign(NV, s == 0 ? D : 0); fhist[s].assign(NV, {}); }
    ng_rewatch = true;
    bevs.clear();
    auto J = [&](int k, int c) { return k * NC + c; };
    auto add = [&](FRow& r, int k, int c, int a) { if (k >= 0) r.a.push_back({J(k, c), a}); };
    auto port = [&](int p, int kind, int c) { long long s = 0; for (int d = 0; d < 4; d++) if (portKind[p][d] == kind) s += portVec[p][d][c]; return s; };
    for (int p = 0; p < N; p++)                                        // channel cells (also under empty tiles):
        for (int c = 0; c < NC; c++)                                   // u[p-d] + e[p] = u[p] + x[p]
            for (int d = 0; d < 4; d++) {
                int q = nb(p, OPP(d));
                FRow u{{}, 0, true, {}};
                if (q >= 0) add(u, uk[q][d], c, 1);
                add(u, ek[p][d], c, 1); add(u, uk[p][d], c, -1); add(u, xk[p][d], c, -1);
                if (!u.a.empty()) frows.push_back(u);
            }
    for (int p = 0; p < N; p++) {
        if (dom[p] == BIT_E) continue;
        FRow cap{{}, D, false, {}};
        for (int c = 0; c < NC; c++) {
            FRow r{{}, port(p, 2, c) - port(p, 1, c), true, {}};     // inflow - outflow = drain - src
            for (int d = 0; d < 4; d++) {
                int q = nb(p, OPP(d));
                if (q >= 0) { add(r, lk[q][d], c, 1); add(cap, lk[q][d], c, 1); }
                add(r, xk[p][d], c, 1); add(cap, xk[p][d], c, 1);
                add(r, lk[p][d], c, -1); add(r, ek[p][d], c, -1);
            }
            if (r.a.empty()) { if (r.b) return false; } else frows.push_back(r);
            cap.b -= port(p, 1, c);
        }
        if (cap.b < 0) return false;
        if (!cap.a.empty()) frows.push_back(cap);
        for (int o = 0; o < 4; o++) {                                  // splitter S o at p: an output that is certainly
            Mask so = BIT(VAL(KS, o));                                 // used carries the share; any other carries the
            if (!(dom[p] & so)) continue;                              // share or nothing
            int ds[3] = {o, (o + 1) & 3, (o + 3) & 3};
            for (int d1 : ds)
                for (int d2 : ds) {
                    if (d1 == d2) continue;
                    bool v1 = nb(p, d1) >= 0, v2 = nb(p, d2) >= 0;
                    if ((v1 ? lk[p][d1] < 0 : portKind[p][d1] != 2) || (v2 ? lk[p][d2] < 0 : portKind[p][d2] != 2)) continue;
                    if (!v1 && !v2) continue;
                    for (int c = 0; c < NC; c++) {                     // f1 - f2 <= 0, port shares as constants
                        FRow r{{}, 0, false, {{p, so}}};
                        if (v1) add(r, lk[p][d1], c, 1); else r.b -= portVec[p][d1][c];
                        if (v2) { add(r, lk[p][d2], c, -1); r.enf.push_back({nb(p, d2), ACC[d2]}); } else r.b += portVec[p][d2][c];
                        frows.push_back(r);
                    }
                }
        }
    }
    return true;
}
static bool lcg_prop(bool& changed) {
    lcg_props++;
    Pairs why;
    for (int k = 0; k < (int)fkeys.size(); k++) {                     // existence
        bool live = false;
        for (int c = 0; c < NC; c++) if (fb[0][k * NC + c] > 0) live = true;
        if (!live) continue;
        why.clear();
        if (key_possible(fkeys[k], &why)) continue;
        for (int c = 0; c < NC; c++) if (!setb(k * NC + c, 0, 0, why)) { lcg_fails++; return false; }
        changed = true;
    }
    auto enforced = [&](const FRow& r) { for (auto& e : r.enf) if (dom[e.first] & ~e.second) return false; return true; };
    // atom of term (j, a) for the lower (lowside) or the upper end of a*f_j, or none if it is the root bound
    auto term_atom = [&](int j, int a, bool lowside, Pairs& w) {
        int side = (a > 0) == lowside ? 1 : 0;
        if (side == 1 ? fb[1][j] > 0 : fb[0][j] < D) w.push_back({aenc(j, side), (Mask)fb[side][j]});
    };
    auto reason = [&](const FRow& r, int skip, bool lowside, Pairs& w) {
        w.clear();
        for (auto& e : r.enf) w.push_back({e.first, ALL & ~e.second});
        for (int t = 0; t < (int)r.a.size(); t++) if (t != skip) term_atom(r.a[t].first, r.a[t].second, lowside, w);
    };
    for (int round = 0, ch = 1; ch && round < 200; round++) {         // a cap only costs pruning
        ch = 0;
        for (auto& r : frows) {
            if (!enforced(r)) continue;
            for (bool again = true; again;) {
                again = false;
                long long sl = 0, sh = 0;                              // range of sum a f
                for (auto& t : r.a) {
                    long long lo = fb[1][t.first], hi = fb[0][t.first];
                    if (t.second > 0) { sl += lo; sh += hi; } else { sl -= hi; sh -= lo; }
                }
                if (sl > r.b || (r.eq && sh < r.b)) {
                    reason(r, -1, sl > r.b, why);
                    CR_dec = false; CRp = why; lcg_fails++;
                    return false;
                }
                for (int t = 0; t < (int)r.a.size() && !again; t++) {
                    int j = r.a[t].first, a = r.a[t].second;
                    long long tl = a > 0 ? fb[1][j] : -fb[0][j], th = a > 0 ? fb[0][j] : -fb[1][j];
                    long long up = r.b - (sl - tl), dn = r.eq ? r.b - (sh - th) : tl;   // dn <= a f_j <= up
                    bool ok = true;
                    if (up < th) { reason(r, t, true, why); ok = a > 0 ? setb(j, 0, up, why) : setb(j, 1, -up, why); again = true; }
                    else if (dn > tl) { reason(r, t, false, why); ok = a > 0 ? setb(j, 1, dn, why) : setb(j, 0, -dn, why); again = true; }
                    if (!ok) { lcg_fails++; return false; }
                }
                if (again) ch = 1, changed = true;
            }
        }
    }
    for (int k = 0; k < (int)fkeys.size(); k++) {                     // a key that carries nothing cannot exist
        auto& fk = fkeys[k];
        if (fk.kind == 3) continue;
        bool zero = true;
        for (int c = 0; c < NC && zero; c++) if (fb[0][k * NC + c] > 0) zero = false;
        if (!zero || !key_possible(fk, nullptr)) continue;
        why.clear();
        for (int c = 0; c < NC; c++) why.push_back({aenc(k * NC + c, 0), 0});
        int p = fk.p, d = fk.d;
        if (fk.kind == 0) {
            int q = nb(p, d);
            if (!(dom[p] & ~PUSH[d])) { why.push_back({p, ALL & ~PUSH[d]}); if (!setdom(q, dom[q] & ~ACC[d], changed, &why)) return false; }
            else if (!(dom[q] & ~ACC[d])) { why.push_back({q, ALL & ~ACC[d]}); if (!setdom(p, dom[p] & ~PUSH[d], changed, &why)) return false; }
        } else if (!setdom(p, dom[p] & ~BIT(VAL(fk.kind == 1 ? KI : KO, d)), changed, &why)) return false;
    }
    return true;
}

// LP over the flow-bound model: the enforced rows on x' = x - lo >= 0, plus x' <= hi - lo as rows. An exactly
// verified Farkas certificate of that system gives the conflict reason: the enforcement items of the rows it uses and
// the bounds those rows were built from (lo of every term, hi of a box row; a fixed variable also needs its hi).
long long lcglp_calls = 0, lcglp_prunes = 0;
static bool lcg_lp() {
    lcglp_calls++;
    int NV = fkeys.size() * NC;
    vector<int> col(NV, -1);
    int n = 0;
    for (int j = 0; j < NV; j++) if (fb[0][j] > fb[1][j]) col[j] = n++;
    vector<LPRow> rows;
    vector<int> src;                                                    // frows index, or -1 - j for a box row
    int ns = n;
    for (int i = 0; i < (int)frows.size(); i++) {
        auto& r = frows[i];
        bool en = true;
        for (auto& e : r.enf) if (dom[e.first] & ~e.second) en = false;
        if (!en) continue;
        LPRow L; L.b = r.b;
        for (auto& t : r.a) { L.b -= t.second * fb[1][t.first]; if (col[t.first] >= 0) L.a.push_back({col[t.first], t.second}); }
        if (!r.eq) L.a.push_back({ns++, 1});
        if (L.a.empty()) continue;                                      // lcg_prop has checked it
        rows.push_back(L); src.push_back(i);
    }
    for (int j = 0; j < NV; j++)
        if (col[j] >= 0 && fb[0][j] < D) { LPRow L; L.b = fb[0][j] - fb[1][j]; L.a = {{col[j], 1}, {ns++, 1}}; rows.push_back(L); src.push_back(-1 - j); }
    int m = rows.size();
    if (m == 0 || (long long)m * (ns + m) > 4000000) return true;
    vector<double> val, y;
    int st = phase1(rows, ns, val, y);
    if (st < 0) return true;
    if (st == 1) {                                                      // keep the point for the value order
        static const int kofs[4] = {0, 8, 12, 4};                     // lp_check's key layout: link, entry, exit, channel
        auto& ls = lastsols[0];
        ls.assign(16 * N * NC, 0.0);
        for (int j = 0; j < NV; j++) {
            auto& k = fkeys[j / NC];
            ls[kofs[k.kind] * N + k.p * 4 + k.d + (j % NC) * 16 * N] = fb[1][j] + (col[j] >= 0 ? val[col[j]] : 0.0);
        }
        return true;
    }
    static const long long Ls[] = {1, 2, 3, 4, 6, 8, 12, 24, 60, 120, 360, 720, 2520, 5040, 27720};
    for (long long Lm : Ls) {
        vector<long long> z(m);
        bool okz = true;
        for (int i = 0; i < m; i++) { double v = y[i] * Lm; if (fabs(v) > 1e15) { okz = false; break; } z[i] = llround(v); }
        if (!okz) continue;
        vector<i128> cs(ns, 0);
        i128 bz = 0;
        for (int i = 0; i < m; i++) {
            if (!z[i]) continue;
            bz += (i128)z[i] * rows[i].b;
            for (auto& t : rows[i].a) cs[t.first] += (i128)z[i] * t.second;
        }
        if (bz <= 0) continue;
        bool good = true;
        for (int j = 0; j < ns && good; j++) if (cs[j] > 0) good = false;
        if (!good) continue;
        Pairs why;
        vector<char> lo_used(NV, 0), hi_used(NV, 0);
        for (int i = 0; i < m; i++) {
            if (!z[i]) continue;
            if (src[i] < 0) { int j = -1 - src[i]; lo_used[j] = hi_used[j] = 1; continue; }
            auto& r = frows[src[i]];
            for (auto& e : r.enf) why.push_back({e.first, ALL & ~e.second});
            for (auto& t : r.a) { lo_used[t.first] = 1; if (col[t.first] < 0) hi_used[t.first] = 1; }
        }
        for (int j = 0; j < NV; j++) {
            if (lo_used[j] && fb[1][j] > 0) why.push_back({aenc(j, 1), (Mask)fb[1][j]});
            if (hi_used[j] && fb[0][j] < D) why.push_back({aenc(j, 0), (Mask)fb[0][j]});
        }
        lcglp_prunes++;
        CR_dec = false; CRp = why;
        return false;
    }
    return true;
}

// ------------------------------------------------------------------ propagation fixpoint
static bool local_fix() {
    bool changed = true;
    while (changed) {
        changed = false;
        for (int p = 0; p < N; p++) {
            Mask m = dom[p], nm = 0;
            for (Mask r = m; r; r &= r - 1) {
                int v = __builtin_ctz(r);
                if (supported(p, v)) nm |= BIT(v);
            }
            if (nm == m) continue;
            if (!R_ON) { if (!setdom(p, nm, changed)) return false; continue; }
            for (Mask r = m & ~nm; r; r &= r - 1) {       // one removal per value, each with its own reason
                int v = __builtin_ctz(r);
                Pairs why;
                explain_unsupported(p, v, why);
                if (!setdom(p, dom[p] & ~BIT(v), changed, &why)) return false;
            }
        }
        if (!unit_rules(changed)) return false;
        bool sr = use_sat && !(lcg_on && lcg_lean);
        if (sr && !sat_rules(changed)) return false;
        if (sr && !outsplit_rules(changed)) return false;
        if (!nogoods.empty() && !nogood_prop(changed)) return false;
    }
    return true;
}
static bool propagate() {
    fail_code = 0;
    while (true) {
        if (!local_fix()) return false;
        bool ch2 = false;
        fail_code = 1;
        if (!reach(ch2)) return false;
        fail_code = 0;
        if (ch2) continue;
        if (lcg_on) {
            bool chb = false;
            fail_code = 7;
            if (!lcg_prop(chb)) return false;
            fail_code = 0;
            if (chb) continue;
        } else if (use_bounds) {
            bool chb = false;
            fail_code = 6;
            if (!lp_check(lp_maxvars, 1, &chb)) return false;
            fail_code = 0;
            if (chb) continue;
        }
        break;
    }
    if (!(lcg_on && lcg_lean)) {
        fail_code = 2;
        if (!flow_lb()) return false;
        fail_code = 3;
        if (use_sat && !flow_ub()) return false;
        fail_code = 4;
        if (!flow_relax()) return false;
    }
    fail_code = 5;
    int lpk = R_ON ? cdcl_lpevery : lp_every;     // CDCL: the LP only every k levels (cheap nodes, learning does the rest)
    if (use_lp && (lpk <= 1 || path_depth() % lpk == 0)) {
        if (lcg_on) { if (!lcg_lp()) return false; }
        else if (lp_split && NC > 1) {
            for (int c = 0; c < NC; c++) { lp_only = c; bool ok = lp_check(lp_maxvars); lp_only = -1; if (!ok) return false; }
        } else if (!lp_check(lp_maxvars)) return false;
    }
    fail_code = 0;
    return true;
}

// ------------------------------------------------------------------ exact leaf check (check.py semantics)
// returns 1 ok, 0 fail, -1 uncertain (overflow)
static int leaf_check() {
    vector<int> idx(N, -1), cells;
    for (int p = 0; p < N; p++) if (dom[p] != BIT_E) { idx[p] = cells.size(); cells.push_back(p); }
    int n = cells.size();
    // outs per tile: link targets (tile index) and sinks (port dirs)
    vector<vector<int>> to(n);
    vector<vector<int>> sinks(n);
    for (int i = 0; i < n; i++) {
        int p = cells[i], v = only(dom[p]), k = KIND(v), o = DIRV(v);
        if (k == KI) {
            for (int s = 1; s <= REACH; s++) {
                int q = nbn(p, o, s);
                if (q < 0) break;
                if (dom[q] == BIT(VAL(KO, o))) { to[i].push_back(idx[q]); break; }
            }
            continue;
        }
        int ds[3]; int nd = push_dirs(v, ds);
        for (int j = 0; j < nd; j++) {
            int d = ds[j], q = nb(p, d);
            if (q < 0) { if (portKind[p][d] == 2) sinks[i].push_back(d); continue; }
            if (dom[q] & ACC[d]) to[i].push_back(idx[q]);
        }
    }
    vector<int> nout(n);
    for (int i = 0; i < n; i++) nout[i] = to[i].size() + sinks[i].size();
    // exact solve (also decides singular cases exactly)
    qctx.overflow = false;
    vector<vector<Q>> A(n, vector<Q>(n + NC));
    for (int i = 0; i < n; i++) {
        A[i][i] = Q(1);
        int p = cells[i];
        for (int c = 0; c < NC; c++) A[i][n + c] = Q((long long)llround(srcv[p][c]));
    }
    for (int i = 0; i < n; i++) {
        int k = KIND(only(dom[cells[i]]));
        Q sh = nout[i] == 0 ? Q(0) : (k == KS ? Q((i128)1, (i128)nout[i]) : Q(1));
        for (int q : to[i]) A[q][i] = subq(A[q][i], sh);
    }
    for (int c = 0; c < n; c++) {
        int piv = -1;
        for (int r = c; r < n; r++) if (!A[r][c].zero()) { piv = r; break; }
        if (piv < 0) return qctx.overflow ? -1 : 0;     // singular: no steady state
        swap(A[c], A[piv]);
        Q inv = divq(Q(1), A[c][c]);
        for (int k2 = c; k2 < n + NC; k2++) A[c][k2] = mulq(A[c][k2], inv);
        for (int r = 0; r < n; r++) {
            if (r == c || A[r][c].zero()) continue;
            Q f = A[r][c];
            for (int k2 = c; k2 < n + NC; k2++) if (!A[c][k2].zero()) A[r][k2] = subq(A[r][k2], mulq(f, A[c][k2]));
        }
        if (qctx.overflow) return -1;
    }
    Q Dq((long long)D);
    for (int i = 0; i < n; i++) {
        Q tot(0);
        for (int c = 0; c < NC; c++) tot = addq(tot, A[i][n + c]);
        if (cmpq(tot, Dq) > 0) return qctx.overflow ? -1 : 0;
        if (tot.n <= 0) return qctx.overflow ? -1 : 0;                        // idle
        if (nout[i] == 0) return qctx.overflow ? -1 : 0;                      // stuck
        int p = cells[i];
        int k = KIND(only(dom[p]));
        Q sh = k == KS ? Q((i128)1, (i128)nout[i]) : Q(1);
        for (int d : sinks[i])
            for (auto& op : outs) if (op.cell == p && op.d == d)
                for (int c = 0; c < NC; c++)
                    if (cmpq(mulq(A[i][n + c], sh), Q((long long)op.vec[c])) != 0) return qctx.overflow ? -1 : 0;
    }
    if (qctx.overflow) return -1;
    return 1;
}

// ------------------------------------------------------------------ search
int branch_mode = 1, wmode = 0;
bool rnd_on = false;
std::mt19937 rng(12345);
double wdeg[MAXN];
static int choose(int& bestv_order_dir) {
    int best = -1, bestscore = 1 << 30;
    bestv_order_dir = -1;
    for (int p = 0; p < N; p++) {
        if (single(dom[p])) continue;
        int fed = -1;
        for (int e = 0; e < 4 && fed < 0; e++) {
            int r = nb(p, OPP(e));
            if (r < 0) { if (portKind[p][OPP(e)] == 1) fed = e; continue; }
            if (single(dom[r]) && dom[r] != BIT_E && (dom[r] & PUSH[e])) fed = e;
        }
        int score;
        if (branch_mode == 0) score = popc(dom[p]) + (fed >= 0 ? 0 : 100) + ((dom[p] & BIT_E) ? 20 : 0);
        else if (branch_mode == 1) score = (int)(1000.0 * popc(dom[p]) / (1.0 + wdeg[p])) * 64 + (rnd_on ? (int)(rng() & 63) : 0);
        else score = popc(dom[p]);
        if (score < bestscore) { bestscore = score; best = p; bestv_order_dir = fed; }
    }
    return best;
}
int vorder = 3;
static vector<int> value_order(int p, int fed) {
    vector<int> order;
    Mask m = dom[p];
    auto add = [&](int v) { if ((m & BIT(v)) && find(order.begin(), order.end(), v) == order.end()) order.push_back(v); };
    if (vorder == 1) {
        if (fed >= 0) { add(VAL(KS, fed)); add(VAL(KB, fed)); }
        for (int o = 0; o < 4; o++) add(VAL(KS, o));
        for (int o = 0; o < 4; o++) add(VAL(KB, o));
    } else if (vorder == 3 && (lastsols[0].size() || lastsols[1].size())) {
        // LP-guided: prefer the piece whose links agree with the last LP flow through this tile
        auto fl = [&](int key) { return lp_flow(key); };
        double out[4], in[4], tot = 0, ent[4], ex[4];
        for (int e = 0; e < 4; e++) {
            out[e] = fl(p * 4 + e);
            int r = nb(p, OPP(e));
            in[e] = r >= 0 ? fl(r * 4 + e) : 0;
            ent[e] = fl(8 * N + p * 4 + e);
            ex[e] = fl(12 * N + p * 4 + e);
            tot += out[e] + in[e] + ent[e] + ex[e];
        }
        vector<pair<double, int>> sc;
        for (Mask r = m; r; r &= r - 1) {
            int v = __builtin_ctz(r);
            double score;
            if (v == 0) score = -tot;
            else {
                int k = KIND(v), o = DIRV(v);
                double good = 0, bad = 0;
                for (int e = 0; e < 4; e++) {
                    bool pushes = (PUSH[e] & BIT(v)) != 0, acc = (ACC[e] & BIT(v)) != 0;
                    (pushes ? good : bad) += out[e];
                    (acc ? good : bad) += in[e];
                    ((k == KI && e == o) ? good : bad) += ent[e];
                    ((k == KO && e == o) ? good : bad) += ex[e];
                }
                score = good - 2 * bad;
            }
            sc.push_back({-score, v});
        }
        sort(sc.begin(), sc.end());
        for (auto& t : sc) order.push_back(t.second);
        return order;
    } else if (vorder == 2) {
        vector<int> vs;
        for (Mask r = m; r; r &= r - 1) vs.push_back(__builtin_ctz(r));
        shuffle(vs.begin(), vs.end(), rng);
        return vs;
    } else if (fed >= 0) {
        add(VAL(KB, fed)); add(VAL(KB, (fed + 1) & 3)); add(VAL(KB, (fed + 3) & 3));
        add(VAL(KS, fed)); add(VAL(KI, fed));
    } else add(0);
    for (int k = 0; k < 4; k++) for (int o = 0; o < 4; o++) add(VAL(k, o));
    add(0);
    return order;
}

bool found = false;
// ---- restarts with nld-nogoods (Lecoutre et al.): when a restart cuts the search, every refuted value x_i != v_i on the
// current branch means the subtree {positive decisions before i, x_i = v_i} was searched completely without success;
// that set is stored as a nogood (it can never hold again). Sound for INFEASIBLE, and complete because restart limits grow.
struct Lit { int p, v; bool pos; };
vector<Lit> path;
static size_t path_depth() { return R_ON ? curlvl : path.size(); }
vector<vector<pair<int, Mask>>> nogoods;
bool use_restarts = false, restart_now = false;
long long restart_limit = 0, fails_at_restart = 0, n_restarts = 0, base_fails = 100;
static long long luby(long long i) {
    long long k = 1;
    while ((1LL << k) - 1 < i + 1) k++;
    while (true) {
        if (i + 1 == (1LL << k) - 1) return 1LL << (k - 1);
        if (i + 1 >= (1LL << (k - 1))) { i -= (1LL << (k - 1)) - 1; k = 1; while ((1LL << k) - 1 < i + 1) k++; }
        else k--;
    }
}
static void record_nogoods() {
    vector<pair<int, Mask>> pos;
    for (auto& l : path) {
        if (l.pos) { pos.push_back({l.p, BIT(l.v)}); continue; }
        vector<pair<int, Mask>> ng = pos;
        ng.push_back({l.p, BIT(l.v)});
        nogoods.push_back(ng);
    }
}
static void note_fail() {
    fails++;
    if (use_restarts && !restart_now && fails - fails_at_restart > restart_limit) {
        restart_now = true;
        record_nogoods();
    }
}
// Item states: 1 holds, 0 cannot hold, 2 open. Cell item (p, M): x_p in M. Bound item: see aenc.
static inline int item_state(const pair<int, Mask>& it) {
    if (it.first >= 0) { Mask m = dom[it.first]; return !(m & it.second) ? 0 : (m & ~it.second) ? 2 : 1; }
    int j = (-1 - it.first) >> 1, side = (-1 - it.first) & 1;
    long long b = it.second;
    if (side == 0) return fb[0][j] <= b ? 1 : fb[1][j] > b ? 0 : 2;
    return fb[1][j] >= b ? 1 : fb[0][j] < b ? 0 : 2;
}
// As a reason: a holding cell item says "the values outside M were removed"; a bound item is its own reason.
static inline pair<int, Mask> item_reason(const pair<int, Mask>& it) { return it.first >= 0 ? make_pair(it.first, ALL & ~it.second) : it; }
// Two watched items per nogood, on items that do not hold (or, failing that, hold since the latest level); a nogood
// is looked at only when the cell or bound variable of a watched item changes. Watch keys: cell p, or N + j.
vector<vector<int>> ng_watch;
size_t ng_watched = 0;
static inline int item_key(const pair<int, Mask>& it) { return it.first >= 0 ? it.first : N + ((-1 - it.first) >> 1); }
static int hold_level(const pair<int, Mask>& it) {
    if (it.first >= 0) {
        int l = 0;
        for (Mask r = ALL & ~it.second; r; r &= r - 1) l = max(l, ev_lvl[it.first * 17 + __builtin_ctz(r)]);
        return l;
    }
    int j = (-1 - it.first) >> 1, side = (-1 - it.first) & 1;
    for (int i : fhist[side][j]) if (side == 0 ? bevs[i].val <= (long long)it.second : bevs[i].val >= (long long)it.second) return bevs[i].lvl;
    return 0;
}
// a nogood whose watches both hold (or its only item): conflict, unit or nothing; shared by attach and the queue
static bool ng_check(const vector<pair<int, Mask>>& ng, bool& changed) {
    int und = -1, nund = 0;
    for (size_t i = 0; i < ng.size(); i++) {
        int st = item_state(ng[i]);
        if (st == 0) return true;
        if (st == 2) { nund++; und = i; if (nund > 1) return true; }
    }
    if (nund == 1 && ng[und].first < 0) return true;                   // bound atoms are never asserted (see aenc)
    Pairs why;
    if (R_ON) for (size_t i = 0; i < ng.size(); i++) if ((int)i != und) why.push_back(item_reason(ng[i]));
    if (nund == 0) {
        if (R_ON) { CR_dec = false; CRp = why; }
        return false;
    }
    auto& l = ng[und];
    return setdom(l.first, dom[l.first] & ~l.second, changed, R_ON ? &why : nullptr);
}
static void ng_attach(int i) {
    auto& ng = nogoods[i];
    auto rank = [&](const pair<int, Mask>& it) { return item_state(it) != 1 ? 1 << 30 : hold_level(it); };
    for (int w = 0; w < 2 && w < (int)ng.size(); w++) {
        int best = w;
        for (int k = w + 1; k < (int)ng.size(); k++) if (rank(ng[k]) > rank(ng[best])) best = k;
        swap(ng[w], ng[best]);
        ng_watch[item_key(ng[w])].push_back(i);
    }
}
// erase nogoods [a, b) and keep the other watches as they are (re-attaching mid-search could break the invariant)
static void ng_erase(size_t a, size_t b) {
    nogoods.erase(nogoods.begin() + a, nogoods.begin() + b);
    int sh = b - a;
    for (auto& wl : ng_watch) {
        size_t w = 0;
        for (int i : wl) if (i < (int)a) wl[w++] = i; else if (i >= (int)b) wl[w++] = i - sh;
        wl.resize(w);
    }
    ng_watched = ng_watched >= b ? ng_watched - sh : min(ng_watched, a);
}
static bool nogood_prop(bool& changed) {
    if (ng_rewatch || ng_watched > nogoods.size()) {
        ng_watch.assign(N + fb[0].size(), {});
        ng_watched = 0; ng_rewatch = false;
    }
    if ((int)ng_watch.size() < N + (int)fb[0].size()) ng_watch.resize(N + fb[0].size());
    for (; ng_watched < nogoods.size(); ng_watched++) {
        ng_attach(ng_watched);
        if (!ng_check(nogoods[ng_watched], changed)) return false;
    }
    while (ng_qhead < trail.size()) {
        int e = trail[ng_qhead++].first, key = e >= 0 ? e : N + (-1 - e);      // a bound entry holds -1 - j
        auto& wl = ng_watch[key];
        for (size_t x = 0; x < wl.size();) {
            int i = wl[x];
            auto& ng = nogoods[i];
            int w = item_key(ng[0]) == key ? 0 : (ng.size() > 1 && item_key(ng[1]) == key) ? 1 : -1;
            if (w < 0) { wl[x] = wl.back(); wl.pop_back(); continue; } // stale
            if (item_state(ng[w]) != 1) { x++; continue; }
            int k = 2;
            while (k < (int)ng.size() && item_state(ng[k]) == 1) k++;
            if (k < (int)ng.size()) {                                   // move the watch
                swap(ng[w], ng[k]);
                int nk = item_key(ng[w]);
                if (nk != key) { ng_watch[nk].push_back(i); wl[x] = wl.back(); wl.pop_back(); continue; }
                x++; continue;
            }
            x++;
            if (!ng_check(ng, changed)) return false;
        }
    }
    return true;
}
// ---- probing (singleton consistency): a value whose assignment fails propagation is removed. Sound because
// propagation is sound. Run at the root of each port combo and at nodes up to --probedepth decisions deep.
long long probes = 0, probe_removed = 0;
int probe_depth = 0;
static bool probe_all() {
    bool again = true;
    while (again) {
        again = false;
        for (int p = 0; p < N; p++) {
            if (single(dom[p])) continue;
            for (Mask r = dom[p]; r; r &= r - 1) {
                int v = __builtin_ctz(r);
                if (!(dom[p] & BIT(v))) continue;
                size_t mk = trail.size();
                bool ch = false;
                setdom(p, BIT(v), ch);
                probes++;
                if ((probes & 7) == 0 && chrono::duration<double>(chrono::steady_clock::now() - t0).count() > timeout) { timed_out = true; undo(mk); return true; }
                bool ok = propagate();
                undo(mk);
                if (timed_out) return true;
                if (!ok) {
                    probe_removed++;
                    if (path.empty()) nogoods.push_back({{p, BIT(v)}});   // root fact: keep it across restarts
                    if (!setdom(p, dom[p] & ~BIT(v), ch)) return false;
                    if (!propagate()) return false;
                    again = true;
                }
            }
        }
        double el = chrono::duration<double>(chrono::steady_clock::now() - t0).count();
        if (el > timeout) { timed_out = true; return true; }
    }
    return true;
}
// ---- learned nogoods from failing checks (domain generalisation). Every global check (reach, flow LB, flow UB,
// max-flow, LP) is monotone in the domains: if it fails for domains M it fails for any smaller domains (fewer possible
// links, more forced tiles only tighten each relaxation). So when a check fails, we widen the domains cell by cell back
// to the root domains as long as the same check, run directly on the widened domains, still fails. The final widened
// domains M give the nogood NOT(x_p in M_p for all p with M_p != root_p); it holds in every node of this port combo.
bool use_learn = false, learn_lp = true, learn_local = true;
Mask rootdom[MAXN];
bool have_root = false, probed_root = false;
long long learned = 0, learn_checks = 0, learned_len = 0;
size_t max_nogoods = 20000;
static bool run_check(int code) {
    size_t mk = trail.size();
    bool ok = true;
    if (code == 0) ok = local_fix();
    else if (code == 1) { bool ch = false; ok = reach(ch); }
    else if (code == 2) ok = flow_lb();
    else if (code == 3) ok = flow_ub();
    else if (code == 4) ok = flow_lb() && flow_relax();
    else if (code == 5) {
        lp_nowarm = true;
        if (lp_split && NC > 1) { ok = flow_lb(); for (int c = 0; c < NC && ok; c++) { lp_only = c; ok = lp_check(lp_maxvars); lp_only = -1; } }
        else ok = lp_check(lp_maxvars);
        lp_nowarm = false;
    }
    else if (code == 6) { bool ch = false; ok = lp_check(lp_maxvars, 1, &ch); }
    undo(mk);
    return ok;
}
static void learn_from_fail(int code) {
    if (!use_learn || !have_root || nogoods.size() >= max_nogoods) return;
    if (code == 0 && !learn_local) return;
    if (code == 5 && !learn_lp) return;
    static Mask snap[MAXN];
    for (int p = 0; p < N; p++) snap[p] = dom[p];
    // the failure state: domains as they are now (the failing check saw exactly these)
    if (run_check(code)) return;             // cannot reproduce (should not happen): learn nothing
    vector<int> order;
    for (int p = 0; p < N; p++) if (dom[p] != rootdom[p] && !single(dom[p])) order.push_back(p);
    for (int p = 0; p < N; p++) if (dom[p] != rootdom[p] && single(dom[p])) order.push_back(p);
    int budget = code == 5 ? 24 : 1000;
    // widen in chunks (most cells are irrelevant to a failure); a chunk that breaks the failure is retried cell by cell
    const size_t CH = 6;
    for (size_t a = 0; a < order.size() && budget > 0; a += CH) {
        vector<pair<int, Mask>> saved;
        for (size_t b = a; b < min(order.size(), a + CH); b++) { int p = order[b]; saved.push_back({p, dom[p]}); dom[p] = rootdom[p]; }
        learn_checks++; budget--;
        if (!run_check(code)) continue;          // still fails with the whole chunk widened
        for (auto& sv : saved) dom[sv.first] = sv.second;
        if (saved.size() == 1) continue;
        for (auto& sv : saved) {
            if (budget-- <= 0) break;
            int p = sv.first;
            dom[p] = rootdom[p];
            learn_checks++;
            if (run_check(code)) dom[p] = sv.second;
        }
    }
    vector<pair<int, Mask>> ng;
    for (int p = 0; p < N; p++) if (dom[p] != rootdom[p]) ng.push_back({p, dom[p]});
    for (int p = 0; p < N; p++) dom[p] = snap[p];
    nogoods.push_back(ng);
    learned++;
    learned_len += ng.size();
}
long long fail_by_code[8];
static void on_prop_fail() {
    int code = fail_code;
    fail_by_code[code]++;
    learn_from_fail(code);
}
// returns 1 found, 0 subtree exhausted, 2 aborted (timeout or restart)
static int dfs() {
    nodes++;
    if ((nodes & 15) == 0) {
        double el = chrono::duration<double>(chrono::steady_clock::now() - t0).count();
        if (el > timeout) timed_out = true;
    }
    if (timed_out || restart_now) return 2;
    if (!propagate()) { on_prop_fail(); note_fail(); return 0; }
    if (!have_root && path.empty()) { for (int q = 0; q < N; q++) rootdom[q] = dom[q]; have_root = true; }
    if ((int)path.size() < probe_depth || (path.empty() && probe_depth >= 0 && !probed_root)) {
        if (path.empty()) probed_root = true;
        if (!probe_all()) { note_fail(); return 0; }
        if (timed_out) return 2;
    }
    if (getenv("BMDUMP") && nodes % atoi(getenv("BMDUMP")) == 0) {
        fprintf(stderr, "node %lld\n", nodes);
        for (int y = 0; y < H; y++) {
            for (int x = 0; x < W; x++) {
                Mask m = dom[y * W + x];
                if (single(m)) { int v = only(m); if (v) fprintf(stderr, " %c%d  ", KCH[KIND(v)], DIRV(v)); else fprintf(stderr, " ..  "); }
                else fprintf(stderr, " ?%-2d%c", popc(m), (m & BIT_E) ? 'e' : ' ');
            }
            fprintf(stderr, "\n");
        }
    }
    int fed;
    int p = choose(fed);
    if (p < 0) {
        leaves++;
        int r = leaf_check();
        if (r == 1) return 1;
        if (r < 0) uncertain++;
        note_fail();
        return 0;
    }
    vector<int> order = value_order(p, fed);
    size_t mark = trail.size(), pmark = path.size();
    int result = 0;
    for (size_t i = 0; i < order.size(); i++) {
        int v = order[i];
        if (!(dom[p] & BIT(v))) continue;
        bool ch = false;
        size_t m2 = trail.size();
        setdom(p, BIT(v), ch);
        path.push_back({p, v, true});
        long long f0 = fails;
        int r = dfs();
        if (r == 1) return 1;
        path.pop_back();
        if (wmode == 0) wdeg[p] += (fails - f0) > 0 ? 1 : 0;
        else if (wmode == 1) wdeg[p] += (fails - f0) == 1 ? 1 : 0;
        else wdeg[p] += (fails - f0);
        if (r == 2) { result = 2; break; }
        undo(m2);
        // refute v
        if (!setdom(p, dom[p] & ~BIT(v), ch)) { result = 0; break; }
        path.push_back({p, v, false});
        if (single(dom[p])) {
            r = dfs();
            if (r == 1) return 1;
            result = r;
            break;
        }
        if (!propagate()) { on_prop_fail(); note_fail(); result = 0; break; }
        if (restart_now) { result = 2; break; }
    }
    undo(mark);
    path.resize(pmark);
    return result;
}

// ------------------------------------------------------------------ CDCL search (--cdcl)
// Decisions x_p = v; every removal carries a reason (see setdom); on a conflict, 1-UIP analysis over removal events
// gives a nogood that is asserting after a backjump. Reasons are exact for the local rules (supports, unit rules,
// nogoods, reach cuts) and DEC (= all decisions up to that level) for the rest, which is always valid.
bool use_cdcl = true;              // default: hybrid (2000 DFS fails, then CDCL); --dfs = plain DFS, --cdcl = pure CDCL
long long hybrid_fails = 2000;
double cbump = 1.05;
long long cdcl_db = 4000;
bool gexplain = false;   // --gexpl: widened reasons for global failures (fewer conflicts, slower overall)
size_t perm_nogoods = 0;
bool minimize = true;
long long conflicts = 0, bad_reasons = 0, learned_cdcl = 0, learned_cdcl_len = 0;
vector<pair<int, Mask>> decs;     // decision x_p in S
vector<size_t> lvl_mark;
static vector<char> seen_ev;
static inline int ev_level(int ev) { return ev < BEV0 ? ev_lvl[ev] : bevs[ev - BEV0].lvl; }
// 1-UIP over value removals and bound events. The asserting item is always a cell item (bound atoms are never
// asserted, see aenc), so resolution goes on past a bound event at the current level.
static void analyze(vector<pair<int, Mask>>& items, int& bj) {
    if (seen_ev.size() < BEV0 + bevs.size()) seen_ev.resize(BEV0 + bevs.size() + 1024, 0);
    vector<int> marked, low;
    static vector<long long> need;                                      // per bound event: the weakest bound needed
    if (need.size() < bevs.size()) need.resize(bevs.size() + 1024);
    int counter = 0;
    bool bad = false;
    auto mark = [&](int ev) {
        if (ev_level(ev) == 0 || seen_ev[ev]) return false;
        seen_ev[ev] = 1; marked.push_back(ev);
        if (ev_level(ev) == curlvl) counter++; else low.push_back(ev);
        return true;
    };
    auto bound_event = [&](int q, long long b) {                       // earliest event implying the bound atom,
        int j = (-1 - q) >> 1, side = (-1 - q) & 1;                     // -1: the root bound [0, D]
        for (int i : fhist[side][j]) if (side == 0 ? bevs[i].val <= b : bevs[i].val >= b) return i;
        return -1;
    };
    auto addpair = [&](int q, Mask M) {
        if (q < 0) {
            int j = (-1 - q) >> 1, side = (-1 - q) & 1;
            long long b = M;
            if (side == 0 ? fb[0][j] > b : fb[1][j] < b) { bad = true; return; }
            int i = bound_event(q, b);
            if (i < 0) return;
            int ev = BEV0 + i;
            if (!seen_ev[ev]) need[i] = b;
            else need[i] = side == 0 ? min(need[i], b) : max(need[i], b);
            mark(ev);
            return;
        }
        for (Mask r = M; r; r &= r - 1) {
            int w = __builtin_ctz(r);
            if (dom[q] & BIT(w)) { bad = true; continue; }    // invalid reason: must never happen
            mark(q * 17 + w);
        }
    };
    auto expand = [&](int rs, int rl) { for (int i = 0; i < rl; i++) addpair(rpool[rs + i].first, rpool[rs + i].second); };
    auto adddec = [&](int L) { for (int l = 1; l <= L; l++) addpair(decs[l - 1].first, ALL & ~decs[l - 1].second); };
    if (CR_dec) adddec(curlvl); else for (auto& pr : CRp) addpair(pr.first, pr.second);
    int uip = -1; bool uip_dec = false;
    for (int idx = (int)evtrail.size() - 1; idx >= 0 && !bad; idx--) {
        int ev = evtrail[idx];
        if (!seen_ev[ev] || ev_level(ev) != curlvl) continue;
        if (ev >= BEV0) { counter--; expand(bevs[ev - BEV0].rs, bevs[ev - BEV0].rl); continue; }
        if (counter == 1) { uip = ev; break; }
        if (ev_rl[ev] == -1) { uip_dec = true; break; }
        counter--;
        if (ev_rl[ev] == -2) adddec(curlvl);
        else expand(ev_rs[ev], ev_rl[ev]);
    }
    // minimization: drop a lower-level event whose reason is implied by events already in the analysis
    auto implied = [&](int rs, int rl) {
        for (int i = 0; i < rl; i++) {
            auto& pr = rpool[rs + i];
            if (pr.first < 0) {          // a lower-level bound event enters the clause with need[k]: tighten it to cover b
                int k = bound_event(pr.first, pr.second);
                if (k < 0 || bevs[k].lvl == 0) continue;
                if (!seen_ev[BEV0 + k]) return false;
                if (bevs[k].lvl < curlvl) need[k] = bevs[k].side == 0 ? min(need[k], (long long)pr.second) : max(need[k], (long long)pr.second);
                continue;
            }
            for (Mask r = pr.second; r; r &= r - 1) { int ev = pr.first * 17 + __builtin_ctz(r); if (ev_lvl[ev] > 0 && !seen_ev[ev]) return false; }
        }
        return true;
    };
    if (!bad && minimize) {
        size_t w = 0;
        for (int ev : low) {
            int rs = ev < BEV0 ? ev_rs[ev] : bevs[ev - BEV0].rs, rl = ev < BEV0 ? ev_rl[ev] : bevs[ev - BEV0].rl;
            if (rl >= 0 && implied(rs, rl)) continue;
            low[w++] = ev;
        }
        low.resize(w);
    }
    for (int ev : marked) seen_ev[ev] = 0;
    if (bad || (uip < 0 && !uip_dec)) {
        // safe fallback: the nogood of all decisions
        bad_reasons += bad;
        items.clear();
        for (int l = 1; l <= curlvl; l++) items.push_back(decs[l - 1]);
        bj = curlvl - 1;
        return;
    }
    static Mask M[MAXN];
    vector<int> cells;
    auto touch = [&](int q) { if (find(cells.begin(), cells.end(), q) == cells.end()) { cells.push_back(q); M[q] = ALL; } };
    items.clear();
    bj = 0;
    map<int, long long> batoms;                                         // atom code -> tightest needed bound
    for (int ev : low) {
        bj = max(bj, ev_level(ev));
        if (ev < BEV0) { int q = ev / 17; touch(q); M[q] &= ~BIT(ev % 17); continue; }
        const BEv& e = bevs[ev - BEV0];
        int code = aenc(e.j, e.side);
        long long b = need[ev - BEV0];
        auto it = batoms.find(code);
        if (it == batoms.end()) batoms[code] = b;
        else it->second = e.side == 0 ? min(it->second, b) : max(it->second, b);
    }
    if (uip >= 0) { int q = uip / 17; touch(q); M[q] &= ~BIT(uip % 17); }
    else { int q = decs[curlvl - 1].first; touch(q); M[q] &= decs[curlvl - 1].second; }
    for (int q : cells) items.push_back({q, M[q]});
    for (auto& kv : batoms) items.push_back({kv.first, (Mask)kv.second});
    // backjump to the level where the nogood becomes unit: a bound atom may hold earlier than its event
    int uq = uip >= 0 ? uip / 17 : decs[curlvl - 1].first;
    bj = 0;
    for (auto& it : items) if (it.first != uq) bj = max(bj, hold_level(it));
}
// Exact-enough reason for a failed global check (lb, ub, max-flow, LP, bounds): widen cells back to the root domains
// while the same check still fails on the widened domains (the checks are sound relaxations on any domains, see
// learn_from_fail); the conflict reason is then "the values outside M_p were removed from p" for the narrowed cells.
long long gexpl = 0, gexpl_checks = 0;
static bool explain_global(int code) {
    if (code < 2 || code > 6) return false;
    if (code == 5 && !learn_lp) return false;
    bool saveR = R_ON; R_ON = false;
    static Mask snap[MAXN];
    for (int p = 0; p < N; p++) snap[p] = dom[p];
    if (run_check(code)) { R_ON = saveR; return false; }
    auto widen = [&](const vector<int>& cells) {      // widen to the root domains; keep it only if the check still fails
        vector<pair<int, Mask>> saved;
        for (int p : cells) { saved.push_back({p, dom[p]}); dom[p] = rootdom[p]; }
        gexpl_checks++;
        if (!run_check(code)) return true;
        for (auto& sv : saved) dom[sv.first] = sv.second;
        return false;
    };
    int budget = code == 5 ? 24 : 400;
    vector<int> order;
    for (int p = 0; p < N; p++) if (dom[p] != rootdom[p] && !single(dom[p])) order.push_back(p);
    for (int p = 0; p < N; p++) if (dom[p] != rootdom[p] && single(dom[p])) order.push_back(p);
    const size_t CH = 6;
    for (size_t a = 0; a < order.size() && budget > 0; a += CH) {
        vector<int> chunk(order.begin() + a, order.begin() + min(order.size(), a + CH));
        budget--;
        if (widen(chunk) || chunk.size() == 1) continue;
        for (int p : chunk) { if (budget-- <= 0) break; widen({p}); }
    }
    CRp.clear();
    for (int p = 0; p < N; p++) if (dom[p] != rootdom[p]) CRp.push_back({p, ALL & ~dom[p]});
    for (int p = 0; p < N; p++) dom[p] = snap[p];
    R_ON = saveR;
    CR_dec = false;
    gexpl++;
    return true;
}
static int cdcl_solve() {
    R_ON = true; curlvl = 0; decs.clear(); lvl_mark.clear();
    CR_dec = true;
    if (!propagate()) { R_ON = false; return 0; }
    if (probe_depth >= 0) { if (!probe_all()) { R_ON = false; return 0; } if (timed_out) { R_ON = false; return 2; } }
    if (use_lcg && D < (1LL << 31) - 2) {
        if (!lcg_build()) { R_ON = false; return 0; }
        lcg_on = true;
        if (!propagate()) { R_ON = false; return 0; }
    }
    for (int q = 0; q < N; q++) rootdom[q] = dom[q];
    have_root = true;
    double bump = 1;
    perm_nogoods = nogoods.size();
    long long k = 0, since = 0, limit = use_restarts ? base_fails * luby(k++) : (1LL << 62);
    while (true) {
        nodes++;
        if ((nodes & 15) == 0 && chrono::duration<double>(chrono::steady_clock::now() - t0).count() > timeout) timed_out = true;
        if (timed_out) { R_ON = false; return 2; }
        int fed;
        int p = choose(fed);
        bool conflict = false;
        if (p < 0) {
            leaves++;
            conflict = true;
            if (lcg_on && !lcg_lp()) fail_by_code[5]++;                // exact reason instead of all decisions
            else {
                int r = leaf_check();
                if (r == 1) { R_ON = false; return 1; }
                if (r < 0) uncertain++;
                CR_dec = true;
            }
        } else {
            Mask S = BIT(value_order(p, fed)[0]);
            curlvl++; decs.push_back({p, S}); lvl_mark.push_back(trail.size());
            bool ch = false;
            deciding = true; setdom(p, S, ch); deciding = false;
            CR_dec = true;
            if (!propagate()) { conflict = true; fail_by_code[fail_code]++; if (CR_dec && gexplain) explain_global(fail_code); }
        }
        while (conflict) {
            conflicts++; fails++; since++;
            if (curlvl == 0) { R_ON = false; return 0; }
            vector<pair<int, Mask>> items; int bj;
            analyze(items, bj);
            undo(lvl_mark[bj]); decs.resize(bj); lvl_mark.resize(bj); curlvl = bj;
            if ((long long)(nogoods.size() - perm_nogoods) >= cdcl_db) {   // forget the older half (sound: only weaker)
                size_t keep_from = perm_nogoods + (nogoods.size() - perm_nogoods) / 2;
                ng_erase(perm_nogoods, keep_from);
            }
            nogoods.push_back(items);
            learned_cdcl++; learned_cdcl_len += items.size();
            for (auto& it : items) wdeg[it.first >= 0 ? it.first : fkeys[((-1 - it.first) >> 1) / NC].p] += bump;
            bump *= cbump;
            if (bump > 1e100) { for (int q = 0; q < N; q++) wdeg[q] *= 1e-100; bump *= 1e-100; }
            if (since > limit && curlvl > 0) {        // restart: back to the root, keep every nogood
                undo(lvl_mark[0]); decs.clear(); lvl_mark.clear(); curlvl = 0;
                since = 0; limit = base_fails * luby(k++); n_restarts++;
            }
            CR_dec = true;
            conflict = !propagate();
            if (conflict) fail_by_code[fail_code]++;
            if (conflict && CR_dec && gexplain) explain_global(fail_code);
        }
    }
}

// ------------------------------------------------------------------ input
static Mask code_mask(const JV& c) {
    if (c.t == JV::NUL) return BIT_E;
    string s = c.str;
    if (s == "..") return BIT_E;
    int k = string("BSIO").find(s[0]);
    return BIT(VAL(k, s[1] - '0'));
}
static Port read_port(const JV& a) {
    Port pt;
    int x = a[0].i(), y = a[1].i();
    pt.cell = y * W + x;
    pt.d = a[2].i();
    pt.kind = a[3].str == "in" ? 1 : 2;
    memset(pt.vec, 0, sizeof pt.vec);
    pt.live = false;
    for (size_t c = 0; c < a[4].size(); c++) { pt.vec[c] = a[4][c].i(); if (pt.vec[c] > 0) pt.live = true; }
    return pt;
}

static bool setup_ports(const vector<Port>& ports) {
    memset(portKind, 0, sizeof portKind);
    memset(portVec, 0, sizeof portVec);
    ins.clear(); outs.clear();
    for (int p = 0; p < N; p++) for (int c = 0; c < NC; c++) srcv[p][c] = 0;
    for (auto& pt : ports) {
        if (portKind[pt.cell][pt.d]) return false;     // two ports on one edge
        if (nb(pt.cell, pt.d) >= 0) { fprintf(stderr, "port not on the edge\n"); exit(2); }
        portKind[pt.cell][pt.d] = pt.kind;
        memcpy(portVec[pt.cell][pt.d], pt.vec, sizeof pt.vec);
        if (pt.kind == 1) { ins.push_back(pt); for (int c = 0; c < NC; c++) srcv[pt.cell][c] += pt.vec[c]; }
        else outs.push_back(pt);
    }
    return true;
}

static bool init_domains() {
    trail.clear(); trail_ev.clear(); trail_pool.clear(); evtrail.clear(); rpool.clear();
    for (int p = 0; p < N; p++) dom[p] = ALL;
    bool ch = false;
    for (auto& ft : fixedTiles) if (!setdom(ft.first, dom[ft.first] & ft.second, ch)) return false;
    for (int p = 0; p < N; p++)
        for (int d = 0; d < 4; d++) {
            if (nb(p, d) >= 0) continue;
            if (portKind[p][d] == 2) { if (!setdom(p, dom[p] & PUSH[d], ch)) return false; }
            else if (portKind[p][d] == 1) { if (!setdom(p, dom[p] & ACC[OPP(d)], ch)) return false; }
        }
    trail.clear(); trail_ev.clear(); trail_pool.clear(); evtrail.clear(); rpool.clear();
    memset(ev_lvl, 0, sizeof(int) * N * 17);
    ng_qhead = 0; ng_rewatch = true;
    return true;
}

int main(int argc, char** argv) {
    init_masks();
    if (argc < 2) { fprintf(stderr, "usage: bmown spec.json [--timeout S] [--seed N]\n"); return 2; }
    string path = argv[1];
    for (int i = 2; i < argc; i++) {
        string a = argv[i];
        if (a == "--timeout" && i + 1 < argc) timeout = atof(argv[++i]);
        else if (a == "--seed" && i + 1 < argc) { rng.seed(atoi(argv[++i])); rnd_on = true; }
        else if (a == "--nolp") use_lp = false;
        else if (a == "--nosat") use_sat = false;
        else if (a == "--nobounds") use_bounds = false;
        else if (a == "--lpsplit") lp_split = true;
        else if (a == "--probedepth" && i + 1 < argc) probe_depth = atoi(argv[++i]);
        else if (a == "--restarts") { use_restarts = true; rnd_on = true; }
        else if (a == "--learn") use_learn = true;
        else if (a == "--clpevery" && i + 1 < argc) cdcl_lpevery = atoi(argv[++i]);
        else if (a == "--cdcl") { use_cdcl = true; hybrid_fails = 0; }
        else if (a == "--dfs") use_cdcl = false;
        else if (a == "--cbump" && i + 1 < argc) cbump = atof(argv[++i]);
        else if (a == "--hybrid" && i + 1 < argc) { use_cdcl = true; hybrid_fails = atoll(argv[++i]); }
        else if (a == "--gexpl") gexplain = true;
        else if (a == "--nolcg") use_lcg = false;
        else if (a == "--nolean") lcg_lean = false;
        else if (a == "--nomin") minimize = false;
        else if (a == "--cdcldb" && i + 1 < argc) cdcl_db = atoll(argv[++i]);
        else if (a == "--vorder" && i + 1 < argc) vorder = atoi(argv[++i]);
        else if (a == "--nolearnlp") learn_lp = false;
        else if (a == "--lpevery" && i + 1 < argc) lp_every = atoi(argv[++i]);
        else if (a == "--rbase" && i + 1 < argc) base_fails = atoll(argv[++i]);
        else if (a == "--wmode" && i + 1 < argc) wmode = atoi(argv[++i]);
        else if (a == "--branch" && i + 1 < argc) branch_mode = atoi(argv[++i]);
        else if (a == "--lpmax" && i + 1 < argc) lp_maxvars = atoi(argv[++i]);
    }
    ifstream f(path);
    stringstream ss; ss << f.rdbuf();
    string txt = ss.str();
    JParser jp(txt);
    JV spec = jp.parse();
    W = spec["W"].i(); H = spec["H"].i(); N = W * H;
    D = spec["D"].i(); NC = spec.has("ncom") ? spec["ncom"].i() : 1;
    if (N > MAXN || NC > MAXC) { fprintf(stderr, "too large\n"); return 2; }
    if (spec.has("ports")) for (auto& a : spec["ports"].arr) fixedPorts.push_back(read_port(a));
    if (spec.has("fixed")) for (auto& a : spec["fixed"].arr) fixedTiles.push_back({(int)(a[1].i() * W + a[0].i()), code_mask(a[2])});
    if (spec.has("port_options"))
        for (auto& g : spec["port_options"].arr) {
            vector<vector<Port>> grp;
            for (auto& opt : g.arr) { vector<Port> o; for (auto& a : opt.arr) o.push_back(read_port(a)); grp.push_back(o); }
            portGroups.push_back(grp);
        }
    t0 = chrono::steady_clock::now();
    vector<int> sel(portGroups.size(), 0);
    bool exhaustive = true, done = false;
    vector<int> foundSel;
    while (!done) {
        vector<Port> ports = fixedPorts;
        for (size_t g = 0; g < portGroups.size(); g++) for (auto& pt : portGroups[g][sel[g]]) ports.push_back(pt);
        if (setup_ports(ports) && init_domains()) {
            nogoods.clear(); ng_rewatch = true;
            have_root = false; probed_root = false;
            int r = 0;
            long long k = 0;
            bool cd = use_cdcl;
            if (cd && hybrid_fails > 0) {      // short plain DFS first (cheap finds), then CDCL on the same combo
                bool ur = use_restarts; use_restarts = true;
                restart_now = false; path.clear();
                restart_limit = hybrid_fails; fails_at_restart = fails;
                r = dfs();
                use_restarts = ur;
                if (r == 1 || timed_out) cd = false;   // r==0: exhaustive; r==2: budget hit, nld nogoods recorded
                else if (r == 0) cd = false;
            }
            if (cd) { r = cdcl_solve(); lcg_on = false; }
            else if (use_cdcl) { /* decided by the DFS phase */ }
            while (!use_cdcl) {
                restart_now = false; path.clear();
                restart_limit = base_fails * luby(k++);
                fails_at_restart = fails;
                r = dfs();
                if (r != 2 || timed_out) break;
                n_restarts++;
            }
            if (r == 1) { found = true; foundSel = sel; break; }
            if (timed_out) { exhaustive = false; break; }
        }
        // next combination
        size_t g = 0;
        while (g < sel.size()) { if (++sel[g] < (int)portGroups[g].size()) break; sel[g] = 0; g++; }
        if (g == sel.size()) done = true;
    }
    double el = chrono::duration<double>(chrono::steady_clock::now() - t0).count();
    if (found) {
        printf("STATUS FOUND\n");
        for (int y = 0; y < H; y++) {
            for (int x = 0; x < W; x++) {
                int v = only(dom[y * W + x]);
                if (v == 0) printf("..");
                else printf("%c%d", KCH[KIND(v)], DIRV(v));
            }
            printf("\n");
        }
        printf("OPTIONS");
        for (int s : foundSel) printf(" %d", s);
        printf("\n");
    } else if (exhaustive && uncertain == 0) printf("STATUS INFEASIBLE\n");
    else printf("STATUS UNKNOWN\n");
    printf("TIME %.3f NODES %lld FAILS %lld LEAVES %lld UNCERTAIN %lld LP %lld/%lld/%lld/%lld RESTARTS %lld NOGOODS %zu PROBES %lld/%lld LEARNED %lld/%lld BOUNDS %lld/%lld/%lld EXACT\n", el, nodes, fails, leaves, uncertain, lp_calls, lp_prunes, lp_cert_fail, lp_warm, n_restarts, nogoods.size(), probes, probe_removed, learned, learn_checks, bnd_calls, bnd_fails, bnd_filters);
    if (use_cdcl) printf("CDCL conflicts %lld learned %lld avglen %.1f badreasons %lld gexpl %lld/%lld lcg %lld/%lld lp %lld/%lld\n", conflicts, learned_cdcl, learned_cdcl ? (double)learned_cdcl_len / learned_cdcl : 0.0, bad_reasons, gexpl, gexpl_checks, lcg_props, lcg_fails, lcglp_calls, lcglp_prunes);
    printf("FAILCODES"); for (int i = 0; i < 8; i++) printf(" %lld", fail_by_code[i]); printf("\n");
    if (learned) printf("AVG_NOGOOD_LEN %.1f\n", (double)learned_len / learned);
    return 0;
}
