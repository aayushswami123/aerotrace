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

/* after a global, but not in any initializer the index records: only probably counter's (it is not),
   so it also stays a blind spot */
_Static_assert(sizeof(((struct conn *)0)->hz) > 0, "hz has a size");

/* two globals on one line: the index can't tell which one breaks, so both are probable */
int x = 1, y = sizeof(g.hz);

int first_name(void) { return table[0].name[0]; }

/* removing the global rate (here and its extern in conn.h) breaks pr's initializer */
int rate = 5;
int *pr = &rate;
