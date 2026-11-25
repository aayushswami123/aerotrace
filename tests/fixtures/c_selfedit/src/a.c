#include "g.h"

int* addr_a(void) {
    int pad = 1;
    (void)pad;
    return &counter;
}
