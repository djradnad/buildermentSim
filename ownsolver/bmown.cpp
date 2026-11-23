// bmown: a tile search solver for Builderment belt layouts (belts, splitters, hops), written for the game.
// Semantics follow bmsolver/model.py (pieces, links, ports) and bmsolver/check.py (exact steady-state flows).
// See TEAM_LOG.md "DESIGN NOTE" for the propagators and why each is sound.
#include <algorithm>
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
bool use_lp = true;
int lp_every = 1;
static size_t path_depth();
struct Lit;
extern vector<vector<pair<int, int>>> nogoods;
static bool nogood_prop(bool& changed);
int lp_maxvars = 3000;
bool timed_out = false;

static inline bool setdom(int p, Mask m, bool& changed) {
    if (m == dom[p]) return true;
    if (!m) return false;
    trail.push_back({p, dom[p]});
    dom[p] = m;
    changed = true;
    return true;
}
static inline void undo(size_t to) {
    while (trail.size() > to) { dom[trail.back().first] = trail.back().second; trail.pop_back(); }
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

// ------------------------------------------------------------------ unit rules on forced tiles
static bool unit_rules(bool& changed) {
    for (int p = 0; p < N; p++) {
        if (!single(dom[p]) || dom[p] == BIT_E) continue;
        int v = only(dom[p]), k = KIND(v), o = DIRV(v);
        // outputs
        if (k == KB || k == KO) {
            int q = nb(p, o);
            if (q >= 0 && !setdom(q, dom[q] & ACC[o], changed)) return false;
        } else if (k == KS) {
            int cnt = 0, lastd = -1;
            int ds[3] = {o, (o + 1) & 3, (o + 3) & 3};
            for (int d : ds) if (out_possible(p, d)) { cnt++; lastd = d; }
            if (cnt == 0) return false;
            if (cnt == 1) {
                int q = nb(p, lastd);
                if (q >= 0 && !setdom(q, dom[q] & ACC[lastd], changed)) return false;
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
            if (cnt == 1 && !setdom(last, od, changed)) return false;
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
            if (cnt == 1 && !setdom(last, id, changed)) return false;
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
                    if (!setdom(r, dom[r] & PUSH[laste], changed)) return false;
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

static bool reach(bool& changed) {
    vector<int> st;
    for (auto& pt : ins) if (pt.live) st.push_back(pt.cell);
    vector<char> f, b;
    bfs_fwd(st, f);
    st.clear();
    for (auto& pt : outs) st.push_back(pt.cell);
    bfs_bwd(st, b);
    for (int p = 0; p < N; p++)
        if (!(f[p] && b[p]) && !setdom(p, dom[p] & BIT_E, changed)) return false;
    // per input lane: must reach an out; every out must be reached by a lane of each commodity it needs
    vector<char> got(outs.size() * NC, 0);
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
        if (!any) return false;
    }
    for (size_t j = 0; j < outs.size(); j++)
        for (int c = 0; c < NC; c++)
            if (outs[j].vec[c] > 0 && !got[j * NC + c]) return false;
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
vector<double> lastsol;
static bool lp_check(int maxvars) {
    lp_calls++;
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
    // in/out lists per tile
    vector<vector<int>> lin(N), lout(N);
    for (int i = 0; i < NL; i++) { lout[L[i].first].push_back(i); lin[L[i].second].push_back(i); }
    for (int c = 0; c < NC; c++) {
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
            for (int i : lin[p]) row.a.push_back({V(lvar[i], c), 1});
            for (int d = 0; d < 4; d++) if (xvar[p][d] >= 0) row.a.push_back({V(xvar[p][d], c), 1});
            for (int d = 0; d < 4; d++) if (portKind[p][d] == 1) src += portVec[p][d][c];
        }
        row.b = D - src;
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
    if (m == 0) return true;
    // warm check: is the last LP solution still feasible here? then nothing to prune
    if ((int)lastsol.size() == 16 * N * NC) {
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
        if (pi < 0) return true;   // unbounded cannot happen in phase 1; be safe
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
        lastsol.assign(16 * N * NC, 0.0);
        vector<double> val(n, 0.0);
        for (int i = 0; i < m; i++) if (basis[i] >= 0) val[basis[i]] = max(0.0, at(i, RH));
        for (int v = 0; v < nv && v < n0; v++) {
            int r = fnd(v);
            double x = fixv[r] >= 0 ? (double)fixv[r] : (newidx[r] >= 0 ? val[newidx[r]] : 0.0);
            int k = v % per, c = v / per;
            lastsol[vkey[k] + c * 16 * N] = x;
        }
        return true;
    }
    // dual y = c_B B^-1: solve B^T y = c_B (c = 1 for artificials) by dense Gauss with partial pivoting
    vector<double> y(m, 0.0);
    {
        vector<vector<double>> Bt(m, vector<double>(m + 1, 0.0));   // row k of B^T = column basis[k] of A
        vector<int> posof(n, -1);
        for (int k = 0; k < m; k++) if (basis[k] >= 0) posof[basis[k]] = k;
        for (int i = 0; i < m; i++) for (auto& t : rows[i].a) if (posof[t.first] >= 0) Bt[posof[t.first]][i] += t.second;
        for (int k = 0; k < m; k++) if (basis[k] < 0) { Bt[k][-1 - basis[k]] = 1; Bt[k][m] = 1; }
        for (int c = 0; c < m; c++) {
            int pv = c;
            for (int r = c + 1; r < m; r++) if (fabs(Bt[r][c]) > fabs(Bt[pv][c])) pv = r;
            if (fabs(Bt[pv][c]) < 1e-12) { lp_cert_fail++; return true; }
            swap(Bt[c], Bt[pv]);
            for (int r = 0; r < m; r++) {
                if (r == c || Bt[r][c] == 0) continue;
                double f = Bt[r][c] / Bt[c][c];
                for (int k = c; k <= m; k++) Bt[r][k] -= f * Bt[c][k];
            }
        }
        for (int i = 0; i < m; i++) y[i] = Bt[i][m] / Bt[i][i];
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

// ------------------------------------------------------------------ propagation fixpoint
static bool propagate() {
    while (true) {
        bool changed = true;
        while (changed) {
            changed = false;
            for (int p = 0; p < N; p++) {
                Mask m = dom[p], nm = 0;
                for (Mask r = m; r; r &= r - 1) {
                    int v = __builtin_ctz(r);
                    if (supported(p, v)) nm |= BIT(v);
                }
                if (!setdom(p, nm, changed)) return false;
            }
            if (!unit_rules(changed)) return false;
            if (!nogoods.empty() && !nogood_prop(changed)) return false;
        }
        bool ch2 = false;
        if (!reach(ch2)) return false;
        if (!ch2) break;
    }
    if (!flow_lb()) return false;
    if (!flow_relax()) return false;
    if (use_lp && (lp_every <= 1 || path_depth() % lp_every == 0) && !lp_check(lp_maxvars)) return false;
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
int branch_mode = 0, wmode = 0;
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
        else if (branch_mode == 1) score = (int)(1000.0 * popc(dom[p]) / (1.0 + wdeg[p]));
        else score = popc(dom[p]);
        if (score < bestscore) { bestscore = score; best = p; bestv_order_dir = fed; }
    }
    return best;
}
static vector<int> value_order(int p, int fed) {
    vector<int> order;
    Mask m = dom[p];
    auto add = [&](int v) { if ((m & BIT(v)) && find(order.begin(), order.end(), v) == order.end()) order.push_back(v); };
    if (fed >= 0) {
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
static size_t path_depth() { return path.size(); }
vector<vector<pair<int, int>>> nogoods;
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
    vector<pair<int, int>> pos;
    for (auto& l : path) {
        if (l.pos) { pos.push_back({l.p, l.v}); continue; }
        vector<pair<int, int>> ng = pos;
        ng.push_back({l.p, l.v});
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
static bool nogood_prop(bool& changed) {
    for (auto& ng : nogoods) {
        int und = -1, nund = 0;
        bool sat = false;
        for (auto& l : ng) {
            Mask m = dom[l.first], b = BIT(l.second);
            if (!(m & b)) { sat = true; break; }
            if (m != b) { nund++; und = &l - &ng[0]; if (nund > 1) break; }
        }
        if (sat || nund > 1) continue;
        if (nund == 0) return false;
        auto& l = ng[und];
        if (!setdom(l.first, dom[l.first] & ~BIT(l.second), changed)) return false;
    }
    return true;
}
// returns 1 found, 0 subtree exhausted, 2 aborted (timeout or restart)
static int dfs() {
    nodes++;
    if ((nodes & 15) == 0) {
        double el = chrono::duration<double>(chrono::steady_clock::now() - t0).count();
        if (el > timeout) timed_out = true;
    }
    if (timed_out || restart_now) return 2;
    if (!propagate()) { note_fail(); return 0; }
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
        if (!propagate()) { note_fail(); result = 0; break; }
        if (restart_now) { result = 2; break; }
    }
    undo(mark);
    path.resize(pmark);
    return result;
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
    trail.clear();
    for (int p = 0; p < N; p++) dom[p] = ALL;
    bool ch = false;
    for (auto& ft : fixedTiles) if (!setdom(ft.first, dom[ft.first] & ft.second, ch)) return false;
    for (int p = 0; p < N; p++)
        for (int d = 0; d < 4; d++) {
            if (nb(p, d) >= 0) continue;
            if (portKind[p][d] == 2) { if (!setdom(p, dom[p] & PUSH[d], ch)) return false; }
            else if (portKind[p][d] == 1) { if (!setdom(p, dom[p] & ACC[OPP(d)], ch)) return false; }
        }
    trail.clear();
    return true;
}

int main(int argc, char** argv) {
    init_masks();
    if (argc < 2) { fprintf(stderr, "usage: bmown spec.json [--timeout S] [--seed N]\n"); return 2; }
    string path = argv[1];
    for (int i = 2; i < argc; i++) {
        string a = argv[i];
        if (a == "--timeout" && i + 1 < argc) timeout = atof(argv[++i]);
        else if (a == "--seed" && i + 1 < argc) ++i;
        else if (a == "--nolp") use_lp = false;
        else if (a == "--restarts") use_restarts = true;
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
            nogoods.clear();
            int r;
            long long k = 0;
            while (true) {
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
    printf("TIME %.3f NODES %lld FAILS %lld LEAVES %lld UNCERTAIN %lld LP %lld/%lld/%lld/%lld RESTARTS %lld NOGOODS %zu EXACT\n", el, nodes, fails, leaves, uncertain, lp_calls, lp_prunes, lp_cert_fail, lp_warm, n_restarts, nogoods.size());
    return 0;
}
