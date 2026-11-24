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

/* Like Redis: run_cmd calls cmd_append only through a pointer; cmd_append calls cmd_check_len directly. */
long cmd_check_len(long n) {
    return n;
}

long cmd_append(long v) {
    return cmd_check_len(v);
}

static cmd_fn commands[] = {cmd_append};

long run_cmd(int i, long v) {
    return commands[i](v);
}
