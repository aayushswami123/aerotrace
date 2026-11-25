#include "defs.h"

int get(int k) {
    return k;
}

static int quiet(int a) {
    return a + 1;
}

int lib_entry(void) {
    return quiet(1);
}

int spare(int a) {
    return a * 3;
}
