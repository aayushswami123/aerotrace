/* this file already fails before any change (the header does not exist), so a change can't be judged */
#include "missing.h"
#include "filter.h"

int read_e(struct conf *c) { return c->upkeep_ms != 0; }
