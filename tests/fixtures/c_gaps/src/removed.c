#include "gaps.h"

/* #25: a removed global */
int read_gone_global(void) { return gone_global; }

/* #29: more errors than clang's default limit (20), each a use of a removed field on its own line */
int use_gone(struct inner *i) {
    int n = 0;
    n += i->gone;
    n += i->gone;
    n += i->gone;
    n += i->gone;
    n += i->gone;
    n += i->gone;
    n += i->gone;
    n += i->gone;
    n += i->gone;
    n += i->gone;
    n += i->gone;
    n += i->gone;
    n += i->gone;
    n += i->gone;
    n += i->gone;
    n += i->gone;
    n += i->gone;
    n += i->gone;
    n += i->gone;
    n += i->gone;
    n += i->gone;
    n += i->gone;
    n += i->gone;
    n += i->gone;
    n += i->gone;
    n += i->gone;
    n += i->gone;
    n += i->gone;
    n += i->gone;
    n += i->gone;
    return n;
}
