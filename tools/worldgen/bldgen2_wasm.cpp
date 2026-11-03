// GEN2 EXPERIMENT copy of genldgen.cpp (do not edit the original)
#include <cstdlib>
// Standalone reimplementation of Builderment's world resource generator.
// Reverse-engineered from Builderment-Win64-Shipping.exe.
//
// Build (x64 Native Tools prompt):  cl /O2 /EHsc /std:c++17 /fp:precise bldgen.cpp third_party\FastNoise.cpp
// Usage:
//   bldgen <seed> [worldSize=100] [resources=100]            -> per-resource totals
//   bldgen <seed> <worldSize> <resources> nodes              -> also dump every node "x y type"
//   bldgen --batch <file> [threads]                          -> lines "seed ws ra", prints totals per line
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cmath>
#include <random>
#include <vector>
// no threads in wasm
#include <atomic>
#include <array>
#include <algorithm>
#include <string>
#include <chrono>
// (xmmintrin removed for portable builds)
#include "third_party/FastNoise.h"
#include "third_party/robin_hood.h"
#include "noise2.h"

// ---------------------------------------------------------------- locked release builds
// Compile with /DLOCKED_PC or /DLOCKED_IOS to get a shareable engine whose settings cannot be changed from outside:
// before anything else runs (static initializer, first in this file), every experiment switch is removed from the
// process environment and only the verified mode is set. LOCKED_PC = Steam Gen 1 only. LOCKED_IOS = iOS Gen 2 by
// default, iOS Gen 1 with the extra argument --ios1 (stripped before main sees the arguments).
#if defined(LOCKED_PC) || defined(LOCKED_IOS)
extern "C" __declspec(dllimport) unsigned long __stdcall GetModuleFileNameA(void*, char*, unsigned long);
static const char* const kAllKnobs[] = {
    "ANGMODE", "ERASEVOID", "FLIPKA", "FRDIV", "G1R", "G1RCEIL", "G2HACK", "G2MASK", "G2MASKWS", "G2R", "GEN1", "IOSG1",
    "IOSHV", "IOSRBV", "IOSRMC", "IOSRMF", "IOSRMV", "N12G1", "N12LOG", "N12N", "NFMA", "NZMODE", "PCNOISE", "POSMODE",
    "RB2H", "RBR", "REJTILE", "SINCOS", "SINCOSBIN", "SLOWVOID", "SMOOTH", "SPR", "SPS", "SQDIV", "STARTONLY", "TIEDUMP",
    "TIEFLAG", "UQ18", "UQ30", "WSPC", "VFMA", "P5ULP", "STOPAFTER", "UMAX", "UMIN", "WMAXK", "WMIN", "WQ", "WQU", "WSP", "WDIV", "WK"};
struct LockedEnv {
    LockedEnv() {
        for (const char* k : kAllKnobs) _putenv_s(k, "");   // an empty value removes the variable
        bool ios1 = false;
        for (int i = 1; i < __argc; i++) {
            const char* a = __argv[i];
            if (!strcmp(a, "--ios1") || !strcmp(a, "--gen2") || !strcmp(a, "--pc1")) {
#ifdef LOCKED_PC
                if (strcmp(a, "--pc1")) { fprintf(stderr, "this is the PC (Steam) engine; use the iOS engine for %s\n", a); exit(2); }
#else
                if (!strcmp(a, "--pc1")) { fprintf(stderr, "this is the iOS engine; use the PC engine for --pc1\n"); exit(2); }
                ios1 = !strcmp(a, "--ios1");
#endif
                for (int j = i; j + 1 < __argc; j++) __argv[j] = __argv[j + 1];   // strip the mode argument
                __argc--; i--;
            }
        }
#ifdef LOCKED_PC
        char path[1024]; unsigned long n = GetModuleFileNameA(nullptr, path, sizeof path);
        while (n > 0 && path[n - 1] != '\\' && path[n - 1] != '/') n--;
        path[n] = 0; strcat_s(path, "sincos_fast.bin");
        _putenv_s("GEN1", "1"); _putenv_s("SINCOS", "4"); _putenv_s("SINCOSBIN", path);
#else
        if (ios1) { _putenv_s("GEN1", "1"); _putenv_s("IOSG1", "1"); }
#endif
    }
};
static LockedEnv g_lockedEnv;   // first static object in the file: runs before any other initializer reads a switch
#endif

// ---------------------------------------------------------------- RNG
// Bit-identical to std::mt19937 (checked in selftest), but twists whole 624-word blocks and makes
// discard() skip tempering, so discarding is ~3 cycles/draw instead of a full draw.
struct MT19937 {
    uint32_t mt[624]; int idx;
    explicit MT19937(uint32_t s) {
        mt[0] = s;
        for (int i = 1; i < 624; i++) mt[i] = 1812433253u * (mt[i - 1] ^ (mt[i - 1] >> 30)) + (uint32_t)i;
        idx = 624;
    }
    void twist() {
        int i = 0;
        for (; i < 227; i++) { uint32_t y = (mt[i] & 0x80000000u) | (mt[i + 1] & 0x7fffffffu); mt[i] = mt[i + 397] ^ (y >> 1) ^ ((0u - (y & 1u)) & 0x9908b0dfu); }
        for (; i < 623; i++) { uint32_t y = (mt[i] & 0x80000000u) | (mt[i + 1] & 0x7fffffffu); mt[i] = mt[i - 227] ^ (y >> 1) ^ ((0u - (y & 1u)) & 0x9908b0dfu); }
        uint32_t y = (mt[623] & 0x80000000u) | (mt[0] & 0x7fffffffu);
        mt[623] = mt[396] ^ (y >> 1) ^ ((0u - (y & 1u)) & 0x9908b0dfu);
        idx = 0;
    }
    uint32_t operator()() {
        if (idx >= 624) twist();
        uint32_t y = mt[idx++];
        y ^= y >> 11; y ^= (y << 7) & 0x9d2c5680u; y ^= (y << 15) & 0xefc60000u; y ^= y >> 18;
        return y;
    }
    // Value that operator() will return `ahead` calls from now, if it is already in the current block
    // (no twist needed); otherwise false. Used only to prefetch, never to change the sequence.
    bool peek(int ahead, uint32_t& out) const {
        int i = idx + ahead;
        if (idx >= 624 || i >= 624) return false;
        uint32_t y = mt[i];
        y ^= y >> 11; y ^= (y << 7) & 0x9d2c5680u; y ^= (y << 15) & 0xefc60000u; y ^= y >> 18;
        out = y; return true;
    }
    void discard(unsigned long long k) {
        while (k) {
            if (idx >= 624) twist();
            unsigned long long step = (unsigned long long)(624 - idx) < k ? (unsigned long long)(624 - idx) : k;
            idx += (int)step; k -= step;
        }
    }
};

// ---------------------------------------------------------------- hash map (robin_hood, as in game)
struct IV { int32_t x, y; bool operator==(IV const& o) const noexcept { return x == o.x && y == o.y; } };
static inline uint64_t hash_int(uint64_t x) { x ^= x >> 33; x *= 0xff51afd7ed558ccdULL; x ^= x >> 33; return x; }
struct IVHash {
    uint64_t operator()(IV const& k) const noexcept {   // 64-bit even on wasm32 (robin_hood mixes all 64 bits)
        uint64_t hx = hash_int((uint64_t)(int64_t)k.x), hy = hash_int((uint64_t)(int64_t)k.y);
        uint64_t s = 0;
        s ^= hx + 0x9e3779b9ULL + (s << 6) + (s >> 2);
        s ^= hy + 0x9e3779b9ULL + (s << 6) + (s >> 2);
        return hash_int(s);
    }
};
using NodeMap = robin_hood::unordered_flat_map<IV, int32_t, IVHash>;

// ---------------------------------------------------------------- terrain / noise
// GEN2: N1 / N5 / N12 (the layers placement uses) come from Noise2, a FastNoise copy that can emulate arm64 FMA
// contraction (env NZMODE, see noise2.h; mode 0 is bit-identical to FastNoise).
// PC Gen 1 (env PCNOISE, default on for GEN1 without IOSG1): N1/N5/N12 via the original FastNoise code, like the
// game. Proven 2026-09-29 by running the Steam game's own GetNoise in an emulator:
// the game returns exactly FastNoise-/fp:fast values (12h4ln N12 at (135,68) = 0.0, not Noise2's -4.26e-9).
struct NoiseL : Noise2 {
    FastNoise fn; bool pc = false;
    float GetNoise(float x, float y) const { if (pc) { calls++; return fn.GetNoise(x, y); } return Noise2::GetNoise(x, y); }
    template <class P> bool below(float x, float y, P pred) const { return pc ? pred(GetNoise(x, y)) : Noise2::below(x, y, pred); }
};

struct Gen {
    int seed, R, H; float res; float Rm, Rb;   // H: map square half-size. GEN2 land mask: centre a = Rm/3, half-width b = Rb*0.1444 (Rm = R, Rb = 2*ceil(R/2))
    NoiseL n1, n5, n12;                  // seeds +1, +5, +12
    FastNoise n2, n3, n4;                // seeds +2, +3, +4 (terrain class / decoration only)
};

static void setupNoise(Noise2& n, int seed, float freq, int oct, float gain, float lac) {
    static const int mode = getenv("NZMODE") ? atoi(getenv("NZMODE")) : 3;   // GEN2 default: FMA in t and FBM sum
    n.setup(seed, freq, oct, gain, lac); n.mode = mode;
}
static void setupNoise(FastNoise& n, int seed, float freq, int oct, float gain, float lac);
static void setupNoise(NoiseL& n, int seed, float freq, int oct, float gain, float lac) {
    setupNoise(static_cast<Noise2&>(n), seed, freq, oct, gain, lac);
    n.pc = getenv("PCNOISE") && atoi(getenv("PCNOISE")) != 0;
    { extern bool fastnoise_pc_fpfast; if (n.pc) fastnoise_pc_fpfast = true; }   // PC: FastNoise as /fp:fast compiled it
    if (n.pc) setupNoise(n.fn, seed, freq, oct, gain, lac);
}
static void setupNoise(FastNoise& n, int seed, float freq, int oct, float gain, float lac) {
    n.SetSeed(seed);
    n.SetFrequency(freq);
    n.SetFractalOctaves(oct);
    n.SetFractalGain(gain);
    n.SetFractalLacunarity(lac);
    n.SetNoiseType(FastNoise::SimplexFractal);
}

// GEN1=1: run the PC Gen 1 generator (genldgen.cpp) instead: sets every Gen 2 knob back to its Gen 1 value before
// any of them is read. Checked against bldgen_verify.exe (identical output).
static void applyGen1Env() {
    // function-local static init is thread-safe: concurrent callers wait until the knobs are set
    static const bool done = [] {
        if (!getenv("GEN1")) return true;
        // iOS Gen 1 is the same iOS build as Gen 2: same angle expression and Apple sinf/cosf (see ANGMODE 3 / SINCOS 3)
        if (!getenv("IOSG1") && !getenv("PCNOISE")) _putenv_s("PCNOISE", "1");   // PC Gen 1: original FastNoise
        if (getenv("IOSG1")) {
            if (!getenv("ANGMODE")) _putenv_s("ANGMODE", "3");
            if (!getenv("SINCOS")) _putenv_s("SINCOS", "3");
            // Same iOS arithmetic as Gen 2: N12 rule (n+1)*0.5 >= 0.5 (12h4ln iOS save: N12 = -4.26e-9 at (135,68) accepted)
            // and arm64 FMA noise (neutral on the 30 iOS Gen 1 saves; kept for consistency with Gen 2).
            if (!getenv("N12N")) _putenv_s("N12N", "1");
            if (!getenv("NZMODE")) _putenv_s("NZMODE", "3");
        }
        const char* kv[][2] = {{"G1R", "1"}, {"WMIN", "20"}, {"WMAXK", "50"}, {"WQ", "1"}, {"WQU", "1"}, {"UMIN", "5"}, {"UMAX", "10"},
                               {"N12G1", "1"}, {"NZMODE", "0"}, {"SINCOS", "0"}, {"ANGMODE", "0"}, {"SPS", "20"}};
        for (auto& p : kv) if (!getenv(p[0])) _putenv_s(p[0], p[1]);
        return true;
    }();
    (void)done;
}

static bool initGen(Gen& g, uint32_t seed, int worldSize, int resources) {
    applyGen1Env();
    g.seed = (int)seed;
    switch (worldSize) {
        case 50: g.R = 1272; break; case 75: g.R = 1558; break; case 100: g.R = 1800; break;
        case 150: g.R = 2204; break; case 200: g.R = 2500; break; default: return false;
    }
    // GEN2: world extent R = ceil(1200 * sqrt(ws / 100)) = 849 / 1200 / 1698 for world size 50 / 100 / 200
    // (fitmap: land-mask cutoff of all observed nodes + exact node matches; 75 -> 1040 and 150 -> 1470 unverified).
    // The map square uses H = ceil(R / 2) (see generate). Env G1R=1 keeps Gen 1 R, G2R overrides R.
    if (!getenv("G1R")) {
        g.R = (int)std::ceil(1200.0 * std::sqrt((double)worldSize / 100.0));
        if (getenv("G2R")) g.R = atoi(getenv("G2R"));
    }
    if (getenv("G1R") && getenv("G1RCEIL")) g.R = worldSize == 100 ? 1800 : (int)std::ceil(1800.0 * std::sqrt((double)worldSize / 100.0));   // iOS Gen 1 hypothesis
    g.H = (g.R + 1) / 2;
    // IOSG1=1 (with GEN1=1): current iOS Gen 1 (fitted 2026-09-29 on 3 iPhone saves, all exact): land-mask centre from
    // the Gen 1 radius table, map square / spacing / mask width from H = ceil(1800*sqrt(ws/100)/2).
    // Verified at 50/100/150 only; 75 and 200 (PC R 2500 vs 1800*sqrt2 = 2546) are open.
    const bool iosg1 = getenv("IOSG1") != nullptr;
    if (iosg1) g.H = (int)std::ceil(1800.0 * std::sqrt((double)worldSize / 100.0) / 2.0);
    if (iosg1 && getenv("IOSHV")) g.H = atoi(getenv("IOSHV"));
    g.Rm = (float)g.R; g.Rb = (getenv("G1R") && !getenv("RB2H") && !iosg1) || getenv("RBR") ? (float)g.R : (float)(2 * g.H);
    // iOS land mask (Gen 2 and current iOS Gen 1): centre a = (2H)/3 in integer arithmetic (Rm = 3*a makes the existing
    // Rm*0.33333334f exactly a). Fitted on 13 iPhone Gen 1 saves at 50/75/100/150/200 and identical to the Gen 2 fit.
    if (!getenv("G1R") || iosg1) g.Rm = (float)(3 * ((2 * g.H) / 3));
    if (iosg1 && getenv("IOSRMF")) g.Rm = (float)(1800.0 * std::sqrt((double)worldSize / 100.0));   // experiment: unrounded radius
    if (iosg1 && getenv("IOSRMC")) g.Rm = (float)std::ceil(1800.0 * std::sqrt((double)worldSize / 100.0));
    if (iosg1 && getenv("IOSRMV")) g.Rm = (float)atof(getenv("IOSRMV"));
    if (iosg1 && getenv("IOSRBV")) g.Rb = (float)atof(getenv("IOSRBV"));
    if (getenv("G2MASK") && worldSize == atoi(getenv("G2MASKWS") ? getenv("G2MASKWS") : "50")) g.Rm = (float)atof(getenv("G2MASK"));
    g.res = (float)resources * 0.01f;
    setupNoise(g.n1, g.seed + 1, 0.002f, 8, 0.6f, 2.0f);
    setupNoise(g.n2, g.seed + 2, 0.003f, 10, 0.59f, 2.1f);
    setupNoise(g.n3, g.seed + 3, 0.003f, 10, 0.59f, 2.1f);
    setupNoise(g.n4, g.seed + 4, 0.4f, 4, 0.5f, 2.0f);
    setupNoise(g.n5, g.seed + 5, 1.0f, 2, 0.8f, 2.0f);
    setupNoise(g.n12, g.seed + 12, 0.02f, 3, 0.5f, 2.0f);
    return true;
}

static inline float clamp01(float v) { if (v <= 0.0f) v = 0.0f; if (1.0f <= v) v = 1.0f; return v; }
// GEN2: N12 node filter in normalised form, (n + 1) * 0.5 >= 0.5. Differs from Gen 1's n * 0.5 >= 0 only for tiny
// negative n (> about -3e-8), where 1 + n rounds to 1.0f; 6 dataset worlds need it. Env N12G1=1 restores Gen 1's test.
static inline bool n12Pass(float n, int x = 0, int y = 0) {
    static const bool g1 = getenv("N12G1") != nullptr && !getenv("N12N");
    volatile float s = n + 1.0f;   // keep the float rounding of n + 1 (no reassociation)
    static const bool lg = getenv("N12LOG") != nullptr;
    if (lg && n < 0.0f && n > -1e-6f) fprintf(stderr, "N12 tiny %.6e pass=%d at %d %d\n", n, (int)(0.5f <= s * 0.5f), x, y);
    return g1 ? 0.0f <= n * 0.5f : 0.5f <= s * 0.5f;
}

// Land-mask falloff s = smoothstep(t). Env SMOOTH: 1 = (3 - 2t) * (t*t), the order in the Steam game
// (MULSS t*t first; verified in an emulator: 9 PC saves had tiles at
// v = 0.25 - 1..4 ulp that the game calls land); 0 = ((3 - 2t) * t) * t (older port). volatile keeps /fp:fast
// builds from regrouping.
static inline float smoothFalloff(float t) {
    static const int m = getenv("SMOOTH") ? atoi(getenv("SMOOTH")) : 1;   // default = game order (PC 42/42; iOS neutral)
    if (m == 1) { volatile float t2 = t * t; volatile float a = 3.0f - (t + t); volatile float s = a * t2; return clamp01(s); }
    return clamp01((3.0f - (t + t)) * t * t);
}

// iOS test hypotheses (research build only; default off = verified model):
//   VFMA=1  : land value e*tc + (1-tc)*0.5 fused (arm64 clang contraction)
//   P5ULP=+1/-1 : nudge the double pow() of the N5 filter by one ulp (a different libm pow, e.g. Apple's)
static inline float maskValue(float e, float tc, float s) {
    static const bool vf = getenv("VFMA") != nullptr;
    return vf ? std::fmaf(e, tc, (1.0f - tc) * 0.5f) * (1.0f - s) : (e * tc + (1.0f - tc) * 0.5f) * (1.0f - s);
}
static inline double n5Pow(float n5) {
    static const int u = getenv("P5ULP") ? atoi(getenv("P5ULP")) : 0;
    double p = std::pow((double)((n5 + 1.0f) * 0.5f), 2.2);
    return u > 0 ? std::nextafter(p, 2.0) : u < 0 ? std::nextafter(p, 0.0) : p;
}

// Returns tile class 1..6 (1 = off-map / void; resources never placed there).
static int classify(const Gen& g, int x, int y) {
    float Rf = g.Rm;
    float a = Rf * 0.33333334f, b = g.Rb * 0.14444444f;
    float lo = a - b;
    float d = (float)std::sqrt((double)(x * x + y * y));
    float tc = clamp01(d * 0.0125f);
    float t = clamp01((d - lo) / ((b + a) - lo));
    float n1 = g.n1.GetNoise((float)x, (float)y);
    float e = clamp01((n1 + 1.0f) * 0.5f);
    float s = smoothFalloff(t);
    float v = maskValue(e, tc, s);
    if (v < 0.25f) return 1;
    float n3 = g.n3.GetNoise((float)x, (float)y);
    float m = clamp01(((n3 + 1.0f) * 0.585f - (0.135f - n3 * 0.135f)) + (1.0f - v) * 0.1f);
    if ((m * 0.05f - (1.0f - m) * 0.05f) + 0.25f <= v) {
        float n2 = g.n2.GetNoise((float)x, (float)y);
        double dm = (double)m;
        if (dm < 0.2) return 4;
        float k = clamp01((n2 + 1.0f) * 0.65f - (0.15f - n2 * 0.15f));
        double dk = (double)k;
        if (dm < 0.4) return dk >= 0.4 ? 5 : 2;
        if (dm >= 0.7) return dk < 0.35 ? 3 : 2;
        if (dk >= 0.2) { if (dk < 0.4) return 2; if (dk >= 0.8) return 4; return 6; }
    }
    return 3;
}

// Placement only ever asks "is this tile void (class 1)?", which depends on N1 alone.
static bool isVoid(const Gen& g, int x, int y) {
    float Rf = g.Rm;
    float a = Rf * 0.33333334f, b = g.Rb * 0.14444444f;
    float lo = a - b;
    float d = (float)std::sqrt((double)(x * x + y * y));
    float tc = clamp01(d * 0.0125f);
    float t = clamp01((d - lo) / ((b + a) - lo));
    float s = smoothFalloff(t);
    auto voidOf = [&](float n1) {
        float e = clamp01((n1 + 1.0f) * 0.5f);
        float v = maskValue(e, tc, s);
        return v < 0.25f;
    };
    static const bool slow = getenv("SLOWVOID") != nullptr;   // full 8-octave evaluation (reference)
    if (slow) return voidOf(g.n1.GetNoise((float)x, (float)y));
    return g.n1.below((float)x, (float)y, voidOf);   // same answer, stops once the remaining octaves can't matter
}

// ---------------------------------------------------------------- placement
// UE's FMath::SinCos (UnrealMathUtility.h), float version. fma = emulate clang's contraction of each a*b+c.
static void ueSinCos(float* S, float* C, float value, bool fma) {
    const float PI = 3.1415926535897932f, INV_PI = 0.31830988618f, HALF_PI = 1.57079632679f;
    volatile float vol;   // keep MSVC from fusing or reassociating anything in the non-FMA path
    float quotient = (INV_PI * 0.5f) * value;
    if (value >= 0.0f) quotient = (float)((int)(quotient + 0.5f));
    else quotient = (float)((int)(quotient - 0.5f));
    float y;
    if (fma) y = std::fmaf(-(2.0f * PI), quotient, value);
    else { vol = (2.0f * PI) * quotient; y = value - vol; }
    float sign;
    if (y > HALF_PI) { y = PI - y; sign = -1.0f; }
    else if (y < -HALF_PI) { y = -PI - y; sign = -1.0f; }
    else sign = +1.0f;
    float y2 = y * y;
    auto mad = [&](float a, float b, float c) { if (fma) return std::fmaf(a, b, c); vol = a * b; return vol + c; };
    float s = mad(-2.3889859e-08f, y2, 2.7525562e-06f);
    s = mad(s, y2, -0.00019840874f); s = mad(s, y2, 0.0083333310f); s = mad(s, y2, -0.16666667f); s = mad(s, y2, 1.0f);
    *S = s * y;
    float p = mad(-2.6051615e-07f, y2, 2.4760495e-05f);
    p = mad(p, y2, -0.0013888378f); p = mad(p, y2, 0.041666638f); p = mad(p, y2, -0.5f); p = mad(p, y2, 1.0f);
    *C = sign * p;
}

struct Region { int x0, y0; uint32_t size; };
struct Params { float target, minN, maxN, minSpacing; };
// Angle and distance in the game are functions of (rng % 1000000), so they can be tabulated exactly.
// cos/sin interleaved so one attempt touches one cache line; sqrt is cheaper to compute than to look up.
// wasm: the page hands the sin/cos table over in memory (no file system)
static float* bld_sincos_ext = nullptr;
struct PolarTables {
    std::vector<float> csn;   // [2k] = cos, [2k+1] = sin
    PolarTables() : csn(2000000) {
        const float PI_E6 = 3.14159274e-06f;
        // GEN2: the angle goes through UE's FMath::SinCos (minimax polynomial, a few ulp off libm). Only visible when
        // cos*dist lands exactly on .5: all 6 such placements in the dataset follow the polynomial, 2 of them contradict
        // libm. Env SINCOS: 0 libm (MSVC cosf/sinf, Gen 1 generator), 1 UE polynomial, 2 UE polynomial with arm64 FMA
        // contraction (default).
        // iOS default SINCOS=3: accurate sinf/cosf (Apple libm; FMath::Sin/Cos = sinf/cosf in UE source), 2026-09-29
        const int sc = getenv("SINCOS") ? atoi(getenv("SINCOS")) : 3;
        // SINCOS=4: table from MSVC /fp:fast combined sincos (sincos_fast.exe -> sincos_fast.bin next to the exe)
        std::vector<float> fastTab;
        if (sc == 4 && bld_sincos_ext) fastTab.assign(bld_sincos_ext, bld_sincos_ext + 2000000);
        else if (sc == 4) { FILE* f = fopen(getenv("SINCOSBIN") ? getenv("SINCOSBIN") : "sincos_fast.bin", "rb"); if (!f) { fprintf(stderr, "no sincos_fast.bin\n"); exit(1); }
                       fastTab.resize(2000000); fread(fastTab.data(), 4, 2000000, f); fclose(f); }
        for (int k = 0; k < 1000000; k++) {
            // iOS default ANGMODE=3: angle = (float)((double)(k / 1e6f) * 2pi) -- fits all 51 decisive ties, 5456/5456
            // dataset and 56/56 own Gen 2 saves. 0 = PC form k*PI_E6*2 (MSVC /fp:fast folded the same source).
            static const int am = getenv("ANGMODE") ? atoi(getenv("ANGMODE")) : 3;
            float ang;
            if (am == 1) { volatile float t = (float)k * 1e-6f; ang = t * 6.2831855f; }
            else if (am == 2) { volatile float t = (float)k * 9.99999997e-07f; ang = 6.2831855f * t; }
            else if (am == 3) { volatile float t = (float)k / 1000000.0f; ang = (float)((double)t * 6.283185307179586); }   // iOS: float k/1e6, double 2*pi
            else { ang = (float)k * PI_E6; ang = ang + ang; }
            if (sc == 0) { csn[2 * k] = std::cos(ang); csn[2 * k + 1] = std::sin(ang); }
            else if (sc == 4) { csn[2 * k] = fastTab[2 * k]; csn[2 * k + 1] = fastTab[2 * k + 1]; }
            else if (sc == 3) { csn[2 * k] = (float)std::cos((double)ang); csn[2 * k + 1] = (float)std::sin((double)ang); }   // correctly rounded
            else ueSinCos(&csn[2 * k + 1], &csn[2 * k], ang, sc == 2);
        }
    }
};
// Distance sqrt(k / 1e6f) on iOS (INFERRED, not proven: 0 of 80,000 test worlds depend on it; chosen because the angle
// and the cluster-size fraction are proven k / 1e6f divisions on iOS and clang keeps divisions without fast-math).
// PC: k * 9.99999997e-07f (MSVC /fp:fast reciprocal). Env SQDIV = 0 or 1 overrides. Read at startup, so the mode must
// be in the environment then (GEN1 / IOSG1), as it is for the wrapper and the locked builds.
static const bool g_sqdiv = getenv("SQDIV") ? atoi(getenv("SQDIV")) != 0 : !(getenv("GEN1") && !getenv("IOSG1"));
static inline float polarDist(uint32_t k) { return std::sqrt(g_sqdiv ? (float)k / 1000000.0f : (float)k * 9.99999997e-07f); }
static const PolarTables& polar() { static PolarTables t; return t; }

// Per-thread flat tile cache covering the map plus a margin; tiles outside fall back to a hash map.
// Cell bits 0-1: 1 = void, 0 = land (filters unknown), 2 = land + filters fail, 3 = land + filters pass.
// Bit 7: occupied by a node. Cells are valid only when stamp == current generation (avoids clearing).
struct TileGrid {
    int half = 0, W = 0; uint16_t gen = 0;
    std::vector<uint16_t> stamp; std::vector<uint8_t> val;
    void begin(int R) {
        int h = R / 2 + 512;
        if (h != half) { half = h; W = 2 * h + 1; stamp.assign((size_t)W * W, 0); val.assign((size_t)W * W, 0); gen = 0; }
        if (++gen == 0) { std::fill(stamp.begin(), stamp.end(), 0); gen = 1; }
    }
    uint8_t* cell(int x, int y) {
        if (x < -half || x > half || y < -half || y > half) return nullptr;
        return &val[(size_t)(y + half) * W + (x + half)];
    }
    bool fresh(int x, int y) { size_t i = (size_t)(y + half) * W + (x + half); if (stamp[i] == gen) return false; stamp[i] = gen; return true; }
};
static TileGrid tgrid;

struct Ctx {
    const Gen* g; MT19937* rng; NodeMap nodes; robin_hood::unordered_flat_map<int32_t, int32_t> count;
    std::vector<std::pair<float, float>> centers;
    robin_hood::unordered_flat_map<IV, uint8_t, IVHash> overflow;   // tiles outside tgrid
    uint8_t& slot(int x, int y) {
        uint8_t* p = tgrid.cell(x, y);
        if (p) { if (tgrid.fresh(x, y)) *p = isVoid(*g, x, y) ? 1 : 0; return *p; }
        auto r = overflow.try_emplace(IV{x, y}, (uint8_t)0);
        if (r.second) r.first->second = isVoid(*g, x, y) ? 1 : 0;
        return r.first->second;
    }
    int C(int x, int y) { return (slot(x, y) & 3) == 1 ? 1 : 0; }
    // Node filters (as in the game): pow((N5+1)/2, 2.2) >= 0.35 and N12/2 >= 0.
    // Returns the cell so the caller can test/set occupancy without a second lookup.
    uint8_t* nodeOk(int x, int y) {
        uint8_t& s = slot(x, y);
        if ((s & 3) == 1) return nullptr;
        if ((s & 3) == 0) {
            float n5 = g->n5.GetNoise((float)x, (float)y);
            float p = (float)n5Pow(n5);
            bool ok = 0.35 <= (double)p && n12Pass(g->n12.GetNoise((float)x, (float)y), x, y);
            s = (uint8_t)((s & 0x80) | (ok ? 3 : 2));
        }
        return (s & 3) == 3 ? &s : nullptr;
    }
};

// Exact (int)roundf(v) (half away from zero) for |v| < 2^23, without the CRT call.
static inline int roundHalfAway(float v) {
    int i = (int)v;                 // truncates toward zero
    float f = v - (float)i;         // exact for |v| < 2^23
    return i + (f >= 0.5f) - (f <= -0.5f);
}

// True if some tile an attempt could hit at radius r is land, passes the node filters, and is free.
// Attempt offsets have length <= r*0.9999995 before rounding, so after rounding they lie within r+0.71.
static bool anyFreeTile(Ctx& c, int cx, int cy, float r) {
    int B = (int)r + 1; float lim = (r + 1.0f) * (r + 1.0f);
    for (int dx = -B; dx <= B; dx++)
        for (int dy = -B; dy <= B; dy++) {
            if ((float)(dx * dx + dy * dy) > lim) continue;
            uint8_t* s = c.nodeOk(cx + dx, cy + dy);
            if (s && !(*s & 0x80)) return true;
        }
    return false;
}

// GEN2 diagnostics: when non-null, placeCluster records attempts that land on tiles with |v - 0.25| < 3e-4 and pass the
// node filters (x, y, placed?) so a fitter can use them as land / void evidence in worlds that match exactly.
struct BorderRec { int x, y; bool placed; };
static std::vector<BorderRec>* g_border = nullptr;
static float landV(const Gen& g, int x, int y) {
    float Rf = g.Rm, a = Rf * 0.33333334f, b = g.Rb * 0.14444444f, lo = a - b;
    float d = (float)std::sqrt((double)(x * x + y * y));
    float tc = clamp01(d * 0.0125f), t = clamp01((d - lo) / ((b + a) - lo));
    float e = clamp01((g.n1.GetNoise((float)x, (float)y) + 1.0f) * 0.5f), s = smoothFalloff(t);
    return (e * tc + (1.0f - tc) * 0.5f) * (1.0f - s);
}

// GEN2 diagnostics: trace log (fitmap "trace" mode). Level 1: centers and placed nodes; level 2 also every attempt
// of cluster number g_traceCl.
static std::string* g_tlog = nullptr;
static long g_attempts = 0, g_ffskip = 0, g_scans = 0;   // profiling counters
struct TieRec { uint32_t ka, kd; float r, fx, fy; int cx, cy, x, y, xalt, yalt; bool placed; };
static std::vector<TieRec>* g_ties = nullptr;   // attempts whose float offset is exactly k + 0.5
struct EffortRec { int growths; long attempts; int x, y, idxInCluster, n; };
static std::vector<EffortRec>* g_effort = nullptr;   // per placement: growths / total attempts so far in cluster
static int g_traceCl = -1, g_clIdx = 0;

static void placeCluster(Ctx& c, int cx, int cy, int n, float r, int type) {
    const Gen& g = *c.g;
    int lo = (int)(-r);
    if ((float)lo <= r) {
        float inv = 1.0f / (r * r), frac = 0.0f;
        // frac only grows, so stopping once it exceeds 0.1 gives the same decision as the full sum.
        for (int i = lo; (float)i <= r; i++) {
            for (int j = lo; (float)j <= r; j++)
                frac += (float)(c.C(i + cx, j + cy) == 1) * inv;
            if (0.1f < frac) { if (g_tlog) *g_tlog += "  void-skip\n"; return; }
        }
    }
    const bool tr2 = g_tlog && g_clIdx == g_traceCl;
    int placed = 0, attempt = 0;
    const PolarTables& P = polar();
    int fails = 0; bool reachable = true;   // reachable==false is never cached; see hopeless check below
    int growths = 0; long total = 0;        // diagnostics (g_effort)
    while (placed < n) {
        // Fast-forward: once no free, filter-passing tile is reachable at the current radius, every
        // remaining attempt before the next radius growth must fail. Those attempts only consume two
        // RNG draws each, so discard exactly that many draws and jump to the growth step.
        if (fails >= 32 && reachable) {
            fails = 0;
            g_scans++;
            if (!anyFreeTile(c, cx, cy, r)) {
                if (tr2) *g_tlog += "  fast-forward from attempt " + std::to_string(attempt) + "\n";
                c.rng->discard(2ull * (1000 - attempt));
                total += 1000 - attempt; g_ffskip += 1000 - attempt;
                attempt = 1000;
            } else reachable = false;   // don't re-scan until something changes (placement or growth)
        }
        total++; g_attempts++;
        int a1 = attempt + 1;
        if (a1 > 1000) { r += 3.0f; reachable = true; growths++; }
        attempt = (a1 <= 1000) ? a1 : 0;
        // Every attempt consumes exactly two draws (angle, distance), so the angle of the attempt
        // PF attempts ahead is the draw 2*PF from now: prefetch its cos/sin entry to hide the miss.
        constexpr int PF = 6;
        uint32_t future;
        if (c.rng->peek(2 * PF, future)) _mm_prefetch((const char*)&P.csn[2 * (future % 1000000u)], _MM_HINT_T0);
        uint32_t ka = (*c.rng)() % 1000000u;
        uint32_t kd = (*c.rng)() % 1000000u;
        float dist = polarDist(kd) * r;
        // GEN2 experiment (env POSMODE): 0 round(cos*dist) + cx (Gen 1), 1 round(fma(cos, dist, cx)) (arm64 contraction
        // of cx + cos*dist), 2 round(cx + cos*dist) in float, 3 floor(fma(cos, dist, cx) + 0.5)
        static const int posmode = getenv("POSMODE") ? atoi(getenv("POSMODE")) : 0;
        int x, y;
        if (posmode == 0) { x = roundHalfAway(P.csn[2 * ka] * dist) + cx; y = roundHalfAway(P.csn[2 * ka + 1] * dist) + cy; }
        else if (posmode == 1) { x = (int)std::roundf(std::fmaf(P.csn[2 * ka], dist, (float)cx)); y = (int)std::roundf(std::fmaf(P.csn[2 * ka + 1], dist, (float)cy)); }
        else if (posmode == 4) { x = (int)std::round((double)P.csn[2 * ka] * (double)dist) + cx; y = (int)std::round((double)P.csn[2 * ka + 1] * (double)dist) + cy; }
        else if (posmode == 2) { volatile float fx = (float)cx + P.csn[2 * ka] * dist, fy = (float)cy + P.csn[2 * ka + 1] * dist; x = (int)std::roundf(fx); y = (int)std::roundf(fy); }
        else { x = (int)std::floor(std::fmaf(P.csn[2 * ka], dist, (float)cx) + 0.5f); y = (int)std::floor(std::fmaf(P.csn[2 * ka + 1], dist, (float)cy) + 0.5f); }
        // Diagnostic (env FLIPKA=<ka>): round the near-.5 axis of attempts with this angle index the other way
        static const long flipka = getenv("FLIPKA") ? atol(getenv("FLIPKA")) : -1;
        if ((long)ka == flipka) {
            float fx = P.csn[2 * ka] * dist, fy = P.csn[2 * ka + 1] * dist;
            if (std::fabs(std::fabs(fx - std::trunc(fx)) - 0.5f) < 4e-6f) x = (x - cx == (int)std::trunc(fx)) ? (int)std::trunc(fx) + (fx < 0 ? -1 : 1) + cx : (int)std::trunc(fx) + cx;
            if (std::fabs(std::fabs(fy - std::trunc(fy)) - 0.5f) < 4e-6f) y = (y - cy == (int)std::trunc(fy)) ? (int)std::trunc(fy) + (fy < 0 ? -1 : 1) + cy : (int)std::trunc(fy) + cy;
        }
        // Game order: void check, occupied check, N5 filter, N12 filter. All are side-effect-free
        // rejections, so testing the cached tile filters before occupancy gives the same outcome.
        uint8_t* s = c.nodeOk(x, y);
        if (g_ties) {
            float fx = P.csn[2 * ka] * dist, fy = P.csn[2 * ka + 1] * dist;
            // near-ties: |frac| within 4e-6 of 0.5; alt tile = the other rounding of that axis
            float dx = std::fabs(std::fabs(fx - std::trunc(fx)) - 0.5f), dy = std::fabs(std::fabs(fy - std::trunc(fy)) - 0.5f);
            bool tx = dx < 4e-6f, ty = dy < 4e-6f;
            if (tx || ty) {
                int xa = x, ya = y;
                if (tx) xa = (x - cx == (int)std::trunc(fx)) ? (int)std::trunc(fx) + (fx < 0 ? -1 : 1) + cx : (int)std::trunc(fx) + cx;
                if (ty) ya = (y - cy == (int)std::trunc(fy)) ? (int)std::trunc(fy) + (fy < 0 ? -1 : 1) + cy : (int)std::trunc(fy) + cy;
                uint8_t* sa = c.nodeOk(xa, ya);
                bool altOk = sa && !(*sa & 0x80), ourOk = s && !(*s & 0x80);
                if (ourOk || altOk) g_ties->push_back({ka, kd, r, fx, fy, cx, cy, x, y, xa, ya, ourOk});
            }
        }
        if (tr2) {
            char b[256];
            float n5 = g.n5.GetNoise((float)x, (float)y); double p5 = std::pow((double)((n5 + 1.0f) * 0.5f), 2.2);
            float sq = polarDist(kd);
            snprintf(b, sizeof b, "  A r=%.9g ka=%u kd=%u fx=%.9g fy=%.9g alt=%.9g,%.9g -> (%d,%d) void=%d p5=%.7f n12=%.4e occ=%d %s\n", r, ka, kd,
                     P.csn[2 * ka] * dist, P.csn[2 * ka + 1] * dist, (P.csn[2 * ka] * sq) * r, (P.csn[2 * ka + 1] * sq) * r,
                     x, y, (int)isVoid(g, x, y), p5, g.n12.GetNoise((float)x, (float)y),
                     s ? (int)((*s & 0x80) != 0) : -1, (s && !(*s & 0x80)) ? "PLACE" : "");
            *g_tlog += b;
        }
        if (g_border) {
            float v = landV(g, x, y);
            if (std::fabs(v - 0.25f) < 3e-4f) {
                float n5 = g.n5.GetNoise((float)x, (float)y);
                bool filt = 0.35 <= n5Pow(n5) && n12Pass(g.n12.GetNoise((float)x, (float)y));
                uint8_t* cl = tgrid.cell(x, y); bool occ = cl ? (*cl & 0x80) != 0 : false;
                if (filt && !occ) g_border->push_back({x, y, s != nullptr});
            }
        }
        if (!s || (*s & 0x80)) { fails++; continue; }
        {   // experiment REJTILE="seed:x:y": reject that tile once (as if the game had rounded elsewhere)
            static unsigned rs = 0; static int rx = 0, ry = 0; static bool ri = false;
            if (!ri) { ri = true; if (const char* h = getenv("REJTILE")) sscanf(h, "%u:%d:%d", &rs, &rx, &ry); }
            thread_local unsigned usedFor = 0;
            if (rs && (unsigned)g.seed == rs && x == rx && y == ry && usedFor != rs + 1) { usedFor = rs + 1; fails++; continue; }
        }
        {   // GEN2 experiment G2HACK="cluster,mode": at that cluster's last node, mode 1 = reject it and keep trying,
            // mode 2 = end the cluster without it
            static int hc = -2, hm0 = 0; static unsigned hs = 0; thread_local int hm = -1;
            if (hc == -2) { hc = -1; if (const char* h = getenv("G2HACK")) sscanf(h, "%u:%d:%d", &hs, &hc, &hm0); }
            if (g_clIdx == 0 && placed == 0) hm = hm0;
            if ((unsigned)g.seed == hs && g_clIdx == hc && placed == n - 1) { if (hm == 1) { hm = 0; fails++; continue; } if (hm == 2) return; }   // mode 1 rejects once
        }
        *s |= 0x80;
        if (g_tlog) *g_tlog += "  P " + std::to_string(x) + " " + std::to_string(y) + " growths " + std::to_string(growths) + " total " + std::to_string(total) + "\n";
        if (g_effort) g_effort->push_back({growths, total, x, y, placed, n});
        c.nodes[IV{x, y}] = type;
        c.count[type]++;
        placed++;
        fails = 0; reachable = true;
    }
}

static void placeType(Ctx& c, const Region& reg, const Params& p, int type) {
    for (;;) {
        if (p.target <= (float)c.count[type]) return;
        float cx = (float)(reg.x0 + (int)((*c.rng)() % reg.size));
        float cy = (float)-(reg.y0 + (int)((*c.rng)() % reg.size));
        bool tooClose = false;
        for (auto& ce : c.centers) {
            float dx = ce.first - cx, dy = ce.second - cy;
            if (std::sqrt(dy * dy + dx * dx) < p.minSpacing) { tooClose = true; break; }
        }
        if (tooClose) { if (g_tlog) { char b[96]; snprintf(b, sizeof b, "X %d %g %g too-close\n", type, cx, cy); *g_tlog += b; } continue; }
        c.centers.push_back({cx, cy});
        // Cluster size n = frac * (max - min) + min. iOS (Gen 1 and Gen 2): frac = k / 1e6f (division) and the multiply-add
        // fused (arm64 clang contraction) -- 2026-09-30 test saves qgEX (Gen 2 200/200) and bzzk (Gen 1 100/200) need both,
        // jISQ rules out FMA alone; all 104 iOS saves + 5,456 dataset worlds exact. PC: frac = k * 9.99999997e-07f (MSVC
        // /fp:fast turned the division into a multiplication), no FMA. Env FRDIV / NFMA = 0 or 1 overrides.
        const bool pcMode = getenv("GEN1") && !getenv("IOSG1");
        static const bool nfma = getenv("NFMA") ? atoi(getenv("NFMA")) != 0 : !pcMode;
        static const bool frdiv = getenv("FRDIV") ? atoi(getenv("FRDIV")) != 0 : !pcMode;
        const uint32_t kfr = (*c.rng)() % 1000000u;
        float fr = frdiv ? (float)kfr / 1000000.0f : (float)kfr * 9.99999997e-07f;
        float n = nfma ? std::fmaf(fr, p.maxN - p.minN, p.minN) : fr * (p.maxN - p.minN) + p.minN;
        if (g_tlog) { char b[128]; snprintf(b, sizeof b, "C %d %d %g %g n=%.6f\n", g_clIdx, type, cx, cy, n); *g_tlog += b; }
        placeCluster(c, (int)cx, (int)cy, (int)n, std::sqrt(n), type);
        g_clIdx++;
    }
}

// minByBit (optional): minimum final count per type, indexed by flag bit (0 wood, 1 iron, 2 copper,
// 3 stone, 4 coal, 5 wolframite, 6 uranium). Types are generated one after another and the final
// cleanup only removes nodes, so a type's count right after it is placed is an upper bound on its
// final count: if that is already below the minimum, the seed can be abandoned. Returns false then.
// Upper bound on Earth Tokens/min from resource counts (calculator order: wood, stone, iron, copper,
// coal, wolframite, uranium). Each plane is a supergradient of the calculator's LP optimum with free
// power plants, which is concave in the counts, so min over planes >= the LP value everywhere.
struct EtBound {
    std::vector<std::array<double, 8>> planes;   // a, g0..g6
    double threshold = 0, margin = 1e-6;
    double eval(const double c[7]) const {
        double best = 1e300;
        for (auto& p : planes) { double v = p[0]; for (int i = 0; i < 7; i++) v += p[1 + i] * c[i]; if (v < best) best = v; }
        return best + margin;
    }
    bool load(const char* path) {
        FILE* f = fopen(path, "r"); if (!f) return false;
        char line[1024];
        while (fgets(line, sizeof line, f)) {
            if (strncmp(line, "plane,", 6)) continue;
            std::array<double, 8> p{};
            if (sscanf(line + 6, "%lf,%lf,%lf,%lf,%lf,%lf,%lf,%lf", &p[0], &p[1], &p[2], &p[3], &p[4], &p[5], &p[6], &p[7]) == 8) planes.push_back(p);
        }
        fclose(f); return !planes.empty();
    }
};
static const EtBound* g_bound = nullptr;

// Largest possible final count of a type: placement stops once count >= target, so the last cluster
// starts at <= ceil(target)-1 and adds at most the largest cluster size the RNG formula can produce.
static int typeCap(float target, float minN, float maxN) {
    int nmax = (int)(999999.0f * 9.99999997e-07f * (maxN - minN) + minN);
    return (int)std::ceil(target) - 1 + nmax;
}

static bool generate(const Gen& g, NodeMap& out, const int* minByBit = nullptr) {
    MT19937 rng((uint32_t)g.seed);
    rng.discard(1000);
    g_clIdx = 0;   // diagnostics: cluster numbering per world
    Ctx c; c.g = &g; c.rng = &rng;
    tgrid.begin(g.R);
    // GEN2 (fitrng2): start area -20..19, cluster size (int)(frac*4+6) = 6..9; spacing via env SPS (default 10)
    const bool gen1 = getenv("GEN1") != nullptr;
    Region start = gen1 ? Region{-40, -40, 80} : Region{-20, -20, 40};
    Params sp{1.0f, gen1 ? 15.0f : 6.0f, gen1 ? 25.0f : 10.0f, (float)atof(getenv("SPS") ? getenv("SPS") : "10")};
    for (int t : {1, 2, 4, 8}) placeType(c, start, sp, t);
    if (getenv("STARTONLY")) { out = std::move(c.nodes); return true; }

    // GEN2 map-stage experiment knobs (env): WDIV region half = R/WDIV, WSP spacing mult, WK cluster-size mult, WQ quota mult, WQU uranium quota mult, STOPAFTER flag
    auto envf = [](const char* k, float d) { const char* v = getenv(k); return v ? (float)atof(v) : d; };
    const int STOPAFTER = (int)envf("STOPAFTER", 0.0f);
    const float WSP = envf("WSP", 1.0f), WQ = envf("WQ", 0.2f), WQU = envf("WQU", 0.6f);
    // GEN2: map square half-size H = ceil(R/2) = 425 / 600 / 849: x0 = -H, side 2H (fitmap center fit: 850 / 1200 / 1698).
    const int H = g.H;
    Region world{-H, -H, (uint32_t)(2 * H)};
    // GEN2: center spacing min(2H/36, 50) (fitmap full: 2H beats R on ws 50, 1930 vs 1760 of 1981). Env SPR=1 uses R.
    const float Rsp = getenv("SPR") ? (float)g.R : (float)(2 * H);
    float spacing = getenv("WSPC") ? envf("WSPC", 0) : (float)std::fmin((double)(Rsp * 0.027777778f), 50.0) * WSP;
    float r350 = g.res * 350.0f * WQ, r280 = g.res * 280.0f * WQ;
    // GEN2 (fitmap size fit): cluster size minN..maxN; env WMIN, WMAXK (maxN = res * WMAXK)
    Params wp{0, envf("WMIN", 6.0f), g.res * envf("WMAXK", 20.0f), spacing};
    // Generation order == calculator order: wood(1) stone(8) iron(2) copper(4) coal(16) wolframite(32) uranium(64).
    static const int order[7] = {1, 8, 2, 4, 0x10, 0x20, 0x40};
    const double caps[7] = {
        (double)typeCap(r350, 20.0f, g.res * 50.0f), (double)typeCap(r280, 20.0f, g.res * 50.0f),
        (double)typeCap(r350, 20.0f, g.res * 50.0f), (double)typeCap(r280, 20.0f, g.res * 50.0f),
        (double)typeCap(r350, 20.0f, g.res * 50.0f), (double)typeCap(g.res * 150.0f, 20.0f, g.res * 50.0f),
        (double)typeCap(g.res * 30.0f, 5.0f, 10.0f)};
    int placedTypes = 0;
    auto keep = [&](int flag) {
        placedTypes++;
        if (STOPAFTER == flag) { out = c.nodes; abort(); }
        if (minByBit) {
            int b = 0; while ((1 << b) != flag) b++;
            if (c.count[flag] < minByBit[b]) return false;
        }
        if (g_bound) {
            double v[7];
            for (int i = 0; i < 7; i++) v[i] = i < placedTypes ? (double)c.count[order[i]] : caps[i];
            if (g_bound->eval(v) < g_bound->threshold) return false;
        }
        return true;
    };
    wp.target = r350; placeType(c, world, wp, 1);    if (!keep(1)) return false;
    wp.target = r280; placeType(c, world, wp, 8);    if (!keep(8)) return false;
    wp.target = r350; placeType(c, world, wp, 2);    if (!keep(2)) return false;
    wp.target = r280; placeType(c, world, wp, 4);    if (!keep(4)) return false;
    wp.target = r350; placeType(c, world, wp, 0x10); if (!keep(0x10)) return false;
    wp.target = g.res * 150.0f * WQ; placeType(c, world, wp, 0x20); if (!keep(0x20)) return false;
    // GEN2: uranium cluster size 3..6 (Gen 1 5..10 x 0.6; fitmap full: exact on ws 100/200)
    // Uranium quota: Gen 2 = res*18 exactly (2026-09-29 saves Test7 100/150, Test8 200/150: res*30*0.6f = 27.0000019f
    // places 3-5 extra uranium nodes, res*18 = 27 is exact). Env UQ30 = old res*30*WQU model. Gen 1: res*30 (WQU 1).
    const bool uq18 = getenv("UQ18") || (!gen1 && !getenv("UQ30"));
    Params up{uq18 ? g.res * 18.0f : g.res * 30.0f * WQU, envf("UMIN", 3.0f), envf("UMAX", 6.0f), spacing};
    placeType(c, world, up, 0x40);                   if (!keep(0x40)) return false;

    // Remove nodes whose 4 neighbours are all occupied (iteration order = robin_hood's, as in game).
    for (auto it = c.nodes.begin(); it != c.nodes.end();) {
        int x = it->first.x, y = it->first.y, occ = 0;
        IV nb[4] = {{x, y - 1}, {x - 1, y}, {x, y + 1}, {x + 1, y}};
        static const bool ev = getenv("ERASEVOID") != nullptr;   // experiment: void neighbours count as blocked
        for (auto& k : nb) occ += c.nodes.find(k) != c.nodes.end() || (ev && isVoid(*c.g, k.x, k.y));
        if (occ == 4) it = c.nodes.erase(it); else ++it;
    }
    out = std::move(c.nodes);
    return true;
}

static void totals(const NodeMap& m, int t[7]) {
    for (int i = 0; i < 7; i++) t[i] = 0;
    for (auto& kv : m) { int b = 0; while ((1 << b) != kv.second) b++; t[b]++; }
}

// Used by sampler.cpp (profiling build): run a batch file on the calling thread, discard output.
// ---------------------------------------------------------------- plant coverage (coarse)
// Greedy set cover of nodes by axis-aligned coverage rectangles (W x H, optionally also H x W).
// Ignores building footprints and fuel inputs. Returns newly covered node count for plant 1, 2, ...
// (non-increasing). Greedy is not optimal, so this may need slightly MORE plants than the true minimum.
struct Pt { int x, y; };
static std::vector<int> greedyCover(const std::vector<Pt>& nodes, int W, int H, bool rotate, int maxPlants) {
    const int n = (int)nodes.size();
    const int CS = 32;   // spatial hash cell size for counting
    robin_hood::unordered_flat_map<IV, std::vector<int>, IVHash> grid;
    auto cellOf = [&](int v) { return v >= 0 ? v / CS : -((-v + CS - 1) / CS); };
    for (int i = 0; i < n; i++) grid[IV{cellOf(nodes[i].x), cellOf(nodes[i].y)}].push_back(i);
    std::vector<char> covered(n, 0);
    auto countIn = [&](int x0, int y0, int w, int h, bool mark) {
        int c = 0;
        for (int cx = cellOf(x0); cx <= cellOf(x0 + w - 1); cx++)
            for (int cy = cellOf(y0); cy <= cellOf(y0 + h - 1); cy++) {
                auto it = grid.find(IV{cx, cy}); if (it == grid.end()) continue;
                for (int i : it->second) {
                    const Pt& p = nodes[i];
                    if (!covered[i] && p.x >= x0 && p.x < x0 + w && p.y >= y0 && p.y < y0 + h) { c++; if (mark) covered[i] = 1; }
                }
            }
        return c;
    };
    // Candidate windows: an optimal window can be slid until a node lies on its left edge and one on its bottom edge.
    struct Win { int x0, y0, w, h; };
    std::vector<Win> wins;
    std::vector<int> byX(n); for (int i = 0; i < n; i++) byX[i] = i;
    std::sort(byX.begin(), byX.end(), [&](int a, int b) { return nodes[a].x < nodes[b].x; });
    robin_hood::unordered_flat_set<uint64_t> seen;
    for (int shape = 0; shape < (rotate && W != H ? 2 : 1); shape++) {
        int w = shape ? H : W, h = shape ? W : H;
        size_t lo = 0, hi = 0;
        for (size_t k = 0; k < byX.size(); k++) {
            int x0 = nodes[byX[k]].x;
            while (lo < byX.size() && nodes[byX[lo]].x < x0) lo++;
            if (hi < lo) hi = lo;
            while (hi < byX.size() && nodes[byX[hi]].x < x0 + w) hi++;
            for (size_t j = lo; j < hi; j++) {
                int y0 = nodes[byX[j]].y;
                uint64_t key = ((uint64_t)(uint32_t)x0 << 34) ^ ((uint64_t)(uint32_t)y0 << 2) ^ (uint64_t)shape;
                if (seen.insert(key).second) wins.push_back({x0, y0, w, h});
            }
        }
    }
    // Lazy greedy: coverage counts only shrink as nodes get covered (submodular), so a stale heap entry
    // that still beats the next-best after recounting is the true maximum.
    std::vector<std::pair<int, int>> heap;
    for (int i = 0; i < (int)wins.size(); i++) heap.push_back({countIn(wins[i].x0, wins[i].y0, wins[i].w, wins[i].h, false), i});
    std::make_heap(heap.begin(), heap.end());
    std::vector<int> curve;
    while ((int)curve.size() < maxPlants && !heap.empty()) {
        std::pop_heap(heap.begin(), heap.end()); auto top = heap.back(); heap.pop_back();
        const Win& w = wins[top.second];
        int c = countIn(w.x0, w.y0, w.w, w.h, false);
        if (c == 0) continue;
        if (!heap.empty() && c < heap.front().first) { heap.push_back({c, top.second}); std::push_heap(heap.begin(), heap.end()); continue; }
        countIn(w.x0, w.y0, w.w, w.h, true);
        curve.push_back(c);
    }
    return curve;
}

int bld_run_batch_single(const char* file) {
    FILE* f = fopen(file, "r"); if (!f) return 1;
    unsigned long long s; int ws, ra, n = 0;
    while (fscanf(f, "%llu %d %d", &s, &ws, &ra) == 3) {
        Gen* g = new Gen;
        if (initGen(*g, (uint32_t)s, ws, ra)) { NodeMap m; generate(*g, m); int t[7]; totals(m, t); n += t[0]; }
        delete g;
    }
    fclose(f);
    return n;
}

#ifndef BLD_NO_MAIN
int main(int argc, char** argv) {
    if (argc >= 2 && !strcmp(argv[1], "--selftest")) {
        // MT19937 must match std::mt19937 exactly, including after discards of every size.
        for (uint32_t seed : {0u, 1u, 5489u, 1820315860u, 4294967295u}) {
            std::mt19937 a(seed); MT19937 b(seed);
            std::mt19937 r(seed ^ 0xabcdefu);
            for (int step = 0; step < 20000; step++) {
                unsigned k = r() % 3000;
                if (step & 1) { a.discard(k); b.discard(k); }
                if (a() != b()) { printf("MT MISMATCH seed=%u step=%d\n", seed, step); return 1; }
            }
        }
        printf("selftest ok\n");
        return 0;
    }
    if (argc >= 3 && !strcmp(argv[1], "--batch")) {
        FILE* f = fopen(argv[2], "r"); if (!f) return 1;
        struct Job { unsigned long long s; int ws, ra; int t[7]; bool ok; };
        std::vector<Job> jobs; Job j{};
        while (fscanf(f, "%llu %d %d", &j.s, &j.ws, &j.ra) == 3) jobs.push_back(j);
        fclose(f);
        int nth = argc > 3 ? atoi(argv[3]) : (int)std::thread::hardware_concurrency();
        // Optional: --min wood stone iron copper coal wolframite uranium  -> only print seeds meeting all minimums.
        int minByBit[7] = {0}; bool useMin = false;
        if (argc >= 12 && !strcmp(argv[4], "--min")) {
            int wd = atoi(argv[5]), st = atoi(argv[6]), ir = atoi(argv[7]), cu = atoi(argv[8]), co = atoi(argv[9]), wf = atoi(argv[10]), ur = atoi(argv[11]);
            int m[7] = {wd, ir, cu, st, co, wf, ur};   // flag-bit order
            memcpy(minByBit, m, sizeof m); useMin = true;
        }
        std::atomic<size_t> next{0};
        std::vector<std::thread> th;
        for (int k = 0; k < nth; k++) th.emplace_back([&] {
            for (size_t i; (i = next++) < jobs.size();) {
                Gen* g = new Gen; Job& jb = jobs[i];
                jb.ok = initGen(*g, (uint32_t)jb.s, jb.ws, jb.ra);
                if (jb.ok) {
                    NodeMap m;
                    jb.ok = generate(*g, m, useMin ? minByBit : nullptr);
                    if (jb.ok) {
                        totals(m, jb.t);
                        for (int b = 0; b < 7 && useMin; b++) if (jb.t[b] < minByBit[b]) jb.ok = false;   // after cleanup
                    }
                }
                delete g;
            }
        });
        for (auto& t : th) t.join();
        for (auto& jb : jobs) if (jb.ok)
            printf("%llu %d %d %d %d %d %d %d %d %d\n", jb.s, jb.ws, jb.ra, jb.t[0], jb.t[1], jb.t[2], jb.t[3], jb.t[4], jb.t[5], jb.t[6]);
        return 0;
    }
    if (argc >= 8 && !strcmp(argv[1], "--scan")) {
        // --scan <firstSeed> <count> <ws> <ra> <planes.csv> <threshold> [threads]
        // Generates seeds firstSeed..firstSeed+count-1, abandoning each as soon as the free-plant ET bound
        // (remaining types at their caps) drops below threshold. Prints survivors: seed then 7 counts
        // (calculator order) then the bound; a stats line goes to stderr.
        unsigned long long first = strtoull(argv[2], 0, 10), count = strtoull(argv[3], 0, 10);
        int ws = atoi(argv[4]), ra = atoi(argv[5]);
        static EtBound eb;
        if (!eb.load(argv[6])) { fprintf(stderr, "cannot load planes %s\n", argv[6]); return 1; }
        eb.threshold = atof(argv[7]); g_bound = &eb;
        int nth = argc > 8 ? atoi(argv[8]) : (int)std::thread::hardware_concurrency();
        std::atomic<unsigned long long> next{0};
        std::vector<std::vector<std::string>> outs(nth);
        auto t0 = std::chrono::steady_clock::now();
        std::vector<std::thread> th;
        for (int k = 0; k < nth; k++) th.emplace_back([&, k] {
            Gen* g = new Gen;
            for (;;) {
                unsigned long long base = next.fetch_add(64);
                if (base >= count) break;
                for (unsigned long long i = base; i < base + 64 && i < count; i++) {
                    uint32_t s = (uint32_t)(first + i);
                    if (!initGen(*g, s, ws, ra)) continue;
                    NodeMap m;
                    if (!generate(*g, m)) continue;
                    int t[7]; totals(m, t);
                    double c[7] = {(double)t[0], (double)t[3], (double)t[1], (double)t[2], (double)t[4], (double)t[5], (double)t[6]};
                    double b = eb.eval(c);
                    if (b < eb.threshold) continue;   // cleanup removed enough nodes to fall below
                    char buf[160];
                    snprintf(buf, sizeof buf, "%u %d %d %d %d %d %d %d %.6f", s, t[0], t[3], t[1], t[2], t[4], t[5], t[6], b);
                    outs[k].push_back(buf);
                }
            }
            delete g;
        });
        for (auto& t : th) t.join();
        double secs = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
        size_t surv = 0;
        for (auto& o : outs) for (auto& l : o) { printf("%s\n", l.c_str()); surv++; }
        fprintf(stderr, "scanned %llu seeds in %.1fs = %.1f seeds/s (%d threads); survivors %zu (%.4f%%)\n",
                count, secs, count / secs, nth, surv, 100.0 * surv / count);
        return 0;
    }
    if (argc >= 5 && !strcmp(argv[1], "--tiecands")) {
        // --tiecands <firstSeed> <count> <ws> <ra> [threads]: seeds whose generation contains a decisive near-tie
        // (|offset frac - 0.5| < 4e-6 where either rounding would place a node). Prints "seed ws ra n minDist".
        unsigned long long first = strtoull(argv[2], 0, 10), count = strtoull(argv[3], 0, 10);
        int ws = atoi(argv[4]), ra = atoi(argv[5]);
        int nth = argc > 6 ? atoi(argv[6]) : (int)std::thread::hardware_concurrency();
        std::atomic<unsigned long long> next{0}; std::vector<std::vector<std::string>> outs(nth); std::vector<std::thread> th;
        for (int k = 0; k < nth; k++) th.emplace_back([&, k] {
            Gen* g = new Gen;
            for (unsigned long long i; (i = next++) < count;) {
                uint32_t sd = (uint32_t)(first + i);
                if (!initGen(*g, sd, ws, ra)) continue;
                std::vector<TieRec> ties; g_ties = &ties;
                NodeMap m; try { generate(*g, m); } catch (int) {}
                g_ties = nullptr;
                if (ties.empty()) continue;
                double md = 1;
                for (auto& q : ties) for (float f : {q.fx, q.fy}) { double dd = std::fabs(std::fabs(f - std::trunc(f)) - 0.5); if (dd < 4e-6 && dd < md) md = dd; }
                char b[96]; snprintf(b, sizeof b, "%u %d %d %zu %.3g", sd, ws, ra, ties.size(), md); outs[k].push_back(b);
                if (getenv("TIEDUMP")) for (auto& q : ties) { char t[300]; snprintf(t, sizeof t, "T %u %d 1 %u %u %.9g %.9g %.9g %d %d %d %d %d %d %d",
                    sd, ws, q.ka, q.kd, q.r, q.fx, q.fy, q.cx, q.cy, q.x, q.y, q.xalt, q.yalt, (int)q.placed); outs[k].push_back(t); }
            }
            delete g;
        });
        for (auto& t : th) t.join();
        for (auto& o : outs) for (auto& l : o) printf("%s\n", l.c_str());
        return 0;
    }
    if (argc >= 5 && !strcmp(argv[1], "--tilecheck")) {
        // --tilecheck <seed> <ws> <file of "x y" lines>: for each tile print "x y void n5ok n12ok"
        // using the Gen 1 terrain/filter noise (isVoid, pow((N5+1)/2,2.2) >= 0.35, N12/2 >= 0).
        Gen g; if (!initGen(g, (uint32_t)strtoull(argv[2], 0, 10), atoi(argv[3]), 100)) return 1;
        FILE* f = fopen(argv[4], "r"); if (!f) return 1;
        int x, y;
        while (fscanf(f, "%d %d", &x, &y) == 2) {
            float n5 = g.n5.GetNoise((float)x, (float)y);
            float p = (float)std::pow((double)((n5 + 1.0f) * 0.5f), 2.2);
            float n12 = g.n12.GetNoise((float)x, (float)y);
            printf("%d %d %d %d %d\n", x, y, isVoid(g, x, y) ? 1 : 0, 0.35 <= (double)p ? 1 : 0, 0.0f <= n12 * 0.5f ? 1 : 0);
        }
        fclose(f);
        return 0;
    }
    if (argc >= 3 && !strcmp(argv[1], "--cover")) {
        // --cover <file of "seed ws ra"> [threads]: per seed, greedy coverage curves for nuclear (22x21, rotatable)
        // and coal (12x12). Output: seed ws ra total | N: k covered counts... | C: k covered counts...
        FILE* f = fopen(argv[2], "r"); if (!f) return 1;
        struct Job { unsigned long long s; int ws, ra; std::string out; };
        std::vector<Job> jobs; Job j{};
        while (fscanf(f, "%llu %d %d", &j.s, &j.ws, &j.ra) == 3) jobs.push_back(j);
        fclose(f);
        int nth = argc > 3 ? atoi(argv[3]) : (int)std::thread::hardware_concurrency();
        std::atomic<size_t> next{0}; std::vector<std::thread> th;
        for (int k = 0; k < nth; k++) th.emplace_back([&] {
            for (size_t i; (i = next++) < jobs.size();) {
                Job& jb = jobs[i]; Gen* g = new Gen;
                if (initGen(*g, (uint32_t)jb.s, jb.ws, jb.ra)) {
                    NodeMap m; generate(*g, m);
                    std::vector<Pt> pts; for (auto& kv : m) pts.push_back({kv.first.x, kv.first.y});
                    auto nuc = greedyCover(pts, 22, 21, true, 150), coal = greedyCover(pts, 12, 12, false, 400);
                    std::string o = std::to_string(jb.s) + " " + std::to_string(jb.ws) + " " + std::to_string(jb.ra) + " " + std::to_string(pts.size()) + " | N:";
                    for (int c : nuc) o += " " + std::to_string(c);
                    o += " | C:"; for (int c : coal) o += " " + std::to_string(c);
                    jb.out = o;
                }
                delete g;
            }
        });
        for (auto& t : th) t.join();
        for (auto& jb : jobs) if (!jb.out.empty()) printf("%s\n", jb.out.c_str());
        return 0;
    }
    if (argc < 2) { fprintf(stderr, "usage: bldgen <seed> [ws] [ra] [nodes] | --batch file | --cover file\n"); return 1; }
    uint32_t seed = (uint32_t)strtoull(argv[1], 0, 10);
    int ws = argc > 2 ? atoi(argv[2]) : 100, ra = argc > 3 ? atoi(argv[3]) : 100;
    Gen g; if (!initGen(g, seed, ws, ra)) { fprintf(stderr, "bad world size\n"); return 1; }
    std::vector<TieRec> ties; g_ties = &ties;
    NodeMap m; try { generate(g, m); } catch (int) {} int t[7]; totals(m, t);
    g_ties = nullptr;
    double md = 1; for (auto& q : ties) for (float f : {q.fx, q.fy}) { double dd = std::fabs(std::fabs(f - std::trunc(f)) - 0.5); if (dd < md) md = dd; }
    if (getenv("TIEFLAG")) fprintf(stderr, "neartie %zu %.3g\n", ties.size(), ties.empty() ? 1.0 : md);
    printf("flags 1 2 4 8 16 32 64: %d %d %d %d %d %d %d (total %zu)\n", t[0], t[1], t[2], t[3], t[4], t[5], t[6], m.size());
    if (argc > 4) for (auto& kv : m) printf("%d %d %d\n", kv.first.x, kv.first.y, kv.second);
    return 0;
}
#endif
