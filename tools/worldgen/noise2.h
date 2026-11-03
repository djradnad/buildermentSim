// Re-implementation of FastNoise (legacy) 2D SimplexFractal FBM, float, with optional emulation of clang/arm64
// floating-point contraction (-ffp-contract=on: a*b+c inside one expression becomes one fused multiply-add).
// mode bit 0: t = 0.5 - x0*x0 - y0*y0 -> fma(-y0, y0, fma(-x0, x0, 0.5))
// mode bit 1: FBM sum += n * amp      -> fma(n, amp, sum)
// mode bit 2: x1 = x0 - i1 + G2 etc. unchanged (no multiply); reserved
// mode 0 must be bit-identical to FastNoise (checked by fitmap noisetest).
#pragma once
#include <cmath>
#include <cstdint>
#include <random>
#include <cstdio>

struct Noise2 {
    uint8_t perm[512], perm12[512];
    int oct = 1; float freq = 0.01f, gain = 0.5f, lac = 2.0f, bounding = 1.0f;
    int mode = 0; bool trace = false; mutable long calls = 0;
    void setup(int seed, float f, int o, float g, float l) {
        std::mt19937_64 gen(seed);
        for (int i = 0; i < 256; i++) perm[i] = (uint8_t)i;
        for (int j = 0; j < 256; j++) {
            int rng = (int)(gen() % (256 - j));
            int k = rng + j;
            int lv = perm[j];
            perm[j] = perm[j + 256] = perm[k];
            perm[k] = (uint8_t)lv;
            perm12[j] = perm12[j + 256] = perm[j] % 12;
        }
        freq = f; oct = o; gain = g; lac = l;
        float amp = gain, ampFractal = 1.0f;
        for (int i = 1; i < oct; i++) { ampFractal += amp; amp *= gain; }
        bounding = 1.0f / ampFractal;
    }
    static int ffloor(float f) { return f >= 0 ? (int)f : (int)f - 1; }
    float grad(uint8_t off, int x, int y, float xd, float yd) const {
        static const float GX[12] = {1, -1, 1, -1, 1, -1, 1, -1, 0, 0, 0, 0};
        static const float GY[12] = {1, 1, -1, -1, 0, 0, 0, 0, 1, -1, 1, -1};
        uint8_t lut = perm12[(x & 0xff) + perm[(y & 0xff) + off]];
        return xd * GX[lut] + yd * GY[lut];   // products are exact (+-1, 0): fused or not gives the same result
    }
    float tval(float a, float b) const {
        if (mode & 1) return std::fmaf(-b, b, std::fmaf(-a, a, 0.5f));
        return 0.5f - a * a - b * b;
    }
    float single(uint8_t off, float x, float y) const {
        const float SQRT3 = 1.7320508075688772935274463415059f;
        const float F2 = 0.5f * (SQRT3 - 1.0f);
        const float G2 = (3.0f - SQRT3) / 6.0f;
        float t = (x + y) * F2;
        int i = ffloor(x + t), j = ffloor(y + t);
        t = (i + j) * G2;
        float X0 = i - t, Y0 = j - t;
        float x0 = x - X0, y0 = y - Y0;
        int i1, j1; if (x0 > y0) { i1 = 1; j1 = 0; } else { i1 = 0; j1 = 1; }
        float x1 = x0 - (float)i1 + G2, y1 = y0 - (float)j1 + G2;
        float x2 = x0 - 1 + 2 * G2, y2 = y0 - 1 + 2 * G2;
        float n0, n1, n2;
        t = tval(x0, y0); if (t < 0) n0 = 0; else { t *= t; n0 = t * t * grad(off, i, j, x0, y0); }
        t = tval(x1, y1); if (t < 0) n1 = 0; else { t *= t; n1 = t * t * grad(off, i + i1, j + j1, x1, y1); }
        t = tval(x2, y2); if (t < 0) n2 = 0; else { t *= t; n2 = t * t * grad(off, i + 1, j + 1, x2, y2); }
        if (trace) printf("    oct x=%.9g y=%.9g i=%d j=%d x0=%.9g y0=%.9g x1=%.9g y1=%.9g x2=%.9g y2=%.9g n=%.3e %.3e %.3e g2=%.3e\n",
                          x, y, i, j, x0, y0, x1, y1, x2, y2, n0, n1, n2, grad(off, i + 1, j + 1, x2, y2));
        return 70 * (n0 + n1 + n2);
    }
    float GetNoise(float x, float y) const { return get(x, y); }
    // Evaluates pred(get(x, y)) for a predicate that is true for small values and false for large ones (monotone),
    // stopping early once the octaves still to come cannot change the answer. |single()| <= 0.885 for this simplex
    // (bound used: 1.0) and the partial sums are exactly the ones get() computes, so the result equals pred(get(x, y));
    // the margin 1e-4 covers float rounding of the bounds.
    template <class P> bool below(float x, float y, P pred) const {
        x *= freq; y *= freq;
        float sum = single(perm[0], x, y), amp = 1;
        double rem = 0; { double a = 1; for (int i = 1; i < oct; i++) { a *= gain; rem += a; } }
        for (int i = 1;; i++) {
            double r = rem * 1.0 + 1e-4;
            if (pred((float)((sum + r) * bounding))) return true;
            if (!pred((float)((sum - r) * bounding))) return false;
            if (i >= oct) break;
            x *= lac; y *= lac; amp *= gain;
            float s = single(perm[i], x, y);
            sum = (mode & 2) ? std::fmaf(s, amp, sum) : sum + s * amp;
            rem -= amp;
        }
        return pred(sum * bounding);
    }
    float get(float x, float y) const {
        calls++;
        x *= freq; y *= freq;
        float sum = single(perm[0], x, y), amp = 1;
        for (int i = 1; i < oct; i++) {
            x *= lac; y *= lac; amp *= gain;
            float s = single(perm[i], x, y);
            sum = (mode & 2) ? std::fmaf(s, amp, sum) : sum + s * amp;
        }
        return sum * bounding;
    }
};
