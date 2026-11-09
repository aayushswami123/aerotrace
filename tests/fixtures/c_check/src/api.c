#include "api.h"

int max_clients = 1000;
struct conn g_conn;
struct conn defaults = {1, 2, 0, 3};

int parse(int x) {
    return x + 1;
}

int scale(int a, int b) {
    return a * b;
}

int status(void) {
    return 0;
}

int old_style(x)
    int x;
{
    return x;
}

int log_msg(const char *fmt, ...) {
    (void)fmt;
    return 0;
}

int touch(struct conn *c) {
    return c->fd;
}

static handler_fn handlers[] = {parse};

int dispatch(int i, int v) {
    return handlers[i](v);
}
