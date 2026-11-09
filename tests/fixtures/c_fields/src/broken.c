#include "fields.h"
#define BUMP_GONE(c) ((c)->gone++)

int still_uses_gone(struct conn *c) { return c->gone; }
void bump_twice(struct conn *c) { BUMP_GONE(c); BUMP_GONE(c); }
void bump_once(struct conn *c) {
    BUMP_GONE(c);
}
static struct conn gone_init = {.gone = 1};
struct conn *gone_init_ptr(void) { return &gone_init; }
typedef struct conn conn_t;
int via_typedef(conn_t *c) { return c->lost; }
