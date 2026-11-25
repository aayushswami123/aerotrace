#include "conn.h"

/* before any global: no initializer to blame, so it stays a blind spot */
_Static_assert(sizeof(((struct conn *)0)->hz) == sizeof(int), "hz is an int");

struct conn g;

struct entry {
    const char *name;
    int *p;
};

/* the address of g.hz is stored in this table (an escape row on line 16 proves it) */
struct entry table[] = {
    {"a", &g.a},
    {"hz", &g.hz},
};

/* the designator sets hz on the line where defaults starts */
struct conn defaults = {.a = 1, .hz = 7};

int counter = 0;

/* after a global, but not in any initializer the index records: probably counter's (it is not) */
_Static_assert(sizeof(((struct conn *)0)->hz) > 0, "hz has a size");

int first_name(void) { return table[0].name[0]; }
