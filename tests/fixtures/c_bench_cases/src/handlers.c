#include "filter.h"

/* redis-05: nothing calls it by name; only cmd_table in cmds.c names it */
int get_keys(int n) { return n + 1; }
