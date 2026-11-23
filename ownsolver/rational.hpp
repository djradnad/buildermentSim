// Exact rationals on __int128 with overflow detection (an overflow sets a flag; callers must then not trust results).
#pragma once
#include <cstdint>
#include <cstdlib>

typedef __int128 i128;

struct QCtx { bool overflow = false; };
extern QCtx qctx;

static inline i128 iabs(i128 a) { return a < 0 ? -a : a; }
static inline i128 igcd(i128 a, i128 b) {
    a = iabs(a); b = iabs(b);
    while (b) { i128 t = a % b; a = b; b = t; }
    return a;
}
static const i128 QLIM = ((i128)1) << 62;

struct Q {
    i128 n = 0, d = 1;
    Q() {}
    Q(long long a) : n(a), d(1) {}
    Q(i128 a, i128 b) : n(a), d(b) { norm(); }
    void norm() {
        if (d < 0) { n = -n; d = -d; }
        i128 g = igcd(n, d);
        if (g > 1) { n /= g; d /= g; }
        if (n == 0) d = 1;
        if (iabs(n) > QLIM || d > QLIM) qctx.overflow = true;
    }
    bool zero() const { return n == 0; }
};
static inline Q mulq(const Q& a, const Q& b) {
    if (qctx.overflow) return Q();
    i128 g1 = igcd(a.n, b.d), g2 = igcd(b.n, a.d);
    if (g1 == 0) g1 = 1;
    if (g2 == 0) g2 = 1;
    return Q((a.n / g1) * (b.n / g2), (a.d / g2) * (b.d / g1));
}
static inline Q divq(const Q& a, const Q& b) { return mulq(a, Q(b.d, b.n)); }
static inline Q addq(const Q& a, const Q& b) {
    if (qctx.overflow) return Q();
    i128 g = igcd(a.d, b.d);
    return Q(a.n * (b.d / g) + b.n * (a.d / g), a.d / g * b.d);
}
static inline Q subq(const Q& a, const Q& b) { Q c = b; c.n = -c.n; return addq(a, c); }
static inline int cmpq(const Q& a, const Q& b) {   // valid while |n|,d < 2^62
    i128 l = a.n * b.d, r = b.n * a.d;
    return l < r ? -1 : l > r ? 1 : 0;
}
