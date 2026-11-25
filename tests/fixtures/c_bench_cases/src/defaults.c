#include "filter.h"

/* an initializer that sets the field: after upkeep_ms becomes void *, 5000 no longer fits */
struct conf g_conf = {.a = 1, .upkeep_ms = 5000};
