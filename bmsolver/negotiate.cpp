#include <cstdlib>
// Negotiated-congestion belt router: the same search as bmsolver/negotiate.py, compiled.
// Reads the problem as plain numbers on stdin, writes the routes on stdout (see negotiate.py: _route_cpp).
//   g++ -O2 -std=c++17 -o negotiate_cpp negotiate.cpp
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <queue>
#include <set>
#include <unordered_map>
#include <vector>
using namespace std;

static const int DX[4] = {0, -1, 0, 1}, DY[4] = {-1, 0, 1, 0};
static int W, H, N, REACH, ROUNDS;
static double HOP, TURN, OPEN, TIMEOUT;

struct Piece { int tile; char kind; int d; };

static inline bool inside(int x, int y) { return x >= 0 && x < W && y >= 0 && y < H; }
static inline int stepT(int t, int d, int k = 1) {
    int x = t % W + DX[d] * k, y = t / W + DY[d] * k;
    return inside(x, y) ? y * W + x : -1;
}
static inline int opp(int d) { return (d + 2) % 4; }

int main() {
    auto T0 = chrono::steady_clock::now();
    auto secs = [&]() { return chrono::duration<double>(chrono::steady_clock::now() - T0).count(); };
    if (scanf("%d %d %d %d %d", &W, &H, &N, &REACH, &ROUNDS) != 5) return 2;
    int LOG;
    if (scanf("%lf %lf %lf %lf %d", &HOP, &TURN, &OPEN, &TIMEOUT, &LOG) != 5) return 2;
    int WH = W * H, nb;
    vector<char> blocked(WH, 0);
    if (scanf("%d", &nb) != 1) return 2;
    for (int i = 0; i < nb; i++) { int x, y; if (scanf("%d %d", &x, &y) != 2) return 2; if (inside(x, y)) blocked[y * W + x] = 1; }
    vector<int> sT(N), sD(N), gT(N), gD(N), team(N), join(N);
    for (int n = 0; n < N; n++) { int x, y; if (scanf("%d %d %d", &x, &y, &sD[n]) != 3) return 2; sT[n] = y * W + x; }
    for (int n = 0; n < N; n++) { int x, y; if (scanf("%d %d %d", &x, &y, &gD[n]) != 3) return 2; gT[n] = x < 0 ? -1 : y * W + x; }
    for (int n = 0; n < N; n++) if (scanf("%d %d", &team[n], &join[n]) != 2) return 2;
    vector<char> fixedUg(WH * 4, 0), noUg(WH * 4, 0);
    int nf; if (scanf("%d", &nf) != 1) return 2;
    for (int i = 0; i < nf; i++) { int x, y, d; if (scanf("%d %d %d", &x, &y, &d) != 3) return 2; if (inside(x, y)) fixedUg[(y * W + x) * 4 + d] = 1; }
    int nn; if (scanf("%d", &nn) != 1) return 2;
    for (int i = 0; i < nn; i++) { int x, y, d; if (scanf("%d %d %d", &x, &y, &d) != 3) return 2; if (inside(x, y)) noUg[(y * W + x) * 4 + d] = 1; }

    // goals per team, reserved port tiles
    vector<vector<pair<int, int>>> teamGoals(N);
    for (int n = 0; n < N; n++) if (join[n] < 0) {
        auto &v = teamGoals[team[n]];
        bool have = false;
        for (auto &g : v) if (g.first == gT[n]) { g.second = gD[n]; have = true; }
        if (!have) v.push_back({gT[n], gD[n]});
    }
    vector<int> resType(WH, 0), resId(WH, -1);      // 1 = start of net id, 2 = goal of team id
    for (int n = 0; n < N; n++) {
        resType[sT[n]] = 1; resId[sT[n]] = n;
        if (join[n] < 0) { resType[gT[n]] = 2; resId[gT[n]] = team[n]; }
    }
    vector<double> base(WH, 0.0), hist(WH, 0.0);
    if (OPEN > 0)
        for (int t = 0; t < WH; t++) if (!blocked[t]) {
            bool near = false;
            for (int d = 0; d < 4; d++) { int q = stepT(t, d); if (q >= 0 && blocked[q]) near = true; }
            if (!near) base[t] = OPEN;
        }
    vector<vector<int>> use(WH), ugs(WH * 4);
    vector<unordered_map<int, pair<char, int>>> paths(N);
    vector<char> hasPath(N, 0);
    vector<int> endedT(N, -1), endedD(N, -1);
    double pres = 0.5;
    const double PMAX = getenv("BM_PMAX") ? atof(getenv("BM_PMAX")) : 100;
    const double HISTUG = getenv("BM_HISTUG") ? atof(getenv("BM_HISTUG")) : 0.5;

    auto freeT = [&](int t) { return t >= 0 && !blocked[t]; };
    auto tileCost = [&](int p, int n) -> double {       // < 0 = not allowed
        if (resType[p] == 1 && resId[p] != n) return -1;
        if (resType[p] == 2 && resId[p] != team[n]) return -1;
        int others = 0;
        for (int m : use[p]) if (m != n) others++;
        return (1 + base[p] + hist[p]) * (1 + pres * others);
    };

    // A* scratch
    int S = WH * 5;
    vector<double> best(S);
    vector<int> stamp(S, 0), prevS(S), p1t(S), p2t(S);
    vector<char> p1k(S), p2k(S);
    vector<int> p1d(S), p2d(S);
    vector<int> gdir(WH, -1);
    int cur = 0;

    auto astar = [&](int n, bool strict, vector<Piece> &out, bool &crossed) -> bool {
        cur++;
        vector<pair<int, int>> G;
        if (join[n] >= 0) {
            auto &pm = paths[join[n]];
            for (auto &kv : pm) {
                if (kv.second.first != 'B') continue;
                int t = kv.first, e = kv.second.second;
                for (int g = 0; g < 4; g++) {
                    if (g == opp(e)) continue;
                    int q = stepT(t, opp(g));
                    if (q < 0 || blocked[q] || pm.count(q)) continue;
                    if (resType[q] != 0 && !(resType[q] == 1 && resId[q] == n)) continue;
                    G.push_back({q, g});
                }
            }
            sort(G.begin(), G.end());
        } else G = teamGoals[team[n]];
        if (G.empty()) return false;
        vector<int> gts;
        for (auto &g : G) if (gdir[g.first] < 0) { gdir[g.first] = g.second; gts.push_back(g.first); }
        bool isJoin = join[n] >= 0;
        auto h = [&](int p) -> double {
            if (isJoin) return 0;
            int x = p % W, y = p / W, b = 1 << 30;
            for (int t : gts) b = min(b, abs(x - t % W) + abs(y - t / W));
            return b;
        };
        auto mine = [&](int key, int c1, int c2) {
            while (stamp[key] == cur && prevS[key] >= 0) {
                int a = p1t[key], b = p2t[key];
                if (a == c1 || a == c2 || (b >= 0 && (b == c1 || b == c2))) return true;
                key = prevS[key];
            }
            return false;
        };
        typedef tuple<double, double, int> QE;
        priority_queue<QE, vector<QE>, greater<QE>> heap;
        int s = sT[n] * 5 + sD[n];
        double c0 = tileCost(sT[n], n);
        if (c0 < 0) c0 = 1;
        stamp[s] = cur; best[s] = c0; prevS[s] = -1;
        heap.push(QE(c0 + h(sT[n]), c0, s));
        bool found = false;
        int endS = -1;
        while (!heap.empty()) {
            auto [f, c, st] = heap.top();
            heap.pop();
            if (stamp[st] != cur || c > best[st]) continue;
            int p = st / 5, d = st % 5;
            if (gdir[p] >= 0 && (d == 4 || d != opp(gdir[p]))) { found = true; endS = st; break; }
            if (d == 4) continue;
            for (int e = 0; e < 4; e++) {
                if (e == opp(d)) continue;
                int q = stepT(p, e);
                if (!freeT(q) || (gdir[q] >= 0 && e == opp(gdir[q]))) continue;
                double tc = tileCost(q, n);
                if (tc < 0 || (strict && mine(st, q, -2))) continue;
                double nc = c + tc + (e != d ? TURN : 0);
                int ns = q * 5 + e;
                if (stamp[ns] != cur || nc < best[ns]) {
                    stamp[ns] = cur; best[ns] = nc; prevS[ns] = st;
                    p1t[ns] = p; p1k[ns] = 'B'; p1d[ns] = e; p2t[ns] = -1;
                    heap.push(QE(nc + h(q), nc, ns));
                }
            }
            double clash = 0;
            for (int k = 1; k <= REACH; k++) {
                int r = stepT(p, d, k);
                if (r < 0 || noUg[p * 4 + d] || fixedUg[r * 4 + d]) break;
                if (!blocked[r] && !noUg[r * 4 + d]) {
                    double tr = tileCost(r, n);
                    int q = stepT(r, d);
                    if (tr >= 0 && !(strict && mine(st, r, q < 0 ? -2 : q))) {
                        if (gdir[r] >= 0 && d == gdir[r]) {
                            double nc = c + tr + HOP + clash + (k - 1) * (1 + OPEN);
                            int ns = r * 5 + 4;
                            if (stamp[ns] != cur || nc < best[ns]) {
                                stamp[ns] = cur; best[ns] = nc; prevS[ns] = st;
                                p1t[ns] = p; p1k[ns] = 'I'; p1d[ns] = d; p2t[ns] = r; p2k[ns] = 'O'; p2d[ns] = d;
                                heap.push(QE(nc, nc, ns));
                            }
                        }
                        if (freeT(q) && gdir[r] < 0 && !(gdir[q] >= 0 && d == opp(gdir[q]))) {
                            double tq = tileCost(q, n);
                            if (tq >= 0) {
                                double nc = c + tr + tq + HOP + clash + (k - 1) * (1 + OPEN);
                                int ns = q * 5 + d;
                                if (stamp[ns] != cur || nc < best[ns]) {
                                    stamp[ns] = cur; best[ns] = nc; prevS[ns] = st;
                                    p1t[ns] = p; p1k[ns] = 'I'; p1d[ns] = d; p2t[ns] = r; p2k[ns] = 'O'; p2d[ns] = d;
                                    heap.push(QE(nc + h(q), nc, ns));
                                }
                            }
                        }
                    }
                }
                for (int m : ugs[r * 4 + d]) if (m != n) { clash += 4 * (1 + pres); break; }
            }
        }
        out.clear();
        crossed = false;
        if (found) {
            int p = endS / 5, d = endS % 5;
            endedT[n] = p; endedD[n] = gdir[p];
            set<int> seen;
            int count = 0;
            if (d != 4) { out.push_back({p, 'B', gdir[p]}); seen.insert(p); count++; }
            int key = endS;
            while (prevS[key] >= 0) {
                out.push_back({p1t[key], p1k[key], p1d[key]}); seen.insert(p1t[key]); count++;
                if (p2t[key] >= 0) { out.push_back({p2t[key], p2k[key], p2d[key]}); seen.insert(p2t[key]); count++; }
                key = prevS[key];
            }
            if (count != (int)seen.size()) crossed = true;
        }
        for (int t : gts) gdir[t] = -1;
        return found;
    };

    auto rip = [&](int n) {
        if (!hasPath[n]) return;
        for (auto &kv : paths[n]) {
            auto &u = use[kv.first];
            u.erase(remove(u.begin(), u.end(), n), u.end());
            if (kv.second.first != 'B') {
                auto &g = ugs[kv.first * 4 + kv.second.second];
                g.erase(remove(g.begin(), g.end(), n), g.end());
            }
        }
        paths[n].clear();
        hasPath[n] = 0;
    };
    auto add = [&](int n, vector<Piece> &pc) {
        for (auto &x : pc) {
            paths[n][x.tile] = {x.kind, x.d};
        }
        for (auto &kv : paths[n]) {
            use[kv.first].push_back(n);
            if (kv.second.first != 'B') ugs[kv.first * 4 + kv.second.second].push_back(n);
        }
        hasPath[n] = 1;
    };
    auto conflicts = [&]() {
        set<int> bad;
        for (int t = 0; t < WH; t++) if (use[t].size() > 1) for (int m : use[t]) bad.insert(m);
        for (int n = 0; n < N; n++)
            for (auto &kv : paths[n]) if (kv.second.first == 'I') {
                int p = kv.first, d = kv.second.second;
                for (int k = 1; k <= REACH; k++) {
                    int r = stepT(p, d, k);
                    if (r < 0) break;
                    auto it = paths[n].find(r);
                    if (it != paths[n].end() && it->second.first == 'O' && it->second.second == d) break;
                    bool any = false;
                    for (int m : ugs[r * 4 + d]) if (m != n) { bad.insert(m); any = true; }
                    if (any) bad.insert(n);
                }
            }
        return bad;
    };

    vector<int> todo(N);
    for (int n = 0; n < N; n++) todo[n] = n;
    set<int> bad;
    int it = 0;
    for (; it < ROUNDS; it++) {
        set<int> ts(todo.begin(), todo.end());
        for (int n = 0; n < N; n++) if (join[n] >= 0 && ts.count(join[n])) ts.insert(n);
        todo.assign(ts.begin(), ts.end());
        stable_sort(todo.begin(), todo.end(), [&](int a, int b) { return (join[a] >= 0) < (join[b] >= 0); });
        for (int n : todo) {
            rip(n);
            vector<Piece> pc;
            bool crossed = false;
            bool ok = astar(n, false, pc, crossed);
            if (ok && crossed) ok = astar(n, true, pc, crossed);
            if (!ok) { printf("FAIL net %d has no route at all\n0\n", n); return 0; }
            add(n, pc);
        }
        bad = conflicts();
        if (LOG) fprintf(stderr, "round %d: %d nets rerouted, %d still clash (%.1f s)\n", it + 1, (int)todo.size(), (int)bad.size(), secs());
        if (bad.empty()) {
            printf("OK %d %.3f\n", it + 1, secs());
            for (int n = 0; n < N; n++) {
                printf("%d %d %d %d %d\n", n, endedT[n] % W, endedT[n] / W, endedD[n], (int)paths[n].size());
                vector<pair<int, pair<char, int>>> v(paths[n].begin(), paths[n].end());
                sort(v.begin(), v.end());
                for (auto &kv : v) printf("%d %d %c %d\n", kv.first % W, kv.first / W, kv.second.first, kv.second.second);
            }
            return 0;
        }
        for (int t = 0; t < WH; t++) if (use[t].size() > 1) hist[t] += 0.5 * (use[t].size() - 1);
        // overuse through a hop (someone else's entrance or exit under this hop) also leaves a mark
        for (int n = 0; n < N; n++)
            for (auto &kv : paths[n]) if (kv.second.first == 'I') {
                int p = kv.first, d = kv.second.second;
                for (int k = 1; k <= REACH; k++) {
                    int r = stepT(p, d, k);
                    if (r < 0) break;
                    auto f = paths[n].find(r);
                    if (f != paths[n].end() && f->second.first == 'O' && f->second.second == d) break;
                    for (int m : ugs[r * 4 + d]) if (m != n) { hist[r] += HISTUG; break; }
                }
            }
        pres = min(pres * 1.6, PMAX);                    // unbounded growth drowned the path length in the cost
        todo.assign(bad.begin(), bad.end());
        if (TIMEOUT > 0 && secs() > TIMEOUT) break;
    }
    printf("FAIL still clashing\n%d\n", (int)bad.size());
    for (int m : bad) printf("%d\n", m);
    printf("TILES\n");                               // where the belts lie now: x y (number of belts on the tile)
    {
        vector<int> cnt(WH, 0);
        for (int t = 0; t < WH; t++) cnt[t] = (int)use[t].size();
        for (int n = 0; n < N; n++)                    // a hop over someone else's entrance or exit of the same direction
            for (auto &kv : paths[n]) if (kv.second.first == 'I') {
                int p = kv.first, d = kv.second.second;
                for (int k = 1; k <= REACH; k++) {
                    int r = stepT(p, d, k);
                    if (r < 0) break;
                    auto f = paths[n].find(r);
                    if (f != paths[n].end() && f->second.first == 'O' && f->second.second == d) break;
                    for (int m : ugs[r * 4 + d]) if (m != n) { cnt[r] = max(cnt[r], 2); break; }
                }
            }
        for (int t = 0; t < WH; t++) if (cnt[t]) printf("%d %d %d\n", t % W, t / W, cnt[t]);
    }
    return 0;
}
