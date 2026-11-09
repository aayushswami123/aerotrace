#include "rank.h"

/* Does not compile: one argument too few. Must be listed first even though this file sorts last. */
int broken_call(void) { return f(1); }
