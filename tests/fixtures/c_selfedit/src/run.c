#include "g.h"

int run_one(void) {
    int x = 5;
    return old_helper() + x;
}

int run_two(void) {
    int a = old_pair();
    int b = old_pair();
    return a + b;
}
