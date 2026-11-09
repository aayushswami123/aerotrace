#include "rank.h"

/* This file sees only the header, so its calls to f are ambiguous (probable). */
int used_probable(void) {
    if (f(1, 2) == 3) return 1;
    return 0;
}

void ignored_probable(void) { f(2, 3); }
