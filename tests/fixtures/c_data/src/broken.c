#include "data.h"

int still_uses_gone(struct conn *c) {
    return c->gone;
}

int too_few_args(void) {
    return parse_num("1");
}
