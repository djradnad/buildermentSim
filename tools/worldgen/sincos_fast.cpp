// Build with /O2 /fp:fast: MSVC may compute sin+cos of the same angle with one combined call
// (__libm_sse2_sincosf_). Writes the table for k = 0..999999 (ang = k*PI_E6 doubled) to sincos_fast.bin as float
// pairs (cos, sin), and prints the values for a few test angles next to separate /fp:precise-style calls.
#include <cmath>
#include <cstdio>
#include <vector>
__declspec(noinline) void sc(float a, float* c, float* s) { *c = std::cos(a); *s = std::sin(a); }
int main() {
    const float PI_E6 = 3.14159274e-06f;
    std::vector<float> t(2000000);
    for (int k = 0; k < 1000000; k++) { float ang = (float)k * PI_E6; ang = ang + ang; sc(ang, &t[2 * k], &t[2 * k + 1]); }
    FILE* f = fopen("sincos_fast.bin", "wb"); fwrite(t.data(), 4, t.size(), f); fclose(f);
    for (int k : {294290, 946520, 431592, 908795, 577338}) printf("k %d cos %.9g sin %.9g\n", k, t[2 * k], t[2 * k + 1]);
    return 0;
}
