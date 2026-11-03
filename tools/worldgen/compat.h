#pragma once
#include <stdlib.h>
static inline int _putenv_s(const char* k, const char* v) { return (v && *v) ? setenv(k, v, 1) : unsetenv(k); }
#define _mm_prefetch(p, h) ((void)(p))
#define _MM_HINT_T0 0
