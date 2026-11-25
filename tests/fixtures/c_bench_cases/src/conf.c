#include "filter.h"

/* curl-07: after upkeep_ms becomes void *, a number assigned to it no longer builds */
void conf_init(struct conf *c) { c->upkeep_ms = 60000L; }

/* these still build with void *: a comparison with 0 and the null pointer constant */
int conf_due(struct conf *c) { return c->upkeep_ms != 0; }

void conf_reset(struct conf *c) { c->upkeep_ms = 0; }
