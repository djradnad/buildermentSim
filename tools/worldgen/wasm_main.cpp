// WebAssembly entry for john's world generator (bldgen2.cpp, unchanged apart from portability: no threads, no SSE
// prefetch, and a 64-bit map hash on wasm32 so robin_hood iterates in the game's order). The mode comes from the
// environment the instance starts with (nothing: iOS Gen 2; GEN1=1 IOSG1=1: iOS Gen 1), as in the release engine.
#define BLD_NO_MAIN
#include "bldgen2_wasm.cpp"

static std::vector<int32_t> g_out;
static std::vector<uint8_t> g_ter;
static Gen* g_last = nullptr;
static uint32_t g_lastSeed = 0; static int g_lastWs = -1, g_lastRa = -1;

static Gen* gen_for(uint32_t seed, int ws, int ra) {
    if (g_last && g_lastSeed == seed && g_lastWs == ws && g_lastRa == ra) return g_last;
    Gen* g = new Gen;
    if (!initGen(*g, seed, ws, ra)) { delete g; return nullptr; }
    delete g_last; g_last = g; g_lastSeed = seed; g_lastWs = ws; g_lastRa = ra;
    return g;
}

extern "C" {
// every resource node: returns the count, triples (x, y, flag) at bld_nodes()
__attribute__((export_name("bld_generate")))
int bld_generate(uint32_t seed, int ws, int ra) {
    Gen* g = gen_for(seed, ws, ra);
    g_out.clear();
    if (!g) return -1;
    NodeMap m; generate(*g, m);
    for (auto& kv : m) { g_out.push_back(kv.first.x); g_out.push_back(kv.first.y); g_out.push_back(kv.second); }
    return (int)m.size();
}
// PC Gen 1 (environ GEN1=1 SINCOS=4): call before the first world with the 8 MB sincos_fast.bin copied here
__attribute__((export_name("bld_sincos_buf"))) float* bld_sincos_buf() { if (!bld_sincos_ext) bld_sincos_ext = new float[2000000]; return bld_sincos_ext; }
__attribute__((export_name("bld_nodes"))) int32_t* bld_nodes() { return g_out.data(); }

// terrain class (1 void/water .. 6) of every step-th tile of a w x h window from (x0, y0); bytes at bld_terrain_buf()
__attribute__((export_name("bld_terrain")))
int bld_terrain(uint32_t seed, int ws, int ra, int x0, int y0, int w, int h, int step) {
    Gen* g = gen_for(seed, ws, ra);
    if (!g) return -1;
    g_ter.assign((size_t)w * h, 0);
    for (int j = 0; j < h; j++)
        for (int i = 0; i < w; i++) g_ter[(size_t)j * w + i] = (uint8_t)classify(*g, x0 + i * step, y0 + j * step);
    return w * h;
}
__attribute__((export_name("bld_terrain_buf"))) uint8_t* bld_terrain_buf() { return g_ter.data(); }
// half-size of the map square (nodes lie within -H..H)
__attribute__((export_name("bld_half"))) int bld_half(uint32_t seed, int ws, int ra) { Gen* g = gen_for(seed, ws, ra); return g ? g->H : -1; }
}
