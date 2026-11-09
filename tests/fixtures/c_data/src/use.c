#include <string.h>
#include "data.h"

int uses(struct conn *c, const char *s) {
    int v = 0;
    parse_num(s, &v);
    (void)parse_num(s, &v);
    int ok = parse_num(s, &v);
    c->count = parse_num(s, &v);
    if (parse_num(s, &v) == 0) ok++;
    if (parse_num(s, &v)) ok++;
    if (!parse_num(s, &v)) ok++;
    log_num(parse_num(s, &v));
    ok += parse_num(s, &v);
    CHECK(parse_num(s, &v));
    return parse_num(s, &v);
}

int through_pointer(parser_fn f, const char *s) {
    int v;
    return f(s, &v) * 2;
}

void setup(void) {
    register_parser(parse_num);
}

void touch(struct conn *c) {
    c->count++;
}

void touch_array(void) {
    conns[1].count = 0;
}

int buf_count(struct buf *b) {
    return b->count;
}

void escapes(void) {
    int *p = &g_total;
    fill(&g_total);
    memset(&g_total, 0, sizeof g_total);
    (void)p;
}
