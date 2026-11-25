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
