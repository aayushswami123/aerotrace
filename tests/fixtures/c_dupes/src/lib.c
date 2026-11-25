#include "util.h"

int calc(int a) {
    return a;
}

static int helper(void) {
    return 1;
}

int lib_entry(void) {
    return helper();
}

__attribute__((weak)) int hook(void) {
    return 0;
}

// inline candidate
int c_commented(int a) {
    return a;
}

static inline int sq(int x) { return x * x; }
int c_after_inline(int a) {
    return sq(a);
}

extern inline int c_extinl(int a) {
    return a;
}

inline int c_plain_inline(int a) {
    return a;
}
