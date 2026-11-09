#include <string.h>
#include "data.h"

int g_total;
struct conn conns[4];
static parser_fn parsers[] = { parse_num };
static struct conn defaults = { .fd = -1 };

int parse_num(const char *s, int *out) {
    *out = (int)strlen(s);
    return *out > 0;
}

void log_num(int n) {
    g_total += n;
}

void abort_now(void) {
    g_total = -1;
}

void register_parser(parser_fn fn) {
    parsers[0] = fn;
}

void fill(int *p) {
    *p = 7;
}
