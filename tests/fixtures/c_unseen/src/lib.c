#include "lib.h"

/* file comment */

static int k(void) {
    return 1;
}

int f(int a) {
    /* in fn */
    return a + k();
}

int g(int a) {
    return a;
}

int h(int a) {
    return a * 2;
}
