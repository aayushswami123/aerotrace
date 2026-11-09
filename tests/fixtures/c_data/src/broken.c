#include "data.h"

int still_uses_gone(struct conn *c) {
    return c->gone;
}

int too_few_args(void) {
    return parse_num("1");
}

static int wants_two(int a, int b) {
    return a + b;
}

int calls_wants_two(void) {
    return wants_two(1);
}
