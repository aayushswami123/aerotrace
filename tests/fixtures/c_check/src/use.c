#include "api.h"

#define PARSE_TWICE(v) parse(parse(v))

int use_parse(int v) {
    return parse(v) + PARSE_TWICE(v);
}

int use_scale(void) {
    return scale(2, 3);
}

void use_status(void) {
    int s = status();
    (void)status();
    status();
    (void)s;
}

int use_old(void) {
    return old_style(1);
}

void use_log(void) {
    log_msg("x");
    log_msg("%d", 1);
}

int read_count(struct conn *c) {
    return c->count;
}

void write_count(struct conn *c) {
    c->count = 5;
}

int read_flags(struct conn *c) {
    return c->flags;
}

int read_max(void) {
    return max_clients;
}

long read_total(void) {
    return g_conn.total;
}
